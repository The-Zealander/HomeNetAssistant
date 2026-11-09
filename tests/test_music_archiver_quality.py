"""Verify archive-quality checks and selection of the expected source tracks."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from mutagen.id3 import COMM, ID3, TIT2

from apps.music_archiver import archiver as music_archiver
from apps.music_archiver import legacy_downloader
from apps.program_manager import PROGRAMMER


class MusicArchiverQualityTests(unittest.TestCase):
    def test_music_archiver_is_registered_as_a_gui_program(self):
        self.assertEqual(PROGRAMMER["musikarkivar"]["type"], "gui")
        self.assertEqual(
            PROGRAMMER["musikarkivar"]["kommando"],
            ["-m", "apps.music_archiver.archiver"],
        )

    def test_legacy_downloader_delegates_to_the_canonical_entry_point(self):
        self.assertIs(legacy_downloader.main, music_archiver.main)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        music_archiver._MUSICBRAINZ_CACHE.clear()
        music_archiver._MUSICBRAINZ_RELEASE_CACHE.clear()
        music_archiver._MUSICBRAINZ_LAST_REQUEST = 0.0
        music_archiver._MUSICBRAINZ_UNAVAILABLE_UNTIL = 0.0
        self.download_dir = music_archiver.DOWNLOAD_DIR
        music_archiver.DOWNLOAD_DIR = self.temp_dir.name
        self.addCleanup(setattr, music_archiver, "DOWNLOAD_DIR", self.download_dir)
        self.spotify_credentials = patch.dict(
            os.environ,
            {
                "SPOTIPY_CLIENT_ID": "",
                "SPOTIPY_CLIENT_SECRET": "",
                "MUSIC_YTDLP_COOKIES_BROWSER": "brave",
            },
        )
        self.spotify_credentials.start()
        self.addCleanup(self.spotify_credentials.stop)
        self.musicbrainz_lookup = patch.object(
            music_archiver,
            "_musicbrainz_metadata",
            return_value={},
        )
        self.musicbrainz_lookup.start()
        self.addCleanup(self.musicbrainz_lookup.stop)

    def test_musicbrainz_503_opens_cooldown_instead_of_repeating_requests(self):
        error = HTTPError(
            "https://musicbrainz.org/ws/2/isrc/example",
            503,
            "Service Temporarily Unavailable",
            {},
            None,
        )
        self.musicbrainz_lookup.stop()
        with patch("apps.music_archiver.archiver.urlopen", side_effect=error) as request:
            with self.assertLogs(music_archiver.logger.name, level="WARNING") as logs:
                first_result = music_archiver._musicbrainz_json(
                    "https://musicbrainz.org/ws/2/isrc/example"
                )
                second_result = music_archiver._musicbrainz_json(
                    "https://musicbrainz.org/ws/2/isrc/another"
                )

        self.assertIsNone(first_result)
        self.assertIsNone(second_result)
        request.assert_called_once()
        self.assertEqual(len(logs.records), 1)
        self.assertIn("503", logs.records[0].getMessage())
        error.close()

    def test_musicbrainz_transient_failure_is_not_cached_as_a_permanent_miss(self):
        metadata = {
            "isrc": "USABC2200001",
            "title": "Example Track",
            "artist": "Example Artist",
        }

        self.musicbrainz_lookup.stop()
        with patch.object(music_archiver, "_musicbrainz_json", return_value=None) as request:
            self.assertEqual(music_archiver._musicbrainz_metadata(metadata), {})
            self.assertEqual(music_archiver._musicbrainz_metadata(metadata), {})

        self.assertEqual(request.call_count, 2)
        self.assertNotIn(
            "isrc:USABC2200001|example track|",
            music_archiver._MUSICBRAINZ_CACHE,
        )

    def test_album_download_uses_album_folder_track_number_and_embedded_cover(self):
        metadata = {
            "id": "track-2",
            "title": "Video title",
            "track": "Second Song",
            "track_number": "2/10",
            "artists": ["Example Artist", "Guest Artist"],
            "album_artist": "Example Artist",
            "album": "Example Album",
            "genre": "Pop",
            "release_date": "2022-01-15",
            "isrc": "USABC2200001",
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

        expected_path = os.path.join(
            self.temp_dir.name,
            "Example Artist",
            "Example Album",
            "02 - Second Song.mp3",
        )
        self.assertTrue(os.path.isfile(expected_path))
        tags = ID3(expected_path)
        self.assertEqual(str(tags["TIT2"]), "Second Song")
        self.assertEqual(str(tags["TALB"]), "Example Album")
        self.assertEqual(str(tags["TRCK"]), "2/10")
        self.assertEqual(str(tags["TPE1"]), "Example Artist / Guest Artist")
        self.assertEqual(tags["TXXX:ARTISTS"].text, ["Example Artist", "Guest Artist"])
        self.assertEqual(str(tags["TPE2"]), "Example Artist")
        self.assertEqual(tags["TXXX:ALBUM ARTISTS"].text, ["Example Artist"])
        self.assertEqual(str(tags["TCON"]), "Pop")
        self.assertEqual(str(tags["TDRL"]), "2022-01-15")
        self.assertNotIn("TDRC", tags)
        self.assertEqual(tags.version, (2, 4, 0))
        self.assertEqual(str(tags["TSRC"]), "USABC2200001")
        self.assertEqual(tags.getall("APIC")[0].data, b"fake cover")
        self.assertIn("--embed-thumbnail", run.call_args_list[1].args[0])
        self.assertIn("--audio-quality", run.call_args_list[1].args[0])
        self.assertIn("--cookies-from-browser", run.call_args_list[0].args[0])
        self.assertIn("brave", run.call_args_list[0].args[0])
        self.assertIn("--cookies-from-browser", run.call_args_list[1].args[0])
        self.assertIn("brave", run.call_args_list[1].args[0])
        quality_option = run.call_args_list[1].args[0].index("--audio-quality")
        self.assertEqual(run.call_args_list[1].args[0][quality_option + 1], "0")

    def test_album_download_uses_musicbrainz_cover_when_source_has_no_artwork(self):
        metadata = {
            "id": "coverless-track",
            "title": "Video title",
            "track": "Song",
            "track_number": 1,
            "artists": ["Track Artist"],
            "album_artist": "Album Artist",
            "album": "Example Album",
            "musicbrainz_release_id": "release-id",
        }

        def fake_run(command, **kwargs):
            if "--dump-json" in command:
                return type("Result", (), {"stdout": json.dumps(metadata)})()
            with open(os.path.join(kwargs["cwd"], "audio.mp3"), "wb") as audio_file:
                audio_file.write(b"fake audio")
            return type("Result", (), {"stderr": ""})()

        with patch.object(music_archiver.subprocess, "run", side_effect=fake_run), patch.object(
            music_archiver,
            "_cover_art_from_musicbrainz",
            return_value=("image/jpeg", b"archive cover"),
        ) as cover_lookup:
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/coverless-track",
                "id": "coverless-track",
                "is_album": True,
            }))

        path = os.path.join(
            self.temp_dir.name,
            "Album Artist",
            "Example Album",
            "01 - Song.mp3",
        )
        self.assertEqual(ID3(path).getall("APIC")[0].data, b"archive cover")
        cover_lookup.assert_called_once_with("release-id")

    def test_album_download_prefers_one_release_cover_over_track_thumbnails(self):
        metadata = {
            "id": "album-track-with-thumbnail",
            "track": "Song",
            "track_number": 1,
            "artists": ["Track Artist"],
            "album_artist": "Album Artist",
            "album": "Example Album",
            "musicbrainz_release_id": "release-id",
        }

        def fake_run(command, **kwargs):
            if "--dump-json" in command:
                return type("Result", (), {"stdout": json.dumps(metadata)})()
            with open(os.path.join(kwargs["cwd"], "audio.mp3"), "wb") as audio_file:
                audio_file.write(b"fake audio")
            with open(os.path.join(kwargs["cwd"], "audio.jpg"), "wb") as image_file:
                image_file.write(b"track thumbnail")
            return type("Result", (), {"stderr": ""})()

        with patch.object(music_archiver.subprocess, "run", side_effect=fake_run), patch.object(
            music_archiver,
            "_cover_art_from_musicbrainz",
            return_value=("image/jpeg", b"release cover"),
        ) as cover_lookup:
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/album-track-with-thumbnail",
                "is_album": True,
            }))

        path = os.path.join(
            self.temp_dir.name,
            "Album Artist",
            "Example Album",
            "01 - Song.mp3",
        )
        self.assertEqual(ID3(path).getall("APIC")[0].data, b"release cover")
        cover_lookup.assert_called_once_with("release-id")

    def test_title_as_source_album_is_replaced_for_tags_and_album_directory(self):
        source_metadata = {
            "id": "alle-gutta-track",
            "title": "Alle Gutta",
            "track": "Alle Gutta",
            "track_number": 1,
            "artist": "Staysman og Staysman & Lazz",
            "album": "Alle Gutta",
        }
        catalog_metadata = {
            "track": "Alle Gutta",
            "title": "Alle Gutta",
            "artists": ["Staysman & Lazz"],
            "album_artist": ["Staysman & Lazz"],
            "album": "Helt Sykt Vol 2",
            "track_number": 1,
        }
        def fake_run(command, **kwargs):
            if "--dump-json" in command:
                return type("Result", (), {"stdout": json.dumps(source_metadata)})()
            with open(os.path.join(kwargs["cwd"], "audio.mp3"), "wb") as audio_file:
                audio_file.write(b"fake audio")
            return type("Result", (), {"stderr": ""})()

        with patch.object(music_archiver, "_musicbrainz_metadata", return_value=catalog_metadata), \
                patch.object(music_archiver.subprocess, "run", side_effect=fake_run):
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/alle-gutta",
                "is_album": True,
                "album_name": "Alle Gutta",
            }, 1))

        expected_path = os.path.join(
            self.temp_dir.name,
            "Staysman & Lazz",
            "Helt Sykt Vol 2",
            "01 - Alle Gutta.mp3",
        )
        self.assertTrue(os.path.isfile(expected_path))
        self.assertEqual(str(ID3(expected_path)["TALB"]), "Helt Sykt Vol 2")
        self.assertFalse(os.path.exists(os.path.join(
            self.temp_dir.name,
            "Staysman & Lazz",
            "Alle Gutta",
        )))

    def test_single_track_link_keeps_catalog_album_and_moves_into_album_folder(self):
        metadata = {
            "id": "album-track",
            "title": "Video title",
            "track": "Song",
            "track_number": 2,
            "artist": "Track Artist",
            "album_artist": "Album Artist",
            "album": "Actual Album",
        }

        def fake_run(command, **kwargs):
            if "--dump-json" in command:
                return type("Result", (), {"stdout": json.dumps(metadata)})()
            with open(os.path.join(kwargs["cwd"], "audio.mp3"), "wb") as audio_file:
                audio_file.write(b"fake audio")
            return type("Result", (), {"stderr": ""})()

        with patch.object(music_archiver.subprocess, "run", side_effect=fake_run):
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/album-track",
                "id": "album-track",
                "is_album": False,
            }))

        path = os.path.join(
            self.temp_dir.name,
            "Album Artist",
            "Actual Album",
            "02 - Song.mp3",
        )
        self.assertEqual(str(ID3(path)["TALB"]), "Actual Album")
        self.assertEqual(ID3(path)["TPE2"].text, ["Album Artist"])

    def test_full_track_credits_replace_flat_playlist_artist_string(self):
        playlist_metadata = {
            "id": "collaboration",
            "title": "30 kmh",
            "artist": "Lia Larsson, NINE",
        }
        full_track_metadata = {
            "id": "collaboration",
            "title": "30 kmh",
            "artists": ["Lia Larsson", "NINE"],
            "artist": "Lia Larsson, NINE",
            "album": "30 kmh",
        }

        def fake_run(command, **kwargs):
            if "--dump-json" in command:
                return type("Result", (), {"stdout": json.dumps(full_track_metadata)})()
            with open(os.path.join(kwargs["cwd"], "audio.mp3"), "wb") as audio:
                audio.write(b"fake audio")
            return type("Result", (), {"stderr": ""})()

        with patch.object(music_archiver.subprocess, "run", side_effect=fake_run):
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/collaboration",
                "id": "collaboration",
                "is_album": False,
                "metadata": playlist_metadata,
            }))

        tags = ID3(os.path.join(self.temp_dir.name, "30 kmh.mp3"))
        self.assertEqual(str(tags["TPE1"]), "Lia Larsson / NINE")
        self.assertEqual(tags["TXXX:ARTISTS"].text, ["Lia Larsson", "NINE"])
        self.assertEqual(str(tags["TPE2"]), "Lia Larsson")

    def test_same_title_replaces_previous_track_instead_of_creating_duplicate(self):
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

        album_dir = os.path.join(self.temp_dir.name, "Example Artist", "Example Album")
        self.assertEqual(os.listdir(album_dir), ["01 - Same title.mp3"])
        tags = ID3(os.path.join(album_dir, "01 - Same title.mp3"))
        self.assertTrue(any(
            frame.desc == "sarah-source-id" and "track-b" in frame.text
            for frame in tags.getall("COMM")
        ))

    def test_same_title_replacement_removes_legacy_artist_suffixed_duplicates(self):
        metadata = {
            "id": "replacement-id",
            "title": "Same title",
            "track": "Same title",
            "track_number": 1,
            "artist": "Example Artist",
            "album": "Example Album",
        }
        album_dir = os.path.join(self.temp_dir.name, "Example Artist", "Example Album")
        os.makedirs(album_dir)
        old_path = os.path.join(album_dir, "01 - Same title - Example Artist.mp3")
        with open(old_path, "wb") as audio:
            audio.write(b"old audio")
        old_tags = ID3()
        old_tags.add(TIT2(encoding=3, text="Same title"))
        old_tags.save(old_path)

        def fake_run(command, **kwargs):
            if "--dump-json" in command:
                return type("Result", (), {"stdout": json.dumps(metadata)})()
            with open(os.path.join(kwargs["cwd"], "audio.mp3"), "wb") as audio:
                audio.write(b"new audio")
            return type("Result", (), {"stderr": ""})()

        with patch.object(music_archiver.subprocess, "run", side_effect=fake_run):
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/replacement",
                "id": "replacement-id",
                "is_album": True,
                "album_name": "Example Album",
            }))

        self.assertEqual(os.listdir(album_dir), ["01 - Same title.mp3"])
        replaced_tags = ID3(os.path.join(album_dir, "01 - Same title.mp3"))
        self.assertTrue(any(
            frame.desc == "sarah-source-id" and "replacement-id" in frame.text
            for frame in replaced_tags.getall("COMM")
        ))

    def test_repeat_download_migrates_existing_artist_suffixed_file_to_canonical_name(self):
        metadata = {
            "id": "existing-id",
            "title": "Repeat",
            "track": "Repeat",
            "artist": "Example Artist",
        }
        old_path = os.path.join(self.temp_dir.name, "Repeat - Example Artist.mp3")
        with open(old_path, "wb") as audio:
            audio.write(b"existing audio")
        tags = ID3()
        tags.add(TIT2(encoding=3, text="Repeat"))
        tags.add(COMM(encoding=3, lang="eng", desc="sarah-source-id", text="existing-id"))
        tags.save(old_path)

        with patch.object(
            music_archiver,
            "_read_json_output",
            return_value=metadata,
        ) as read_metadata:
            self.assertTrue(music_archiver.download_single_track({
                "url": "https://example.test/repeat",
                "id": "existing-id",
                "is_album": False,
            }))

        self.assertEqual(os.listdir(self.temp_dir.name), ["Repeat.mp3"])
        with open(os.path.join(self.temp_dir.name, "Repeat.mp3"), "rb") as audio:
            self.assertTrue(audio.read().endswith(b"existing audio"))
        read_metadata.assert_called_once()

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
            self.temp_dir.name, "Example Artist", "Playlist album", "01 - Track.mp3"
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

    def test_browser_cookies_can_be_disabled(self):
        with patch.dict(os.environ, {"MUSIC_YTDLP_COOKIES_BROWSER": ""}):
            self.assertEqual(music_archiver._yt_dlp_cookie_args(), [])

    def test_auto_browser_cookies_only_enable_when_brave_profile_exists(self):
        with patch.dict(os.environ, {"MUSIC_YTDLP_COOKIES_BROWSER": "auto"}), patch.object(
            music_archiver,
            "_brave_profile_directory",
            return_value=Path(self.temp_dir.name) / "missing-profile",
        ):
            self.assertEqual(music_archiver._yt_dlp_cookie_args(), [])

        with patch.dict(os.environ, {"MUSIC_YTDLP_COOKIES_BROWSER": "auto"}), patch.object(
            music_archiver,
            "_brave_profile_directory",
            return_value=Path(self.temp_dir.name),
        ):
            self.assertEqual(
                music_archiver._yt_dlp_cookie_args(),
                ["--cookies-from-browser", "brave"],
            )

    def test_browser_cookie_profile_is_passed_as_a_single_argument(self):
        with patch.dict(os.environ, {"MUSIC_YTDLP_COOKIES_BROWSER": "brave:Profile 1"}):
            self.assertEqual(
                music_archiver._yt_dlp_cookie_args(),
                ["--cookies-from-browser", "brave:Profile 1"],
            )

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

    def test_playlist_failures_report_track_and_extractor_reason(self):
        playlist = {
            "title": "Example playlist",
            "entries": [{
                "id": "blocked-track",
                "url": "https://music.youtube.com/watch?v=blocked-track",
                "title": "Blocked track",
            }],
        }
        error = subprocess.CalledProcessError(
            1,
            ["yt-dlp"],
            stderr="ERROR: Sign in to confirm you’re not a bot",
        )
        messages = []
        with patch.object(
            music_archiver,
            "_read_json_output",
            side_effect=[playlist, error],
        ):
            result = music_archiver.process_url(
                "https://music.youtube.com/playlist?list=example",
                messages.append,
            )

        self.assertFalse(result)
        self.assertTrue(any(
            "Blocked track" in message and "not a bot" in message
            for message in messages
        ))
        self.assertIn("se fejl ovenfor", messages[-1])

    def test_failed_track_link_is_saved_for_later_library_restore(self):
        playlist = {
            "title": "Recovery playlist",
            "entries": [{
                "id": "blocked-track",
                "url": "https://music.youtube.com/watch?v=blocked-track",
                "title": "Blocked track",
                "artist": "Example Artist",
            }],
        }
        with patch.object(music_archiver, "_read_json_output", return_value=playlist), patch.object(
            music_archiver,
            "download_single_track",
            return_value=False,
        ):
            self.assertFalse(music_archiver.process_url(
                "https://music.youtube.com/playlist?list=recovery",
            ))

        with open(
            os.path.join(self.temp_dir.name, "_samlet_arkiv.txt"),
            encoding="utf-8",
        ) as archive:
            saved_links = archive.read()
        self.assertIn("Blocked track", saved_links)
        self.assertIn(
            "Track link: https://music.youtube.com/watch?v=blocked-track",
            saved_links,
        )

    def test_empty_playlist_is_not_reported_as_success(self):
        messages = []
        with patch.object(
            music_archiver,
            "_read_json_output",
            return_value={"title": "Empty playlist", "entries": []},
        ) as read_json:
            result = music_archiver.process_url(
                "https://music.youtube.com/playlist?list=empty",
                messages.append,
            )

        self.assertFalse(result)
        command = read_json.call_args.args[0]
        self.assertIn("--cookies-from-browser", command)
        self.assertIn("brave", command)
        self.assertIn("ingen tilgængelige spor", messages[-1])

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
            music_archiver.process_url("https://example.test/album", download_video=True)

        self.assertEqual(
            [call.args[0]["track_number"] for call in download.call_args_list],
            [2, 1]
        )
        self.assertEqual(
            [call.args[0]["metadata"]["id"] for call in download.call_args_list],
            ["second", "first"]
        )
        self.assertTrue(all(
            call.kwargs["download_video"] for call in download.call_args_list
        ))

    def test_video_download_saves_mp4_outside_music_library(self):
        with tempfile.TemporaryDirectory() as video_directory:
            metadata = {
                "id": "video-id",
                "webpage_url": "https://www.youtube.com/watch?v=video-id",
                "title": "Example Video",
                "artist": "Example Artist",
                "album": "Example Album",
            }

            def fake_run(command, **kwargs):
                Path(kwargs["cwd"], "video.mp4").write_bytes(b"mp4 video")

            messages = []
            with patch.dict(
                os.environ,
                {"MUSIC_VIDEO_DOWNLOAD_PATH": video_directory},
            ), patch.object(
                music_archiver.subprocess,
                "run",
                side_effect=fake_run,
            ) as run:
                self.assertTrue(music_archiver._download_music_video(metadata, messages.append))

            saved_video = Path(video_directory) / "Example Artist" / "Example Album" / (
                "Example Video [video-id].mp4"
            )
            self.assertTrue(saved_video.is_file())
            self.assertFalse(Path(self.temp_dir.name) in saved_video.parents)
            command = run.call_args.args[0]
            self.assertIn("--merge-output-format", command)
            self.assertIn("mp4", command)
            self.assertTrue(any("Musikvideo gemt separat" in message for message in messages))

    def test_missing_optional_video_does_not_fail_successful_audio_download(self):
        metadata = {
            "id": "track-id",
            "webpage_url": "https://www.youtube.com/watch?v=track-id",
            "title": "Example Track",
            "artist": "Example Artist",
        }
        with patch.object(music_archiver, "_read_json_output", return_value=metadata), \
                patch.object(music_archiver, "_already_downloaded", return_value=True), \
                patch.object(music_archiver, "_download_music_video", return_value=False) as video:
            succeeded = music_archiver.download_single_track(
                {
                    "url": metadata["webpage_url"],
                    "id": metadata["id"],
                    "metadata": metadata,
                },
                download_video=True,
            )

        self.assertTrue(succeeded)
        video.assert_called_once()


if __name__ == "__main__":
    unittest.main()
