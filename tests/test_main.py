import os
import unittest
from unittest.mock import patch

from main import start_server


class ServerStartupTests(unittest.TestCase):
    def test_network_bind_requires_long_api_token(self):
        for token in ("", "short"):
            with self.subTest(token_length=len(token)), patch.dict(
                os.environ,
                {"SARAH_API_TOKEN": token},
            ):
                with self.assertRaises(SystemExit):
                    start_server("0.0.0.0", 8000)

    def test_loopback_bind_does_not_require_api_token(self):
        with patch.dict(os.environ, {"SARAH_API_TOKEN": ""}), patch(
            "uvicorn.run"
        ) as run:
            start_server("127.0.0.1", 8000)
        run.assert_called_once()

    def test_invalid_port_is_rejected(self):
        with self.assertRaises(SystemExit):
            start_server("127.0.0.1", 0)
