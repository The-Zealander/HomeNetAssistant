import os
import tempfile
import unittest
from unittest.mock import patch

from mutagen.id3 import ID3

from apps.music_archiver.archiver import (
    append_to_master_log,
    artist_names,
    source_url,
    tag_mp3,
)


class MusicArchiverTagTests(unittest.TestCase):
    def test_artist_names_splits_collaborations_and_preserves_explicit_artist_lists(self):
        self.assertEqual(artist_names("Artist One, Artist Two"), ["Artist One", "Artist Two"])
        self.assertEqual(artist_names("Artist One feat. Artist Two"), ["Artist One", "Artist Two"])
        self.assertEqual(artist_names(["Tyler, The Creator", "Artist Two"]), [
            "Tyler, The Creator",
            "Artist Two",
        ])

    def test_album_tracks_keep_multiple_track_artists_and_one_album_artist(self):
        with tempfile.TemporaryDirectory() as directory:
            track_paths = [os.path.join(directory, f"track-{number}.mp3") for number in (1, 2)]

            for path, track_artists in zip(
                track_paths,
                (["Artist One", "Guest Artist"], ["Artist One"]),
            ):
                with open(path, "wb") as audio_file:
                    audio_file.write(b"audio")
                tag_mp3(
                    path,
                    {
                        "title": "Song",
                        "artists": track_artists,
                        "track_number": 1,
                    },
                    is_album=True,
                    album_name="Example Album",
                    master_album_artist="Artist One",
                )

            for path, expected_artists in zip(
                track_paths,
                (["Artist One", "Guest Artist"], ["Artist One"]),
            ):
                tags = ID3(path)
                self.assertEqual(tags.getall("TPE1")[0].text, expected_artists)
                self.assertEqual(str(tags["TPE2"]), "Artist One")
                self.assertEqual(str(tags["TALB"]), "Example Album")

    def test_single_collaboration_uses_primary_artist_as_album_artist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "single.mp3")
            with open(path, "wb") as audio_file:
                audio_file.write(b"audio")

            tag_mp3(
                path,
                {"title": "Song", "artist": "Artist One, Artist Two"},
                is_album=False,
                album_name=None,
                master_album_artist="Ignored Album Artist",
            )

            tags = ID3(path)
            self.assertEqual(tags.getall("TPE1")[0].text, ["Artist One", "Artist Two"])
            self.assertEqual(str(tags["TPE2"]), "Artist One")

    def test_master_log_keeps_album_and_track_links_with_recovery_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("apps.music_archiver.archiver.DOWNLOAD_DIR", directory):
                append_to_master_log(
                    "https://music.youtube.com/playlist?list=album-id",
                    True,
                    "Example Album",
                    [{
                        "artist": "Artist One, Guest Artist",
                        "title": "Song",
                        "track_number": 2,
                        "url": "https://music.youtube.com/watch?v=track-id",
                    }],
                    album_artist="Artist One",
                )

            with open(os.path.join(directory, "_samlet_arkiv.txt"), encoding="utf-8") as log:
                contents = log.read()

            self.assertIn("ALBUM: Example Album", contents)
            self.assertIn("ALBUM ARTIST: Artist One", contents)
            self.assertIn("SOURCE LINK: https://music.youtube.com/playlist?list=album-id", contents)
            self.assertIn("Artist One, Guest Artist - Song [Track 2]", contents)
            self.assertIn("Track link: https://music.youtube.com/watch?v=track-id", contents)

    def test_source_url_falls_back_to_flat_entry_id(self):
        self.assertEqual(
            source_url({"id": "track-id"}),
            "https://music.youtube.com/watch?v=track-id",
        )


if __name__ == "__main__":
    unittest.main()
