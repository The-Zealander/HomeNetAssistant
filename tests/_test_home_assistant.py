"""Legacy Home Assistant tests kept for reference outside default discovery."""

import unittest
from unittest.mock import Mock, patch

import httpx

from integrations.home_assistant.client import HomeAssistantClient


class HomeAssistantClientTests(unittest.TestCase):
    def test_unconfigured_client_reports_status(self):
        client = HomeAssistantClient(base_url="", token="")
        self.assertEqual(client.check_connection(), "not_configured")
        client.close()

    def test_requires_url_and_token_together(self):
        with self.assertRaises(ValueError):
            HomeAssistantClient(base_url="http://homeassistant.local:8123", token="")

    def test_checks_api_with_bearer_token(self):
        response = Mock()
        session = Mock()
        session.get.return_value = response
        with patch(
            "integrations.home_assistant.client.httpx.Client",
            return_value=session,
        ) as client_factory:
            client = HomeAssistantClient("http://homeassistant.local:8123", "secret")
            self.assertEqual(client.check_connection(), "connected")
            self.assertEqual(
                client_factory.call_args.kwargs["headers"],
                {"Authorization": "Bearer secret"},
            )
            session.get.assert_called_once_with("/api/")
            client.close()

    def test_unavailable_home_assistant_is_reported(self):
        session = Mock()
        session.get.side_effect = httpx.ConnectError("unavailable")
        with patch(
            "integrations.home_assistant.client.httpx.Client",
            return_value=session,
        ):
            client = HomeAssistantClient("http://homeassistant.local:8123", "secret")
            self.assertEqual(client.check_connection(), "unavailable")
            self.assertEqual(client.error, "Home Assistant kunne ikke kontaktes.")
            client.close()
