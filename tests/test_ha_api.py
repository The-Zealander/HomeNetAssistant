import os
import unittest
from unittest.mock import Mock, patch

from integrations.home_assistant.client import HomeAssistantAPI


class HomeAssistantAPITests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "HA_URL": "http://homeassistant.test:8123",
                "HA_TOKEN": "test-token",
            },
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.client = HomeAssistantAPI()

    @patch("integrations.home_assistant.client.requests.get")
    def test_lists_entities_from_all_domains_by_default(self, get):
        get.return_value = Mock(
            status_code=200,
            json=lambda: [
                {
                    "entity_id": "vacuum.robot",
                    "attributes": {"friendly_name": "Robotstøvsuger"},
                    "state": "docked",
                },
                {
                    "entity_id": "sensor.temperatur",
                    "attributes": {"friendly_name": "Temperatur"},
                    "state": "20",
                },
                {
                    "entity_id": "lock.hoveddor",
                    "attributes": {"friendly_name": "Hoveddør"},
                    "state": "locked",
                },
                {
                    "entity_id": "light.stue",
                    "attributes": {"friendly_name": "Stuelampe"},
                    "state": "off",
                },
            ],
        )

        entities = self.client.get_all_entities()

        self.assertEqual(len(entities), 4)
        self.assertTrue(entities[0].startswith("light.stue "))
        self.assertTrue(any(entity.startswith("light.stue ") for entity in entities))
        self.assertTrue(any(entity.startswith("vacuum.robot ") for entity in entities))
        self.assertTrue(any(entity.startswith("sensor.temperatur ") for entity in entities))
        self.assertTrue(any(entity.startswith("lock.hoveddor ") for entity in entities))

    @patch("integrations.home_assistant.client.requests.get")
    def test_optional_domain_filter_is_still_supported(self, get):
        get.return_value = Mock(
            status_code=200,
            json=lambda: [
                {"entity_id": "light.stue", "attributes": {}, "state": "off"},
                {"entity_id": "vacuum.robot", "attributes": {}, "state": "docked"},
            ],
        )

        entities = self.client.get_all_entities(domain_filter=("light",))

        self.assertEqual(len(entities), 1)
        self.assertTrue(entities[0].startswith("light.stue "))

    @patch("integrations.home_assistant.client.requests.post")
    def test_service_accepts_multiple_entity_ids(self, post):
        post.return_value = Mock(status_code=200)

        result = self.client.call_service(
            "light",
            "turn_on",
            entity_id=["light.stue", "light.kokken"],
        )

        self.assertTrue(result)
        self.assertEqual(
            post.call_args.kwargs["json"],
            {"entity_id": ["light.stue", "light.kokken"]},
        )


if __name__ == "__main__":
    unittest.main()
