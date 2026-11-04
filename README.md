# Lokalsystemet

Projektet har nu en første headless servertilstand. Serveren starter SARAHs
HTTP-API; den starter ikke automatisk skrivebordsprogrammer, netværksscanning
eller ændringer i Home Assistant.

## Projektstruktur

- `ai_core/`: SARAH, lokal stemme, personlighed og samtalelogik.
- `apps/`: brugerrettede programmer, grupperet efter funktion.
- `integrations/home_assistant/`: Home Assistant-klienten og integrationen.
- `server/`: web-API og serverens ruter.
- `tests/`: automatiserede regressionstests.
- `data/`: lokale, persistente brugerdata grupperet efter funktion.
- `main.py`: fælles indgangspunkt for server, skrivebord og stemmetilstand.

Eksempelvis gemmes inventaret i `data/hardware/inventory.json`,
netværksdata i `data/network/`, og SARAHs personlige noter i `data/sarah/`.

Den egentlige musikarkivar ligger i `apps/music_archiver/`; webgrænsefladerne
ligger i `apps/web/`, og musikarkivarens dashboard ligger sammen med appen.
Når serveren kører, åbnes
browserens kontrolpanel på `/`, og musikarkivarens køside på `/music`.
Hardwarelagerets datamodel og vindue ligger sammen i `apps/hardware_inventory/`;
selve inventaret gemmes separat i `data/hardware/inventory.json`.

## Kør lokalt

Aktivér projektets virtuelle miljø, installér serverafhængigheder, og start:

```bash
python -m pip install -r requirements-server.txt
python main.py --server
```

Serveren lytter som standard kun på `127.0.0.1:8000`. Åbn
`http://127.0.0.1:8000/` for SARAH-chat, Home Assistant-enheder, hardwarelager
og links til de øvrige funktioner. Se status på
`http://127.0.0.1:8000/health` og API-dokumentationen på
`http://127.0.0.1:8000/docs`. Ollama skal køre separat, og modellen i
`SARAH_MODEL` skal være hentet lokalt. Hvis serveren skal kunne nås fra andre
maskiner, skal `SARAH_API_TOKEN` sættes; brug HTTPS via en betroet reverse
proxy før adgang fra et netværk.
Når nøglen er sat i `.env`, genstart serveren. Indtast kun selve værdien i
feltet "Servernøgle" på websiden, og vælg "Gem og test nøgle". Nøglen deles
mellem kontrolpanelet og musikarkivaren.

Den eksisterende desktopvisning startes stadig med `python main.py --desktop`
(eller `python main.py`, som standard).
Musikarkivaren åbnes på `http://127.0.0.1:8000/music`. Den accepterer HTTPS-links
fra YouTube og YouTube Music og downloader sekventielt. I Compose gemmes musikken
i det persistente `/app/data/music`-volume; ved anden installation sættes
`MUSIC_DOWNLOAD_PATH` til en skrivbar, persistent mappe.

Skrivebords-UI startes med `python main.py --desktop`. Stemmestyring på en
maskine med mikrofon startes med `python main.py --voice` efter installation af
`requirements.txt`. Det kræver også systempakkerne `espeak-ng`, PortAudio og
`paplay` eller `aplay`. Whisper-modellen hentes første gang og gemmes lokalt;
selve mikrofonoptagelsen sendes ikke til en ekstern tjeneste. Stemmeinput er
ikke slået til i servertilstand.

Webchatten har browserbaseret talegenkendelse og kan læse SARAHs svar op i
browseren eller sende dem til en Home Assistant-højttaler. Højttaleroutput
bruger Home Assistants `tts.speak`-tjeneste; vælg en `media_player` og en
`tts`-entitet i SARAH-panelet. For helt lokal talesyntese skal Home Assistant
have en lokal TTS-motor som Piper installeret og konfigureret. Browserens
talegenkendelse kan stadig benytte browserens eller operativsystemets
tale-tjeneste og er derfor ikke nødvendigvis lokal. Webchatten har endnu ikke
always-on mikrofon eller wake-word-detektion; tekstchatten virker fortsat uden
støttede stemmefunktioner.

## Container-demo

Opret `.env` ud fra eksemplet uden at overskrive en eksisterende fil, og
generér en stærk API-nøgle:

```bash
cp -n .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Indsæt den genererede nøgle i `.env`, og start derefter:

```bash
docker compose up --build -d
docker compose exec ollama ollama pull llama3.1
```

Ollama-modellen downloades første gang og gemmes i et persistent volume.
Sarahs noter gemmes i et separat persistent volume. API'et udgives kun på
serverens loopback-adresse; brug en autentificeret HTTPS-reverse proxy, hvis
det skal tilgås fra andre maskiner.

Home Assistant skal allerede køre som en separat installation. Angiv dens URL
og en langlivet token via `HA_URL` og `HA_TOKEN` i `.env`. Health-endpointet
viser forbindelsesstatus. Kontrolpanelet kan styre lys, kontakter, blæsere og
medieafspillere; højttalere har afspil/pause, stop og lydstyrkekontrol. SARAH
kan sende svar til en valgt Home Assistant-højttaler gennem en valgt TTS-motor.
Det er ikke en generel adgang til alle Home Assistant-tjenester. Direkte
lys- og kontakthandlinger er stadig begrænset og valideret.

## Begrænsninger før egentlig smart-home-drift

- `/api/chat` og status er en prototype, ikke en færdig flerbruger- eller
  produktions-API. Samtalehistorik er kun i hukommelsen og nulstilles ved genstart.
- Home Assistant og Ollama er separate tjenester. Projektet kan kontrollere
  forbindelsen til Home Assistant, men starter ikke Home Assistant-processen.
- `apps/network_scanner.py` er en pladsholder uden netværksaktivitet.
  Modulet starter ikke scanning eller ping-sweep.
  Browserkontrolpanelet er en tidlig prototype: hardwarelageret er indtil videre
  skrivebeskyttet dér, og køstatus gemmes kun i serverens hukommelse.
  Musikarkivarens webkø er en tidlig prototype: kø og status gemmes kun i
  serverens hukommelse og forsvinder ved genstart. Dens downloads kører i en
  baggrundstråd; brug et persistent musikbibliotek og en sikker HTTPS-proxy ved
  netværksadgang.
- Hardwarelageret er en Tkinter-app og hører til skrivebordstilstand, ikke en
  headless server.
- Hemmeligheder skal ligge i miljøvariabler eller en secrets manager, aldrig i
  projektfiler eller versionsstyring.

SpotDL-playlister hentes med en enkelt download-tråd og flere lydkilder som
fallback. Musikfiler gemmes under `MUSIC_DOWNLOAD_PATH` (standard: `~/Musik`) i
mapperne `Kunstner/Album`. Manglende kunstner- eller albumdata fra YouTube
placeres under `Ukendt Kunstner/Ukendt Album`. Genoptagelsesdata, logs og
fejlrapporter gemmes separat i `~/.local/state/lokalsystemet/music/`. Ved
delvise fejl bevares allerede hentede numre; genkør samme Spotify-link for at
prøve manglende numre igen.

## Udvikling og afhængigheder

`requirements-server.txt` indeholder kun API-serverens afhængigheder.
`requirements-music.txt` er valgfri for musikværktøjerne. `requirements.txt`
installerer hele desktop-, stemme-, server- og musikmiljøet.

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Testene bruger Pythons indbyggede `unittest`; de kontrollerer afgrænset
programadfærd, såsom opstart og API, SARAHs intentioner og hukommelse,
Home Assistant, hardwarelager, musikarkivering og netværksscannerens
inaktive placeholder. Eksterne tjenester erstattes normalt af mocks, så
kørslen kræver ikke en aktiv Ollama- eller Home Assistant-server og starter
ikke faktiske downloads. Filer med navne som `_test_server.py` er ældre
eksperimenter og bliver ikke samlet op af standardkommandoen; aktive tests
hedder `test_*.py`. Kør kun SARAH-tests med
`python -m unittest discover -s tests -p 'test_sarah*.py' -v`.

### SARAHs hukommelse

SARAH træner ikke sin sprogmodel og lærer ikke automatisk af private samtaler.
Hun kan i stedet gemme brugerens egne noter lokalt: skriv `Husk: ...` i chatten,
brug `Hvad husker du?` for at se dem, og `Glem alle noter` for at slette dem.
Noterne gemmes i `data/sarah/sarah_noter.json`, bevares efter genstart og indgår som
kontekst i svar. De påvirker ikke SARAHs regler eller systemadgang.
