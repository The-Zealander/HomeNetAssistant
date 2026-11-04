import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROGRAMMER = {
    "dashboard": {
        "kommando": [str(PROJECT_ROOT / "main.py"), "--desktop"],
        "type": "gui",
    },
    "hardwarelager": {
        "kommando": [str(PROJECT_ROOT / "main.py"), "--hardware-lager"],
        "type": "gui",
    },
    "musikarkivar": {
        "kommando": ["-m", "apps.music_archiver.archiver"],
        "type": "gui",
    },
}


class ProgramManager:
    """Starter kun projektets eksplicit godkendte programmer."""

    def __init__(self, server_mode=False):
        self._processer = {}
        self.server_mode = server_mode

    def start(self, programnavn):
        program = PROGRAMMER.get(programnavn)
        if program is None:
            raise ValueError(f"Programmet er ikke på tilladelseslisten: {programnavn}")
        if self.server_mode:
            return (
                f"{programnavn} kan ikke startes fra servertilstand. "
                "Start skrivebordsprogrammet manuelt fra en lokal computer."
            )

        if program["type"] == "gui" and sys.platform != "win32" and not (
            sys.platform == "darwin" or os.environ.get("DISPLAY")
        ):
            return f"{programnavn} kræver et aktivt grafisk skrivebord."

        kommando = [sys.executable, *program["kommando"]]
        if program["type"] == "interaktiv":
            try:
                resultat = subprocess.run(kommando, cwd=PROJECT_ROOT, check=False)
            except OSError as fejl:
                return f"Kunne ikke starte {programnavn}: {fejl}"
            if resultat.returncode:
                return f"{programnavn} lukkede med fejlkode {resultat.returncode}."
            return f"{programnavn} er afsluttet."

        eksisterende = self._processer.get(programnavn)
        if eksisterende is not None and eksisterende.poll() is None:
            return f"{programnavn} kører allerede."

        try:
            self._processer[programnavn] = subprocess.Popen(
                kommando,
                cwd=PROJECT_ROOT,
            )
        except OSError as fejl:
            return f"Kunne ikke starte {programnavn}: {fejl}"
        return f"{programnavn} er startet."
