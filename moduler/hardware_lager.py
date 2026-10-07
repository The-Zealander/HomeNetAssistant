import json
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_FIL = BASE_DIR / "data" / "hardware_lager.json"


class HardwareLagerDB:
    """Datamodel til Lager og AI-venlig søgning."""

    KATEGORIER = [
        "CPU",
        "GPU",
        "Disk",
        "Bundkort",
        "RAM",
        "Netværk",
        "Zigbee/Matter",
        "Andet",
    ]
    STATUSER = [
        "Ledig på lager",
        "I brug (Server)",
        "I brug (Router NAS)",
        "I brug (Stue)",
        "Defekt",
    ]

    KATEGORI_SPECIFIKKE_FELTER = {
        "CPU": {
            "socket": "Socket",
            "igpu": "Integreret grafik (iGPU/APU)",
            "tdp": "TDP",
            "cores": "Antal kerner",
            "threads": "Antal tråde",
        },
        "GPU": {
            "vram": "VRAM",
            "low_profile": "Low profile",
            "slot_count": "Antal slots",
            "external_power": "Kræver ekstern strøm",
            "power_connectors": "Strømstik",
        },
        "Disk": {
            "disk_type": "Disk type",
            "capacity": "Kapacitet",
            "interface": "Interface",
            "form_factor": "Formfaktor",
            "cache": "Cache",
        },
        "RAM": {
            "memory_type": "RAM type",
            "capacity": "Kapacitet",
            "speed": "Hastighed",
            "modules": "Moduler",
        },
        "Bundkort": {
            "chipset": "Chipset",
            "socket": "Socket",
            "form_factor": "Formfaktor",
            "ram_slots": "RAM slots",
            "m2_slots": "M.2 slots",
        },
        "Netværk": {
            "type": "Type",
            "speed": "Hastighed",
            "ports": "Antal porte",
            "wifi_standard": "WiFi-standard",
        },
        "Zigbee/Matter": {
            "protocol": "Protokol",
            "radio_type": "Radiotype",
            "channels": "Kanaler",
            "power": "Strøm",
        },
        "Andet": {},
    }

    @staticmethod
    def _as_text(value, fallback=""):
        if value is None:
            return fallback
        return str(value).strip()

    @staticmethod
    def _as_int(value, fallback=1):
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return int(float(str(value).replace(",", ".")))
            except (TypeError, ValueError):
                return fallback

    def __init__(self, data_file=DATA_FIL):
        self.data_file = Path(data_file)
        self.ensure_storage()

    def ensure_storage(self):
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.data_file.exists():
            self.data_file.write_text("[]", encoding="utf-8")

    def load_data(self):
        try:
            with self.data_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            self.ensure_storage()
            return []
        except json.JSONDecodeError as fejl:
            raise ValueError(
                f"Hardwarelagerets JSON-fil er ugyldig og blev ikke ændret: {self.data_file}"
            ) from fejl

        if not isinstance(data, list):
            raise ValueError(
                f"Hardwarelagerets JSON-fil skal indeholde en liste: {self.data_file}"
            )

        return [self.normalize_item(item) for item in data]

    def save_data(self, data):
        tmp_path = self.data_file.with_suffix(".tmp")
        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, self.data_file)

    def normalize_item(self, item):
        if not isinstance(item, dict):
            return {}

        normalized = {
            "id": self._as_int(item.get("id"), 1),
            "kategori": self._as_text(item.get("kategori"), "Andet"),
            "brand": self._as_text(item.get("brand"), ""),
            "navn": self._as_text(item.get("navn"), ""),
            "release_year": self._as_text(item.get("release_year"), ""),
            "specs": self._as_text(item.get("specs"), ""),
            "antal": str(self._as_int(item.get("antal"), 1)),
            "status": self._as_text(item.get("status"), "Ledig på lager"),
            "purpose": self._as_text(item.get("purpose"), ""),
            "tags": self._parse_tags(item.get("tags")),
            "details": self._normalize_details(item.get("details"), self._as_text(item.get("kategori"), "Andet")),
        }

        if normalized["kategori"] not in self.KATEGORIER:
            normalized["kategori"] = "Andet"

        if normalized["status"] not in self.STATUSER:
            normalized["status"] = "Ledig på lager"

        normalized["search_text"] = self.build_search_text(normalized)
        return normalized

    def _normalize_details(self, details, kategori):
        if not isinstance(details, dict):
            details = {}

        valid_fields = self.KATEGORI_SPECIFIKKE_FELTER.get(kategori, {})
        normalized = {}
        for key, label in valid_fields.items():
            value = details.get(key)
            if value is None:
                continue
            normalized[key] = str(value).strip()
        return normalized

    def _parse_tags(self, raw_tags):
        if isinstance(raw_tags, str):
            tags = [part.strip() for part in raw_tags.split(",") if part.strip()]
        elif isinstance(raw_tags, list):
            tags = [str(tag).strip() for tag in raw_tags if str(tag).strip()]
        else:
            tags = []
        return sorted(set(tags))

    def build_search_text(self, item):
        details_text = " ".join(
            f"{key} {value}" for key, value in item.get("details", {}).items()
        )
        text = " ".join([
            item.get("kategori", ""),
            item.get("brand", ""),
            item.get("navn", ""),
            item.get("specs", ""),
            item.get("purpose", ""),
            " ".join(item.get("tags", [])),
            item.get("status", ""),
            details_text,
        ])
        return re.sub(r"\s+", " ", text).strip().lower()

    def next_id(self, items):
        return max([int(item.get("id", 0)) for item in items] + [0]) + 1

    def search(self, query, items):
        if not query:
            return items

        query_lower = query.lower().strip()
        return [item for item in items if query_lower in self.build_search_text(item)]

    def score_for_goal(self, item, goal):
        goal_lower = goal.lower()
        text = self.build_search_text(item)
        score = 0

        if not goal_lower:
            return 0

        for keyword in re.findall(r"[a-zA-Z0-9]+", goal_lower):
            if len(keyword) < 2:
                continue
            if keyword in text:
                score += 3
            if keyword in item.get("kategori", "").lower():
                score += 2
            if keyword in item.get("brand", "").lower():
                score += 2
            if keyword in item.get("navn", "").lower():
                score += 2

        # ekstra score til typiske brugsscenarier
        goal_key = goal_lower.replace(" ", "")
        if "nas" in goal_key:
            if item.get("kategori") in {"Disk", "RAM", "CPU", "Bundkort", "Netværk"}:
                score += 4
            if "nas" in " ".join(item.get("tags", [])).lower():
                score += 6
        if "server" in goal_key:
            if item.get("kategori") in {"CPU", "RAM", "Disk", "Bundkort"}:
                score += 4
        if "router" in goal_key:
            if item.get("kategori") in {"Netværk", "CPU", "RAM"}:
                score += 4
        return score

    def recommend_for_goal(self, goal, items, limit=5):
        if not goal:
            return []

        scored = []
        for item in items:
            if item.get("status") == "Defekt":
                continue
            score = self.score_for_goal(item, goal)
            if score > 0:
                scored.append((score, item))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [item for _, item in scored[:limit]]


def __getattr__(name):
    if name == "HardwareLagerVindue":
        from apps.hardware_inventory import HardwareLagerVindue

        return HardwareLagerVindue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
