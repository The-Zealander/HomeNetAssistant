import argparse
import ipaddress
import os
from pathlib import Path
import socket
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Indlæs altid miljøvariabler fra .env ved opstart
load_dotenv(BASE_DIR / ".env")


def start_skrivebord(hardware_lager=False):
    os.chdir(BASE_DIR)
    from apps.desktop_ui import start_skrivebord as start_ui

    start_ui(hardware_lager=hardware_lager)


def start_stemme():
    os.chdir(BASE_DIR)
    from ai_core.SARAH import SarahV0_2, koer_stemmesamtale
    from ai_core.voice_io import LokalStemme

    sarah = SarahV0_2()
    koer_stemmesamtale(sarah, LokalStemme())


def start_server(host, port):
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ip = ipaddress.ip_address(socket.gethostbyname(host))
        except OSError as fejl:
            raise SystemExit(f"Kan ikke slå serveradressen op: {host}") from fejl
    token = os.environ.get("SARAH_API_TOKEN", "")
    if not ip.is_loopback and len(token) < 32:
        raise SystemExit(
            "En server, der kan nås fra netværket, kræver en SARAH_API_TOKEN "
            "på mindst 32 tegn. Bind alternativt til 127.0.0.1."
        )
    if not 1 <= port <= 65535:
        raise SystemExit("Porten skal være mellem 1 og 65535.")

    try:
        import uvicorn
    except ImportError as fejl:
        raise SystemExit(
            "Serverafhængigheder mangler. Kør: "
            "python -m pip install -r requirements-server.txt"
        ) from fejl

    os.chdir(BASE_DIR)
    os.environ["SARAH_HOST"] = host
    uvicorn.run(
        "server.app:app",
        host=host,
        port=port,
        proxy_headers=False,
    )


def main():
    parser = argparse.ArgumentParser(description="Start Lokalsystemet og SARAH.")
    tilstand = parser.add_mutually_exclusive_group()
    tilstand.add_argument(
        "--desktop",
        action="store_true",
        help="Start det grafiske dashboard/GUI (standard).",
    )
    tilstand.add_argument(
        "--server",
        action="store_true",
        help="Start Uvicorn API-serveren i stedet for skrivebordet.",
    )
    tilstand.add_argument(
        "--hardware-lager",
        action="store_true",
        help="Åbn hardwarelageret direkte i skrivebordstilstand.",
    )
    tilstand.add_argument(
        "--voice",
        action="store_true",
        help="Start SARAHs lokale stemmesamtale direkte i terminalen.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("SARAH_HOST", "127.0.0.1"),
        help="Serverens lytteadresse (standard: 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("SARAH_PORT", "8000")),
        help="Serverens port (standard: 8000).",
    )
    args = parser.parse_args()

    # Vælg afviklingstilstand
    if args.server:
        start_server(args.host, args.port)
    elif args.voice:
        start_stemme()
    else:
        # Standard: Åbn GUI / Skrivebordet direkte
        start_skrivebord(hardware_lager=args.hardware_lager)


if __name__ == "__main__":
    main()