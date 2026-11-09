"""Preview or apply repairs to music tags, filenames, and album folders."""

import argparse
import os
import re
import shutil

from mutagen import MutagenError
from mutagen.id3 import COMM, ID3, ID3NoHeaderError, TALB, TCMP, TIT2, TPE1, TPE2, TRCK

from apps.music_archiver.archiver import DOWNLOAD_DIR, _set_artist_tags, artist_names

def clean_name(name):
    if not name:
        return ""
    name = re.sub(r"(?i)^Album\s*-\s*", "", str(name))
    return re.sub(r'[\\/*?:"<>|]', "", name).strip().rstrip(".")


def get_video_id(tags):
    """Find a YouTube video ID in legacy TXXX tags."""
    for tag in tags.getall("TXXX"):
        if getattr(tag, "desc", "") == "YOUTUBE_VIDEO_ID" and tag.text:
            return str(tag.text[0])
    return None


def unique_target_path(target_path, source_path, reserved_paths):
    """Choose a target that never overwrites another file."""
    stem, extension = os.path.splitext(target_path)
    candidate = target_path
    suffix = 2
    source = os.path.normcase(os.path.abspath(source_path))

    while True:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized not in reserved_paths and (
                normalized == source or not os.path.exists(candidate)
        ):
            reserved_paths.add(normalized)
            return candidate
        candidate = f"{stem} ({suffix}){extension}"
        suffix += 1


def migrate_library(download_dir=DOWNLOAD_DIR, apply_changes=False, skip_paths=None):
    if not os.path.isdir(download_dir):
        raise FileNotFoundError(f"Musikmappen findes ikke: {download_dir}")

    skipped_paths = {
        os.path.normcase(os.path.abspath(path))
        for path in (skip_paths or ())
    }
    print("==================================================")
    print(" SARAH Library Migrator")
    print("==================================================")
    print(f"Scanner {download_dir}...\n")

    mp3_files = []
    for root, _, files in os.walk(download_dir):
        for filename in files:
            if filename.lower().endswith(".mp3"):
                file_path = os.path.join(root, filename)
                if os.path.normcase(os.path.abspath(file_path)) not in skipped_paths:
                    mp3_files.append(file_path)

    action = "Starter oprydning og tagging" if apply_changes else "Forhåndsvisning (ingen filer ændres)"
    print(f"Fandt {len(mp3_files)} MP3-filer. {action}...\n")

    album_registry = {}
    reserved_paths = set()
    updated_count = 0
    moved_count = 0
    error_count = 0

    for file_path in mp3_files:
        try:
            try:
                tags = ID3(file_path)
            except ID3NoHeaderError:
                tags = ID3()

            raw_title = str(tags.get("TIT2", "")).strip()
            fallback_title = os.path.splitext(os.path.basename(file_path))[0]
            title = clean_name(raw_title) or clean_name(fallback_title) or "Ukendt titel"

            artist_frame = tags.get("TXXX:ARTISTS") or tags.get("TPE1")
            artist_values = artist_frame.text if artist_frame else []
            artists = artist_names(
                artist_values[0] if len(artist_values) == 1 else artist_values
            )
            if not artists:
                artists = ["Ukendt Kunstner"]
            artist = ", ".join(artists)
            compilation_types = tags.get("TXXX:MusicBrainz Album Type")
            is_compilation = (
                str(tags.get("TCMP", "")).strip().casefold() in {"1", "true", "yes"}
                or any(
                    str(value).casefold() == "compilation"
                    for value in (compilation_types.text if compilation_types else [])
                )
            )
            album_artist_frame = tags.get("TXXX:ALBUM ARTISTS") or tags.get("TPE2")
            album_artist_values = (
                ["Various Artists"]
                if is_compilation
                else album_artist_frame.text if album_artist_frame else artists[:1]
            )
            album_artists = artist_names(
                album_artist_values[0]
                if len(album_artist_values) == 1
                else album_artist_values
            ) or artists[:1]
            album_artist = clean_name(album_artists[0]) or "Ukendt Kunstner"

            raw_album = str(tags.get("TALB", "")).strip()
            album = clean_name(raw_album)
            track_number = str(tags.get("TRCK", "")).strip()
            if not track_number:
                filename_track = re.match(
                    r"\s*(\d+)\s*[-.]",
                    os.path.basename(file_path),
                )
                if filename_track:
                    track_number = filename_track.group(1)
            is_single = (
                not album
                or album.lower()
                in {"unknown album", "ukendt album", "singles", "single"}
                or (
                    album.casefold() == title.casefold()
                    and not track_number
                )
            )
            if not track_number and is_single:
                track_number = "1"
            album_name = title if is_single else album

            video_id = get_video_id(tags)
            source_url = (
                f"https://music.youtube.com/watch?v={video_id}"
                if video_id
                else "Link ikke fundet i gammel metadata"
            )

            if is_single:
                target_dir = download_dir
                target_filename = f"{title}.mp3"
            else:
                track_tag = track_number or "1"
                match = re.search(r"\d+", track_tag)
                track_num = match.group(0).zfill(2) if match else "01"
                target_dir = os.path.join(download_dir, album_artist, album_name)
                target_filename = f"{track_num} - {title}.mp3"

            target_path = unique_target_path(
                os.path.join(target_dir, target_filename), file_path, reserved_paths
            )
            group_key = (is_single, album_artist.casefold(), album_name.casefold())

            if apply_changes:
                tags.add(TIT2(encoding=3, text=title))
                _set_artist_tags(tags, artists, album_artists)
                tags.add(TALB(encoding=3, text=album_name))
                if track_number:
                    tags.add(TRCK(encoding=3, text=track_number))
                if video_id:
                    tags.add(COMM(encoding=3, lang="dan", desc="Kilde", text=source_url))
                tags.save(file_path, v2_version=4)
                updated_count += 1

                if file_path != target_path:
                    os.makedirs(target_dir, exist_ok=True)
                    shutil.move(file_path, target_path)
                    moved_count += 1

            print(f"{'Planlægger' if not apply_changes else 'Behandlede'}: "
                  f"{os.path.basename(file_path)} -> {target_path}")
            album_registry.setdefault(
                group_key,
                {
                    "is_album": not is_single,
                    "dir": target_dir,
                    "album_name": album_name,
                    "album_artist": album_artist,
                    "songs": [],
                },
            )["songs"].append({"title": title, "artist": artist, "url": source_url})
            if not apply_changes:
                updated_count += 1
                if file_path != target_path:
                    moved_count += 1

        except (MutagenError, OSError, ValueError) as error:
            error_count += 1
            print(f"[Fejl] Kunne ikke behandle {os.path.basename(file_path)}: {error}")

    if apply_changes:
        print("\nGenererer _info.txt filer...")
        for data in album_registry.values():
            if data["is_album"]:
                info_name = f"_info_{clean_name(data['album_name'])}.txt"
                info_path = os.path.join(data["dir"], info_name)
                lines = [
                    "Type: [Album]",
                    f"Album navn: {data['album_name']}",
                    "\nIndividuelle sange:",
                    "=" * 40,
                ]
                for song in data["songs"]:
                    lines.append(
                        f"Sang navn: {song['title']}\nLink: {song['url']}\n"
                        f"Kunstner: {song['artist']}\nType: Album\n" + "-" * 40
                    )
                write_info_file(info_path, lines)
            else:
                for song in data["songs"]:
                    info_name = (
                        f"_info_{clean_name(song['title'])} - "
                        f"{clean_name(song['artist'])}.txt"
                    )
                    info_path = os.path.join(download_dir, info_name)
                    lines = [
                        "Type: [Single]",
                        f"Sang navn: {song['title']}",
                        f"Link: {song['url']}",
                        f"Kunstner: {song['artist']}",
                    ]
                    write_info_file(info_path, lines)

    print("\n" + "=" * 50)
    print("FORHÅNDSVISNING FÆRDIG" if not apply_changes else "MIGRATION FÆRDIG!")
    print(f" Opdaterede ID3-tags: {updated_count} filer")
    print(f" Flyttede/omdøbte: {moved_count} filer")
    print(f" Fejl: {error_count} filer")
    if not apply_changes:
        print("Ingen filer er ændret. Kør igen med --apply for at gennemføre.")
    print("==================================================")


def write_info_file(path, lines):
    content = "\n".join(lines)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as existing_file:
            if existing_file.read() == content:
                return
        stem, extension = os.path.splitext(path)
        suffix = 2
        while os.path.exists(f"{stem} ({suffix}){extension}"):
            suffix += 1
        path = f"{stem} ({suffix}){extension}"
    with open(path, "w", encoding="utf-8") as info_file:
        info_file.write(content)


def main():
    parser = argparse.ArgumentParser(description="Ryd op i MP3-tags og musikmapper.")
    parser.add_argument("--directory", default=DOWNLOAD_DIR, help="Mappen med MP3-filer")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Gem tags og flyt filer (standard er kun forhåndsvisning)",
    )
    args = parser.parse_args()
    migrate_library(args.directory, apply_changes=args.apply)


if __name__ == "__main__":
    main()
