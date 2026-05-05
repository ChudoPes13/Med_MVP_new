from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .dialog import DialogManager
from .llm import LlmClient
from .questions import QuestionBank
from .schedule import ScheduleBook
from .schemas import VadSettingsPayload
from .session_store import SessionStore
from .stt import WhisperStt
from .tts import SileroTts
from .vad import VadSegmenter, VadSettings


settings.logs_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(settings.logs_dir / "backend.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("medjarvis")

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://localhost:5173", "https://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

store = SessionStore(settings)
questions = QuestionBank(settings.question_style_path)
schedule = ScheduleBook(settings)
llm = LlmClient(settings)
stt = WhisperStt(settings)
tts = SileroTts(settings)
dialog = DialogManager(store, questions, schedule, llm, settings.symptom_map_path)
vad_settings = VadSettings.from_settings(settings)
segmenters: dict[str, VadSegmenter] = {}


@app.on_event("startup")
async def startup() -> None:
    log.info("MedJarvis backend starting")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.models_dir.mkdir(parents=True, exist_ok=True)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "gpu_required": settings.require_gpu,
        "stt_loaded": stt.loaded,
        "tts_loaded": tts.loaded,
        "llm_ok": await llm.health(),
        "vad_settings": vad_settings.asdict(),
    }


@app.get("/healthz")
async def healthz() -> dict:
    return await health()


@app.get("/clinics")
async def clinics() -> dict:
    cfg = json.loads(settings.clinic_config_path.read_text(encoding="utf-8"))
    return {"clinics": [cfg]}


@app.get("/sessions")
async def sessions() -> dict:
    return {"sessions": store.list_sessions()}


@app.get("/slots")
async def slots(specialty: str | None = None, q: str | None = None, limit: int = 20) -> dict:
    found = schedule.find_slots(specialty, q, limit=limit)
    return {"slots": [slot.asdict() for slot in found]}


@app.get("/admin/vad-settings")
async def get_vad_settings() -> dict:
    return vad_settings.asdict()


@app.post("/admin/vad-settings")
async def post_vad_settings(
    wlk_min_duration_real_silence: float = Query(vad_settings.wlk_min_duration_real_silence),
    vad_energy_threshold: float = Query(vad_settings.vad_energy_threshold),
    vad_silence_ratio: float = Query(vad_settings.vad_silence_ratio),
    silero_threshold: float = Query(vad_settings.silero_threshold),
    silero_speech_pad_ms: int = Query(vad_settings.silero_speech_pad_ms),
    silero_min_silence_ms: int = Query(vad_settings.silero_min_silence_ms),
) -> dict:
    global vad_settings
    payload = VadSettingsPayload(
        wlk_min_duration_real_silence=wlk_min_duration_real_silence,
        vad_energy_threshold=vad_energy_threshold,
        vad_silence_ratio=vad_silence_ratio,
        silero_threshold=silero_threshold,
        silero_speech_pad_ms=silero_speech_pad_ms,
        silero_min_silence_ms=silero_min_silence_ms,
    )
    vad_settings = VadSettings(**payload.model_dump())
    for segmenter in segmenters.values():
        segmenter.update_settings(vad_settings)
    log.info("VAD settings updated: %s", vad_settings.asdict())
    return vad_settings.asdict()


async def _send_assistant(ws: WebSocket, response: dict) -> None:
    await ws.send_json(response)
    await ws.send_json({"event": "slots_update", "slots": response["slots"], "phase": response["phase"]})
    if settings.tts_enabled:
        try:
            wav = await asyncio.to_thread(tts.synthesize_wav, response["text"])
            if wav:
                await ws.send_json({"event": "assistant_tts_start"})
                await ws.send_bytes(wav)
                await ws.send_json({"event": "assistant_tts_end"})
        except Exception as exc:
            log.warning("TTS failed: %s", exc)
            await ws.send_json({"event": "tts_error", "message": str(exc)})


async def _handle_transcript(ws: WebSocket, session_id: str, text: str) -> None:
    session = store.get_or_create(session_id, settings.clinic_id)
    if not text:
        await ws.send_json({"event": "empty_transcript"})
        return
    await ws.send_json({"event": "final_transcript", "text": text})
    response = await dialog.process_text(session, text)
    await _send_assistant(ws, response)


@app.websocket("/ws")
async def websocket_endpoint(
    ws: WebSocket,
    session_id: str | None = None,
    clinic_id: str | None = None,
) -> None:
    await ws.accept()
    sid = session_id or uuid.uuid4().hex
    session = store.get_or_create(sid, clinic_id or settings.clinic_id)
    segmenter = VadSegmenter(settings, vad_settings)
    segmenters[sid] = segmenter

    try:
        await ws.send_json({"event": "session_started", "session_id": sid, "vad_settings": vad_settings.asdict()})
        if not session.dialogue:
            greeting = dialog.start_message(session)
            await _send_assistant(
                ws,
                {
                    "event": "assistant_response",
                    "text": greeting,
                    "slots": session.slots,
                    "phase": session.phase,
                    "status": session.status,
                    "question_key": session.question_key,
                    "extra": session.extra,
                },
            )

        while True:
            message = await ws.receive()
            if "bytes" in message and message["bytes"] is not None:
                pcm = message["bytes"]
                speech = segmenter.accept_pcm16(pcm)
                if speech:
                    text = await asyncio.to_thread(stt.transcribe_pcm16, speech)
                    await _handle_transcript(ws, sid, text)
                continue

            if "text" in message and message["text"] is not None:
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    payload = {"event": "user_text", "text": message["text"]}

                event = payload.get("event")
                if event == "user_text":
                    await _handle_transcript(ws, sid, str(payload.get("text", "")))
                elif event == "flush_audio":
                    speech = segmenter.flush()
                    if speech:
                        text = await asyncio.to_thread(stt.transcribe_pcm16, speech)
                        await _handle_transcript(ws, sid, text)
                elif event == "ping":
                    await ws.send_json({"event": "pong"})
                else:
                    await ws.send_json({"event": "unknown_event", "payload": payload})
    except WebSocketDisconnect:
        log.info("WebSocket disconnected: %s", sid)
    finally:
        segmenters.pop(sid, None)

