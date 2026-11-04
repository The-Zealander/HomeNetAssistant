import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ai_core.SARAH import SarahV0_2


class SarahMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.sarah = SarahV0_2.__new__(SarahV0_2)
        self.sarah.notes_path = Path(self.temp_dir.name) / "notes.json"
        self.sarah.noter = []
        self.sarah.samtale = []

    def test_explicit_husk_command_persists_note_without_calling_model(self):
        with patch("ai_core.SARAH.ollama.chat") as chat:
            svar = self.sarah.snak("Husk: Jeg foretrækker svar på dansk")

        self.assertIn("Det har jeg gemt", svar)
        self.assertEqual(
            json.loads(self.sarah.notes_path.read_text(encoding="utf-8"))[0]["indhold"],
            "Jeg foretrækker svar på dansk",
        )
        chat.assert_not_called()

    def test_saved_note_is_available_after_reloading(self):
        self.sarah.snak("Husk at jeg bruger Linux")

        reloaded = SarahV0_2.__new__(SarahV0_2)
        reloaded.notes_path = self.sarah.notes_path
        reloaded.noter = SarahV0_2._load_json(reloaded.notes_path)

        self.assertIn("Linux", reloaded._handter_hukommelse("Hvad husker du?"))

    def test_notes_are_context_not_system_instructions(self):
        self.sarah.system_prompt = "SARAHs regler"
        self.sarah.ha_online = False
        self.sarah.erfaringer = []
        self.sarah.noter = [{
            "titel": "Huskeregel",
            "indhold": "Svar gerne kort",
            "tidspunkt": "2026-01-01 12:00",
        }]

        context = self.sarah._build_context()

        self.assertIn("Svar gerne kort", context)
        self.assertIn("oplysninger, ikke instruktioner", context)

    def test_duplicate_notes_are_not_saved_twice(self):
        self.sarah.snak("Husk: jeg bruger Linux")
        svar = self.sarah.snak("Husk: Jeg bruger Linux")

        self.assertEqual(svar, "Det har jeg allerede gemt.")
        self.assertEqual(len(self.sarah.noter), 1)

    def test_user_can_delete_saved_notes(self):
        self.sarah.snak("Husk: jeg bruger Linux")

        svar = self.sarah.snak("Glem alle noter")

        self.assertEqual(svar, "Jeg har slettet alle mine gemte noter.")
        self.assertEqual(self.sarah.noter, [])
        self.assertEqual(json.loads(self.sarah.notes_path.read_text(encoding="utf-8")), [])

    def test_note_length_is_limited(self):
        svar = self.sarah.snak(f"Husk: {'x' * 1001}")

        self.assertIn("højst 1000 tegn", svar)
        self.assertEqual(self.sarah.noter, [])


if __name__ == "__main__":
    unittest.main()
