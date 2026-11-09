"""Test music metadata lookup, artist credits, and ID3 tag generation."""

import json
import os
import sys
import tempfile
import types
import unittest
from io import BytesIO
from unittest.mock import Mock, patch

from mutagen.id3 import ID3

from apps.music_archiver import archiver as music_archiver
from apps.music_archiver.archiver import (
    append_to_master_log,
    artist_names,
    source_url,
    tag_mp3,
)


class MusicArchiverTagTests(unittest.TestCase):
    def test_artist_names_splits_collaborations_and_preserves_explicit_artist_lists(self):
        self.assertEqual(artist_names("Artist One, Artist Two"), ["Artist One", "Artist Two"])
        self.assertEqual(
            artist_names("Lia Larsson, Tone Sekelius, Lisa Ajax"),
            ["Lia Larsson", "Tone Sekelius", "Lisa Ajax"],
        )
        self.assertEqual(artist_names("Artist One feat. Artist Two"), ["Artist One", "Artist Two"])
        self.assertEqual(artist_names("Tyler, The Creator"), ["Tyler, The Creator"])
        self.assertEqual(artist_names(["Tyler, The Creator", "Artist Two"]), [
            "Tyler, The Creator",
            "Artist Two",
        ])

    def test_metadata_merge_prefers_full_track_data_and_keeps_playlist_fallbacks(self):
        merged = music_archiver._merge_track_metadata(
            {"id": "track-id", "artist": "Playlist Artist", "album": "Playlist Album"},
            {"id": "track-id", "artists": ["Artist One", "Artist Two"], "genre": "Pop"},
        )

        self.assertEqual(merged["artists"], ["Artist One", "Artist Two"])
        self.assertEqual(merged["album"], "Playlist Album")
        self.assertEqual(merged["genre"], "Pop")

    def test_youtube_upload_date_is_not_mistagged_as_a_release_date(self):
        self.assertIsNone(music_archiver._release_date({"upload_date": "20240102"}))
        self.assertIsNone(music_archiver._release_date({"date": "20240102"}))
        self.assertEqual(
            music_archiver._recording_date({"date": "2024-01-02"}),
            "2024-01-02",
        )
        self.assertEqual(
            music_archiver._release_date({"release_date": "2024-01-02"}),
            "2024-01-02",
        )

    def test_spotify_metadata_is_used_only_for_an_exact_isrc_match(self):
        spotify_client = types.ModuleType("spotipy")
        spotify_client.SpotifyException = RuntimeError
        spotify_client.Spotify = lambda **kwargs: client
        spotify_auth = types.ModuleType("spotipy.oauth2")
        spotify_auth.SpotifyClientCredentials = lambda **kwargs: "credentials"
        client = Mock()
        client.search.return_value = {
            "tracks": {
                "items": [
                    {
                        "external_ids": {"isrc": "USOTHER00001"},
                        "name": "Wrong recording",
                    },
                    {
                        "external_ids": {"isrc": "USABC2400001"},
                        "name": "Correct recording",
                        "artists": [{"name": "Artist One"}, {"name": "Artist Two"}],
                        "album": {
                            "name": "Correct Album",
                            "artists": [{"name": "Album Artist"}],
                            "release_date": "2024-06-07",
                            "first_release_date": "2020-01-02",
                            "total_tracks": 12,
                        },
                        "track_number": 2,
                        "disc_number": 1,
                    },
                ]
            }
        }

        with patch.dict(
            os.environ,
            {"SPOTIPY_CLIENT_ID": "client-id", "SPOTIPY_CLIENT_SECRET": "client-secret"},
        ), patch.dict(
            sys.modules,
            {"spotipy": spotify_client, "spotipy.oauth2": spotify_auth},
        ):
            result = music_archiver._spotify_metadata_by_isrc({"isrc": "USABC2400001"})

        self.assertEqual(result["track"], "Correct recording")
        self.assertEqual(result["artists"], ["Artist One", "Artist Two"])
        self.assertEqual(result["album_artist"], ["Album Artist"])
        self.assertEqual(result["album"], "Correct Album")
        self.assertEqual(result["track_count"], 12)
        client.search.assert_called_once_with(q="isrc:USABC2400001", type="track", limit=10)

    def test_musicbrainz_isrc_lookup_returns_release_and_structured_artist_metadata(self):
        isrc_response = {
            "isrc": "USABC2100001",
            "recordings": [{
                "id": "recording-id",
                "title": "Song",
                "artist-credit": [
                    {"artist": {"id": "artist-id", "name": "Artist One"}},
                    {"artist": {"id": "guest-id", "name": "Guest Artist"}},
                ],
                "first-release-date": "2021-04-03",
            }],
        }
        recording_response = {
            "id": "recording-id",
            "title": "Song",
            "artist-credit": [
                {"artist": {"id": "artist-id", "name": "Artist One"}},
                {"artist": {"id": "guest-id", "name": "Guest Artist"}},
            ],
            "first-release-date": "2021-04-03",
            "releases": [{
                "id": "release-id",
                "title": "Example Album",
                "status": "Official",
                "date": "2021-04-03",
                "barcode": "1234567890123",
                "artist-credit": [
                    {"artist": {"id": "artist-id", "name": "Artist One"}},
                ],
                "release-group": {
                    "id": "release-group-id",
                    "primary-type": "Album",
                    "secondary-types": ["Compilation"],
                },
                "label-info": [{
                    "catalog-number": "CAT-123",
                    "label": {"name": "Example Label"},
                }],
            }],
        }
        release_detail_response = {
            "id": "release-id",
            "title": "Example Album",
            "status": "Official",
            "date": "2021-04-03",
            "barcode": "1234567890123",
            "artist-credit": [
                {"artist": {"id": "artist-id", "name": "Artist One"}},
            ],
            "release-group": {
                "id": "release-group-id",
                "primary-type": "Album",
                "secondary-types": ["Compilation"],
            },
            "label-info": [{
                "catalog-number": "CAT-123",
                "label": {"name": "Example Label"},
            }],
            "media": [{
                "position": 1,
                "track-count": 8,
                "tracks": [{
                    "id": "release-track-id",
                    "position": 2,
                    "recording": {"id": "recording-id"},
                }],
            }],
        }
        with patch(
            "apps.music_archiver.archiver.urlopen",
            side_effect=[
                BytesIO(json.dumps(isrc_response).encode("utf-8")),
                BytesIO(json.dumps(recording_response).encode("utf-8")),
                BytesIO(json.dumps(release_detail_response).encode("utf-8")),
            ],
        ) as open_url, patch.object(music_archiver, "_MUSICBRAINZ_LAST_REQUEST", 0), patch.object(
            music_archiver,
            "_MUSICBRAINZ_MIN_INTERVAL",
            0,
        ):
            result = music_archiver._musicbrainz_metadata({
                "isrc": "US-ABC-21-00001",
                "track": "Song",
                "album": "Example Album",
            })

        self.assertEqual(result["artists"], ["Artist One", "Guest Artist"])
        self.assertEqual(result["album_artist"], ["Artist One"])
        self.assertEqual(result["album"], "Example Album")
        self.assertEqual(result["track_number"], 2)
        self.assertEqual(result["track_count"], 8)
        self.assertEqual(result["disc_number"], 1)
        self.assertEqual(result["musicbrainz_release_id"], "release-id")
        self.assertEqual(result["musicbrainz_release_track_id"], "release-track-id")
        self.assertEqual(result["musicbrainz_artist_ids"], ["artist-id", "guest-id"])
        self.assertEqual(result["musicbrainz_album_artist_ids"], ["artist-id"])
        self.assertEqual(result["barcode"], "1234567890123")
        self.assertEqual(result["catalog_number"], ["CAT-123"])
        self.assertEqual(result["label"], ["Example Label"])
        self.assertEqual(result["album_type"], "compilation")
        self.assertEqual(result["musicbrainz_album_type"], ["Album", "Compilation"])
        self.assertEqual(result["musicbrainz_album_status"], "Official")
        self.assertEqual(open_url.call_count, 3)
        self.assertIn("USABC2100001", open_url.call_args_list[0].args[0].full_url)
        self.assertIn("recording-id", open_url.call_args_list[1].args[0].full_url)
        self.assertIn("release-id", open_url.call_args_list[2].args[0].full_url)

    def test_musicbrainz_without_isrc_requires_one_exact_title_and_artist_match(self):
        search_response = {
            "recordings": [{
                "id": "recording-id",
                "title": "Exact Song",
                "artist-credit": [
                    {"artist": {"id": "artist-id", "name": "Exact Artist"}},
                ],
            }],
        }
        recording_response = {
            "id": "recording-id",
            "title": "Exact Song",
            "artist-credit": [
                {"artist": {"id": "artist-id", "name": "Exact Artist"}},
            ],
            "releases": [],
        }
        with patch(
            "apps.music_archiver.archiver.urlopen",
            side_effect=[
                BytesIO(json.dumps(search_response).encode("utf-8")),
                BytesIO(json.dumps(recording_response).encode("utf-8")),
            ],
        ) as open_url, patch.object(music_archiver, "_MUSICBRAINZ_LAST_REQUEST", 0), patch.object(
            music_archiver,
            "_MUSICBRAINZ_MIN_INTERVAL",
            0,
        ):
            result = music_archiver._musicbrainz_metadata({
                "track": "Exact Song",
                "artist": "Exact Artist",
                "album": "Exact Album",
            })

        self.assertEqual(result["musicbrainz_recording_id"], "recording-id")
        self.assertEqual(result["artists"], ["Exact Artist"])
        self.assertIn("query=recording%3A", open_url.call_args_list[0].args[0].full_url)
        self.assertIn("release%3A%22Exact+Album%22", open_url.call_args_list[0].args[0].full_url)
        self.assertEqual(open_url.call_count, 2)

    def test_musicbrainz_recovers_real_album_when_source_album_repeats_track_title(self):
        artist_credit = [{"artist": {"id": "staysman-lazz", "name": "Staysman & Lazz"}}]
        search_response = {
            "recordings": [{
                "id": "alle-gutta-recording",
                "title": "Alle Gutta",
                "artist-credit": artist_credit,
                "releases": [{
                    "id": "helt-sykt-release",
                    "title": "Helt Sykt Vol 2",
                    "status": "Official",
                    "date": "2017-02-17",
                }],
            }],
        }
        recording_response = {
            **search_response["recordings"][0],
            "first-release-date": "2017-02-17",
        }
        release_response = {
            "id": "helt-sykt-release",
            "title": "Helt Sykt Vol 2",
            "status": "Official",
            "date": "2017-02-17",
            "artist-credit": artist_credit,
        }
        metadata = {
            "track": "Alle Gutta",
            "title": "Alle Gutta",
            "artist": "Staysman og Staysman & Lazz",
            "album": "Alle Gutta",
        }

        with patch.object(
            music_archiver,
            "_musicbrainz_json",
            side_effect=[
                {"recordings": []},
                search_response,
                recording_response,
                release_response,
            ],
        ) as lookup:
            catalog_metadata = music_archiver._musicbrainz_metadata(metadata)

        self.assertEqual(catalog_metadata["album"], "Helt Sykt Vol 2")
        self.assertEqual(catalog_metadata["album_artist"], ["Staysman & Lazz"])
        self.assertNotIn("release%3A", lookup.call_args_list[0].args[0])

        merged = music_archiver._merge_track_metadata(metadata, catalog_metadata)
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "Alle Gutta.mp3")
            with open(path, "wb") as audio_file:
                audio_file.write(b"audio")
            tag_mp3(
                path,
                merged,
                is_album=True,
                album_name=catalog_metadata["album"],
                master_album_artist=catalog_metadata["album_artist"],
            )
            self.assertEqual(str(ID3(path)["TALB"]), "Helt Sykt Vol 2")

    def test_musicbrainz_does_not_replace_a_different_known_album(self):
        isrc_response = {
            "isrc": "USABC2100002",
            "recordings": [{"id": "recording-id", "title": "Song"}],
        }
        recording_response = {
            "id": "recording-id",
            "title": "Song",
            "artist-credit": [
                {"artist": {"id": "artist-id", "name": "Artist One"}},
            ],
            "releases": [{
                "id": "release-id",
                "title": "Different Album",
                "status": "Official",
                "artist-credit": [
                    {"artist": {"id": "artist-id", "name": "Artist One"}},
                ],
            }],
        }
        with patch(
            "apps.music_archiver.archiver.urlopen",
            side_effect=[
                BytesIO(json.dumps(isrc_response).encode("utf-8")),
                BytesIO(json.dumps(recording_response).encode("utf-8")),
            ],
        ) as open_url, patch.object(music_archiver, "_MUSICBRAINZ_LAST_REQUEST", 0), patch.object(
            music_archiver,
            "_MUSICBRAINZ_MIN_INTERVAL",
            0,
        ):
            result = music_archiver._musicbrainz_metadata({
                "isrc": "USABC2100002",
                "track": "Song",
                "artists": ["Artist One"],
                "album": "Saved Album",
            })

        self.assertEqual(result["track"], "Song")
        self.assertNotIn("album", result)
        self.assertNotIn("musicbrainz_release_id", result)
        self.assertEqual(open_url.call_count, 2)

    def test_tagging_includes_richer_catalog_metadata_and_multi_value_artists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "track.mp3")
            with open(path, "wb") as audio_file:
                audio_file.write(b"audio")

            tag_mp3(
                path,
                {
                    "title": "Song",
                    "artists": ["Artist One", "Artist Two", "Artist Three"],
                    "album_artist": ["Album Artist One", "Album Artist Two"],
                    "track_number": "2/12",
                    "disc_number": "1/2",
                    "release_date": "2024-06-07",
                    "date": "2021-03-04",
                    "first_release_date": "2020-01-02",
                    "genres": ["Pop", "Dance"],
                    "isrc": "USABC2400001",
                    "composers": ["Composer One", "Composer Two"],
                    "label": "Example Records",
                    "musicbrainz_artist_ids": ["artist-id", "guest-id"],
                    "musicbrainz_album_artist_ids": ["album-artist-id"],
                    "musicbrainz_release_id": "release-id",
                    "musicbrainz_release_group_id": "release-group-id",
                    "musicbrainz_recording_id": "recording-id",
                    "musicbrainz_release_track_id": "release-track-id",
                    "catalog_number": "CAT-123",
                    "track": "Song",
                    "album_type": "album",
                    "musicbrainz_album_type": ["Album"],
                    "musicbrainz_album_status": "Official",
                    "musicbrainz_album_release_country": "DK",
                },
                is_album=True,
                album_name="Example Album",
                master_album_artist="Fallback Artist",
            )

            tags = ID3(path)

            self.assertEqual(
                tags["TPE1"].text,
                ["Artist One / Artist Two / Artist Three"],
            )
            self.assertEqual(tags["TPE2"].text, ["Album Artist One / Album Artist Two"])
            self.assertEqual(
                tags["TXXX:ARTISTS"].text,
                ["Artist One", "Artist Two", "Artist Three"],
            )
            self.assertEqual(
                tags["TXXX:ALBUM ARTISTS"].text,
                ["Album Artist One", "Album Artist Two"],
            )
            self.assertEqual(str(tags["TRCK"]), "2/12")
            self.assertEqual(str(tags["TPOS"]), "1/2")
            self.assertEqual(str(tags["TDRC"]), "2021-03-04")
            self.assertEqual(str(tags["TDRL"]), "2024-06-07")
            self.assertEqual(str(tags["TDOR"]), "2020-01-02")
            self.assertEqual(tags.version, (2, 4, 0))
            self.assertEqual(tags["TCON"].text, ["Pop", "Dance"])
            self.assertEqual(str(tags["TSRC"]), "USABC2400001")
            self.assertEqual(tags["TCOM"].text, ["Composer One", "Composer Two"])
            self.assertEqual(str(tags["TPUB"]), "Example Records")
            self.assertEqual(
                tags["TXXX:MusicBrainz Artist Id"].text,
                ["artist-id", "guest-id"],
            )
            self.assertEqual(
                tags["TXXX:MusicBrainz Album Artist Id"].text,
                ["album-artist-id"],
            )
            self.assertEqual(tags["TXXX:MusicBrainz Album Id"].text, ["release-id"])
            self.assertEqual(
                tags["TXXX:MusicBrainz Release Track Id"].text,
                ["release-track-id"],
            )
            self.assertEqual(tags["TXXX:CATALOGNUMBER"].text, ["CAT-123"])
            self.assertNotIn("TCMP", tags)
            self.assertEqual(
                tags["TXXX:MusicBrainz Album Type"].text,
                ["Album"],
            )
            self.assertEqual(
                tags["TXXX:MusicBrainz Album Status"].text,
                ["Official"],
            )
            self.assertEqual(
                tags["TXXX:MusicBrainz Album Release Country"].text,
                ["DK"],
            )

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
                self.assertEqual(tags.getall("TPE1")[0].text, [" / ".join(expected_artists)])
                self.assertEqual(tags["TXXX:ARTISTS"].text, expected_artists)
                self.assertEqual(str(tags["TPE2"]), "Artist One")
                self.assertEqual(str(tags["TALB"]), "Example Album")

    def test_compilation_uses_various_artists_as_album_artist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "compilation.mp3")
            with open(path, "wb") as audio_file:
                audio_file.write(b"audio")

            tag_mp3(
                path,
                {
                    "title": "Song",
                    "artist": "Track Artist",
                    "album": "Various Album",
                    "album_type": "compilation",
                },
                is_album=True,
                album_name="Various Album",
                master_album_artist="Track Artist",
            )

            tags = ID3(path)
            self.assertEqual(str(tags["TPE2"]), "Various Artists")
            self.assertEqual(tags["TXXX:ALBUM ARTISTS"].text, ["Various Artists"])
            self.assertEqual(str(tags["TCMP"]), "1")

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
            self.assertEqual(str(tags["TPE1"]), "Artist One / Artist Two")
            self.assertEqual(tags["TXXX:ARTISTS"].text, ["Artist One", "Artist Two"])
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
