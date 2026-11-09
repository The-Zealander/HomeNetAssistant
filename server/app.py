"""Expose SARAH, Home Assistant, hardware, and music features through HTTP routes."""

import hmac
import logging
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import ollama
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from dotenv import load_dotenv
from mutagen import MutagenError
from mutagen.id3 import ID3
from mutagen.mp3 import HeaderNotFoundError, MP3

from ai_core.SARAH import SarahV0_1
from ai_core.voice_io import LokalStemme
from apps.hardware_inventory.storage import DATA_FIL, HardwareLagerDB
from apps.music_archiver.library_restore import (
    audit_album_integrity,
    parse_recovery_document,
    restore_library,
)
from apps.music_archiver.music_assistant import MusicAssistant, MusicSourceDiscovery
from apps.music_archiver.queue import MusicQueue, MusicQueueBusyError
from integrations.home_assistant.client import HomeAssistantAPI


load_dotenv(Path(__file__).resolve().parents[1] / ".env")

MAX_SESSIONER = 128
SESSION_TIMEOUT = 60 * 60
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    besked: str = Field(min_length=1, max_length=2000)
    session_id: str | None = Field(default=None, min_length=1, max_length=64)


class VoiceSynthesisRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


class MusicQueueRequest(BaseModel):
    urls: list[str]
    download_video: bool = False


class HomeAssistantActionRequest(BaseModel):
    entity_id: str = Field(min_length=3, max_length=128)
    action: str
    volume_level: float | None = Field(default=None, ge=0, le=1)


class HomeAssistantSpeakRequest(BaseModel):
    media_player_entity_id: str = Field(min_length=3, max_length=128)
    tts_entity_id: str = Field(min_length=3, max_length=128)
    message: str = Field(min_length=1, max_length=1000)


class SarahServer:
    def __init__(self):
        self.sarah_template = SarahV0_1(server_mode=True)
        try:
            self.home_assistant = HomeAssistantAPI()
        except ValueError as error:
            self.home_assistant = None
            logger.warning("Home Assistant er ikke konfigureret: %s", error)
        self.music_queue = MusicQueue()
        self.music_assistant = MusicAssistant()
        discovery_state_path = Path(
            os.environ.get(
                "MUSIC_ASSISTANT_STATE_PATH",
                Path(__file__).resolve().parents[1]
                / "data"
                / "music_assistant"
                / "discovery.json",
            )
        )
        self.music_discovery = MusicSourceDiscovery(discovery_state_path)
        self.sessioner = OrderedDict()
        self.lock = threading.RLock()

    def get_sarah(self, session_id):
        if session_id is None:
            session_id = str(uuid.uuid4())
        session_id = session_id.strip()
        if not session_id:
            session_id = str(uuid.uuid4())

        nu = time.monotonic()
        with self.lock:
            for gammel_id, (_, sidst_aktiv) in list(self.sessioner.items()):
                if nu - sidst_aktiv > SESSION_TIMEOUT:
                    del self.sessioner[gammel_id]

            session = self.sessioner.get(session_id)
            if session is None:
                if len(self.sessioner) >= MAX_SESSIONER:
                    raise HTTPException(
                        status_code=429,
                        detail="Serveren har nået sin grænse for aktive samtaler.",
                    )
                sarah = SarahV0_1(server_mode=True)
            else:
                sarah, _ = session
            sarah.noter = sarah._load_json(sarah.notes_path)
            self.sessioner[session_id] = (sarah, nu)
            self.sessioner.move_to_end(session_id)
            return session_id, sarah


@asynccontextmanager
async def lifespan(app):
    try:
        runtime = SarahServer()
    except (ValueError, OSError) as fejl:
        raise RuntimeError(f"Serverkonfigurationen er ugyldig: {fejl}") from fejl

    app.state.runtime = runtime
    #runtime.home_assistant.check_connection()
    yield


app = FastAPI(
    title="Lokalsystemet",
    description="Lokal server-API til SARAH, musikarkivaren og Home Assistant.",
    version="0.1.0",
    lifespan=lifespan,
)


def _runtime():
    runtime = getattr(app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="Serveren starter stadig.")
    return runtime


def _check_token(authorization):
    token = os.environ.get("SARAH_API_TOKEN")
    if not token:
        return
    expected = f"Bearer {token}"
    if authorization is None or not hmac.compare_digest(
        authorization.encode("utf-8"),
        expected.encode("utf-8"),
    ):
        raise HTTPException(status_code=401, detail="Ugyldig eller manglende API-nøgle.")


@app.get("/music", include_in_schema=False)
def music_dashboard():
    dashboard_file = (
        Path(__file__).resolve().parents[1]
        / "apps"
        / "music_archiver"
        / "dashboard.html"
    )
    return FileResponse(
        dashboard_file,
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/", include_in_schema=False)
def web_dashboard():
    dashboard_file = (
        Path(__file__).resolve().parents[1] / "apps" / "web" / "dashboard.html"
    )
    return FileResponse(
        dashboard_file,
        media_type="text/html",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/system/status")
def system_status(authorization: str | None = Header(default=None)):
    _check_token(authorization)
    return _status(_runtime())


@app.get("/api/home-assistant/entities")
def home_assistant_entities(authorization: str | None = Header(default=None)):
    _check_token(authorization)
    home_assistant = _runtime().home_assistant
    if home_assistant is None:
        raise HTTPException(status_code=503, detail="Home Assistant er ikke konfigureret.")

    entities = home_assistant.get_entities()
    if entities is None:
        raise HTTPException(status_code=502, detail="Kunne ikke hente enheder fra Home Assistant.")
    return [
        {
            "entity_id": item.get("entity_id", ""),
            "name": item.get("attributes", {}).get("friendly_name") or item.get("entity_id", ""),
            "state": item.get("state", "unknown"),
            "domain": item.get("entity_id", "").split(".", 1)[0],
            **(
                {"volume_level": item.get("attributes", {}).get("volume_level")}
                if item.get("entity_id", "").startswith("media_player.")
                else {}
            ),
        }
        for item in entities
        if item.get("entity_id")
    ]


@app.post("/api/home-assistant/speak")
def home_assistant_speak(
    request: HomeAssistantSpeakRequest,
    authorization: str | None = Header(default=None),
):
    _check_token(authorization)
    player_id = request.media_player_entity_id.strip()
    tts_id = request.tts_entity_id.strip()
    if not re.fullmatch(r"media_player\.[a-z0-9_]+", player_id):
        raise HTTPException(status_code=422, detail="Vælg en gyldig højttaler.")
    if not re.fullmatch(r"tts\.[a-z0-9_]+", tts_id):
        raise HTTPException(status_code=422, detail="Vælg en gyldig TTS-motor.")
    if not request.message.strip():
        raise HTTPException(status_code=422, detail="Beskeden må ikke være tom.")

    home_assistant = _runtime().home_assistant
    if home_assistant is None:
        raise HTTPException(status_code=503, detail="Home Assistant er ikke konfigureret.")

    entities = home_assistant.get_entities()
    if entities is None:
        raise HTTPException(status_code=502, detail="Kunne ikke hente enheder fra Home Assistant.")
    known_entities = {item.get("entity_id") for item in entities}
    if player_id not in known_entities:
        raise HTTPException(status_code=404, detail="Højttaleren blev ikke fundet.")
    if tts_id not in known_entities:
        raise HTTPException(status_code=404, detail="TTS-motoren blev ikke fundet.")
    if not home_assistant.call_service(
        "tts",
        "speak",
        entity_id=tts_id,
        extra_data={
            "media_player_entity_id": player_id,
            "message": request.message.strip(),
        },
    ):
        raise HTTPException(status_code=502, detail="Home Assistant kunne ikke afspille beskeden.")
    return {"media_player_entity_id": player_id, "status": "ok"}


@app.post("/api/home-assistant/action")
def home_assistant_action(
    request: HomeAssistantActionRequest,
    authorization: str | None = Header(default=None),
):
    _check_token(authorization)
    entity_id = request.entity_id.strip()
    if not re.fullmatch(r"[a-z0-9_]+\.[a-z0-9_]+", entity_id):
        raise HTTPException(status_code=422, detail="Ugyldigt entity_id.")
    domain = entity_id.split(".", 1)[0]
    allowed_services = {
        "light": {"turn_on", "turn_off", "toggle"},
        "switch": {"turn_on", "turn_off", "toggle"},
        "fan": {"turn_on", "turn_off", "toggle"},
        "input_boolean": {"turn_on", "turn_off", "toggle"},
        "media_player": {
            "turn_on",
            "turn_off",
            "media_play_pause",
            "media_stop",
            "volume_up",
            "volume_down",
            "volume_set",
        },
    }
    if request.action not in allowed_services.get(domain, set()):
        raise HTTPException(status_code=422, detail="Handlingen understøttes ikke for denne enhed.")
    if request.action == "volume_set" and request.volume_level is None:
        raise HTTPException(status_code=422, detail="Angiv et lydstyrkeniveau mellem 0 og 1.")
    if request.action != "volume_set" and request.volume_level is not None:
        raise HTTPException(status_code=422, detail="Lydstyrkeniveau kan kun bruges sammen med volume_set.")

    home_assistant = _runtime().home_assistant
    if home_assistant is None:
        raise HTTPException(status_code=503, detail="Home Assistant er ikke konfigureret.")
    entities = home_assistant.get_entities(domain_filter=(domain,))
    if entities is None:
        raise HTTPException(status_code=502, detail="Kunne ikke hente enheder fra Home Assistant.")
    if not any(item.get("entity_id") == entity_id for item in entities):
        raise HTTPException(status_code=404, detail="Enheden blev ikke fundet.")
    if request.action == "volume_set":
        success = home_assistant.call_service(
            domain,
            request.action,
            entity_id=entity_id,
            extra_data={"volume_level": request.volume_level},
        )
    else:
        success = home_assistant.call_service(domain, request.action, entity_id=entity_id)
    if not success:
        raise HTTPException(status_code=502, detail="Home Assistant kunne ikke udføre handlingen.")
    return {"entity_id": entity_id, "action": request.action, "status": "ok"}


@app.get("/api/hardware")
def hardware_inventory(q: str = "", authorization: str | None = Header(default=None)):
    _check_token(authorization)
    if len(q) > 200:
        raise HTTPException(status_code=422, detail="Søgeteksten må højst være 200 tegn.")
    try:
        database = HardwareLagerDB(DATA_FIL)
        items = database.search(q.strip(), database.load_data())
    except (OSError, ValueError) as error:
        logger.exception("Kunne ikke hente hardwarelageret.")
        raise HTTPException(status_code=503, detail="Hardwarelageret kunne ikke læses.") from error
    return [
        {key: value for key, value in item.items() if key != "search_text"}
        for item in items
    ]


def _music_library_summary():
    music_dir = Path(os.environ.get("MUSIC_DOWNLOAD_PATH", str(Path.home() / "Musik")))
    recovery_document = music_dir / "_samlet_arkiv.txt"
    total_files = 0
    corrupt_files = 0
    missing_covers = 0
    missing_album_artists = 0
    track_paths = {}
    if not music_dir.is_dir():
        return {
            "total_files": 0,
            "corrupt_files": 0,
            "missing_covers": 0,
            "missing_album_artists": 0,
            "duplicate_groups": 0,
            "recovery_document_found": recovery_document.is_file(),
            "saved_track_links": 0,
            "album_tracks_checked": 0,
            "album_tracks_missing": 0,
            "album_tracks_needing_repair": 0,
        }

    for root, _, filenames in os.walk(music_dir):
        for filename in filenames:
            if not filename.lower().endswith(".mp3"):
                continue
            total_files += 1
            file_path = os.path.join(root, filename)
            try:
                audio = MP3(file_path, ID3=ID3)
                tags = audio.tags
                if tags is None or not tags.getall("APIC"):
                    missing_covers += 1
                album_artist = tags.get("TPE2") if tags else None
                if not album_artist or not str(album_artist).strip():
                    missing_album_artists += 1
                artist = str(tags.get("TPE1", "")).casefold().strip() if tags else ""
                title = str(tags.get("TIT2", "")).casefold().strip() if tags else ""
                if artist and title:
                    track_paths.setdefault(
                        (artist, title, round(float(audio.info.length or 0))),
                        [],
                    ).append(file_path)
            except (HeaderNotFoundError, MutagenError, OSError, ValueError):
                corrupt_files += 1
    try:
        saved_track_links = len(parse_recovery_document(recovery_document)) if recovery_document.is_file() else 0
    except (OSError, UnicodeError):
        logger.exception("Kunne ikke læse musikbibliotekets arkivlog.")
        saved_track_links = 0
    try:
        album_health = (
            audit_album_integrity(music_dir)
            if recovery_document.is_file()
            else {
                "album_tracks_checked": 0,
                "album_tracks_missing": 0,
                "album_tracks_needing_repair": 0,
            }
        )
    except (OSError, UnicodeError):
        logger.exception("Kunne ikke kontrollere albumplaceringer i musikbiblioteket.")
        album_health = {
            "album_tracks_checked": 0,
            "album_tracks_missing": 0,
            "album_tracks_needing_repair": 0,
        }
    return {
        "total_files": total_files,
        "corrupt_files": corrupt_files,
        "missing_covers": missing_covers,
        "missing_album_artists": missing_album_artists,
        "duplicate_groups": sum(len(paths) > 1 for paths in track_paths.values()),
        "recovery_document_found": recovery_document.is_file(),
        "saved_track_links": saved_track_links,
        **album_health,
    }


@app.get("/api/music/queue")
def music_queue_status(authorization: str | None = Header(default=None)):
    _check_token(authorization)
    return _runtime().music_queue.snapshot()


@app.post("/api/music/queue")
def add_music_queue_items(
    request: MusicQueueRequest,
    authorization: str | None = Header(default=None),
):
    _check_token(authorization)
    try:
        added = _runtime().music_queue.add(
            request.urls,
            download_video=request.download_video,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"added": added, **_runtime().music_queue.snapshot()}


@app.post("/api/music/queue/pause")
def pause_music_queue(authorization: str | None = Header(default=None)):
    _check_token(authorization)
    return _runtime().music_queue.pause()


@app.post("/api/music/queue/resume")
def resume_music_queue(authorization: str | None = Header(default=None)):
    _check_token(authorization)
    return _runtime().music_queue.resume()


@app.delete("/api/music/queue/finished")
def clear_finished_music_queue(authorization: str | None = Header(default=None)):
    _check_token(authorization)
    return _runtime().music_queue.clear_finished()


@app.delete("/api/music/queue/{job_id}")
def remove_music_queue_item(job_id: str, authorization: str | None = Header(default=None)):
    _check_token(authorization)
    try:
        removed = _runtime().music_queue.remove(job_id)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if not removed:
        raise HTTPException(status_code=404, detail="Linket findes ikke i køen.")
    return _runtime().music_queue.snapshot()


@app.get("/api/music/library")
def music_library_health(authorization: str | None = Header(default=None)):
    _check_token(authorization)
    return _music_library_summary()


@app.get("/api/music/assistant")
def music_assistant(
    q: str = Query(default="", max_length=200),
    limit: int = Query(default=10, ge=1, le=25),
    refresh: bool = False,
    authorization: str | None = Header(default=None),
):
    _check_token(authorization)
    music_dir = Path(os.environ.get("MUSIC_DOWNLOAD_PATH", str(Path.home() / "Musik")))
    try:
        return _runtime().music_assistant.analyze(
            music_dir,
            query=q,
            limit=limit,
            refresh=refresh,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        logger.exception("Den lokale Music Assistant kunne ikke læse biblioteket.")
        raise HTTPException(
            status_code=503,
            detail="Musikbiblioteket kunne ikke analyseres.",
        ) from error


@app.get("/api/music/assistant/discovery")
def music_source_discovery_status(
    authorization: str | None = Header(default=None),
):
    _check_token(authorization)
    return _runtime().music_discovery.snapshot()


@app.post("/api/music/assistant/discovery/start")
def start_music_source_discovery(
    authorization: str | None = Header(default=None),
):
    _check_token(authorization)
    music_dir = Path(os.environ.get("MUSIC_DOWNLOAD_PATH", str(Path.home() / "Musik")))
    try:
        return _runtime().music_discovery.start(music_dir, refresh=True)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        logger.exception("Kildesøgningen kunne ikke gemme sin status.")
        raise HTTPException(
            status_code=500,
            detail="Kildesøgningen kunne ikke starte på grund af en filsystemfejl.",
        ) from error


@app.post("/api/music/assistant/discovery/pause")
def pause_music_source_discovery(
    authorization: str | None = Header(default=None),
):
    _check_token(authorization)
    return _runtime().music_discovery.pause()


@app.post("/api/music/assistant/discovery/resume")
def resume_music_source_discovery(
    authorization: str | None = Header(default=None),
):
    _check_token(authorization)
    try:
        return _runtime().music_discovery.resume()
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/music/assistant/discovery/queue-high-confidence")
def queue_music_source_matches(
    authorization: str | None = Header(default=None),
):
    _check_token(authorization)
    runtime = _runtime()
    try:
        return runtime.music_discovery.queue_high_confidence(runtime.music_queue)
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/music/library/restore")
def restore_music_library(authorization: str | None = Header(default=None)):
    _check_token(authorization)
    music_dir = Path(os.environ.get("MUSIC_DOWNLOAD_PATH", str(Path.home() / "Musik")))
    queue = _runtime().music_queue
    try:
        with queue.maintenance():
            return restore_library(str(music_dir), queue)
    except MusicQueueBusyError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        logger.exception("Musikbiblioteket kunne ikke gendannes.")
        raise HTTPException(
            status_code=500,
            detail="Musikbiblioteket kunne ikke gendannes på grund af en filsystemfejl.",
        ) from error


def _status(runtime):
    if runtime.home_assistant is None:
        home_assistant_status = "not_configured"
        home_assistant_error = "Angiv HA_URL og HA_TOKEN for at aktivere Home Assistant."
    else:
        connected = runtime.home_assistant.is_connected()
        home_assistant_status = "online" if connected else "offline"
        home_assistant_error = None if connected else "Kunne ikke forbinde til Home Assistant."

    ollama_host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    model = os.environ.get("SARAH_MODEL", "llama3.1")
    try:
        response = ollama.Client(host=ollama_host, timeout=2).list()
        installed_models = {
            item.model for item in response.models if item.model
        }
        ollama_status = (
            "ready"
            if model in installed_models or f"{model}:latest" in installed_models
            else "model_missing"
        )
    except (httpx.HTTPError, ollama.ResponseError, OSError):
        ollama_status = "unavailable"

    return {
        "status": "ready" if ollama_status == "ready" else "not_ready",
        "sarah": "ready",
        "sarah_model": model,
        "ollama": ollama_status,
        "home_assistant": home_assistant_status,
        "home_assistant_error": home_assistant_error,
        "active_conversations": len(runtime.sessioner),
        "conversation_limit": MAX_SESSIONER,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    status = _status(_runtime())
    if status["ollama"] != "ready":
        raise HTTPException(status_code=503, detail=status)
    return status


@app.get("/status")
def status():
    return _status(_runtime())


@app.post("/api/chat")
def chat(request: ChatRequest, authorization: str | None = Header(default=None)):
    _check_token(authorization)
    besked = request.besked.strip()
    if not besked:
        raise HTTPException(status_code=422, detail="Beskeden må ikke være tom.")

    runtime = _runtime()
    session_id, sarah = runtime.get_sarah(request.session_id)
    try:
        with runtime.lock:
            svar = sarah.snak(besked)
    except ollama.ResponseError as fejl:
        raise HTTPException(
            status_code=503,
            detail=f"Ollama kunne ikke bruge modellen '{sarah.model}'.",
        ) from fejl
    except httpx.HTTPError as fejl:
        raise HTTPException(
            status_code=503,
            detail="Ollama kunne ikke kontaktes. Kontrollér Ollama-tjenesten.",
        ) from fejl
    except ConnectionError as fejl:
        raise HTTPException(
            status_code=503,
            detail="Ollama kunne ikke kontaktes. Kontrollér Ollama-tjenesten.",
        ) from fejl
    return {"session_id": session_id, "svar": svar}


@app.post("/api/voice/speak")
def voice_speak(
    request: VoiceSynthesisRequest,
    authorization: str | None = Header(default=None),
):
    _check_token(authorization)
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Beskeden må ikke være tom.")
    try:
        audio = LokalStemme.generer_lyd(message)
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})
