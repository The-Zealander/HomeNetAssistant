import unittest
from unittest.mock import Mock, patch

from ai_core.SARAH import SarahV0_2


class SarahLightIntentTests(unittest.TestCase):
    def setUp(self):
        self.sarah = SarahV0_2.__new__(SarahV0_2)
        self.sarah.samtale = []
        self.sarah._sidste_lys_handling = None
        self.sarah.ha_online = True
        self.sarah.ha = Mock()
        self.sarah.ha.get_entities.return_value = [
            {
                "entity_id": "light.stue",
                "attributes": {"friendly_name": "Stue"},
            },
            {
                "entity_id": "light.kokken",
                "attributes": {"friendly_name": "Køkkenlampe"},
            },
        ]
        self.sarah.ha.call_service.return_value = True

    def test_turn_on_all_lights_in_english(self):
        svar = self.sarah.snak("Could you turn on the lights?")

        self.assertEqual(svar, "Jeg tændte alle lys.")
        self.sarah.ha.call_service.assert_called_once_with(
            "light",
            "turn_on",
            entity_id=["light.stue", "light.kokken"],
        )

    def test_turn_off_all_lights_in_danish(self):
        svar = self.sarah.snak("Sluk lysene")

        self.assertEqual(svar, "Jeg slukkede alle lys.")
        self.sarah.ha.call_service.assert_called_once_with(
            "light",
            "turn_off",
            entity_id=["light.stue", "light.kokken"],
        )

    def test_polite_danish_request_to_turn_on_lights(self):
        svar = self.sarah.snak("Kan du tænde lyset?")

        self.assertEqual(svar, "Jeg tændte alle lys.")
        self.sarah.ha.call_service.assert_called_once_with(
            "light",
            "turn_on",
            entity_id=["light.stue", "light.kokken"],
        )

    def test_english_action_at_end_is_understood(self):
        svar = self.sarah.snak("Turn the lights off")

        self.assertEqual(svar, "Jeg slukkede alle lys.")
        self.sarah.ha.call_service.assert_called_once_with(
            "light",
            "turn_off",
            entity_id=["light.stue", "light.kokken"],
        )

    def test_named_light_matches_friendly_name(self):
        svar = self.sarah.snak("Tænd køkkenlampen")

        self.assertEqual(svar, "Jeg tændte Køkkenlampe.")
        self.sarah.ha.call_service.assert_called_once_with(
            "light",
            "turn_on",
            entity_id=["light.kokken"],
        )

    def test_follow_up_pronoun_targets_the_last_light_sarah_controlled(self):
        first = self.sarah.snak("Tænd stuen")
        second = self.sarah.snak("Kan du slukke det igen?")

        self.assertEqual(first, "Jeg tændte Stue.")
        self.assertEqual(second, "Jeg slukkede Stue.")
        self.assertEqual(self.sarah.ha.call_service.call_count, 2)
        self.assertEqual(
            self.sarah.ha.call_service.call_args_list[1].kwargs["entity_id"],
            ["light.stue"],
        )

    def test_reference_to_previous_light_is_answered_without_model_guessing(self):
        self.sarah.snak("Tænd stuen")

        svar = self.sarah.snak("Nej altså det lys du tændte før")

        self.assertEqual(svar, "Det var Stue. Vil du have mig til at slukke det?")
        self.sarah.ha.call_service.assert_called_once()

    def test_thanks_gets_a_short_natural_reply_without_model(self):
        self.sarah.snak("Tænd stuen")

        with patch("ai_core.SARAH.ollama.chat") as chat:
            svar = self.sarah.snak("Tak for det")

        self.assertEqual(svar, "Det var så lidt.")
        chat.assert_not_called()

    def test_partial_name_does_not_match_an_unrelated_light(self):
        self.sarah.ha.get_entities.return_value = [{
            "entity_id": "light.jorden",
            "attributes": {"friendly_name": "Jorden"},
        }]

        svar = self.sarah.snak("Tænd jord")

        self.assertIn("Jeg har ikke foretaget nogen handling", svar)
        self.sarah.ha.call_service.assert_not_called()

    def test_generated_action_cannot_control_unknown_entity(self):
        svar = self.sarah.parser_handling(
            "Jeg tændte kontakten. [HA_ACTION: turn_on | switch.ukendt]"
        )

        self.assertIn("Jeg kunne ikke finde enheden", svar)
        self.sarah.ha.call_service.assert_not_called()

    def test_generated_action_cannot_target_a_sensor(self):
        svar = self.sarah.parser_handling(
            "Jeg tændte sensoren. [HA_ACTION: turn_on | sensor.jord]"
        )

        self.assertIn("understøttet", svar)
        self.sarah.ha.get_entities.assert_not_called()
        self.sarah.ha.call_service.assert_not_called()

    def test_context_excludes_sensors_and_limits_devices_to_supported_domains(self):
        self.sarah.system_prompt = "SARAHs kerne"
        self.sarah.erfaringer = []
        self.sarah.noter = []
        self.sarah.ha.get_entities.return_value = [
            {"entity_id": "light.stue", "attributes": {"friendly_name": "Stue"}},
            {"entity_id": "sensor.jordens_kerne", "attributes": {"friendly_name": "Jordens kerne"}},
        ]

        context = self.sarah._build_context()

        self.assertIn("light.stue (Stue)", context)
        self.assertNotIn("sensor.jordens_kerne", context)
        self.sarah.ha.get_entities.assert_called_once_with(
            domain_filter=("light", "switch", "fan", "input_boolean")
        )

    def test_validated_model_action_reports_actual_result(self):
        svar = self.sarah.parser_handling(
            "Jeg udfører det nu. [HA_ACTION: turn_on | light.stue]"
        )

        self.assertEqual(svar, "Jeg tændte Stue.")
        self.sarah.ha.call_service.assert_called_once_with(
            "light", "turn_on", entity_id="light.stue"
        )

    def test_unrecognized_question_is_not_executed_as_a_command(self):
        svar = self.sarah._handter_lys_intent("How do I turn on the lights?")

        self.assertIsNone(svar)
        self.sarah.ha.call_service.assert_not_called()

    def test_failed_home_assistant_action_is_reported(self):
        self.sarah.ha.call_service.return_value = False

        svar = self.sarah.snak("Turn on the lights")

        self.assertEqual(
            svar,
            "Home Assistant kunne ikke udføre handlingen på lyset.",
        )


if __name__ == "__main__":
    unittest.main()
