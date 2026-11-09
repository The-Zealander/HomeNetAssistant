"""Download music and maintain its metadata, album layout, and recovery log."""

import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    ID3NoHeaderError,
    TCOM,
    TCMP,
    TCON,
    TALB,
    TEXT,
    TDRC,
    TDOR,
    TDRL,
    TIT1,
    TIT2,
    TPE1,
    TPE2,
    TPUB,
    TPOS,
    TRCK,
    TSRC,
    TXXX,
)
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
DOWNLOAD_DIR = os.environ.get("MUSIC_DOWNLOAD_PATH", str(Path.home() / "Musik"))
MASTER_LOG_FILE = os.path.join(DOWNLOAD_DIR, "_samlet_arkiv.txt")
logger = logging.getLogger(__name__)
_MUSICBRAINZ_MIN_INTERVAL = 1.1
_MUSICBRAINZ_FAILURE_COOLDOWN = 60.0
_MUSICBRAINZ_LAST_REQUEST = 0.0
_MUSICBRAINZ_UNAVAILABLE_UNTIL = 0.0
_MUSICBRAINZ_LOCK = threading.Lock()
_MUSICBRAINZ_CACHE = {}
_MUSICBRAINZ_RELEASE_CACHE = {}


def clean_name(name):
    """Fjerner ulovlige tegn fra mappe- og filnavne og fjerner 'Album - '."""
    if not name:
        return "Ukendt"
    name = re.sub(r'(?i)^Album\s*-\s*', '', str(name))
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def artist_names(value):
    """Return individual artist credits suitable for multi-value ID3 tags."""
    if isinstance(value, (list, tuple)):
        names = []
        for item in value:
            if isinstance(item, dict):
                item = item.get("name") or item.get("artist")
            if item:
                names.append(str(item).strip())
    elif value:
        name = str(value).strip()
        if re.fullmatch(
            r"[^,]+,\s+(?:the\s+.+|jr\.?|sr\.?|ii|iii|iv)",
            name,
            re.IGNORECASE,
        ):
            names = [name]
        else:
            names = re.split(
                r"\s*,\s*|\s+/\s+|\s*;\s*|\s+(?:feat(?:uring)?\.?|ft\.?)\s+",
                name,
                flags=re.IGNORECASE,
            )
    else:
        names = []

    return list(dict.fromkeys(name.strip() for name in names if name and name.strip()))


def _text_values(value):
    if isinstance(value, (list, tuple)):
        values = value
    elif value is None:
        values = []
    else:
        values = [value]
    return list(dict.fromkeys(str(item).strip() for item in values if item and str(item).strip()))


def _is_compilation(metadata):
    return (
        str(metadata.get("album_type") or "").casefold() == "compilation"
        or any(
            str(album_type).casefold() == "compilation"
            for album_type in _text_values(metadata.get("musicbrainz_album_type"))
        )
    )


def _set_artist_tags(tags, track_artists, album_artists):
    track_artists = artist_names(track_artists)
    album_artists = artist_names(album_artists)
    if not track_artists:
        track_artists = ["Ukendt Kunstner"]
    if not album_artists:
        album_artists = track_artists[:1]

    tags.delall("TPE1")
    tags.delall("TPE2")
    tags.delall("TXXX:ARTISTS")
    tags.delall("TXXX:ALBUM ARTISTS")
    tags.add(TPE1(encoding=3, text=" / ".join(track_artists)))
    tags.add(TPE2(encoding=3, text=" / ".join(album_artists)))
    tags.add(TXXX(encoding=3, desc="ARTISTS", text=track_artists))
    tags.add(TXXX(encoding=3, desc="ALBUM ARTISTS", text=album_artists))


def _number_tag(value, total=None):
    original_value = str(value or "").strip()
    if "/" in original_value and not total:
        numerator, denominator = original_value.split("/", 1)
        if _track_number(numerator) and _track_number(denominator):
            return f"{_track_number(numerator)}/{_track_number(denominator)}"
    number = _track_number(value)
    if number is None:
        return None
    total_number = _track_number(total)
    return f"{number}/{total_number}" if total_number else str(number)


def _release_date(metadata):
    value = (
        metadata.get("release_date")
        or metadata.get("releasedate")
        or metadata.get("year")
    )
    if not value:
        return None
    date = re.sub(r"\D", "", str(value))
    if len(date) >= 8:
        return f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    if len(date) >= 4:
        return date[:4]
    return None


def _recording_date(metadata):
    value = metadata.get("recording_date") or metadata.get("date")
    if not value:
        return None
    date = re.sub(r"\D", "", str(value))
    if len(date) >= 8:
        return f"{date[:4]}-{date[4:6]}-{date[6:8]}"
    if len(date) >= 4:
        return date[:4]
    return None


def source_url(meta):
    """Return a reusable source link, including for flat playlist entries."""
    for key in ("webpage_url", "original_url", "url"):
        value = meta.get(key)
        if isinstance(value, str) and value.startswith(("https://", "http://")):
            return value

    video_id = meta.get("id")
    return f"https://music.youtube.com/watch?v={video_id}" if video_id else ""


def append_to_master_log(url, is_album, album_name, songs, album_artist=None):
    """Append source links and identifying metadata to the recovery log."""
    lines = []
    type_str = "ALBUM" if is_album else "SINGLE"

    lines.append("=" * 60)
    lines.append(f"TYPE: [{type_str}]")
    if is_album:
        lines.append(f"ALBUM: {album_name}")
        if album_artist:
            lines.append(f"ALBUM ARTIST: {album_artist}")
    lines.append(f"SOURCE LINK: {url}")

    lines.append("SANGE:")
    for song in songs:
        track_number = song.get("track_number")
        track_label = f" [Track {track_number}]" if track_number else ""
        lines.append(f"  • {song['artist']} - {song['title']}{track_label}")
        lines.append(f"    Track link: {song['url'] or 'Link ikke fundet'}")
    lines.append("")  # Tom linje til adskillelse

    log_path = os.path.join(DOWNLOAD_DIR, os.path.basename(MASTER_LOG_FILE))
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"  ✓ Opdaterede den samlede logfil: {os.path.basename(log_path)}")


def tag_mp3(file_path, meta, is_album, album_name, master_album_artist):
    """Write standards-based tags that preserve artists and group album tracks."""
    try:
        tags = ID3(file_path)
    except ID3NoHeaderError:
        tags = ID3()

    # 1. Titel
    title = meta.get('track') or meta.get('title') or "Ukendt Sang"
    title = clean_name(title)
    tags.add(TIT2(encoding=3, text=title))

    # 2. Kunstnere
    track_artists = artist_names(meta.get("artists") or meta.get("artist"))
    if not track_artists:
        track_artists = artist_names(meta.get("uploader")) or ["Ukendt Kunstner"]

    has_catalog_album = bool(
        meta.get("album") and str(meta.get("album")).casefold() != "single"
    )
    if _is_compilation(meta) and (is_album or has_catalog_album):
        album_artist = ["Various Artists"]
    elif is_album or has_catalog_album:
        album_artist = (
            artist_names(meta.get("album_artist"))
            or artist_names(master_album_artist)[:1]
            or [track_artists[0]]
        )
    else:
        album_artist = [track_artists[0]]

    _set_artist_tags(tags, track_artists, album_artist)

    # 3. Album (Singler bruger deres egen titel som albumnavn)
    metadata_album = meta.get("album")
    if metadata_album and str(metadata_album).casefold() != "single":
        final_album = clean_name(metadata_album)
    else:
        final_album = album_name if (is_album and album_name) else title
    tags.add(TALB(encoding=3, text=final_album))

    # 4. Spornummer
    track_num = _number_tag(
        meta.get("track_number") or meta.get("playlist_index"),
        meta.get("track_count") or meta.get("n_entries"),
    ) or "1"
    tags.add(TRCK(encoding=3, text=track_num))

    # 5. Udgivelsesdato
    tags.delall("TDRC")
    tags.delall("TDRL")
    tags.delall("TDOR")
    recording_date = _recording_date(meta)
    if recording_date:
        tags.add(TDRC(encoding=3, text=recording_date))
    release_date = _release_date(meta)
    if release_date:
        tags.add(TDRL(encoding=3, text=release_date))
    original_date = _release_date({"release_date": meta.get("first_release_date")})
    if original_date:
        tags.add(TDOR(encoding=3, text=original_date))

    tags.delall("TPOS")
    disc_number = _number_tag(meta.get("disc_number"), meta.get("disc_count"))
    if disc_number:
        tags.add(TPOS(encoding=3, text=disc_number))

    for frame_type, keys in (
        (TCON, ("genres", "genre")),
        (TCOM, ("composers", "composer")),
        (TEXT, ("writers", "writer", "lyricist")),
        (TPUB, ("label", "publisher")),
        (TSRC, ("isrc", "release_isrc")),
    ):
        value = next((meta.get(key) for key in keys if meta.get(key)), None)
        values = _text_values(value)
        if values:
            tags.add(frame_type(encoding=3, text=values))

    for description, key in (
        ("MusicBrainz Artist Id", "musicbrainz_artist_ids"),
        ("MusicBrainz Album Artist Id", "musicbrainz_album_artist_ids"),
        ("MusicBrainz Album Id", "musicbrainz_release_id"),
        ("MusicBrainz Release Group Id", "musicbrainz_release_group_id"),
        ("MusicBrainz Track Id", "musicbrainz_recording_id"),
        ("MusicBrainz Release Track Id", "musicbrainz_release_track_id"),
        ("MusicBrainz Album Type", "musicbrainz_album_type"),
        ("MusicBrainz Album Status", "musicbrainz_album_status"),
        ("MusicBrainz Album Release Country", "musicbrainz_album_release_country"),
        ("BARCODE", "barcode"),
        ("CATALOGNUMBER", "catalog_number"),
    ):
        values = _text_values(meta.get(key))
        if values:
            tags.add(TXXX(encoding=3, desc=description, text=values))

    grouping = meta.get("grouping") or meta.get("work")
    if grouping:
        tags.add(TIT1(encoding=3, text=str(grouping).strip()))
    tags.delall("TCMP")
    if _is_compilation(meta):
        tags.add(TCMP(encoding=3, text="1"))

    # 6. Kilde-link i Kommentar-feltet (COMM)
    track_source_url = source_url(meta)
    if track_source_url:
        tags.add(COMM(encoding=3, lang='eng', desc='', text=track_source_url))
    if meta.get("id"):
        tags.add(COMM(encoding=3, lang="eng", desc="sarah-source-id", text=str(meta["id"])))

    tags.save(file_path, v2_version=4)


def _read_json_output(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return json.loads(result.stdout)


def _yt_dlp_cookie_args():
    browser = os.environ.get("MUSIC_YTDLP_COOKIES_BROWSER", "auto").strip()
    if not browser:
        return []
    if browser.casefold() == "auto":
        if not _brave_profile_directory().is_dir():
            return []
        browser = "brave"
    if "\n" in browser or "\r" in browser:
        raise ValueError("MUSIC_YTDLP_COOKIES_BROWSER must be a browser and optional profile.")
    return ["--cookies-from-browser", browser]


def _brave_profile_directory():
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BraveSoftware" / "Brave-Browser" / "User Data"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "BraveSoftware" / "Brave-Browser"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "BraveSoftware" / "Brave-Browser"


def _error_detail(error):
    if isinstance(error, FileNotFoundError):
        return "yt-dlp blev ikke fundet. Installér eller tilføj programmet til PATH."
    if isinstance(error, subprocess.TimeoutExpired):
        return "Operationen fik timeout."

    output = getattr(error, "stderr", None) or getattr(error, "stdout", None)
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    detail = str(output or error).strip()
    return detail[-600:] if len(detail) > 600 else detail


def _report_failure(progress_callback, message):
    print(f"[FEJL] {message}")
    if progress_callback:
        progress_callback(f"Fejl: {message}")


def _track_number(value):
    match = re.match(r"\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def _merge_track_metadata(playlist_metadata, track_metadata):
    merged = dict(playlist_metadata or {})
    for key, value in track_metadata.items():
        if value not in (None, "", [], {}):
            merged[key] = value
    return merged


def _spotify_metadata_by_isrc(metadata):
    isrc = metadata.get("isrc") or metadata.get("release_isrc")
    client_id = os.environ.get("SPOTIPY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET")
    if not isrc or not client_id or not client_secret:
        return {}

    try:
        import requests
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
    except ImportError as error:
        logger.warning("Spotify-metadata blev sprunget over; installér requirements-music.txt: %s", error)
        return {}

    try:
        client = spotipy.Spotify(
            auth_manager=SpotifyClientCredentials(
                client_id=client_id,
                client_secret=client_secret,
            ),
            requests_timeout=5,
            retries=0,
        )
        results = client.search(q=f"isrc:{isrc}", type="track", limit=10)
    except (requests.RequestException, spotipy.SpotifyException, OSError, ValueError) as error:
        logger.warning("Spotify kunne ikke levere metadata for ISRC %s: %s", isrc, error)
        return {}

    for track in results.get("tracks", {}).get("items", []):
        track_isrc = track.get("external_ids", {}).get("isrc")
        if not track_isrc or track_isrc.casefold() != str(isrc).casefold():
            continue
        album = track.get("album") or {}
        metadata_values = {
            "track": track.get("name"),
            "artists": [artist["name"] for artist in track.get("artists", []) if artist.get("name")],
            "album_artist": [
                artist["name"] for artist in album.get("artists", []) if artist.get("name")
            ],
            "album": album.get("name"),
            "release_date": album.get("release_date"),
            "track_number": track.get("track_number"),
            "track_count": album.get("total_tracks"),
            "disc_number": track.get("disc_number"),
            "isrc": track_isrc,
        }
        return {key: value for key, value in metadata_values.items() if value not in (None, "", [], {})}
    return {}


def _musicbrainz_credit(credit):
    artists = []
    artist_ids = []
    for item in credit or []:
        artist = item.get("artist") if isinstance(item, dict) else None
        if not isinstance(artist, dict):
            continue
        name = artist.get("name")
        if name:
            artists.append(name)
        if artist.get("id"):
            artist_ids.append(artist["id"])
    return artists, artist_ids


def _musicbrainz_json(url):
    global _MUSICBRAINZ_LAST_REQUEST
    global _MUSICBRAINZ_UNAVAILABLE_UNTIL
    with _MUSICBRAINZ_LOCK:
        now = time.monotonic()
        if now < _MUSICBRAINZ_UNAVAILABLE_UNTIL:
            return None
        wait_time = _MUSICBRAINZ_MIN_INTERVAL - (
            now - _MUSICBRAINZ_LAST_REQUEST
        )
        if wait_time > 0:
            time.sleep(wait_time)
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "HomeNetAssistant/1.0 (self-hosted music library metadata)",
            },
        )
        try:
            with urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (
            HTTPError,
            URLError,
            TimeoutError,
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as error:
            logger.warning("MusicBrainz request failed: %s", error)
            _MUSICBRAINZ_LAST_REQUEST = time.monotonic()
            _MUSICBRAINZ_UNAVAILABLE_UNTIL = (
                _MUSICBRAINZ_LAST_REQUEST + _MUSICBRAINZ_FAILURE_COOLDOWN
            )
            return None
        _MUSICBRAINZ_LAST_REQUEST = time.monotonic()
        _MUSICBRAINZ_UNAVAILABLE_UNTIL = 0.0
        return data


def _musicbrainz_release_detail(release):
    release_id = release.get("id")
    if not release_id:
        return release
    if release_id in _MUSICBRAINZ_RELEASE_CACHE:
        return dict(_MUSICBRAINZ_RELEASE_CACHE[release_id])
    detail = _musicbrainz_json(
        "https://musicbrainz.org/ws/2/release/"
        f"{quote(release_id, safe='')}?inc=recordings+artists+labels&fmt=json"
    )
    if detail is None:
        return release
    result = (
        detail
        if isinstance(detail, dict) and detail.get("id") == release_id
        else release
    )
    _MUSICBRAINZ_RELEASE_CACHE[release_id] = result
    return dict(result)


def _musicbrainz_query_phrase(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _musicbrainz_artist_match(expected_artists, candidate_artists):
    if [name.casefold() for name in expected_artists] == [
        name.casefold() for name in candidate_artists
    ]:
        return True

    def artist_tokens(names):
        normalized = unicodedata.normalize("NFKD", " ".join(names).casefold())
        normalized = "".join(
            character for character in normalized
            if not unicodedata.combining(character)
        )
        return set(re.findall(r"[a-z0-9]+", normalized)) - {
            "og", "and", "feat", "featuring", "ft",
        }

    expected_tokens = artist_tokens(expected_artists)
    candidate_tokens = artist_tokens(candidate_artists)
    return len(candidate_tokens) >= 2 and candidate_tokens.issubset(expected_tokens)


def _musicbrainz_metadata(metadata):
    """Enrich from exact ISRCs or one unambiguous exact title/artist match."""
    isrc = re.sub(r"[\s-]", "", str(metadata.get("isrc") or metadata.get("release_isrc") or ""))
    has_isrc = bool(re.fullmatch(r"[A-Z]{2}[A-Z0-9]{3}\d{7}", isrc, re.IGNORECASE))
    expected_title = _normalized_title(metadata.get("track") or metadata.get("title"))
    expected_artists = artist_names(metadata.get("artists") or metadata.get("artist"))
    expected_album_name = metadata.get("album")
    normalized_album = _normalized_title(expected_album_name)
    if (
        normalized_album in {"single", "unknown album", "ukendt album"}
        or normalized_album in {
            _normalized_title(metadata.get("track")),
            _normalized_title(metadata.get("title")),
        }
    ):
        normalized_album = ""
    if has_isrc:
        isrc = isrc.upper()
        cache_key = f"isrc:{isrc}|{expected_title}|{normalized_album}"
        request_url = (
            "https://musicbrainz.org/ws/2/isrc/"
            f"{quote(isrc, safe='')}?inc=artists&fmt=json"
        )
    else:
        if not expected_title or not expected_artists:
            return {}
        cache_key = "search:" + "|".join(
            (
                expected_title,
                normalized_album,
                *(name.casefold() for name in expected_artists),
            )
        )
        search_query = (
            f'recording:"{_musicbrainz_query_phrase(metadata.get("track") or metadata.get("title"))}" '
            f'AND artist:"{_musicbrainz_query_phrase(expected_artists[0])}"'
        )
        if expected_album_name and normalized_album:
            search_query += (
                f' AND release:"{_musicbrainz_query_phrase(expected_album_name)}"'
            )
        request_url = (
            "https://musicbrainz.org/ws/2/recording/?"
            + urlencode({"query": search_query, "fmt": "json", "limit": 25})
        )
    if cache_key in _MUSICBRAINZ_CACHE:
        return dict(_MUSICBRAINZ_CACHE[cache_key])

    data = _musicbrainz_json(request_url)
    if not isinstance(data, dict):
        return {}

    isrc_data = data.get("isrc")
    recordings = data.get("recordings")
    if not recordings and isinstance(isrc_data, dict):
        recordings = isrc_data.get("recordings")
    recordings = [
        recording
        for recording in recordings or []
        if isinstance(recording, dict)
    ]
    if not recordings and has_isrc:
        _MUSICBRAINZ_CACHE[cache_key] = {}
        return {}

    if has_isrc:
        candidates = sorted(
            recordings,
            key=lambda recording: (
                _normalized_title(recording.get("title")) != expected_title,
                not bool(recording.get("releases")),
            ),
        )
        recording = candidates[0]
    else:
        exact_matches = []
        for candidate in recordings:
            candidate_artists, _ = _musicbrainz_credit(candidate.get("artist-credit"))
            if (
                _normalized_title(candidate.get("title")) == expected_title
                and [name.casefold() for name in candidate_artists]
                == [name.casefold() for name in expected_artists]
            ):
                exact_matches.append(candidate)
        if not exact_matches:
            fallback_query = (
                f'recording:"{_musicbrainz_query_phrase(metadata.get("track") or metadata.get("title"))}"'
            )
            fallback_url = (
                "https://musicbrainz.org/ws/2/recording/?"
                + urlencode({"query": fallback_query, "fmt": "json", "limit": 25})
            )
            fallback_data = _musicbrainz_json(fallback_url)
            if fallback_data is None:
                return {}
            fallback_recordings = (
                fallback_data.get("recordings", [])
                if isinstance(fallback_data, dict)
                else []
            )
            exact_matches = []
            for candidate in fallback_recordings:
                candidate_artists, _ = _musicbrainz_credit(candidate.get("artist-credit"))
                if (
                    _normalized_title(candidate.get("title")) == expected_title
                    and _musicbrainz_artist_match(expected_artists, candidate_artists)
                ):
                    exact_matches.append(candidate)
        if len(exact_matches) != 1:
            if exact_matches:
                logger.info(
                    "MusicBrainz title/artist lookup was ambiguous; skipping %s",
                    metadata.get("track") or metadata.get("title"),
                )
            _MUSICBRAINZ_CACHE[cache_key] = {}
            return {}
        recording = exact_matches[0]
    if recording.get("id"):
        detail = _musicbrainz_json(
            "https://musicbrainz.org/ws/2/recording/"
            f"{quote(recording['id'], safe='')}?"
            "inc=artists+releases+release-groups&fmt=json"
        )
        if isinstance(detail, dict) and detail.get("id") == recording.get("id"):
            recording = detail
    recording_artists, recording_artist_ids = _musicbrainz_credit(
        recording.get("artist-credit")
    )
    releases = [
        release
        for release in recording.get("releases") or []
        if isinstance(release, dict)
    ]
    expected_album = normalized_album
    releases = sorted(
        releases,
        key=lambda release: (
            _normalized_title(release.get("title")) != expected_album,
            str(release.get("status", "")).casefold() != "official",
            not bool(release.get("date")),
        ),
    )
    release = releases[0] if releases else {}
    if release and expected_album and _normalized_title(release.get("title")) != expected_album:
        release = {}
    if release:
        release = _musicbrainz_release_detail(release)
    release_artists, release_artist_ids = _musicbrainz_credit(
        release.get("artist-credit")
    )

    metadata_values = {
        "track": recording.get("title"),
        "artists": recording_artists,
        "album_artist": release_artists or recording_artists,
        "musicbrainz_recording_id": recording.get("id"),
        "musicbrainz_artist_ids": recording_artist_ids,
        "first_release_date": recording.get("first-release-date"),
    }
    if release:
        metadata_values.update({
            "album": release.get("title"),
            "release_date": release.get("date"),
            "musicbrainz_album_status": release.get("status"),
            "musicbrainz_album_release_country": release.get("country"),
            "musicbrainz_release_id": release.get("id"),
            "musicbrainz_album_artist_ids": release_artist_ids or recording_artist_ids,
        })
        release_group = release.get("release-group") or {}
        metadata_values["musicbrainz_release_group_id"] = release_group.get("id")
        release_types = [
            release_type
            for release_type in [
                release_group.get("primary-type"),
                *(release_group.get("secondary-types") or []),
            ]
            if release_type
        ]
        if release_types:
            metadata_values["musicbrainz_album_type"] = release_types
        if any(
            str(release_type).casefold() == "compilation"
            for release_type in release_group.get("secondary-types") or []
        ):
            metadata_values["album_type"] = "compilation"
        media = release.get("media") or []
        if media:
            total_tracks = sum(
                medium.get("track-count") or len(medium.get("tracks") or [])
                for medium in media
            )
            if total_tracks:
                metadata_values["track_count"] = total_tracks
            for medium in media:
                matched_track = next(
                    (
                        track
                        for track in medium.get("tracks") or []
                        if (track.get("recording") or {}).get("id") == recording.get("id")
                    ),
                    None,
                )
                if matched_track:
                    metadata_values["track_number"] = (
                        matched_track.get("position") or matched_track.get("number")
                    )
                    metadata_values["disc_number"] = medium.get("position")
                    metadata_values["musicbrainz_release_track_id"] = matched_track.get("id")
                    break
        if release.get("barcode"):
            metadata_values["barcode"] = release["barcode"]
        labels = [
            label_info.get("label", {}).get("name")
            for label_info in release.get("label-info") or []
            if isinstance(label_info, dict)
            and isinstance(label_info.get("label"), dict)
            and label_info["label"].get("name")
        ]
        if labels:
            metadata_values["label"] = labels
        catalog_numbers = [
            label_info.get("catalog-number")
            for label_info in release.get("label-info") or []
            if isinstance(label_info, dict) and label_info.get("catalog-number")
        ]
        if catalog_numbers:
            metadata_values["catalog_number"] = catalog_numbers

    result = {
        key: value
        for key, value in metadata_values.items()
        if value not in (None, "", [], {})
    }
    _MUSICBRAINZ_CACHE[cache_key] = result
    return dict(result)


def _cover_art_from_musicbrainz(release_id):
    if not release_id:
        return None
    request = Request(
        f"https://coverartarchive.org/release/{quote(str(release_id), safe='')}/front-500",
        headers={"User-Agent": "HomeNetAssistant/1.0 (self-hosted music library metadata)"},
    )
    try:
        with urlopen(request, timeout=12) as response:
            mime = response.headers.get_content_type()
            if mime not in {"image/jpeg", "image/png"}:
                logger.warning("Cover Art Archive returned unsupported image type %s", mime)
                return None
            image_data = response.read(20 * 1024 * 1024 + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        logger.info("No Cover Art Archive image for release %s: %s", release_id, error)
        return None
    if len(image_data) > 20 * 1024 * 1024:
        logger.warning("Cover Art Archive image for release %s exceeds 20 MiB", release_id)
        return None
    return mime, image_data


def _already_downloaded(directory, source_id):
    if not source_id:
        return False
    for path in Path(directory).glob("*.mp3"):
        try:
            tags = ID3(path)
        except (ID3NoHeaderError, OSError):
            continue
        if any(
            frame.desc == "sarah-source-id" and str(source_id) in frame.text
            for frame in tags.getall("COMM")
        ):
            return True
    return False


def _normalized_title(value):
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(normalized.split())


def _same_title_files(directory, title):
    normalized_title = _normalized_title(title)
    if not normalized_title:
        return []

    matches = []
    for path in Path(directory).glob("*.mp3"):
        try:
            tags = ID3(path)
        except (ID3NoHeaderError, OSError):
            continue
        if _normalized_title(tags.get("TIT2")) == normalized_title:
            matches.append(path)
    return matches


def _file_has_source_id(path, source_id):
    if not source_id:
        return False
    try:
        tags = ID3(path)
    except (ID3NoHeaderError, OSError):
        return False
    return any(
        frame.desc == "sarah-source-id" and str(source_id) in frame.text
        for frame in tags.getall("COMM")
    )


def _destination_path(directory, metadata, is_album):
    title = clean_name(metadata.get("track") or metadata.get("title") or "Ukendt Sang")
    number = _track_number(metadata.get("track_number") or metadata.get("playlist_index"))
    if is_album and number:
        filename = f"{number:02d} - {title}.mp3"
    else:
        filename = f"{title}.mp3"

    return Path(directory) / filename


def _download_music_video(metadata, progress_callback=None):
    """Save an authorized source video outside the Navidrome music library."""
    url = source_url(metadata)
    if not url:
        if progress_callback:
            progress_callback("Musikvideo blev sprunget over: kilde-link mangler.")
        return False

    default_video_path = (
        Path(DOWNLOAD_DIR).parent / f"{Path(DOWNLOAD_DIR).name} - Musikvideoer"
    )
    video_root = Path(
        os.environ.get("MUSIC_VIDEO_DOWNLOAD_PATH", "").strip()
        or default_video_path
    ).expanduser()
    artists = artist_names(metadata.get("artists") or metadata.get("artist"))
    artist = clean_name(artists[0] if artists else metadata.get("uploader") or "Ukendt Kunstner")
    title = clean_name(metadata.get("track") or metadata.get("title") or "Ukendt video")
    album_value = metadata.get("album")
    album = clean_name(album_value) if album_value and str(album_value).casefold() != "single" else None
    directory = video_root / artist
    if album:
        directory /= album
    video_id = re.sub(r"[^A-Za-z0-9._-]", "_", str(metadata.get("id") or "video"))
    target_path = directory / f"{title} [{video_id}].mp4"
    if target_path.is_file() and target_path.stat().st_size:
        if progress_callback:
            progress_callback(f"Musikvideo findes allerede: {target_path.name}")
        return True

    try:
        directory.mkdir(parents=True, exist_ok=True)
        if progress_callback:
            progress_callback(f"Prøver at hente musikvideo: {title}")
        with tempfile.TemporaryDirectory(prefix=".sarah-video-", dir=directory) as work_dir:
            subprocess.run(
                [
                    "yt-dlp",
                    *_yt_dlp_cookie_args(),
                    "--format",
                    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
                    "--merge-output-format",
                    "mp4",
                    "--embed-metadata",
                    "--no-playlist",
                    "--no-warnings",
                    "--output",
                    "video.%(ext)s",
                    url,
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=3600,
            )
            video_path = Path(work_dir) / "video.mp4"
            if not video_path.is_file() or not video_path.stat().st_size:
                raise OSError("yt-dlp oprettede ikke en brugbar MP4-videofil.")
            os.replace(video_path, target_path)
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        OSError,
        ValueError,
    ) as error:
        if progress_callback:
            progress_callback(
                f"Musikvideo kunne ikke hentes for '{title}': {_error_detail(error)}"
            )
        return False

    if progress_callback:
        progress_callback(f"Musikvideo gemt separat: {target_path}")
    return True


def download_single_track(
    song,
    track_number=None,
    progress_callback=None,
    download_video=False,
):
    url = song.get("url") or source_url(song.get("metadata", {}))
    if not url:
        _report_failure(progress_callback, "Nummeret mangler et kilde-link.")
        return False

    playlist_metadata = song.get("metadata") or {}
    try:
        fetched_metadata = _read_json_output(
            [
                "yt-dlp",
                *_yt_dlp_cookie_args(),
                "--dump-json",
                "--no-download",
                "--no-warnings",
                "--no-playlist",
                url,
            ]
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        json.JSONDecodeError,
    ) as error:
        title = playlist_metadata.get("track") or playlist_metadata.get("title") or url
        _report_failure(
            progress_callback,
            f"Kunne ikke hente metadata for '{title}': {_error_detail(error)}",
        )
        return False
    if playlist_metadata.get("id") and playlist_metadata.get("id") != fetched_metadata.get("id"):
        _report_failure(
            progress_callback,
            "Linket peger ikke på det forventede nummer; download blev afbrudt.",
        )
        return False
    metadata = _merge_track_metadata(playlist_metadata, fetched_metadata)
    metadata = _merge_track_metadata(metadata, _musicbrainz_metadata(metadata))
    metadata = _merge_track_metadata(metadata, _spotify_metadata_by_isrc(metadata))

    expected_id = song.get("id")
    if expected_id and metadata.get("id") != expected_id:
        _report_failure(
            progress_callback,
            "Linket peger ikke på det forventede nummer; download blev afbrudt.",
        )
        return False

    if track_number and not metadata.get("track_number"):
        metadata = {**metadata, "track_number": track_number}
    song["resolved_metadata"] = metadata

    metadata_album = metadata.get("album")
    if not metadata_album or str(metadata_album).casefold() == "single":
        metadata_album = song.get("album_name")
    is_album = bool(song.get("is_album")) or bool(
        metadata.get("album")
        and str(metadata.get("album")).casefold() != "single"
        and (metadata.get("track_number") or metadata.get("album_artist"))
    )
    song["resolved_is_album"] = is_album
    album_name = clean_name(metadata_album or "Ukendt Album")
    if is_album:
        album_artists = (
            artist_names(metadata.get("album_artist"))
            or artist_names(playlist_metadata.get("album_artist"))
            or artist_names(metadata.get("artists") or metadata.get("artist"))
            or artist_names(playlist_metadata.get("artists") or playlist_metadata.get("artist"))
            or ["Ukendt Kunstner"]
        )
        directory = Path(DOWNLOAD_DIR) / clean_name(album_artists[0]) / album_name
    else:
        directory = Path(DOWNLOAD_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    title = clean_name(metadata.get("track") or metadata.get("title") or "Ukendt Sang")
    target_path = _destination_path(directory, metadata, is_album)

    def save_video_if_requested():
        if download_video:
            _download_music_video(metadata, progress_callback)

    same_title_paths = _same_title_files(directory, title)
    already_downloaded_path = next(
        (
            path
            for path in same_title_paths
            if _file_has_source_id(path, metadata.get("id"))
        ),
        None,
    )
    if already_downloaded_path is not None:
        if already_downloaded_path != target_path:
            os.replace(already_downloaded_path, target_path)
        for duplicate_path in same_title_paths:
            if duplicate_path != target_path and duplicate_path != already_downloaded_path:
                duplicate_path.unlink()
        print(f"[INFO] Spring over; nummeret findes allerede: {metadata.get('title', url)}")
        save_video_if_requested()
        return True
    if _already_downloaded(directory, metadata.get("id")):
        print(f"[INFO] Spring over; nummeret findes allerede: {metadata.get('title', url)}")
        save_video_if_requested()
        return True

    try:
        with tempfile.TemporaryDirectory(prefix=".sarah-download-", dir=directory) as work_dir:
            subprocess.run(
                [
                    "yt-dlp",
                    *_yt_dlp_cookie_args(),
                    "--extract-audio",
                    "--audio-format",
                    "mp3",
                    "--audio-quality",
                    "0",
                    "--embed-metadata",
                    "--embed-thumbnail",
                    "--no-warnings",
                    "--output",
                    "audio.%(ext)s",
                    "--no-playlist",
                    url,
                ],
                cwd=work_dir,
                capture_output=True,
                text=True,
                check=True,
                timeout=3600,
            )
            audio_path = Path(work_dir) / "audio.mp3"
            if not audio_path.is_file():
                _report_failure(
                    progress_callback,
                    f"yt-dlp afsluttede uden at oprette en MP3-fil for "
                    f"'{metadata.get('track') or metadata.get('title') or url}'.",
                )
                return False

            master_album_artist = metadata.get("album_artist") or metadata.get("artist")
            tag_mp3(
                audio_path,
                metadata,
                is_album,
                album_name if is_album else None,
                master_album_artist,
            )
            for image_path in Path(work_dir).glob("audio.*"):
                if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                    continue
                mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
                tags = ID3(audio_path)
                tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_path.read_bytes()))
                tags.save(audio_path, v2_version=4)
                break
            tags = ID3(audio_path)
            has_source_art = bool(tags.getall("APIC"))
            catalog_cover = None
            if is_album or not has_source_art:
                catalog_cover = _cover_art_from_musicbrainz(
                    metadata.get("musicbrainz_release_id")
                )
            if catalog_cover:
                mime, image_data = catalog_cover
                if is_album:
                    tags.delall("APIC")
                tags.add(
                    APIC(
                        encoding=3,
                        mime=mime,
                        type=3,
                        desc="Cover",
                        data=image_data,
                    )
                )
                tags.save(audio_path, v2_version=4)
            os.replace(audio_path, target_path)
            for duplicate_path in _same_title_files(directory, title):
                if duplicate_path != target_path:
                    duplicate_path.unlink()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError) as error:
        title = metadata.get("track") or metadata.get("title") or url
        _report_failure(
            progress_callback,
            f"Download af '{title}' kunne ikke færdiggøres: {_error_detail(error)}",
        )
        return False

    save_video_if_requested()

    return True


def process_url(url, progress_callback=None, download_video=False):
    if progress_callback:
        progress_callback("Henter information om linket.")
    try:
        info = _read_json_output(
            [
                "yt-dlp",
                *_yt_dlp_cookie_args(),
                "--dump-single-json",
                "--flat-playlist",
                "--no-warnings",
                url,
            ]
        )
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
        json.JSONDecodeError,
    ) as error:
        _report_failure(
            progress_callback,
            f"Kunne ikke læse linket: {_error_detail(error)}",
        )
        return False

    entries = info.get("entries")
    if entries is not None and not any(entries):
        _report_failure(
            progress_callback,
            "Playlisten indeholder ingen tilgængelige spor (tom, privat eller utilgængelig).",
        )
        return False
    is_album = bool(entries) or "playlist?list=" in url
    album_name = clean_name(info.get("title") or "Ukendt Album") if is_album else None
    master_artist = (
        info.get("album_artist")
        or info.get("artist")
        or info.get("playlist_uploader")
        or info.get("uploader")
        or "Ukendt Kunstner"
    )
    collected_songs = []
    recovery_songs = []
    succeeded = True
    track_count = 0

    for index, entry in enumerate(entries or [info], start=1):
        if not entry:
            continue
        track_count += 1
        song_url = source_url(entry)
        track_metadata = dict(entry)
        if not track_metadata.get("album_artist"):
            source_album_artist = info.get("album_artist")
            if source_album_artist:
                track_metadata["album_artist"] = source_album_artist
            elif info.get("artist"):
                track_metadata["album_artist"] = artist_names(info["artist"])[:1]
        if not track_metadata.get("album"):
            track_metadata["album"] = info.get("title")
        song = {
            "url": song_url,
            "id": entry.get("id"),
            "track_number": entry.get("track_number") or entry.get("playlist_index") or index,
            "is_album": is_album,
            "album_name": album_name,
            "metadata": track_metadata,
        }
        artists = artist_names(entry.get("artists") or entry.get("artist") or entry.get("uploader"))
        recovery_song = {
            "artist": ", ".join(artists) if artists else "Ukendt Kunstner",
            "title": clean_name(entry.get("track") or entry.get("title") or "Ukendt Sang"),
            "url": song_url,
            "track_number": entry.get("track_number") or entry.get("playlist_index") or index,
        }
        if not song_url:
            _report_failure(
                progress_callback,
                f"Spor {index} mangler et download-link.",
            )
            succeeded = False
            continue
        recovery_songs.append(recovery_song)
        if not download_single_track(
            song,
            song["track_number"],
            progress_callback,
            download_video=download_video,
        ):
            succeeded = False
            continue

        resolved_metadata = song.get("resolved_metadata") or {}
        resolved_artists = (
            artist_names(resolved_metadata.get("artists") or resolved_metadata.get("artist"))
            or artists
        )
        recovery_song["artist"] = ", ".join(resolved_artists)
        recovery_song["title"] = clean_name(
            resolved_metadata.get("track")
            or resolved_metadata.get("title")
            or recovery_song["title"]
        )
        recovery_song["track_number"] = (
            resolved_metadata.get("track_number")
            or recovery_song["track_number"]
        )
        if song.get("resolved_is_album"):
            is_album = True
        if is_album:
            resolved_album = resolved_metadata.get("album")
            if resolved_album and str(resolved_album).casefold() != "single":
                album_name = clean_name(resolved_album)
            resolved_album_artists = (
                artist_names(resolved_metadata.get("album_artist"))
                or resolved_artists
            )
            if resolved_album_artists:
                master_artist = resolved_album_artists[0]
        collected_songs.append(recovery_song)

    if recovery_songs:
        append_to_master_log(
            url,
            is_album,
            album_name,
            recovery_songs,
            album_artist=artist_names(master_artist)[0] if master_artist else None,
        )
    if track_count == 0:
        _report_failure(progress_callback, "Linket indeholdt ingen spor.")
        return False
    if progress_callback:
        downloaded_count = len(collected_songs)
        progress_callback(
            f"Arkiveringen er færdig: {downloaded_count}/{track_count} spor hentet."
            if succeeded
            else f"Arkiveringen sluttede: {downloaded_count}/{track_count} spor hentet; se fejl ovenfor."
        )
    return succeeded


def download_music(url):
    return process_url(url)


def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    print("==================================================")
    print(" SARAH Musik Henter - Ren Arkitektur")
    print("==================================================")

    while True:
        url_input = input("\nIndsæt YouTube Music link (eller 'q' for at afslutte): ").strip()
        if url_input.lower() == 'q':
            print("Lukker programmet.")
            break
        if url_input:
            download_music(url_input)


if __name__ == "__main__":
    main()