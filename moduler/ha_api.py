import os
import requests
from dotenv import load_dotenv
from pathlib import Path
import logging

# Tving indlæsning af .env fra projektroden
BASE_DIR = Path(__file__).resolve().parents[1]
try:
    load_dotenv(BASE_DIR / ".env")
except Exception as e:
    logger.error(f"Kunne ikke indlæse .env-filen: {e}")
    raise

# Konfigurér logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class HomeAssistantAPI:
    def __init__(self):
        self.base_url = os.getenv("HA_URL", "http://homeassistant.local:8123").rstrip('/')
        self.token = os.getenv("HA_TOKEN")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

        if not self.base_url or not self.token:
            logger.error("HA_URL eller HA_TOKEN er ikke defineret i .env-filen.")
            raise ValueError("HA_URL eller HA_TOKEN er ikke defineret i .env-filen.")

    def is_connected(self) -> bool:
        """Tjekker om HA svarer og tokenet er gyldigt."""
        try:
            res = requests.get(f"{self.base_url}/api/", headers=self.headers, timeout=3)
            if res.status_code == 200:
                logger.info("Forbindelse til Home Assistant givet.")
                return True
            else:
                logger.error(f"Ugyldig forbindelse til Home Assistant. Statuskode: {res.status_code}")
                return False
        except requests.RequestException as e:
            logger.error(f"Kunne ikke forbinde til Home Assistant: {e}")
            return False

    def get_state(self, entity_id: str):
        """Henter tilstanden for en specifik enhed (f.eks. 'light.stue')."""
        try:
            res = requests.get(f"{self.base_url}/api/states/{entity_id}", headers=self.headers, timeout=3)
            if res.status_code == 200:
                logger.info(f"Hentet tilstand for {entity_id}: {res.json()}")
                return res.json()
            else:
                logger.error(f"Kunne ikke hente tilstand for {entity_id}. Statuskode: {res.status_code}")
        except requests.RequestException as e:
            logger.error(f"Kunne ikke hente tilstand for {entity_id}: {e}")
        return None

    def call_service(self, domain: str, service: str, entity_id: str = None, extra_data: dict = None):
        """Udfører en handling i HA (f.eks. domain='light', service='turn_on')."""
        url = f"{self.base_url}/api/services/{domain}/{service}"
        payload = extra_data or {}
        if entity_id:
            payload["entity_id"] = entity_id

        try:
            res = requests.post(url, headers=self.headers, json=payload, timeout=3)
            if res.status_code == 200:
                logger.info(f"Udført {domain}.{service} på {entity_id}")
                return True
            else:
                logger.error(f"Kunne ikke udføre {domain}.{service} på {entity_id}. Statuskode: {res.status_code}")
        except requests.RequestException as e:
            logger.error(f"Kunne ikke udføre {domain}.{service} på {entity_id}: {e}")
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
                logger.info(f"Hentet alle entiteter: {entities}")
                return entities
            else:
                logger.error(f"Kunne ikke hente alle entiteter. Statuskode: {res.status_code}")
        except requests.RequestException as e:
            logger.error(f"Kunne ikke hente alle entiteter: {e}")
        return []
