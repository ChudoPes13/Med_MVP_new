from __future__ import annotations

import json
import random
import string
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings


@dataclass
class DialogueMessage:
    role: str
    text: str
    ts: str
    meta: dict = field(default_factory=dict)


@dataclass
class CallSession:
    session_id: str
    clinic_id: str
    created_at: str
    updated_at: str
    phase: str = "collecting"
    status: str = "in_progress"
    question_key: str = "ask_name"
    turns: int = 0
    slots: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    dialogue: list[DialogueMessage] = field(default_factory=list)
    event_log: list[dict] = field(default_factory=list)
    saved_path: str | None = None


class SessionStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, CallSession] = {}
        self.tz = ZoneInfo(settings.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def now_iso(self) -> str:
        return self.now().isoformat(timespec="seconds")

    def get_or_create(self, session_id: str, clinic_id: str) -> CallSession:
        if session_id in self._sessions:
            return self._sessions[session_id]
        now = self.now_iso()
        session = CallSession(
            session_id=session_id,
            clinic_id=clinic_id,
            created_at=now,
            updated_at=now,
        )
        self._sessions[session_id] = session
        self.save(session)
        return session

    def append_message(self, session: CallSession, role: str, text: str, meta: dict | None = None) -> None:
        session.dialogue.append(DialogueMessage(role=role, text=text, ts=self.now_iso(), meta=meta or {}))
        session.updated_at = self.now_iso()

    def log_event(self, session: CallSession, event: str, payload: dict | None = None) -> None:
        session.event_log.append({"event": event, "payload": payload or {}, "ts": self.now_iso()})
        session.updated_at = self.now_iso()

    def _filename_for(self, session: CallSession) -> Path:
        if session.saved_path:
            return Path(session.saved_path)
        stamp = self.now().strftime("%d-%m-%Y-%H%M")
        suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=3))
        path = self.settings.data_dir / f"{stamp}-{suffix}.json"
        session.saved_path = str(path)
        return path

    def save(self, session: CallSession) -> Path:
        session.updated_at = self.now_iso()
        path = self._filename_for(session)
        payload = asdict(session)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def list_sessions(self) -> list[dict]:
        result: list[dict] = []
        for path in sorted(self.settings.data_dir.glob("*.json"), reverse=True):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return result

