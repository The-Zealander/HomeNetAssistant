import os
import time
from urllib.parse import urlparse

import httpx


class HomeAssistantClient:
    """Læser forbindelsesstatus fra en eksisterende Home Assistant-instans."""

    def __init__(self, base_url=None, token=None):
        self.base_url = (base_url or os.environ.get("HA_URL", "")).rstrip("/")
        self.token = token or os.environ.get("HA_TOKEN", "")
        self.status = "not_configured"
        self.error = None
        self._client = None
        self._last_checked = 0.0

        if not self.base_url and not self.token:
            return
        if not self.base_url or not self.token:
            raise ValueError("Home Assistant kræver både HA_URL og HA_TOKEN.")

        parsed = urlparse(self.base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("HA_URL skal være en gyldig http://- eller https://-adresse.")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.token}"},
            timeout=3.0,
        )

    def check_connection(self):
        if self._client is None:
            self.status = "not_configured"
            return self.status
        if time.monotonic() - self._last_checked < 30:
            return self.status

        self._last_checked = time.monotonic()
        try:
            response = self._client.get("/api/")
            response.raise_for_status()
        except httpx.HTTPStatusError as fejl:
            self.status = "error"
            self.error = f"Home Assistant svarede med HTTP {fejl.response.status_code}."
        except httpx.HTTPError:
            self.status = "unavailable"
            self.error = "Home Assistant kunne ikke kontaktes."
        else:
            self.status = "connected"
            self.error = None
        return self.status

    def close(self):
        if self._client is not None:
            self._client.close()
