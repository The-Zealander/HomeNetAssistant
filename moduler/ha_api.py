import os
import requests
from dotenv import load_dotenv
from pathlib import Path

# Tving indlæsning af .env fra projektroden
BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

class HomeAssistantAPI:
    def __init__(self):
        self.base_url = os.getenv("HA_URL", "http://homeassistant.local:8123").rstrip('/')
        self.token = os.getenv("HA_TOKEN")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def is_connected(self) -> bool:
        """Tjekker om HA svarer og tokenet er gyldigt."""
        try:
            res = requests.get(f"{self.base_url}/api/", headers=self.headers, timeout=3)
            return res.status_code == 200
        except Exception:
            return False

    def get_state(self, entity_id: str):
        """Henter tilstanden for en specifik enhed (f.eks. 'light.stue')."""
        try:
            res = requests.get(f"{self.base_url}/api/states/{entity_id}", headers=self.headers, timeout=3)
            if res.status_code == 200:
                return res.json()
        except Exception as e:
            print(f"[HA API Fejl] Kunne ikke hente {entity_id}: {e}")
        return None

    def call_service(self, domain: str, service: str, entity_id: str = None, extra_data: dict = None):
        """Udfører en handling i HA (f.eks. domain='light', service='turn_on')."""
        url = f"{self.base_url}/api/services/{domain}/{service}"
        payload = extra_data or {}
        if entity_id:
            payload["entity_id"] = entity_id

        try:
            res = requests.post(url, headers=self.headers, json=payload, timeout=3)
            return res.status_code == 200
        except Exception as e:
            print(f"[HA API Fejl] Kunne ikke kalde {domain}.{service}: {e}")
            return False

    def turn_on(self, entity_id: str):
        domain = entity_id.split('.')[0]
        return self.call_service(domain, "turn_on", entity_id=entity_id)

    def turn_off(self, entity_id: str):
        domain = entity_id.split('.')[0]
        return self.call_service(domain, "turn_off", entity_id=entity_id)

    def get_all_entities(self, domain_filter=("light", "switch", "climate", "sensor")):
        """Henter en liste over tilgængelige enheder i Home Assistant."""
        try:
            res = requests.get(f"{self.base_url}/api/states", headers=self.headers, timeout=3)
            if res.status_code == 200:
                states = res.json()
                entities = []
                for item in states:
                    entity_id = item.get('entity_id', '')
                    domain = entity_id.split('.')[0]
                    if domain in domain_filter:
                        friendly_name = item.get('attributes', {}).get('friendly_name', entity_id)
                        state = item.get('state', 'unknown')
                        entities.append(f"{entity_id} ({friendly_name}) - status: {state}")
                return entities
        except Exception as e:
            print(f"[HA API Fejl] Kunne ikke hente entiteter: {e}")
        return []