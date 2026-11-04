import json
import os
import tempfile
import unittest
from unittest.mock import patch

from mutagen.id3 import ID3

from apps.music_archiver import archiver as music_archiver
from apps.program_manager import PROGRAMMER


class MusicArchiverQualityTests(unittest.TestCase):
    def test_music_archiver_is_registered_as_a_gui_program(self):
        self.assertEqual(PROGRAMMER["musikarkivar"]["type"], "gui")
        self.assertEqual(
            PROGRAMMER["musikarkivar"]["kommando"],
            ["-m", "apps.music_archiver.archiver"],
        )

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.download_dir = music_archiver.DOWNLOAD_DIR
        music_archiver.DOWNLOAD_DIR = self.temp_dir.name
        self.addCleanup(setattr, music_archiver, "DOWNLOAD_DIR", self.download_dir)

    def test_album_download_uses_album_folder_track_number_and_embedded_cover(self):
        metadata = {
            "id": "track-2",
            "title": "Video title",
            "track": "Second Song",
            "track_number": "2/10",
            "artist": "Example Artist",
            "album": "Example Album",
        }

        def fake_run(command, **kwargs):
            if "--dump-json" in command:
                return type("Result", (), {"stdout": json.dumps(metadata)})()
            with open(os.path.join(kwargs["cwd"], "audio.mp3"), "wb") as audio:
                audio.write(b"fake audio")
            with open(os.path.join(kwargs["cwd"], "audio.jpg"), "wb") as cover:
                cover.write(b"fake cover")
            return type("Result", (), {"stderr": ""})()

        with patch.object(music_archiver.subprocess, "run", side_effect=fake_run) as run:
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/track-2",
                "track_number": 8,
                "is_album": True,
                "album_name": "Playlist title",
            }, 10))

        expected_path = os.path.join(self.temp_dir.name, "Example Album", "02 - Second Song.mp3")
        self.assertTrue(os.path.isfile(expected_path))
        tags = ID3(expected_path)
        self.assertEqual(str(tags["TIT2"]), "Second Song")
        self.assertEqual(str(tags["TALB"]), "Example Album")
        self.assertEqual(str(tags["TRCK"]), "2")
        self.assertEqual(tags.getall("APIC")[0].data, b"fake cover")
        self.assertIn("--embed-thumbnail", run.call_args_list[1].args[0])

    def test_same_filename_from_different_tracks_is_never_overwritten(self):
        metadata = {
            "title": "Same title",
            "track": "Same title",
            "track_number": 1,
            "artist": "Example Artist",
            "album": "Example Album",
        }
        current_id = {"value": "track-a"}

        def fake_run(command, **kwargs):
            if "--dump-json" in command:
                result = dict(metadata, id=current_id["value"])
                return type("Result", (), {"stdout": json.dumps(result)})()
            with open(os.path.join(kwargs["cwd"], "audio.mp3"), "wb") as audio:
                audio.write(b"fake audio")
            return type("Result", (), {"stderr": ""})()

        with patch.object(music_archiver.subprocess, "run", side_effect=fake_run):
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/track-a",
                "track_number": 1,
                "is_album": True,
                "album_name": "Example Album",
            }, 1))
            current_id["value"] = "track-b"
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/track-b",
                "track_number": 1,
                "is_album": True,
                "album_name": "Example Album",
            }, 1))

        album_dir = os.path.join(self.temp_dir.name, "Example Album")
        self.assertEqual(len(os.listdir(album_dir)), 2)
        self.assertTrue(os.path.isfile(os.path.join(album_dir, "01 - Same title.mp3")))
        self.assertTrue(os.path.isfile(os.path.join(album_dir, "01 - Same title - Example Artist.mp3")))

    def test_playlist_album_name_is_used_when_track_metadata_says_single(self):
        metadata = {
            "id": "playlist-track",
            "title": "Track",
            "artist": "Example Artist",
            "album": "Single",
        }

        def fake_run(command, **kwargs):
            if "--dump-json" in command:
                return type("Result", (), {"stdout": json.dumps(metadata)})()
            with open(os.path.join(kwargs["cwd"], "audio.mp3"), "wb") as audio:
                audio.write(b"fake audio")
            return type("Result", (), {"stderr": ""})()

        with patch.object(music_archiver.subprocess, "run", side_effect=fake_run):
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/playlist-track",
                "id": "playlist-track",
                "track_number": 1,
                "is_album": True,
                "album_name": "Playlist album",
            }, 1))

        self.assertTrue(os.path.isfile(os.path.join(
            self.temp_dir.name, "Playlist album", "01 - Track.mp3"
        )))

    def test_same_video_is_skipped_on_repeat_download(self):
        metadata = {
            "id": "repeat-id",
            "title": "Repeat",
            "artist": "Example Artist",
        }

        def fake_run(command, **kwargs):
            if "--dump-json" in command:
                return type("Result", (), {"stdout": json.dumps(metadata)})()
            with open(os.path.join(kwargs["cwd"], "audio.mp3"), "wb") as audio:
                audio.write(b"fake audio")
            return type("Result", (), {"stderr": ""})()

        song = {
            "url": "https://example.test/repeat-id",
            "id": "repeat-id",
            "track_number": 1,
            "is_album": False,
        }
        with patch.object(music_archiver.subprocess, "run", side_effect=fake_run) as run:
            self.assertTrue(music_archiver.download_single_track(song, 1))
            self.assertTrue(music_archiver.download_single_track(song, 1))

        self.assertEqual(run.call_count, 3)
        self.assertEqual(os.listdir(self.temp_dir.name), ["Repeat.mp3"])

    def test_single_without_album_is_saved_directly_under_music_root(self):
        metadata = {
            "id": "single-id",
            "title": "Single title",
            "artist": "Example Artist",
        }

        def fake_run(command, **kwargs):
            if "--dump-json" in command:
                return type("Result", (), {"stdout": json.dumps(metadata)})()
            with open(os.path.join(kwargs["cwd"], "audio.mp3"), "wb") as audio:
                audio.write(b"fake audio")
            return type("Result", (), {"stderr": ""})()

        with patch.object(music_archiver.subprocess, "run", side_effect=fake_run):
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/single-id",
                "track_number": 1,
                "is_album": False,
            }, 1))

        self.assertTrue(os.path.isfile(os.path.join(self.temp_dir.name, "Single title.mp3")))
        self.assertEqual(
            [entry for entry in os.listdir(self.temp_dir.name) if os.path.isdir(os.path.join(self.temp_dir.name, entry))],
            []
        )

    def test_refuses_to_archive_a_different_video_than_the_playlist_entry(self):
        metadata = {"id": "wrong-id", "title": "Wrong song", "artist": "Example Artist"}
        info_result = type("Result", (), {"stdout": json.dumps(metadata)})()

        with patch.object(music_archiver.subprocess, "run", return_value=info_result) as run:
            self.assertFalse(music_archiver.download_single_track({
                "url": "https://example.test/expected-id",
                "id": "expected-id",
                "track_number": 1,
                "title": "Expected song",
                "is_album": True,
                "album_name": "Expected album",
            }, 1))

        self.assertEqual(run.call_count, 1)
        self.assertEqual(os.listdir(self.temp_dir.name), [])

    def test_playlist_metadata_is_loaded_and_track_order_is_retained(self):
        playlist_data = {
            "title": "Album",
            "entries": [
                {"id": "second", "url": "https://example.test/second", "track_number": 2, "title": "Second"},
                {"id": "first", "url": "https://example.test/first", "track_number": 1, "title": "First"},
            ],
        }
        result = type("Result", (), {"stdout": json.dumps(playlist_data)})()

        with patch.object(music_archiver.subprocess, "run", return_value=result), \
                patch.object(music_archiver, "download_single_track", return_value=True) as download:
            music_archiver.process_url("https://example.test/album")

        self.assertEqual(
            [call.args[0]["track_number"] for call in download.call_args_list],
            [2, 1]
        )
        self.assertEqual(
            [call.args[0]["metadata"]["id"] for call in download.call_args_list],
            ["second", "first"]
        )


if __name__ == "__main__":
    unittest.main()
