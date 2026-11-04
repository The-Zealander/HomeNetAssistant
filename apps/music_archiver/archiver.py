import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    ID3NoHeaderError,
    TALB,
    TDRC,
    TIT2,
    TPE1,
    TPE2,
    TRCK,
)
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
DOWNLOAD_DIR = os.environ.get("MUSIC_DOWNLOAD_PATH", str(Path.home() / "Musik"))
MASTER_LOG_FILE = os.path.join(DOWNLOAD_DIR, "_samlet_arkiv.txt")


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
        names = re.split(r"\s*,\s*|\s+(?:feat(?:uring)?\.?|ft\.?)\s+", str(value), flags=re.IGNORECASE)
    else:
        names = []

    return list(dict.fromkeys(name.strip() for name in names if name and name.strip()))


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

    if is_album:
        album_artist = master_album_artist
    else:
        album_artist = track_artists[0]

    # ID3 multi-value text keeps collaborations as separate artists, not one comma-name.
    tags.delall("TPE1")
    tags.delall("TPE2")
    tags.add(TPE1(encoding=3, text=track_artists))
    tags.add(TPE2(encoding=3, text=album_artist))

    # 3. Album (Singler bruger deres egen titel som albumnavn)
    final_album = album_name if (is_album and album_name) else title
    tags.add(TALB(encoding=3, text=final_album))

    # 4. Spornummer
    track_num = _track_number(meta.get("track_number") or meta.get("playlist_index")) or 1
    tags.add(TRCK(encoding=3, text=str(track_num)))

    # 5. Udgivelsesdato
    date_str = meta.get('release_date') or meta.get('upload_date')
    if date_str and len(date_str) >= 4:
        tags.add(TDRC(encoding=3, text=date_str[:4]))

    # 6. Kilde-link i Kommentar-feltet (COMM)
    track_source_url = source_url(meta)
    if track_source_url:
        tags.add(COMM(encoding=3, lang='eng', desc='', text=track_source_url))
    if meta.get("id"):
        tags.add(COMM(encoding=3, lang="eng", desc="sarah-source-id", text=str(meta["id"])))

    tags.save(file_path)


def _read_json_output(command):
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return json.loads(result.stdout)


def _track_number(value):
    match = re.match(r"\s*(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def _has_artist_metadata(metadata):
    return any(metadata.get(key) for key in ("artists", "artist", "album_artist"))


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


def _destination_path(directory, metadata, is_album):
    title = clean_name(metadata.get("track") or metadata.get("title") or "Ukendt Sang")
    number = _track_number(metadata.get("track_number") or metadata.get("playlist_index"))
    if is_album and number:
        filename = f"{number:02d} - {title}.mp3"
    else:
        filename = f"{title}.mp3"

    path = Path(directory) / filename
    if not path.exists():
        return path

    artists = artist_names(metadata.get("artists") or metadata.get("artist"))
    artist = clean_name(artists[0] if artists else metadata.get("uploader") or "Ukendt Kunstner")
    path = path.with_name(f"{path.stem} - {artist}{path.suffix}")
    if path.exists():
        source_id = clean_name(metadata.get("id") or "ukendt")
        path = path.with_name(f"{path.stem} - {source_id}{path.suffix}")
    return path


def download_single_track(song, track_number=None):
    url = song.get("url") or source_url(song.get("metadata", {}))
    if not url:
        print("[FEJL] Nummeret mangler et kilde-link.")
        return False

    metadata = song.get("metadata")
    if metadata is None or not _has_artist_metadata(metadata):
        try:
            fetched_metadata = _read_json_output(
                [
                    "yt-dlp",
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
            print(f"[FEJL] Kunne ikke hente metadata: {error}")
            return False
        if metadata and metadata.get("id") != fetched_metadata.get("id"):
            print("[FEJL] Linket peger ikke på det forventede nummer; download blev afbrudt.")
            return False
        metadata = fetched_metadata

    expected_id = song.get("id")
    if expected_id and metadata.get("id") != expected_id:
        print("[FEJL] Linket peger ikke på det forventede nummer; download blev afbrudt.")
        return False

    if track_number and not metadata.get("track_number"):
        metadata = {**metadata, "track_number": track_number}

    is_album = bool(song.get("is_album"))
    metadata_album = metadata.get("album")
    if not metadata_album or str(metadata_album).casefold() == "single":
        metadata_album = song.get("album_name")
    album_name = clean_name(metadata_album or "Ukendt Album")
    directory = Path(DOWNLOAD_DIR) / album_name if is_album else Path(DOWNLOAD_DIR)
    directory.mkdir(parents=True, exist_ok=True)

    if _already_downloaded(directory, metadata.get("id")):
        print(f"[INFO] Spring over; nummeret findes allerede: {metadata.get('title', url)}")
        return True

    target_path = _destination_path(directory, metadata, is_album)
    try:
        with tempfile.TemporaryDirectory(prefix=".sarah-download-", dir=directory) as work_dir:
            subprocess.run(
                [
                    "yt-dlp",
                    "--extract-audio",
                    "--audio-format",
                    "mp3",
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
                print("[FEJL] yt-dlp afsluttede uden at oprette en MP3-fil.")
                return False

            tag_mp3(
                audio_path,
                metadata,
                is_album,
                album_name if is_album else None,
                metadata.get("album_artist") or metadata.get("artist") or "Ukendt Kunstner",
            )
            for image_path in Path(work_dir).glob("audio.*"):
                if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
                    continue
                mime = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
                tags = ID3(audio_path)
                tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=image_path.read_bytes()))
                tags.save(audio_path)
                break
            os.replace(audio_path, target_path)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError, ValueError) as error:
        print(f"[FEJL] Downloadet kunne ikke færdiggøres: {error}")
        return False

    return True


def process_url(url, progress_callback=None):
    if progress_callback:
        progress_callback("Henter information om linket.")
    try:
        info = _read_json_output(
            [
                "yt-dlp",
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
        print(f"[FEJL] Kunne ikke læse linket: {error}")
        return False

    entries = info.get("entries")
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
    succeeded = True

    for index, entry in enumerate(entries or [info], start=1):
        if not entry:
            continue
        song_url = source_url(entry)
        song = {
            "url": song_url,
            "id": entry.get("id"),
            "track_number": entry.get("track_number") or entry.get("playlist_index") or index,
            "is_album": is_album,
            "album_name": album_name,
            "metadata": entry,
        }
        if not song_url or not download_single_track(song, song["track_number"]):
            succeeded = False
            continue

        artists = artist_names(entry.get("artists") or entry.get("artist") or entry.get("uploader"))
        collected_songs.append({
            "artist": ", ".join(artists) if artists else "Ukendt Kunstner",
            "title": clean_name(entry.get("track") or entry.get("title") or "Ukendt Sang"),
            "url": song_url,
            "track_number": entry.get("track_number") or entry.get("playlist_index") or index,
        })

    if collected_songs:
        append_to_master_log(
            url,
            is_album,
            album_name,
            collected_songs,
            album_artist=artist_names(master_artist)[0] if master_artist else None,
        )
    if progress_callback:
        progress_callback("Arkiveringen er færdig." if succeeded else "Et eller flere numre kunne ikke hentes.")
    return succeeded


def download_music(url):
    return process_url(url)


if __name__ == '__main__':
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