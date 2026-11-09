"""Legacy server tests kept for reference outside the default test discovery."""

import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from server import app as app_server


class FakeSarah:
    instances = []

    def __init__(self, server_mode=False):
        if not server_mode:
            raise AssertionError("Server sessions must disable process launching.")
        self.notes_path = Path("/tmp/sarah-test-notes.json")
        self.noter = []
        self.messages = []
        self.instances.append(self)

    @staticmethod
    def _load_json(path):
        return []

    def snak(self, besked):
        self.messages.append(besked)
        return f"SARAH: {besked}"


class FakeOllama:
    models = [SimpleNamespace(model="llama3.1:latest")]

    def __init__(self, **kwargs):
        pass

    def list(self):
        return SimpleNamespace(models=self.models)


class ServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.previous_token = os.environ.get("SARAH_API_TOKEN")
        os.environ["SARAH_API_TOKEN"] = "test-secret"
        FakeSarah.instances = []
        self.patches = (
            patch.object(app_server, "SarahV0_1", FakeSarah),
            patch.object(app_server.ollama, "Client", FakeOllama),
        )
        for patcher in self.patches:
            patcher.start()
        app_server.ollama.Client.models = [SimpleNamespace(model="llama3.1:latest")]
        self.lifespan = app_server.app.router.lifespan_context(app_server.app)
        await self.lifespan.__aenter__()
        self.transport = httpx.ASGITransport(app=app_server.app)
        self.client = httpx.AsyncClient(
            transport=self.transport,
            base_url="http://test",
        )

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.lifespan.__aexit__(None, None, None)
        for patcher in reversed(self.patches):
            patcher.stop()
        if self.previous_token is None:
            os.environ.pop("SARAH_API_TOKEN", None)
        else:
            os.environ["SARAH_API_TOKEN"] = self.previous_token

    async def test_health_and_ready(self):
        health = await self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json(), {"status": "ok"})

        ready = await self.client.get("/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual(ready.json()["ollama"], "ready")
        self.assertEqual(ready.json()["home_assistant"], "not_configured")

        app_server.ollama.Client.models = []
        not_ready = await self.client.get("/ready")
        self.assertEqual(not_ready.status_code, 503)
        self.assertEqual(not_ready.json()["detail"]["ollama"], "model_missing")

    async def test_chat_requires_token_and_keeps_sessions_separate(self):
        unauthorized = await self.client.post("/api/chat", json={"besked": "hej"})
        self.assertEqual(unauthorized.status_code, 401)

        headers = {"Authorization": "Bearer test-secret"}
        first = await self.client.post("/api/chat", headers=headers, json={"besked": "hej"})
        self.assertEqual(first.status_code, 200)
        first_payload = first.json()
        self.assertEqual(first_payload["svar"], "SARAH: hej")

        followup = await self.client.post(
            "/api/chat",
            headers=headers,
            json={"besked": "igen", "session_id": first_payload["session_id"]},
        )
        self.assertEqual(followup.status_code, 200)
        self.assertEqual(len(FakeSarah.instances), 2)
        self.assertEqual(FakeSarah.instances[-1].messages, ["hej", "igen"])

        other = await self.client.post(
            "/api/chat",
            headers=headers,
            json={"besked": "ny samtale"},
        )
        self.assertNotEqual(other.json()["session_id"], first_payload["session_id"])
        self.assertEqual(len(FakeSarah.instances), 3)

    async def test_chat_rejects_oversized_messages(self):
        response = await self.client.post(
            "/api/chat",
            headers={"Authorization": "Bearer test-secret"},
            json={"besked": "x" * 2001},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(FakeSarah.instances), 1)

    async def test_ollama_connection_failure_is_a_service_error(self):
        headers = {"Authorization": "Bearer test-secret"}
        with patch.object(FakeSarah, "snak", side_effect=ConnectionError("unavailable")):
            response = await self.client.post(
                "/api/chat",
                headers=headers,
                json={"besked": "hej"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertIn("Ollama", response.json()["detail"])
