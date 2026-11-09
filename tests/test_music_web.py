"""Test the music web API's queue, authentication, and download lifecycle."""

import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from threading import RLock
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from apps.music_archiver.queue import MusicQueue
from server import app as app_server


class MusicWebTests(unittest.TestCase):
    def setUp(self):
        self.runner = Mock(return_value=True)
        runtime = SimpleNamespace(
            music_queue=MusicQueue(runner=self.runner),
            music_assistant=Mock(),
            music_discovery=Mock(),
            home_assistant=Mock(),
            sessioner={},
            lock=RLock(),
        )
        self.sarah = Mock()
        self.sarah.snak.return_value = "Hej fra SARAH"
        runtime.get_sarah = Mock(return_value=("session-1", self.sarah))
        self.runtime = runtime
        self.token_patch = patch.dict("os.environ", {"SARAH_API_TOKEN": "test-token"})
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)
        self.previous_runtime = getattr(app_server.app.state, "runtime", None)
        app_server.app.state.runtime = runtime
        self.addCleanup(self._restore_runtime)

    def _restore_runtime(self):
        if self.previous_runtime is None:
            del app_server.app.state.runtime
        else:
            app_server.app.state.runtime = self.previous_runtime

    def request(self, method, path, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=app_server.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def test_music_dashboard_is_served(self):
        response = self.request("GET", "/music")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Musikarkivar", response.text)
        self.assertIn("Spotify- og Tidal-links genkendes", response.text)
        self.assertIn("Genkendte kilder", response.text)
        self.assertIn("Gem og test nøgle", response.text)
        self.assertIn("Kontrollér og reparér hele arkivet", response.text)
        self.assertIn("gennemgår hvert spor i _samlet_arkiv.txt", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_root_dashboard_is_served(self):
        response = self.request("GET", "/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Lokalsystemet", response.text)
        self.assertIn("SARAH Chat", response.text)
        self.assertIn("sarah-avatar", response.text)
        self.assertIn("Start talelytning", response.text)
        self.assertIn("Afspil SARAHs svar", response.text)
        self.assertIn('option value="local">SARAH (lokal dansk stemme)', response.text)
        self.assertIn("/api/voice/speak", response.text)
        self.assertIn('id="new-chat"', response.text)
        self.assertIn('role="log"', response.text)
        self.assertIn("restoreChatHistory", response.text)
        self.assertIn("Home Assistant", response.text)
        self.assertIn("Gem og test nøgle", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_system_status_requires_authentication_and_returns_service_status(self):
        denied = self.request("GET", "/api/system/status")
        self.assertEqual(denied.status_code, 401)

        self.runtime.home_assistant.is_connected.return_value = True
        with patch("server.app.ollama.Client") as client:
            client.return_value.list.return_value.models = [SimpleNamespace(model="llama3.1")]
            response = self.request(
                "GET",
                "/api/system/status",
                headers={"Authorization": "Bearer test-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ollama"], "ready")
        self.assertEqual(response.json()["home_assistant"], "online")

    def test_browser_chat_uses_existing_sarah_chat_api(self):
        response = self.request(
            "POST",
            "/api/chat",
            headers={"Authorization": "Bearer test-token"},
            json={"besked": "Hej", "session_id": None},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"session_id": "session-1", "svar": "Hej fra SARAH"})
        self.sarah.snak.assert_called_once_with("Hej")

    def test_local_voice_synthesis_requires_auth_and_returns_wav_audio(self):
        denied = self.request("POST", "/api/voice/speak", json={"message": "Hej"})
        self.assertEqual(denied.status_code, 401)

        with patch("server.app.LokalStemme.generer_lyd", return_value=b"RIFF-test-audio") as synthesize:
            response = self.request(
                "POST",
                "/api/voice/speak",
                headers={"Authorization": "Bearer test-token"},
                json={"message": "Hej SARAH"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "audio/wav")
        self.assertEqual(response.content, b"RIFF-test-audio")
        synthesize.assert_called_once_with("Hej SARAH")

    def test_local_voice_synthesis_rejects_empty_text(self):
        response = self.request(
            "POST",
            "/api/voice/speak",
            headers={"Authorization": "Bearer test-token"},
            json={"message": "   "},
        )

        self.assertEqual(response.status_code, 422)

    def test_home_assistant_entity_api_returns_safe_entity_fields(self):
        self.runtime.home_assistant.get_entities.return_value = [{
            "entity_id": "light.stue",
            "attributes": {"friendly_name": "Stuelampe", "secret": "not returned"},
            "state": "off",
        }]

        response = self.request(
            "GET",
            "/api/home-assistant/entities",
            headers={"Authorization": "Bearer test-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{
            "entity_id": "light.stue",
            "name": "Stuelampe",
            "state": "off",
            "domain": "light",
        }])

    def test_home_assistant_action_is_restricted_and_checks_known_entity(self):
        headers = {"Authorization": "Bearer test-token"}
        self.runtime.home_assistant.get_entities.return_value = [
            {"entity_id": "light.stue", "attributes": {}, "state": "off"},
        ]
        self.runtime.home_assistant.call_service.return_value = True

        response = self.request(
            "POST",
            "/api/home-assistant/action",
            headers=headers,
            json={"entity_id": "light.stue", "action": "turn_on"},
        )
        self.assertEqual(response.status_code, 200)
        self.runtime.home_assistant.call_service.assert_called_once_with(
            "light", "turn_on", entity_id="light.stue"
        )

        forbidden = self.request(
            "POST",
            "/api/home-assistant/action",
            headers=headers,
            json={"entity_id": "lock.front_door", "action": "unlock"},
        )
        self.assertEqual(forbidden.status_code, 422)

        unknown = self.request(
            "POST",
            "/api/home-assistant/action",
            headers=headers,
            json={"entity_id": "light.other", "action": "turn_on"},
        )
        self.assertEqual(unknown.status_code, 404)

    def test_home_assistant_media_player_supports_safe_transport_and_volume_controls(self):
        headers = {"Authorization": "Bearer test-token"}
        self.runtime.home_assistant.get_entities.return_value = [
            {"entity_id": "media_player.stue", "attributes": {}, "state": "playing"},
        ]
        self.runtime.home_assistant.call_service.return_value = True

        response = self.request(
            "POST",
            "/api/home-assistant/action",
            headers=headers,
            json={"entity_id": "media_player.stue", "action": "volume_set", "volume_level": 0.35},
        )

        self.assertEqual(response.status_code, 200)
        self.runtime.home_assistant.call_service.assert_called_once_with(
            "media_player",
            "volume_set",
            entity_id="media_player.stue",
            extra_data={"volume_level": 0.35},
        )

    def test_home_assistant_speak_uses_only_configured_speaker_and_tts_entities(self):
        headers = {"Authorization": "Bearer test-token"}
        self.runtime.home_assistant.get_entities.return_value = [
            {"entity_id": "media_player.stue", "attributes": {}, "state": "idle"},
            {"entity_id": "tts.piper", "attributes": {}, "state": "unknown"},
        ]
        self.runtime.home_assistant.call_service.return_value = True

        response = self.request(
            "POST",
            "/api/home-assistant/speak",
            headers=headers,
            json={
                "media_player_entity_id": "media_player.stue",
                "tts_entity_id": "tts.piper",
                "message": "Hej fra SARAH",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.runtime.home_assistant.call_service.assert_called_once_with(
            "tts",
            "speak",
            entity_id="tts.piper",
            extra_data={
                "media_player_entity_id": "media_player.stue",
                "message": "Hej fra SARAH",
            },
        )

    def test_home_assistant_speak_rejects_unknown_speaker_without_calling_service(self):
        self.runtime.home_assistant.get_entities.return_value = [
            {"entity_id": "tts.piper", "attributes": {}, "state": "unknown"},
        ]

        response = self.request(
            "POST",
            "/api/home-assistant/speak",
            headers={"Authorization": "Bearer test-token"},
            json={
                "media_player_entity_id": "media_player.unknown",
                "tts_entity_id": "tts.piper",
                "message": "Hej",
            },
        )

        self.assertEqual(response.status_code, 404)
        self.runtime.home_assistant.call_service.assert_not_called()

    def test_hardware_api_searches_inventory(self):
        with patch("server.app.HardwareLagerDB") as database_class:
            database = database_class.return_value
            database.load_data.return_value = [
                {"id": 1, "brand": "Example", "navn": "CPU", "search_text": "example cpu"}
            ]
            database.search.return_value = database.load_data.return_value

            response = self.request(
                "GET",
                "/api/hardware?q=CPU",
                headers={"Authorization": "Bearer test-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [{"id": 1, "brand": "Example", "navn": "CPU"}])
        database.search.assert_called_once_with("CPU", database.load_data.return_value)

    def test_music_library_health_counts_corrupt_files_without_exposing_paths(self):
        with tempfile.TemporaryDirectory() as library, patch.dict(
            os.environ, {"MUSIC_DOWNLOAD_PATH": library}
        ):
            with open(os.path.join(library, "broken.mp3"), "wb") as track:
                track.write(b"not an mp3")
            response = self.request(
                "GET",
                "/api/music/library",
                headers={"Authorization": "Bearer test-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_files"], 1)
        self.assertEqual(response.json()["corrupt_files"], 1)
        self.assertEqual(response.json()["duplicate_groups"], 0)
        self.assertFalse(response.json()["recovery_document_found"])
        self.assertEqual(response.json()["saved_track_links"], 0)

    def test_restore_library_requires_auth_and_uses_saved_archive_restore(self):
        denied = self.request("POST", "/api/music/library/restore")
        self.assertEqual(denied.status_code, 401)

        report = {"saved_tracks": 2, "queued_tracks": 1}
        with patch.dict(os.environ, {"MUSIC_DOWNLOAD_PATH": "/tmp/music-library"}), patch(
            "server.app.restore_library",
            return_value=report,
        ) as restore:
            response = self.request(
                "POST",
                "/api/music/library/restore",
                headers={"Authorization": "Bearer test-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), report)
        restore.assert_called_once_with("/tmp/music-library", self.runtime.music_queue)

    def test_restore_library_waits_for_an_idle_download_queue(self):
        self.runtime.music_queue.pause()
        self.runtime.music_queue.add(["https://youtu.be/pending"])

        response = self.request(
            "POST",
            "/api/music/library/restore",
            headers={"Authorization": "Bearer test-token"},
        )

        self.assertEqual(response.status_code, 409)

    def test_api_requires_token_and_accepts_valid_queue_items(self):
        denied = self.request("GET", "/api/music/queue")
        self.assertEqual(denied.status_code, 401)

        headers = {"Authorization": "Bearer test-token"}
        response = self.request(
            "POST",
            "/api/music/queue",
            headers=headers,
            json={"urls": ["https://youtube.com/playlist?list=one"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["added"]), 1)
        self.assertTrue(response.json()["added"][0]["id"])

    def test_queue_api_preserves_optional_video_download_setting(self):
        self.runtime.music_queue.pause()
        with patch.dict("os.environ", {"SARAH_API_TOKEN": ""}):
            response = self.request(
                "POST",
                "/api/music/queue",
                json={
                    "urls": ["https://youtube.com/watch?v=video"],
                    "download_video": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["added"][0]["download_video"])

    def test_empty_token_configuration_allows_local_development(self):
        with patch.dict("os.environ", {"SARAH_API_TOKEN": ""}):
            response = self.request("GET", "/api/music/queue")
        self.assertEqual(response.status_code, 200)

    def test_api_rejects_non_youtube_urls(self):
        response = self.request(
            "POST",
            "/api/music/queue",
            headers={"Authorization": "Bearer test-token"},
            json={"urls": ["https://example.com/music"]},
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("YouTube", response.json()["detail"])

    def test_api_explains_that_spotify_links_are_not_audio_sources(self):
        with patch.dict("os.environ", {"SARAH_API_TOKEN": ""}):
            response = self.request(
                "POST",
                "/api/music/queue",
                json={"urls": ["https://open.spotify.com/track/track-id"]},
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Spotify-link genkendt", response.json()["detail"])
        self.assertEqual(self.runtime.music_queue.snapshot()["jobs"], [])

    def test_music_assistant_api_forwards_local_search_and_refresh(self):
        report = {"mode": "local_metadata_ranker", "recommendations": []}
        self.runtime.music_assistant.analyze.return_value = report
        with tempfile.TemporaryDirectory() as music_directory, patch.dict(
            os.environ,
            {
                "MUSIC_DOWNLOAD_PATH": music_directory,
                "SARAH_API_TOKEN": "",
            },
        ):
            response = self.request(
                "GET",
                "/api/music/assistant?q=jazz&limit=5&refresh=true",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), report)
        self.runtime.music_assistant.analyze.assert_called_once_with(
            Path(music_directory),
            query="jazz",
            limit=5,
            refresh=True,
        )

    def test_source_discovery_status_and_start_use_authenticated_api(self):
        status = {"status": "paused", "processed": 2, "total": 5}
        self.runtime.music_discovery.snapshot.return_value = status
        response = self.request(
            "GET",
            "/api/music/assistant/discovery",
            headers={"Authorization": f"Bearer {os.environ['SARAH_API_TOKEN']}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), status)

        self.runtime.music_discovery.start.return_value = {
            "status": "running",
            "total": 5,
        }
        with tempfile.TemporaryDirectory() as music_directory, patch.dict(
            os.environ,
            {
                "MUSIC_DOWNLOAD_PATH": music_directory,
                "SARAH_API_TOKEN": "",
            },
        ):
            started = self.request(
                "POST",
                "/api/music/assistant/discovery/start",
            )

        self.assertEqual(started.status_code, 200)
        self.runtime.music_discovery.start.assert_called_once_with(
            Path(music_directory),
            refresh=True,
        )

    def test_queue_status_updates_after_worker_finishes(self):
        finished = threading.Event()

        def runner(url, progress_callback):
            finished.set()
            return True

        self.runtime.music_queue = MusicQueue(runner=runner)
        headers = {"Authorization": "Bearer test-token"}
        self.request(
            "POST",
            "/api/music/queue",
            headers=headers,
            json={"urls": ["https://youtu.be/video"]},
        )
        self.assertTrue(finished.wait(timeout=3))
        response = self.request("GET", "/api/music/queue", headers=headers)
        self.assertEqual(response.json()["jobs"][0]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
