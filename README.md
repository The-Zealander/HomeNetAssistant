# Lokalsystemet

Projektet har nu en første headless servertilstand. Serveren starter SARAHs
HTTP-API; den starter ikke automatisk skrivebordsprogrammer, netværksscanning
eller ændringer i Home Assistant.

## Projektstruktur

- `ai_core/`: SARAH, lokal stemme, personlighed og samtalelogik.
- `apps/`: brugerrettede programmer, grupperet efter funktion. Musikarkivaren,
  dens webkø og den lokale Music Assistant ligger samlet i
  `apps/music_archiver/`; hardwarelageret ligger i `apps/hardware_inventory/`.
- `integrations/home_assistant/`: Home Assistant-klienten og integrationen.
- `server/`: web-API og serverens ruter.
- `tests/`: automatiserede regressionstests.
- `data/`: lokale, persistente brugerdata grupperet efter funktion. Downloads,
  SARAH-noter og anden maskinlokal tilstand bør ikke versionsstyres.
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
Musikarkivaren åbnes på `http://127.0.0.1:8000/music`. Den genkender HTTPS-links
fra YouTube, YouTube Music, Spotify og Tidal og viser kildetypen. Kun YouTube-
og YouTube Music-links accepteres som downloadkilder; Spotify- og Tidal-links
afvises med en forklaring, da køen ikke henter streamet lyd fra de tjenester.
Spotify kan fortsat bruges som valgfri metadata-kilde via et eksakt ISRC-match.
I Compose gemmes musikken i det persistente `/app/data/music`-volume; ved anden installation sættes
`MUSIC_DOWNLOAD_PATH` til en skrivbar, persistent mappe.
I musikarkivarens kø kan hvert batch valgfrit også forsøge at hente en MP4-
musikvideo for hvert spor. Videoer gemmes separat som
`<musikmappe> - Musikvideoer/Kunstner/Album/` (eller i den valgfri mappe fra
`MUSIC_VIDEO_DOWNLOAD_PATH`), aldrig i Navidromes musikmappe som standard.
Videoen hentes kun, når kilden har en passende MP4-version; manglende video
forhindrer ikke lydsporet i at blive gemt. Brug kun funktionen til indhold, du
har ret til at downloade. Navidrome er en musikserver og katalogiserer ikke
disse videofiler; afspilning i Symfonium afhænger af klientens mediekilde og
understøttelse. Compose gemmer dem persistent i `/app/data/music-videos`.
YouTube-downloads prøver som standard cookies fra en automatisk fundet Brave-profil
(`MUSIC_YTDLP_COOKIES_BROWSER=auto`), så videoer, der kræver login eller
aldersbekræftelse, kan tilgås med den konto, der allerede er logget ind i Brave.
Kontoen skal selv have adgang til videoen; cookies omgår ikke alders-, regions-
eller andre kontobegrænsninger. Brugeren, der kører musikarkivaren, skal have
adgang til sin Brave-profil på samme maskine. Angiv en tom værdi for at slå
cookiebrug fra, eller `brave:Profile 1` for en anden Brave-profil. Arkivaren
overfører ikke browserens cookiedatabase til musikbiblioteket.

Compose-containeren bruger ikke hostens Brave-profil og falder automatisk tilbage
til almindelig, ikke-login YouTube-adgang. For videoer, der kræver kontocookies,
kør serveren direkte med `python main.py --server` som den Brave-bruger, der skal
hente dem.

For hvert nummer henter arkivaren de fulde yt-dlp-sporoplysninger, også når
playlistens forhåndsvisning allerede indeholder en kunstner. Når kilden har et
ISRC, slår arkivaren det op i MusicBrainz' gratis offentlige katalog og bruger
kun den præcise ISRC-forbindelse; udgivelsesdata hentes fra den tilknyttede
optagelse og udgivelse. Uden ISRC bruges katalogdata kun, hvis titel og alle
kunstnernavne matcher præcist, og der kun findes ét match. Cover Art Archive
bruges som gratis cover-fallback, når YouTube ikke har leveret et indlejret
cover. Tjenesternes offentlige API-rategrænse overholdes, og arkiveringen
virker fortsat uden netadgang til katalogerne. Der kræves ingen abonnementer,
API-nøgler eller AI-model til denne berigelse.

MP3-filer gemmes som ID3v2.4. `ARTIST` og `ALBUMARTIST` indeholder læsbare
visningscredits, mens Navidromes flerkunstnerfelter (`ARTISTS` /
`ALBUM ARTISTS`) gemmer hver kunstner som en separat værdi. Datoer mærkes efter
betydning: optagelsesdato (`TDRC`), udgivelsesdato (`TDRL`) og oprindelig
udgivelsesdato (`TDOR`). MusicBrainz-ID'er, albumtype, spornummer/-antal,
disknummer, genre, ISRC, komponist, label og indlejret cover gemmes, når
kilderne har oplysningerne. Compilationer bruger `Various Artists` som
albumkunstner og ID3's compilationsflag.
Album gemmes som
`Albumkunstner/Album/nummer - titel.mp3`, singler i bibliotekets rodmappe.
Det giver Navidrome og Subsonic-klienter som Symfonium velstrukturerede tags og
en almindelig album-mappestruktur. Manglende eksterne identifikatorer udfyldes
ikke ved løse titelmatch. En ny download erstatter filer med samme titel i
destinationsmappen i stedet for at oprette kopier med kunstner- eller
ID-suffiks. Hvis `SPOTIPY_CLIENT_ID` og `SPOTIPY_CLIENT_SECRET` allerede er
konfigureret, kan Spotify også supplere felterne ved et eksakt ISRC-match;
det er valgfrit og kræver ikke et betalt Spotify-abonnement.

Musikarkivarens **Music Assistant** kan søge i og anbefale spor fra det lokale
bibliotek ud fra titel, kunstner, album og genre. Den bruger en lille lokal
TF-IDF-lignende rangeringsmotor, som ikke henter modeller, bruger Ollama eller
sender bibliotekets metadata ud på nettet. Biblioteksanalysen viser også
metadatafelter, der mangler. Den første version finder musik, der allerede er
lokalt; anbefalinger fra eksterne kataloger og tjenester er ikke slået til.

Under **Find manglende kilder** kan Music Assistant gennemgå MP3-biblioteket
med YouTubes søgning og gemme mulige kilder i en genoptagelig statusfil. Titel
og kunstner sendes til YouTube som søgetermer; denne første kildeundersøgelse
søger ikke Spotify eller Tidal, da deres links ikke kan bruges som lydkilder af
downloadkøen. Match rangeres efter titel, kunstner og varighed. Søgningen ændrer
ikke lydfiler; kun efter særskilt bekræftelse kan stærke match sættes i
downloadkøen. Brug kun kilder, du har ret til at hente. Tvetydige match skal
gennemgås manuelt; dashboardet viser de seneste 200 fund, mens stærke match kan
sættes samlet i kø. Kildeundersøgelsen kører ét spor ad gangen med forsinkelse
mellem forespørgsler og fortsætter fra gemt status efter genstart.
`MUSIC_ASSISTANT_STATE_PATH` kan sættes til en persistent filsti; Compose bruger
`/app/data/music-assistant/discovery.json`.

På musikarkivarsiden kan biblioteket kontrolleres og gendannes fra
`_samlet_arkiv.txt`. Gendannelsen matcher og gennemgår alle gemte spor, retter
metadata og filplacering for eksisterende lydfiler uden at hente lyden igen, og
sætter kun spor, der mangler eller er beskadigede, i downloadkøen. Spor matches
først via kilde-id og derefter forsigtigt via titel/kunstner; tvetydige titelmatch
overskrives ikke. Oplysninger, der aldrig blev gemt i arkivdokumentet, kan ikke
gendannes fra dette dokument alene. Tydelige dubletter og genskabelige
beskadigede filer flyttes til `.restore-quarantine`; de slettes ikke permanent.
Behold arkivdokumentet sammen med den persistente musikmappe; uden dokumentet
kan manglende numre ikke hentes igen via deres gemte links. Nye arkiveringer gemmer nu også track-links
for numre, der fejler under download, så de kan prøves igen senere. For album
gemmer loggen albumnavn, albumkunstner, tracknummer og den oprindelige
album-playliste. Gendannelsen bruger dette til at rette albumtags og
mappeplacering, downloader manglende albumspor fra playlisten samlet og viser
spor, der mangler eller stadig ikke matcher den gemte albumstruktur.

Eksisterende filer kan også kontrolleres og repareres med library-fixeren. Kør
først forhåndsvisningen uden `--apply`; anvend derefter ændringerne, når den
viste flytning og navngivning ser rigtig ud:

```bash
python -m apps.music_archiver.library_fixer --directory ~/Musik
python -m apps.music_archiver.library_fixer --directory ~/Musik --apply
```

Efter ændringer i et allerede indekseret bibliotek skal Navidrome genindlæse
biblioteket, så dets kunstnerindeks opdateres.

Skrivebords-UI startes med `python main.py --desktop`. Chatten læser som standard
SARAHs svar højt med lokal dansk eSpeak-NG-tale; slå det fra med afkrydsningsfeltet.
Webdashboardet bruger også den lokale stemme som standard, og afspiller lyden i
browseren. Installér `espeak-ng` på serveren og `paplay` eller `aplay` til
skrivebordsafspilning. Terminalens stemmestyring startes med `python main.py
--voice` efter installation af `requirements.txt`; den kræver desuden PortAudio
og en mikrofon. Whisper-modellen hentes første gang og gemmes lokalt. Mikrofon-
optagelser sendes ikke til en ekstern tjeneste. Stemmeinput er ikke slået til i
servertilstand.

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

Musikarkivarens aktive indgangspunkt er `apps.music_archiver.archiver`; den
ældre `apps.music_archiver.legacy_downloader`-sti viderestiller til samme
implementering for bagudkompatibilitet. Musikindhold hentes fra YouTube og
YouTube Music. Spotify er kun en valgfri metadata-kilde via ISRC-match; Spotify
links kan ikke bruges til at hente lyd, og der kræves ikke et abonnement.

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
