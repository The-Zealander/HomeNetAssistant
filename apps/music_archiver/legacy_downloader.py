import os
import subprocess
from dotenv import load_dotenv

# Indlæs variabler fra .env filen (hvis du har en sti defineret der)
load_dotenv()

# Definer hvor musikken skal gemmes.
DOWNLOAD_DIR = os.getenv("MUSIC_DOWNLOAD_PATH", "./home/nobara-user/Musik")


def download_music(link):
    print(f"\nStarter download og konvertering for: {link}")

    # Dynamisk filstruktur:
    # Mappe 1: Kunstner (hvis den mangler, bruges kanalens navn)
    # Mappe 2: Album (hvis det er en playliste, bruges playlistens navn, ellers 'Single')
    # Filnavn: Tracknummer (hvis det findes) - Sangtitel.mp3
    output_template = f"{DOWNLOAD_DIR}/%(artist,uploader)s/%(album,playlist|Single)s/%(playlist_index&%02d - |)s%(title)s.%(ext)s"

    command = [
        "yt-dlp",
        "-x", "--audio-format", "mp3",
        "--audio-quality", "0",  # 0 er den bedst mulige variable bitrate
        "--embed-metadata",  # Skriver kunstner, titel, album ind i MP3'ens ID3-tags
        "--embed-thumbnail",  # Downloader coverbilledet og bager det ind i selve filen
        "-o", output_template,
        link
    ]

    try:
        # Kører kommandoen i terminalen i baggrunden
        subprocess.run(command, check=True)
        print("\nSuccess! Filen er downloadet, konverteret og tagget.")
    except subprocess.CalledProcessError as e:
        print(f"\nFejl under download: Det underliggende yt-dlp program fejlede. Tjek linket. {e}")
    except FileNotFoundError:
        print(
            "\nKRITISK FEJL: Kunne ikke finde 'yt-dlp' eller 'ffmpeg'. Sørg for at FFmpeg er installeret på din maskine.")


def main():
    # Sikr at download-mappen eksisterer
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    print("SARAH Music Archiver (Lokal yt-dlp version)")
    print("-" * 45)

    while True:
        url = input("\nIndsæt YouTube/YT Music link (eller skriv 'exit' for at lukke): ").strip()

        if url.lower() == 'exit':
            break
        elif not url:
            continue

        download_music(url)


if __name__ == "__main__":
    main()