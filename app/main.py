from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from contextlib import suppress

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

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
warmup_state: dict[str, str] = {"status": "not_started"}


@app.on_event("startup")
async def startup() -> None:
    log.info("MedJarvis backend starting")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.models_dir.mkdir(parents=True, exist_ok=True)
    if settings.warmup_on_startup:
        await warmup_models()


async def warmup_models() -> None:
    warmup_state.clear()
    warmup_state.update({"status": "running"})
    log.info("Model warm-up started")

    try:
        await asyncio.to_thread(VadSegmenter, settings, vad_settings)
        warmup_state["vad"] = "ok"
    except Exception as exc:
        warmup_state["vad"] = f"failed: {exc}"
        log.warning("VAD warm-up failed: %s", exc)

    try:
        silence = bytes(settings.pcm_sample_rate * 2)
        await asyncio.to_thread(stt.transcribe_pcm16, silence)
        warmup_state["stt"] = "ok"
    except Exception as exc:
        warmup_state["stt"] = f"failed: {exc}"
        log.warning("STT warm-up failed: %s", exc)

    try:
        if settings.tts_enabled:
            await asyncio.to_thread(tts.synthesize_wav, "Здравствуйте.")
        warmup_state["tts"] = "ok"
    except Exception as exc:
        warmup_state["tts"] = f"failed: {exc}"
        log.warning("TTS warm-up failed: %s", exc)

    try:
        if await llm.health():
            await llm.chat(
                [
                    {"role": "system", "content": "Ответь одним словом."},
                    {"role": "user", "content": "Готов?"},
                ],
                temperature=0.0,
                max_tokens=8,
            )
            warmup_state["llm"] = "ok"
        else:
            warmup_state["llm"] = "unavailable"
    except Exception as exc:
        warmup_state["llm"] = f"failed: {exc}"
        log.warning("LLM warm-up failed: %s", exc)

    warmup_state["status"] = "done"
    log.info("Model warm-up finished: %s", warmup_state)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "gpu_required": settings.require_gpu,
        "stt_loaded": stt.loaded,
        "tts_loaded": tts.loaded,
        "llm_ok": await llm.health(),
        "warmup": warmup_state,
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
    if response.get("event") == "conversation_closed":
        await ws.send_json(response)
        return
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
    if response.get("should_close"):
        await ws.send_json({"event": "conversation_closed", "status": response.get("status")})
        await ws.close(code=1000, reason="finalized")


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
    response_task: asyncio.Task | None = None

    async def cancel_response(reason: str) -> None:
        nonlocal response_task
        if response_task and not response_task.done():
            response_task.cancel()
            with suppress(asyncio.CancelledError):
                await response_task
            log.info("Cancelled assistant response for %s: %s", sid, reason)
        response_task = None

    def track_response(task: asyncio.Task) -> None:
        def _done(done: asyncio.Task) -> None:
            if done.cancelled():
                return
            exc = done.exception()
            if exc:
                log.warning("Assistant response task failed for %s: %s", sid, exc)

        task.add_done_callback(_done)

    async def start_response(text: str, reason: str) -> None:
        nonlocal response_task
        await cancel_response(reason)
        response_task = asyncio.create_task(_handle_transcript(ws, sid, text))
        track_response(response_task)

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
                    await start_response(text, "new_audio_transcript")
                continue

            if "text" in message and message["text"] is not None:
                try:
                    payload = json.loads(message["text"])
                except json.JSONDecodeError:
                    payload = {"event": "user_text", "text": message["text"]}

                event = payload.get("event")
                if event == "user_text":
                    await start_response(str(payload.get("text", "")), "new_user_text")
                elif event == "flush_audio":
                    speech = segmenter.flush()
                    if speech:
                        text = await asyncio.to_thread(stt.transcribe_pcm16, speech)
                        await start_response(text, "flush_audio")
                elif event == "barge_in":
                    await cancel_response(str(payload.get("reason", "barge_in")))
                    await ws.send_json({"event": "barge_in_ack"})
                elif event == "ping":
                    await ws.send_json({"event": "pong"})
                else:
                    await ws.send_json({"event": "unknown_event", "payload": payload})
    except WebSocketDisconnect:
        log.info("WebSocket disconnected: %s", sid)
    finally:
        await cancel_response("disconnect")
        segmenters.pop(sid, None)


if settings.static_dir.exists():
    app.mount("/", StaticFiles(directory=settings.static_dir, html=True), name="frontend")
