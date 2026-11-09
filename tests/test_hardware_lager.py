"""Test inventory persistence, validation, search, and recommendations."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from apps.hardware_inventory.storage import HardwareLagerDB


class HardwareLagerTests(unittest.TestCase):
    def test_corrupt_json_is_reported_instead_of_returning_empty_inventory(self):
        with tempfile.TemporaryDirectory() as temp:
            data_file = Path(temp) / "hardware.json"
            data_file.write_text("{broken", encoding="utf-8")
            database = HardwareLagerDB(data_file)

            with self.assertRaisesRegex(ValueError, "er ugyldig"):
                database.load_data()

    def test_missing_inventory_is_initialized(self):
        with tempfile.TemporaryDirectory() as temp:
            data_file = Path(temp) / "data" / "hardware.json"
            database = HardwareLagerDB(data_file)

            self.assertEqual(database.load_data(), [])
            self.assertEqual(json.loads(data_file.read_text(encoding="utf-8")), [])

    def test_valid_inventory_still_loads(self):
        with tempfile.TemporaryDirectory() as temp:
            data_file = Path(temp) / "hardware.json"
            data_file.write_text(
                json.dumps([{"id": 1, "brand": "Test", "navn": "Enhed"}]),
                encoding="utf-8",
            )
            database = HardwareLagerDB(data_file)

            items = database.load_data()
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]["brand"], "Test")

    def test_search_functionality(self):
        with tempfile.TemporaryDirectory() as temp:
            data_file = Path(temp) / "hardware.json"
            data_file.write_text(
                json.dumps([
                    {"id": 1, "brand": "Test", "navn": "Enhed 1", "kategori": "CPU"},
                    {"id": 2, "brand": "Test", "navn": "Enhed 2", "kategori": "GPU"},
                    {"id": 3, "brand": "Test", "navn": "Enhed 3", "kategori": "CPU"}
                ]),
                encoding="utf-8",
            )
            database = HardwareLagerDB(data_file)

            items = database.search("CPU", database.load_data())
            self.assertEqual(len(items), 2)
            self.assertIn({"id": 1, "brand": "Test", "navn": "Enhed 1", "kategori": "CPU", "release_year": "", "specs": "", "antal": "1", "status": "Ledig på lager", "purpose": "", "tags": [], "details": {}, "search_text": "cpu test enhed 1 ledig på lager"}, items)
            self.assertIn({"id": 3, "brand": "Test", "navn": "Enhed 3", "kategori": "CPU", "release_year": "", "specs": "", "antal": "1", "status": "Ledig på lager", "purpose": "", "tags": [], "details": {}, "search_text": "cpu test enhed 3 ledig på lager"}, items)

            items = database.search("GPU", database.load_data())
            self.assertEqual(len(items), 1)
            self.assertIn({"id": 2, "brand": "Test", "navn": "Enhed 2", "kategori": "GPU", "release_year": "", "specs": "", "antal": "1", "status": "Ledig på lager", "purpose": "", "tags": [], "details": {}, "search_text": "gpu test enhed 2 ledig på lager"}, items)

    def test_search_reuses_normalized_search_text(self):
        with tempfile.TemporaryDirectory() as temp:
            database = HardwareLagerDB(Path(temp) / "hardware.json")
            items = [
                database.normalize_item({
                    "brand": "Test", "navn": "Enhed", "kategori": "CPU",
                })
            ]

            with patch.object(database, "build_search_text", side_effect=AssertionError):
                self.assertEqual(database.search("CPU", items), items)

    def test_recommendations_rank_matches_and_exclude_defective_items(self):
        with tempfile.TemporaryDirectory() as temp:
            database = HardwareLagerDB(Path(temp) / "hardware.json")
            items = [
                database.normalize_item({
                    "id": "1", "brand": "Acme", "navn": "NAS disk", "kategori": "Disk",
                }),
                database.normalize_item({
                    "id": "2", "brand": "Acme", "navn": "NAS server", "kategori": "CPU",
                }),
                database.normalize_item({
                    "id": "3", "brand": "Acme", "navn": "NAS disk", "kategori": "Disk",
                    "status": "Defekt",
                }),
                database.normalize_item({
                    "id": "4", "brand": "Acme", "navn": "Router", "kategori": "Netværk",
                }),
            ]

            recommendations = database.recommend_for_goal(
                "Jeg skal bygge en NAS disk", items, limit=1
            )

            self.assertEqual([item["id"] for item in recommendations], [1])
