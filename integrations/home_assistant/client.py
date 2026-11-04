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
    logging.error(f"Kunne ikke indlæse .env-filen: {e}")
    raise

# Konfigurér logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

PRIORITEREDE_DOMAENER = (
    "light",
    "switch",
    "climate",
    "vacuum",
    "cover",
    "fan",
    "media_player",
)


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

    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str | list[str] | None = None,
        extra_data: dict | None = None,
    ):
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

    def get_all_entities(self, domain_filter=None):
        """Henter en liste over tilgængelige enheder i Home Assistant."""
        entities = self.get_entities(domain_filter=domain_filter)
        if entities is None:
            return []
        return [
            f"{item['entity_id']} ({item['attributes'].get('friendly_name', item['entity_id'])}) "
            f"- status: {item.get('state', 'unknown')}"
            for item in entities
        ]

    def get_entities(self, domain_filter=None):
        """Henter strukturerede entiteter fra Home Assistant."""
        try:
            res = requests.get(f"{self.base_url}/api/states", headers=self.headers, timeout=3)
            if res.status_code == 200:
                entities = []
                for item in res.json():
                    entity_id = item.get('entity_id', '')
                    domain = entity_id.split('.')[0]
                    if domain_filter is not None and domain not in domain_filter:
                        continue
                    entities.append(item)
                prioriteter = {
                    domain: index for index, domain in enumerate(PRIORITEREDE_DOMAENER)
                }
                entities.sort(
                    key=lambda entity: (
                        prioriteter.get(entity.get('entity_id', '').split('.')[0], len(prioriteter)),
                        entity.get('entity_id', '').casefold(),
                    )
                )
                logger.info("Hentede %d Home Assistant-entiteter.", len(entities))
                return entities
            else:
                logger.error(f"Kunne ikke hente alle entiteter. Statuskode: {res.status_code}")
        except requests.RequestException as e:
            logger.error(f"Kunne ikke hente alle entiteter: {e}")
        return None
