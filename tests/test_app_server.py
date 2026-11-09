"""Verify server runtime initialization and status when Home Assistant is absent."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from server import app as app_server


class AppServerTests(unittest.TestCase):
    @patch("server.app.SarahV0_1")
    @patch("server.app.HomeAssistantAPI", side_effect=ValueError("HA credentials missing"))
    def test_server_starts_without_home_assistant_configuration(self, home_assistant, sarah):
        runtime = app_server.SarahServer()

        self.assertIsNone(runtime.home_assistant)
        self.assertIsNotNone(runtime.music_queue)
        sarah.assert_called_once_with(server_mode=True)
        home_assistant.assert_called_once_with()

    @patch("server.app.ollama.Client")
    def test_status_reports_home_assistant_as_unconfigured(self, ollama_client):
        ollama_client.return_value.list.return_value.models = [
            SimpleNamespace(model="llama3.1")
        ]
        runtime = SimpleNamespace(
            home_assistant=None,
            sessioner={},
        )

        status = app_server._status(runtime)

        self.assertEqual(status["home_assistant"], "not_configured")
        self.assertIn("HA_URL", status["home_assistant_error"])


if __name__ == "__main__":
    unittest.main()
