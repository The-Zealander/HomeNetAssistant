import os
import shutil
import re
from mutagen.mp3 import MP3, HeaderNotFoundError
from mutagen.id3 import ID3, TIT2, TPE1, TALB, TRCK
from dotenv import load_dotenv
from ytmusicapi import YTMusic

load_dotenv()

DOWNLOAD_DIR = os.getenv("Musik", "/home/nobara-user/Musik")

# Initialiserer YouTube Music uden brug af login eller API-nøgler
ytmusic = YTMusic()


def clean_artist_name(artist_string):
    if not artist_string:
        return "Ukendt Kunstner"
    separators = [r'(?i)\s+feat\.\s+', r'(?i)\s+ft\.\s+', r'\s*,\s*', r'\s*&\s*', r'(?i)\s+x\s+']
    cleaned = artist_string
    for sep in separators:
        parts = re.split(sep, cleaned)
        cleaned = parts[0]
    return cleaned.strip()


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name).strip()


def get_ytmusic_metadata(title, artist):
    """Slår sangen op på YT Music for at hente det præcise tracknummer og album."""
    query = f"{artist} {title}".strip()
    if not query or query == "Ukendt Kunstner Ukendt Titel":
        return None

    try:
        results = ytmusic.search(query, filter="songs", limit=1)
        if not results:
            return None

        song = results[0]
        song_title = song.get('title', title)

        # Kunstner
        artists = song.get('artists', [])
        primary_artist = artists[0]['name'] if artists else artist

        # Album & Tracknummer
        album_info = song.get('album')
        if not album_info or not album_info.get('id'):
            return {
                'title': song_title,
                'artist': primary_artist,
                'album': 'Singles',
                'track_number': '01'
            }

        album_name = album_info.get('name', 'Ukendt Album')
        album_id = album_info.get('id')

        # Slå albummet op for at finde sangens præcise placering (track nummer)
        track_num = "01"
        try:
            album_details = ytmusic.get_album(album_id)
            tracks = album_details.get('tracks', [])
            for index, track in enumerate(tracks, start=1):
                if track.get('videoId') == song.get('videoId') or track.get('title', '').lower() == song_title.lower():
                    track_num = str(index).zfill(2)
                    break
        except Exception:
            pass

        return {
            'title': song_title,
            'artist': primary_artist,
            'album': album_name,
            'track_number': track_num
        }

    except Exception as e:
        print(f"  [Advarsel] Kunne ikke slå '{query}' op online: {e}")
        return None


def fix_library():
    print("SARAH Library Fixer v4 (YT Music Unauthenticated Mode)")
    print("-" * 60)
    print(f"Scanner {DOWNLOAD_DIR} for musikfiler...\n")

    moved_count = 0
    error_count = 0

    for root, dirs, files in os.walk(DOWNLOAD_DIR):
        for file in files:
            if not file.lower().endswith(".mp3"):
                continue

            old_path = os.path.join(root, file)

            try:
                audio = MP3(old_path, ID3=ID3)
                tags = audio.tags if audio.tags else ID3()

                # Udled lokal titel og kunstner fra tags eller filnavn
                local_title = str(tags.get('TIT2', ''))
                if not local_title or local_title == 'None':
                    local_title = re.sub(r'^\d{1,3}\s*-\s*', '', file).replace('.mp3', '')

                local_artist = str(tags.get('TPE1', ''))
                if not local_artist or local_artist == 'None':
                    # Brug overmappens navn som gæt på kunstner
                    parent_dir = os.path.basename(os.path.dirname(os.path.dirname(old_path)))
                    local_artist = parent_dir if parent_dir != os.path.basename(DOWNLOAD_DIR) else ""

                print(f"Slår op: '{local_title}' (Kunstner: '{local_artist}')")
                meta = get_ytmusic_metadata(local_title, local_artist)

                if meta:
                    title = meta['title']
                    artist = meta['artist']
                    album = meta['album']
                    track_num = meta['track_number']
                    print(f"  -> Fundet: {artist} - {album} [Track {track_num}]")

                    # Gem de nye korrekte data i MP3-filen
                    tags.add(TIT2(encoding=3, text=title))
                    tags.add(TPE1(encoding=3, text=artist))
                    tags.add(TALB(encoding=3, text=album))
                    tags.add(TRCK(encoding=3, text=track_num))
                    tags.save(old_path)
                else:
                    # Fallback hvis opstramning online mislykkes
                    title = local_title
                    artist = local_artist if local_artist else "Ukendt Kunstner"
                    album = str(tags.get('TALB', 'Singles'))
                    match = re.search(r'^(\d{1,3})\s*-', file)
                    track_num = match.group(1).zfill(2) if match else "01"

                # Byg den nye, rene mappestruktur
                folder_artist = sanitize_filename(clean_artist_name(artist))
                folder_album = sanitize_filename(album)

                if folder_album.lower() == title.lower() or folder_album == 'Ukendt Album' or "single" in folder_album.lower():
                    folder_album = "Singles"

                final_filename = sanitize_filename(f"{track_num} - {title}.mp3")

                new_dir = os.path.join(DOWNLOAD_DIR, folder_artist, folder_album)
                new_path = os.path.join(new_dir, final_filename)

                # Flyt og omdøb filen
                if old_path != new_path:
                    os.makedirs(new_dir, exist_ok=True)
                    if os.path.exists(new_path):
                        os.remove(new_path)
                    shutil.move(old_path, new_path)
                    moved_count += 1

            except HeaderNotFoundError:
                print(f"[Fejl] Filen {file} er korrupt og springes over.")
                error_count += 1
            except Exception as e:
                print(f"[Fejl] Ved håndtering af {file}: {e}")
                error_count += 1

    print("\nRydder op i tomme mapper...")
    for root, dirs, files in os.walk(DOWNLOAD_DIR, topdown=False):
        for dir_name in dirs:
            dir_path = os.path.join(root, dir_name)
            if not os.listdir(dir_path):
                try:
                    os.rmdir(dir_path)
                except Exception:
                    pass

    print("-" * 60)
    print(f"Færdig! Omdøbte, taggede og samlede {moved_count} sange korrekt.")


if __name__ == "__main__":
    fix_library()