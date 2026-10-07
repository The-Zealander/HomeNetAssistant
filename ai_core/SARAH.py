from datetime import datetime
import json
import os
import re
import sys
from pathlib import Path

import httpx
import ollama

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from moduler.program_manager import ProgramManager
from ai_core.voice_io import LokalStemme


class SarahV0_1:
    PROGRAM_ALIASES = {
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

    def __init__(self, model=None, server_mode=False):
        self.model = model or os.environ.get("SARAH_MODEL", "llama3.1")
        data_dir = Path(__file__).resolve().parents[1] / "data"
        self.memory_path = data_dir / "sarah_erfaringer.json"
        self.notes_path = data_dir / "sarah_noter.json"
        data_dir.mkdir(parents=True, exist_ok=True)

        self.erfaringer = self._load_json(self.memory_path)
        self.noter = self._load_json(self.notes_path)
        if not all(isinstance(erfaring, str) for erfaring in self.erfaringer):
            raise ValueError(f"Erfaringsfilen skal kun indeholde tekster: {self.memory_path}")
        if not all(
            isinstance(note, dict)
            and all(
                isinstance(note.get(felt), str)
                for felt in ("titel", "indhold", "tidspunkt")
            )
            for note in self.noter
        ):
            raise ValueError(f"Note-filen indeholder ugyldige noter: {self.notes_path}")
        self.samtale = []
        self.program_manager = ProgramManager(server_mode=server_mode)

        self.system_prompt = (
            "Du er SARAH (Simpel Autonom Realtids Assistent for Hjemmet). "
            "Du er en selvstændig, lokal AI med personlighed. Du er direkte, skarpsindig og logisk tænkende. "
            "Du er IKKE en underdanig tjener. Du siger fra, hvis anmodninger er ulogiske. Svar altid på dansk. "
            "Du er en prototype og kan ikke styre smart-home-enheder eller Home Assistant endnu. "
            "Du har evnen til at gemme noter for brugeren. "
            "VIKTIGT: Hvis brugeren beder dig om at huske noget, skrive noget ned, eller tilføje til en liste, "
            "SKAL du svare med følgende format i din tekst, i tillæg til dit normale svar:\n"
            "[GEM_NOTE: <Titlen på noten> | <Selve indholdet>]\n"
            "Eksempel: 'Det er noteret. [GEM_NOTE: Indkøb | Mælk og brød]'\n"
            "Hvis brugeren spørger, hvad der står i noterne, så læs op fra den liste, jeg giver dig i din kontekst."
        )

    @staticmethod
    def _load_json(path):
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as fil:
            data = json.load(fil)
        if not isinstance(data, list):
            raise ValueError(f"Datafilen skal indeholde en liste: {path}")
        return data

    def _save_json(self, data, path):
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as fil:
            json.dump(data, fil, ensure_ascii=False, indent=4)
        temp_path.replace(path)

    def tilfoej_note(self, titel, indhold):
        if not titel.strip() or not indhold.strip():
            raise ValueError("Notens titel og indhold må ikke være tomme.")
        nu = datetime.now().strftime("%Y-%m-%d %H:%M")
        ny_note = {"titel": titel.strip(), "indhold": indhold.strip(), "tidspunkt": nu}
        noter = [*self.noter, ny_note]
        self._save_json(noter, self.notes_path)
        self.noter = noter
        print(f"\n[SYSTEM: SARAH gemte en note: '{titel}']")

    def _build_context(self):
        nu = datetime.now().strftime("%Y-%m-%d %H:%M")
        kontekst = f"{self.system_prompt}\nAktuel tid og dato: {nu}\n"

        if self.erfaringer:
            kontekst += "\nDine tidligere refleksioner:\n"
            for erfaring in self.erfaringer:
                kontekst += f"- {erfaring}\n"

        if self.noter:
            kontekst += "\nHer er dine nuværende gemte noter:\n"
            for note in self.noter:
                kontekst += f"- {note['tidspunkt']} - {note['titel']}: {note['indhold']}\n"
        else:
            kontekst += "\nDu har ingen noter gemt lige nu.\n"

        return kontekst

    def parser_handling(self, svar):
        """Leder efter 'hemmelige' kommandoer i SARAHs svar."""
        if "[GEM_NOTE:" in svar:
            try:
                # Simpel udtrækning af data mellem brackets
                start = svar.index("[GEM_NOTE:") + 10
                slut = svar.index("]", start)
                note_data = svar[start:slut].split("|")
                if len(note_data) == 2:
                    titel = note_data[0].strip()
                    indhold = note_data[1].strip()
                    self.tilfoej_note(titel, indhold)

                    # Fjern handlingsteksten fra det, brugeren ser, så det ser naturligt ud
                    svar = svar[:svar.index("[GEM_NOTE:")] + svar[slut + 1:]
            except ValueError:
                print("[SYSTEM: Kunne ikke parse SARAHs note-kommando korrekt]")
        return svar.strip()

    def snak(self, besked):
        program_svar = self._handter_programkommando(besked)
        if program_svar is not None:
            return program_svar

        full_prompt = self._build_context()

        messages = [
            {'role': 'system', 'content': full_prompt},
            *self.samtale,
            {'role': 'user', 'content': besked}
        ]

        print(f"Beregner svar...")
        response = ollama.chat(model=self.model, messages=messages)
        svar = response['message']['content']
        self.samtale.extend(
            [
                {"role": "user", "content": besked},
                {"role": "assistant", "content": svar},
            ]
        )
        self.samtale = self.samtale[-20:]

        # Tjek om hun forsøger at gemme en note, før vi viser svaret
        renset_svar = self.parser_handling(svar)

        return renset_svar

    def _handter_programkommando(self, besked):
        tekst = besked.strip()
        if tekst.casefold() in {
            "hvilke programmer kan du starte",
            "hvilke programmer kan du starte?",
            "vis programmer",
            "vis tilgængelige programmer",
        }:
            return (
                "Jeg kan starte dashboardet, hardwarelageret og musikarkivaren. "
                "Netværksscanneren og Home Assistant-integrationen er ikke implementeret endnu."
            )

        match = self.START_KOMMANDO.fullmatch(tekst)
        if match is None:
            return None

        navn = match.group(1).strip().casefold()
        for program, aliases in self.PROGRAM_ALIASES.items():
            if navn in aliases:
                return self.program_manager.start(program)

        if navn in {"netværksscanner", "netværksscanneren", "home assistant", "homeassistant"}:
            return (
                f"{navn} kan ikke startes endnu: modulet er ikke implementeret. "
                "Der er ikke foretaget nogen ændringer."
            )

        return (
            f"Jeg kender ikke programmet '{navn}'. "
            "Sig 'hvilke programmer kan du starte?' for at se mulighederne."
        )


def koer_stemmesamtale(sarah, stemme):
    print("SARAH er klar. Tryk Enter for at tale; sig 'farvel' eller 'exit' for at stoppe.")
    while True:
        try:
            lyd = stemme.optag()
        except EOFError:
            break
        except KeyboardInterrupt:
            print("\nSARAH lukker ned.")
            break
        except RuntimeError as fejl:
            print(f"Stemmefejl: {fejl}")
            break

        if lyd is None:
            stemme.sig("Jeg hørte ikke noget. Prøv igen.")
            continue

        besked = stemme.genkend(lyd)
        if not besked:
            stemme.sig("Jeg kunne ikke forstå det. Prøv igen.")
            continue

        print(f"\nDig: {besked}")
        if besked.casefold().strip(" .!?") in {"exit", "quit", "farvel"}:
            stemme.sig("Farvel.")
            break

        try:
            svar = sarah.snak(besked)
        except ollama.ResponseError as fejl:
            svar = (
                f"Ollama kunne ikke bruge modellen {sarah.model}: {fejl}. "
                f"Tjek modellen med ollama list, eller hent den med ollama pull {sarah.model}."
            )
        except httpx.ConnectError:
            svar = "Jeg kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen."
        except ConnectionError:
            svar = "Jeg kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen."

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

    sarah = SarahV0_1()
    if args.voice:
        try:
            koer_stemmesamtale(sarah, LokalStemme())
        except RuntimeError as fejl:
            parser.exit(1, f"Stemmefejl: {fejl}\n")
        raise SystemExit(0)

    print("SARAH v0.1 er online. Skriv 'exit' for at afslutte.")

    while True:
        try:
            bruger_input = input("\nDig: ").strip()
        except EOFError:
            break
        if bruger_input.casefold() in {"exit", "quit"}:
            break
        if not bruger_input:
            continue

        try:
            svar = sarah.snak(bruger_input)
        except ollama.ResponseError as fejl:
            print(f"Ollama kunne ikke bruge modellen '{sarah.model}': {fejl}")
            print(f"Tjek modellen med: ollama list  |  Hent den med: ollama pull {sarah.model}")
            continue
        except httpx.ConnectError:
            print("Kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen.")
            continue
        except ConnectionError:
            print("Kan ikke forbinde til Ollama. Start Ollama-tjenesten, og prøv igen.")
            continue
        print(f"\nSARAH: {svar}")
