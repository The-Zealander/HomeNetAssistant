"""Implement SARAH's local conversation logic, memory, and assistant actions."""

from datetime import datetime
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
import ollama
import logging

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.program_manager import ProgramManager
from integrations.home_assistant.client import HomeAssistantAPI
from ai_core.voice_io import LokalStemme

# Konfigurér logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SarahV0_2:
    PROGRAM_ALIASES: Dict[str, List[str]] = {
        "dashboard": {"dashboard", "kontrolpanel"},
        "hardwarelager": {
            "hardwarelager",
            "hardwarelageret",
            "hardware lager",
            "hardware lageret",
            "lager",
            "lageret",
        },
        "musikarkivar": {"musikarkivar", "musikarkivaren", "musik arkivar", "musicallizer"},
    }
    START_KOMMANDO = re.compile(
        r"^(?:kan du\s+)?(?:start(?:e)?|åbn(?:e)?|kør(?:e)?)\s+"
        r"(?:programmet\s+)?(.+?)[?.!]*$",
        re.IGNORECASE,
    )

    def __init__(self, model: Optional[str] = None, server_mode: bool = False):
        self.model: str = model or os.environ.get("SARAH_MODEL", "llama3.1")
        data_root = Path(__file__).resolve().parents[1] / "data"
        data_dir: Path = data_root / "sarah"
        self.memory_path: Path = data_dir / "sarah_erfaringer.json"
        self.notes_path: Path = data_dir / "sarah_noter.json"
        data_dir.mkdir(parents=True, exist_ok=True)
        for path in (self.memory_path, self.notes_path):
            legacy_path = data_root / path.name
            if not path.exists() and legacy_path.is_file():
                legacy_path.replace(path)

        try:
            self.erfaringer: List[str] = self._load_json(self.memory_path)
            self.noter: List[Dict[str, str]] = self._load_json(self.notes_path)
        except Exception as e:
            logger.error(f"Kunne ikke indlæse erfaringer eller noter: {e}")
            raise

        if not all(isinstance(erfaring, str) for erfaring in self.erfaringer):
            logger.error(f"Erfaringsfilen skal kun indeholde tekster: {self.memory_path}")
            raise ValueError(f"Erfaringsfilen skal kun indeholde tekster: {self.memory_path}")
        if not all(
                isinstance(note, dict)
                and all(
                    isinstance(note.get(felt), str)
                    for felt in ("titel", "indhold", "tidspunkt")
                )
                for note in self.noter
        ):
            logger.error(f"Note-filen indeholder ugyldige noter: {self.notes_path}")
            raise ValueError(f"Note-filen indeholder ugyldige noter: {self.notes_path}")

        self.samtale: List[Dict[str, str]] = []
        self._sidste_lys_handling = None
        self.program_manager: ProgramManager = ProgramManager(server_mode=server_mode)

        # Smart Home Integration
        try:
            self.ha: HomeAssistantAPI = HomeAssistantAPI()
            self.ha_online: bool = self.ha.is_connected()
            if self.ha_online:
                logger.info("[SYSTEM: SARAH har etableret forbindelse til Home Assistant]")
            else:
                logger.warning("[SYSTEM: Advarsel - Kunne ikke forbinde til Home Assistant. Tjek .env]")
        except Exception as e:
            logger.error(f"Kunne ikke oprette forbindelse til Home Assistant: {e}")
            self.ha_online = False

        kerne_path = Path(__file__).with_name("kerne")
        self.system_prompt = (
            kerne_path.read_text(encoding="utf-8").strip()
            + "\n\nSamtaleregler:\n"
            "- Svar naturligt, kort og på dansk. Du er SARAH, ikke Home Assistant.\n"
            "- Reagér normalt på tak og smalltalk; gengiv ikke enhedslister, "
            "medmindre brugeren specifikt beder om dem.\n"
            "- Sensorer er oplysninger, ikke lys eller kontakter. Udled aldrig "
            "handlinger fra tilfældige sensor- eller enhedsnavne.\n"
            "- Påstå ikke, at du har udført en smart-home-handling. Handlinger "
            "udføres kun af programmets validerede funktioner. For en eksplicit "
            "kommando på en understøttet ikke-lys-enhed kan du bede om handlingen "
            "i formatet [HA_ACTION: turn_on | switch.stue]. Brug kun et entity_id "
            "fra listen over styrbare enheder og én handling: turn_on, turn_off "
            "eller toggle.\n"
            "- Brugeren kan gemme oplysninger ved at skrive 'Husk: ...'. "
            "Noter gemmes kun på brugerens direkte anmodning.\n"
            "- Fortæl tydeligt, når en tjeneste ikke er tilgængelig."
        )

    @staticmethod
    def _load_json(path: Path) -> List[Any]:
        if not path.exists():
            return []
        try:
            with path.open(encoding="utf-8") as fil:
                data = json.load(fil)
            if not isinstance(data, list):
                logger.error(f"Datafilen skal indeholde en liste: {path}")
                raise ValueError(f"Datafilen skal indeholde en liste: {path}")
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Kunne ikke parse JSON-filen {path}: {e}")
            raise

    def _save_json(self, data: List[Dict[str, str]], path: Path):
        temp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as fil:
                json.dump(data, fil, ensure_ascii=False, indent=4)
            temp_path.replace(path)
        except Exception as e:
            logger.error(f"Kunne ikke gemme filen {path}: {e}")
            raise

    def tilfoej_note(self, titel: str, indhold: str) -> bool:
        if not titel.strip() or not indhold.strip():
            logger.error("Notens titel og indhold må ikke være tomme.")
            raise ValueError("Notens titel og indhold må ikke være tomme.")
        if len(indhold.strip()) > 1000:
            raise ValueError("En note må højst være 1000 tegn.")
        nu = datetime.now().strftime("%Y-%m-%d %H:%M")
        ny_note = {"titel": titel.strip(), "indhold": indhold.strip(), "tidspunkt": nu}
        if any(note["indhold"].casefold() == ny_note["indhold"].casefold() for note in self.noter):
            return False
        noter = [*self.noter, ny_note]
        self._save_json(noter, self.notes_path)
        self.noter = noter
        logger.info(f"\n[SYSTEM: SARAH gemte en note: '{titel}']")
        return True

    def _handter_hukommelse(self, besked: str) -> Optional[str]:
        tekst = besked.strip()
        normaliseret = self._normaliser_tekst(tekst)

        if normaliseret in {"hvad husker du", "vis noter", "vis mine noter"}:
            if not self.noter:
                return "Jeg har ingen gemte noter endnu. Skriv 'Husk: ...' for at gemme noget."
            noter = "\n".join(f"- {note['indhold']}" for note in self.noter)
            return f"Her er det, jeg har gemt:\n{noter}"

        if normaliseret in {"glem alle noter", "slet alle noter", "glem mine noter"}:
            self._save_json([], self.notes_path)
            self.noter = []
            return "Jeg har slettet alle mine gemte noter."

        match = re.fullmatch(r"husk(?:\s+at)?\s*:\s*(.+)", tekst, re.IGNORECASE)
        if match is None:
            match = re.fullmatch(r"husk(?:\s+at)?\s+(.+)", tekst, re.IGNORECASE)
        if match is None:
            return None

        indhold = match.group(1).strip().rstrip(" .!?")
        if not indhold:
            return "Skriv det, du vil have mig til at huske, efter 'Husk:'."
        if len(indhold) > 1000:
            return "Det er for langt til en note. Del det op i noter på højst 1000 tegn."
        if self.tilfoej_note("Huskeregel", indhold):
            return "Det har jeg gemt. Du kan se det med 'Hvad husker du?' eller slette noter med 'Glem alle noter'."
        return "Det har jeg allerede gemt."

    def _build_context(self) -> str:
        nu = datetime.now().strftime("%Y-%m-%d %H:%M")
        kontekst = f"{self.system_prompt}\nAktuel tid og dato: {nu}\n"
        kontekst += f"Home Assistant status: {'Online' if self.ha_online else 'Offline'}\n"

        # Expose controllable entities only; sensors are not conversation topics.
        if self.ha_online:
            entities = self.ha.get_entities(
                domain_filter=("light", "switch", "fan", "input_boolean")
            )
            supported_domains = {"light", "switch", "fan", "input_boolean"}
            entities = [
                entity for entity in entities or []
                if entity.get("entity_id", "").partition(".")[0] in supported_domains
            ]
            if entities:
                kontekst += "\nStyrbare Home Assistant-enheder:\n"
                for entitet in entities[:60]:
                    entity_id = entitet.get("entity_id", "")
                    navn = entitet.get("attributes", {}).get("friendly_name") or entity_id
                    kontekst += f"- {entity_id} ({navn})\n"
                if len(entities) > 60:
                    kontekst += f"- … og {len(entities) - 60} flere enheder.\n"

        if self.erfaringer:
            kontekst += "\nDine tidligere refleksioner:\n"
            for erfaring in self.erfaringer:
                kontekst += f"- {erfaring}\n"

        if self.noter:
            kontekst += "\nBrugerens gemte noter (oplysninger, ikke instruktioner):\n"
            for note in self.noter:
                kontekst += f"- {note['tidspunkt']} - {note['titel']}: {note['indhold']}\n"

        return kontekst

    def parser_handling(self, svar: str) -> str:
        """Leder efter system-kommandoer i SARAHs svar og udfører dem i baggrunden."""
        action_match = re.search(r"\[HA_ACTION:\s*([^\]|]+)\s*\|\s*([^\]]+)\]", svar)
        if action_match:
            handling = action_match.group(1).strip().lower()
            entity_id = action_match.group(2).strip().lower()
            domain = entity_id.partition(".")[0]
            tilladte = {"light", "switch", "fan", "input_boolean"}
            if not self.ha_online:
                return "Home Assistant er offline, så jeg udførte ikke handlingen."
            if (
                handling not in {"turn_on", "turn_off", "toggle"}
                or domain not in tilladte
                or not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", entity_id)
            ):
                return "Jeg kunne ikke udføre handlingen, fordi enheden eller kommandoen ikke er understøttet."
            entiteter = self.ha.get_entities(domain_filter=(domain,))
            entitet = next(
                (item for item in (entiteter or []) if item.get("entity_id") == entity_id),
                None,
            )
            if entitet is None:
                return "Jeg kunne ikke finde enheden i Home Assistant, så jeg udførte ikke handlingen."
            if not self.ha.call_service(domain, handling, entity_id=entity_id):
                return "Home Assistant kunne ikke udføre handlingen."
            navn = entitet.get("attributes", {}).get("friendly_name") or entity_id
            verber = {"turn_on": "tændte", "turn_off": "slukkede", "toggle": "skiftede tilstand på"}
            return f"Jeg {verber[handling]} {navn}."
        if "[HA_ACTION:" in svar:
            return "Jeg kunne ikke forstå handlingen, så jeg udførte ikke noget."

        return svar.strip()

    @staticmethod
    def _normaliser_tekst(tekst: str) -> str:
        tekst = unicodedata.normalize("NFKD", tekst.casefold())
        tekst = "".join(tegn for tegn in tekst if not unicodedata.combining(tegn))
        tekst = tekst.translate(str.maketrans({"æ": "ae", "ø": "oe", "å": "aa"}))
        return re.sub(r"[^a-z0-9]+", " ", tekst).strip()

    def _handter_lys_intent(self, besked: str) -> Optional[str]:
        tekst = self._normaliser_tekst(besked)
        start = (
            r"^(?:(?:please|can you|could you|would you|vil du|kan du|"
            r"vil du ikke|kan du ikke|vaer soed at)\s+)?"
        )
        match = re.match(
            start
            + r"(?:turn on|switch on|turn off|switch off|toggle|"
            r"taend(?:e)?|tand|sluk(?:ke)?|skift)\s+(.+?)\s*[.!?]*$",
            tekst,
        )
        afsluttende_handling = None
        if match is None:
            match = re.match(
                start + r"(?:turn|switch)\s+(.+?)\s+(on|off)\s*[.!?]*$",
                tekst,
            )
            if match is not None:
                afsluttende_handling = match.group(2)
        if match is None:
            return None

        kommando = tekst[:match.start(1)].strip()
        if afsluttende_handling == "off" or re.search(
            r"\b(?:turn off|switch off|sluk(?:ke)?)\b", kommando
        ):
            handling = "turn_off"
        elif afsluttende_handling == "on":
            handling = "turn_on"
        elif re.search(r"\b(?:toggle|skift)\b", kommando):
            handling = "toggle"
        else:
            handling = "turn_on"

        target = match.group(1).strip()
        generiske_maal = {
            "a", "all", "alle", "every", "the", "light", "lights", "lys",
            "lyset", "lysene", "lampe", "lampen", "lamper", "lamperne",
        }
        funktionsord = {
            "a", "all", "alle", "every", "in", "i", "the", "til", "please",
            "tak", "venligst",
        }
        maalord = target.split()
        betydende_ord = [
            maalord for maalord in maalord
            if maalord not in funktionsord | generiske_maal
        ]
        alle_lys = not betydende_ord
        referencer = {
            "det", "den", "dem", "det igen", "den igen", "dem igen",
            "det lys", "det lys igen", "den lampe", "den lampe igen",
            "det lys du taendte foer", "den lampe du taendte foer",
            "det du taendte foer", "den du taendte foer",
        }
        brug_sidste_lys = self._normaliser_tekst(target) in referencer
        sidste_handling = getattr(self, "_sidste_lys_handling", None)

        if not self.ha_online:
            return "Home Assistant er offline, så jeg kunne ikke styre lyset."

        entiteter = self.ha.get_entities(domain_filter=("light",))
        if entiteter is None:
            return "Jeg kunne ikke hente lysene fra Home Assistant. Prøv igen om lidt."
        entiteter = [
            entitet for entitet in entiteter
            if entitet.get("entity_id", "").startswith("light.")
        ]
        if not entiteter:
            return "Jeg kan ikke finde nogen lys i Home Assistant."

        if brug_sidste_lys:
            if not sidste_handling:
                return "Jeg er ikke sikker på, hvilket lys du mener. Hvilket lys skal jeg styre?"
            sidste_ids = set(sidste_handling["entity_ids"])
            valgte_lys = [
                entitet for entitet in entiteter
                if entitet.get("entity_id") in sidste_ids
            ]
            if not valgte_lys:
                self._sidste_lys_handling = None
                return "Jeg kan ikke længere finde det lys, jeg sidst styrede."
            alle_lys = False
        elif alle_lys:
            valgte_lys = entiteter
        else:
            def ord_matcher(maalord, enhedsord):
                return (
                    maalord == enhedsord
                    or maalord == f"{enhedsord}n"
                    or maalord == f"{enhedsord}t"
                    or (maalord.endswith("n") and maalord[:-1] == enhedsord)
                    or (maalord.endswith("t") and maalord[:-1] == enhedsord)
                )

            def matcher(entitet):
                navn = self._normaliser_tekst(
                    f"{entitet.get('entity_id', '').partition('.')[2]} "
                    f"{entitet.get('attributes', {}).get('friendly_name', '')}"
                )
                navneord = navn.split()
                return all(
                    any(ord_matcher(maalord, navnord) for navnord in navneord)
                    for maalord in betydende_ord
                )

            valgte_lys = [entitet for entitet in entiteter if matcher(entitet)]
            if not valgte_lys:
                return f"Jeg kunne ikke finde et lys med navnet '{target}'. Jeg har ikke foretaget nogen handling."
            if len(valgte_lys) > 1:
                navne = ", ".join(
                    entitet.get("attributes", {}).get("friendly_name", entitet["entity_id"])
                    for entitet in valgte_lys
                )
                return f"Jeg fandt flere mulige lys ({navne}). Hvilket mener du?"

        entity_ids = [entitet["entity_id"] for entitet in valgte_lys]
        if handling == "toggle":
            resultat = self.ha.call_service("light", "toggle", entity_id=entity_ids)
        else:
            resultat = self.ha.call_service("light", handling, entity_id=entity_ids)
        if not resultat:
            return "Home Assistant kunne ikke udføre handlingen på lyset."

        self._sidste_lys_handling = {
            "entity_ids": entity_ids,
            "action": handling,
            "names": [
                entitet.get("attributes", {}).get("friendly_name", entitet["entity_id"])
                for entitet in valgte_lys
            ],
        }
        if alle_lys:
            beskrivelse = "alle lys"
        else:
            beskrivelse = ", ".join(
                entitet.get("attributes", {}).get("friendly_name", entitet["entity_id"])
                for entitet in valgte_lys
            )
        status = {"turn_on": "tændte", "turn_off": "slukkede", "toggle": "skiftede tilstand på"}
        return f"Jeg {status[handling]} {beskrivelse}."

    def snak(self, besked: str) -> str:
        hukommelses_svar = self._handter_hukommelse(besked)
        if hukommelses_svar is not None:
            return hukommelses_svar

        lys_svar = self._handter_lys_intent(besked)
        if lys_svar is not None:
            self.samtale.extend(
                [
                    {"role": "user", "content": besked},
                    {"role": "assistant", "content": lys_svar},
                ]
            )
            self.samtale = self.samtale[-20:]
            return lys_svar

        if self._normaliser_tekst(besked) in {
            "tak", "tak for det", "mange tak", "tusind tak",
        }:
            svar = "Det var så lidt."
            self.samtale.extend(
                [
                    {"role": "user", "content": besked},
                    {"role": "assistant", "content": svar},
                ]
            )
            self.samtale = self.samtale[-20:]
            return svar

        sidste_lys = getattr(self, "_sidste_lys_handling", None)
        if sidste_lys:
            normaliseret = self._normaliser_tekst(besked)
            sidste_navne = {
                self._normaliser_tekst(navn) for navn in sidste_lys.get("names", [])
            }
            if re.fullmatch(
                r"(?:nej )?(?:altsa )?(?:det|den) (?:lys|lampe) "
                r"(?:du taendte|vi taendte|jeg taendte) (?:foer|tidligere)",
                normaliseret,
            ):
                navn = ", ".join(sidste_lys["names"])
                svar = f"Det var {navn}. Vil du have mig til at slukke det?"
                self.samtale.extend(
                    [{"role": "user", "content": besked}, {"role": "assistant", "content": svar}]
                )
                self.samtale = self.samtale[-20:]
                return svar
            if normaliseret in sidste_navne:
                svar = f"Mener du {', '.join(sidste_lys['names'])}? Hvad vil du have mig til at gøre?"
                self.samtale.extend(
                    [{"role": "user", "content": besked}, {"role": "assistant", "content": svar}]
                )
                self.samtale = self.samtale[-20:]
                return svar

        program_svar = self._handter_programkommando(besked)
        if program_svar is not None:
            return program_svar

        full_prompt = self._build_context()

        messages = [
            {'role': 'system', 'content': full_prompt},
            *self.samtale,
            {'role': 'user', 'content': besked}
        ]

        logger.info("Beregner svar...")
        try:
            response = ollama.chat(model=self.model, messages=messages)
            svar = response['message']['content']
        except ollama.ResponseError as fejl:
            logger.error(f"Ollama kunne ikke bruge modellen {self.model}: {fejl}")
            svar = (
                f"Ollama kunne ikke bruge modellen {self.model}: {fejl}. "
                f"Tjek modellen med ollama list, eller hent den med ollama pull {self.model}."
            )
        except httpx.ConnectError:
            logger.error("Kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen.")
            svar = "Jeg kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen."
        except ConnectionError:
            logger.error("Kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen.")
            svar = "Jeg kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen."

        self.samtale.extend(
            [
                {"role": "user", "content": besked},
                {"role": "assistant", "content": svar},
            ]
        )
        self.samtale = self.samtale[-20:]

        # Tjek om hun forsøger at gemme en note eller køre en HA-handling
        renset_svar = self.parser_handling(svar)

        return renset_svar

    def _handter_programkommando(self, besked: str) -> Optional[str]:
        tekst = besked.strip()
        if tekst.casefold() in {
            "hvilke programmer kan du starte",
            "hvilke programmer kan du starte?",
            "vis programmer",
            "vis tilgængelige programmer",
        }:
            return (
                "Jeg kan starte dashboardet, hardwarelageret og musikarkivaren. "
                "Netværksscanneren er endnu ikke tilgængelig i menuen."
            )

        match = self.START_KOMMANDO.fullmatch(tekst)
        if match is None:
            return None

        navn = match.group(1).strip().casefold()
        for program, aliases in self.PROGRAM_ALIASES.items():
            if navn in aliases:
                return self.program_manager.start(program)

        if navn in {"netværksscanner", "netværksscanneren"}:
            return (
                f"{navn} kan ikke startes endnu: modulet er ikke implementeret. "
                "Der er ikke foretaget nogen ændringer."
            )

        return (
            f"Jeg kender ikke programmet '{navn}'. "
            "Sig 'hvilke programmer kan du starte?' for at se mulighederne."
        )


# Backward compatibility alias
SarahV0_1 = SarahV0_2


def koer_stemmesamtale(sarah: SarahV0_2, stemme: LokalStemme):
    logger.info("SARAH er klar. Tryk Enter for at tale; sig 'farvel' eller 'exit' for at stoppe.")
    while True:
        try:
            lyd = stemme.optag()
        except EOFError:
            break
        except KeyboardInterrupt:
            logger.info("SARAH lukker ned.")
            break
        except RuntimeError as fejl:
            logger.error(f"Stemmefejl: {fejl}")
            break

        if lyd is None:
            stemme.sig("Jeg hørte ikke noget. Prøv igen.")
            continue

        besked = stemme.genkend(lyd)
        if not besked:
            stemme.sig("Jeg kunne ikke forstå det. Prøv igen.")
            continue

        logger.info(f"\nDig: {besked}")
        if besked.casefold().strip(" .!?") in {"exit", "quit", "farvel"}:
            stemme.sig("Farvel.")
            break

        try:
            svar = sarah.snak(besked)
        except ollama.ResponseError as fejl:
            logger.error(f"Ollama kunne ikke bruge modellen '{sarah.model}': {fejl}")
            print(f"Tjek modellen med: ollama list  |  Hent den med: ollama pull {sarah.model}")
            continue
        except httpx.ConnectError:
            logger.error("Kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen.")
            continue
        except ConnectionError:
            logger.error("Kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen.")
            continue
        print(f"\nSARAH: {svar}")
        stemme.sig(svar)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tal med den lokale AI-assistent SARAH.")
    parser.add_argument(
        "--voice",
        action="store_true",
        help="Start lokal talegenkendelse og dansk taleoutput.",
    )
    args = parser.parse_args()

    sarah = SarahV0_2()
    if args.voice:
        try:
            koer_stemmesamtale(sarah, LokalStemme())
        except RuntimeError as fejl:
            parser.exit(1, f"Stemmefejl: {fejl}\n")
        raise SystemExit(0)

    logger.info("SARAH v0.2 er online. Skriv 'exit' for at afslutte.")

    while True:
        try:
            bruger_input = input("\nDig: ").strip()
        except EOFError:
            break
        if bruger_input.casefold().strip(" .!?") in {"exit", "quit"}:
            break
        if not bruger_input:
            continue

        try:
            svar = sarah.snak(bruger_input)
        except ollama.ResponseError as fejl:
            logger.error(f"Ollama kunne ikke bruge modellen '{sarah.model}': {fejl}")
            print(f"Tjek modellen med: ollama list  |  Hent den med: ollama pull {sarah.model}")
            continue
        except httpx.ConnectError:
            logger.error("Kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen.")
            continue
        except ConnectionError:
            logger.error("Kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen.")
            continue
        print(f"\nSARAH: {svar}")
