from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .llm import LlmClient
from .questions import QuestionBank
from .schedule import ScheduleBook, ScheduleSlot
from .session_store import CallSession, SessionStore


YES_WORDS = {"да", "ага", "угу", "верно", "правильно", "подходит", "подойдет", "подойдёт", "хорошо", "ок", "окей"}
NO_WORDS = {"нет", "неа", "не подходит", "другое", "неверно", "не правильно"}
PHONE_RE = re.compile(r"(?:\+?7|8)?[\s\-()]*(\d{3})[\s\-()]*(\d{3})[\s\-]*(\d{2})[\s\-]*(\d{2})")

AGE_WORDS = {
    "один": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
}

UNSUPPORTED_INTENTS = {
    "cancel": ["отмен", "отказаться от записи"],
    "reschedule": ["перенес", "перенести", "перезапис"],
    "results": ["результат", "анализы готовы"],
    "price": ["сколько стоит", "цена", "стоимость", "прайс"],
    "medical_advice": ["можно принимать", "чем лечить", "что пить", "дозировка", "назнач"],
}

EMERGENCY_PATTERNS = [
    r"боль.*груд",
    r"давит.*груд",
    r"не.*могу.*дыш",
    r"задыха",
    r"потер.*созн",
    r"инсульт",
    r"онемел.*рук",
    r"кровотеч",
    r"очень.*плохо",
]


class DialogManager:
    def __init__(
        self,
        store: SessionStore,
        questions: QuestionBank,
        schedule: ScheduleBook,
        llm: LlmClient,
        symptom_map_path: Path,
    ):
        self.store = store
        self.questions = questions
        self.schedule = schedule
        self.llm = llm
        self.symptom_rules = json.loads(symptom_map_path.read_text(encoding="utf-8"))

    def start_message(self, session: CallSession) -> str:
        msg = self.questions.get("ask_name", self._ctx(session))
        self.store.append_message(session, "assistant", msg, {"question_key": "ask_name"})
        self.store.save(session)
        return msg

    async def process_text(self, session: CallSession, text: str) -> dict[str, Any]:
        cleaned = self._clean_text(text)
        if not cleaned:
            msg = self.questions.get("ask_repeat", self._ctx(session))
            self.store.append_message(session, "assistant", msg, {"question_key": "ask_repeat"})
            self.store.save(session)
            return self._response(session, msg)

        session.turns += 1
        self.store.append_message(session, "user", cleaned)
        self.store.log_event(session, "user_text", {"text": cleaned})

        deterministic = self._extract_rules(cleaned, session)
        llm_data = await self.llm.extract(cleaned, session.slots, self.schedule.specialties())
        self._merge_slots(session, deterministic)
        self._merge_slots(session, llm_data)

        reply = self._advance(session, cleaned, deterministic, llm_data)
        self.store.append_message(session, "assistant", reply["text"], {"question_key": session.question_key})
        self.store.save(session)
        return reply

    def _advance(
        self,
        session: CallSession,
        text: str,
        deterministic: dict[str, Any],
        llm_data: dict[str, Any],
    ) -> dict[str, Any]:
        confirmation = self._boolish(deterministic.get("confirmation"), llm_data.get("confirmation"))
        denial = self._boolish(deterministic.get("denial"), llm_data.get("denial"))

        if self._detect_unsupported(text):
            session.phase = "needs_operator"
            session.status = "needs_operator"
            session.question_key = "operator_handoff"
            return self._response(session, self.questions.get("operator_handoff", self._ctx(session)))

        if self._detect_emergency(text, session):
            session.phase = "needs_operator"
            session.status = "needs_operator"
            session.question_key = "emergency_redirect"
            return self._response(session, self.questions.get("emergency_redirect", self._ctx(session)))

        if session.phase == "awaiting_slot_confirmation":
            if confirmation:
                pending = session.extra.pop("pending_slot", None)
                if pending:
                    session.slots["appointment"] = pending
                    session.slots["appointment_datetime_iso"] = pending["starts_at"]
                    session.phase = "collecting"
                else:
                    session.phase = "collecting"
            elif denial:
                session.extra.pop("pending_slot", None)
                session.slots.pop("desired_time_text", None)
                session.phase = "collecting"
                session.question_key = "ask_datetime"
                return self._response(session, self.questions.get("ask_datetime", self._ctx(session)))
            else:
                session.question_key = "ask_datetime_confirm"
                return self._response(session, self.questions.get("ask_datetime_confirm", self._ctx(session)))

        if session.phase == "awaiting_final_confirmation":
            if confirmation:
                session.phase = "finalized"
                session.status = "finalized"
                session.question_key = "final_success"
                return self._response(session, self.questions.get("final_success", self._ctx(session)))
            if denial:
                session.phase = "needs_operator"
                session.status = "needs_operator"
                session.question_key = "operator_handoff"
                return self._response(session, self.questions.get("operator_handoff", self._ctx(session)))
            session.question_key = "ask_final_confirmation"
            return self._response(session, self.questions.get("ask_final_confirmation", self._ctx(session)))

        missing_key = self._next_missing_question(session)
        if missing_key:
            session.question_key = missing_key
            return self._response(session, self.questions.get(missing_key, self._ctx(session)))

        if not session.slots.get("appointment"):
            specialty = session.slots.get("specialty")
            desired = session.slots.get("desired_time_text")
            slots = self.schedule.find_slots(specialty, desired, limit=3)
            if not slots:
                session.slots.pop("desired_time_text", None)
                session.question_key = "no_slots"
                return self._response(session, self.questions.get("no_slots", self._ctx(session)))
            chosen = slots[0].asdict()
            session.extra["pending_slot"] = chosen
            session.extra["alternatives"] = [slot.asdict() for slot in slots[1:]]
            session.phase = "awaiting_slot_confirmation"
            session.question_key = "ask_datetime_confirm"
            return self._response(session, self.questions.get("ask_datetime_confirm", self._ctx(session)))

        if not session.slots.get("phone"):
            session.question_key = "ask_phone"
            return self._response(session, self.questions.get("ask_phone", self._ctx(session)))

        session.phase = "awaiting_final_confirmation"
        session.question_key = "ask_final_confirmation"
        return self._response(session, self.questions.get("ask_final_confirmation", self._ctx(session)))

    def _next_missing_question(self, session: CallSession) -> str | None:
        slots = session.slots
        if not slots.get("fio"):
            return "ask_name"
        if not slots.get("complaint"):
            return "ask_complaint"
        if not slots.get("age"):
            return "ask_age"
        if not slots.get("specialty"):
            inferred = self._infer_specialty(slots.get("complaint", ""))
            if inferred:
                slots["specialty"] = inferred
            else:
                return "ask_specialty"
        if not slots.get("desired_time_text"):
            return "ask_datetime"
        return None

    def _extract_rules(self, text: str, session: CallSession) -> dict[str, Any]:
        data: dict[str, Any] = {}
        lower = text.lower()

        phone = self._parse_phone(text)
        if phone:
            data["phone"] = phone

        age = self._parse_age(lower)
        if age:
            data["age"] = age

        specialty = self.schedule.normalize_specialty(lower)
        if specialty:
            data["requested_specialty"] = specialty
            data["specialty"] = specialty

        if self._looks_like_confirmation(lower):
            data["confirmation"] = True
        if self._looks_like_denial(lower):
            data["denial"] = True

        intent = self._detect_unsupported(lower)
        if intent:
            data["intent"] = intent

        if session.question_key == "ask_name" and not data.get("fio"):
            fio = self._parse_name(text)
            if fio:
                data["fio"] = fio

        if session.question_key in {"ask_complaint", "ask_specialty"} or self._has_symptom_words(lower):
            complaint = self._parse_complaint(text)
            if complaint:
                data["complaint"] = complaint
                inferred = self._infer_specialty(complaint)
                if inferred:
                    data["specialty"] = inferred

        if session.question_key == "ask_datetime" or self._has_datetime_words(lower):
            data["desired_time_text"] = text

        if session.question_key == "ask_age" and not data.get("age"):
            data["age"] = self._parse_age_from_short_answer(lower)

        return {k: v for k, v in data.items() if v not in (None, "", [])}

    def _merge_slots(self, session: CallSession, data: dict[str, Any]) -> None:
        mapping = {
            "fio": "fio",
            "complaint": "complaint",
            "age": "age",
            "phone": "phone",
            "desired_time_text": "desired_time_text",
            "requested_specialty": "specialty",
            "specialty": "specialty",
            "requested_doctor": "doctor_name",
        }
        for src, dst in mapping.items():
            value = data.get(src)
            if value in (None, "", False):
                continue
            if dst == "specialty":
                value = self.schedule.normalize_specialty(str(value)) or value
            if dst == "phone":
                value = self._parse_phone(str(value)) or value
            session.slots[dst] = value

    def _parse_phone(self, text: str) -> str | None:
        match = PHONE_RE.search(text)
        if not match:
            digits = re.sub(r"\D+", "", text)
            if len(digits) == 11 and digits[0] in {"7", "8"}:
                return "+7" + digits[1:]
            if len(digits) == 10:
                return "+7" + digits
            return None
        return "+7" + "".join(match.groups())

    def _parse_age(self, lower: str) -> int | None:
        match = re.search(r"\b(?:мне|возраст)?\s*(\d{1,3})\s*(?:год|года|лет)\b", lower)
        if match:
            age = int(match.group(1))
            return age if 0 < age < 120 else None
        return self._parse_age_from_short_answer(lower)

    def _parse_age_from_short_answer(self, lower: str) -> int | None:
        numeric = re.fullmatch(r"\D*(\d{1,3})\D*", lower.strip())
        if numeric:
            age = int(numeric.group(1))
            return age if 0 < age < 120 else None
        for word, value in AGE_WORDS.items():
            if re.search(rf"\b{word}\b", lower):
                return value
        return None

    def _parse_name(self, text: str) -> str | None:
        cleaned = re.sub(r"(?i)\b(здравствуйте|добрый день|меня зовут|зовут|это|я)\b", " ", text)
        cleaned = re.sub(r"[^А-Яа-яЁёA-Za-z\-\s]", " ", cleaned)
        words = [w.strip(" -").capitalize() for w in cleaned.split() if len(w.strip(" -")) >= 2]
        if not words or len(words) > 4:
            return None
        blocked = {"Хочу", "Нужно", "Запишите", "Болит", "Можно", "Мне"}
        if words[0] in blocked:
            return None
        return " ".join(words)

    def _parse_complaint(self, text: str) -> str | None:
        cleaned = text.strip()
        cleaned = re.sub(r"(?i)^(у меня|меня беспокоит|беспокоит|болит|жалоба)\s+", "", cleaned)
        return cleaned[:300] if len(cleaned) >= 3 else None

    def _infer_specialty(self, complaint: str) -> str | None:
        lower = complaint.lower()
        best: tuple[int, str] | None = None
        for item in self.symptom_rules:
            for pattern in item.get("patterns", []):
                if re.search(pattern, lower):
                    score = int(item.get("priority", 0))
                    if best is None or score > best[0]:
                        best = (score, item["specialty"])
        return best[1] if best else None

    def _detect_unsupported(self, text: str) -> str | None:
        lower = text.lower()
        for intent, needles in UNSUPPORTED_INTENTS.items():
            if any(needle in lower for needle in needles):
                return intent
        return None

    def _detect_emergency(self, text: str, session: CallSession) -> bool:
        lower = f"{text} {session.slots.get('complaint', '')}".lower()
        if any(re.search(pattern, lower) for pattern in EMERGENCY_PATTERNS):
            return True
        return False

    def _has_symptom_words(self, lower: str) -> bool:
        return any(word in lower for word in ["бол", "температур", "давлен", "сып", "каш", "тошн", "круж", "зуб"])

    def _has_datetime_words(self, lower: str) -> bool:
        return any(
            word in lower
            for word in [
                "сегодня",
                "завтра",
                "послезавтра",
                "понедельник",
                "вторник",
                "сред",
                "четверг",
                "пятниц",
                "суббот",
                "утр",
                "вечер",
                "после",
            ]
        )

    def _looks_like_confirmation(self, lower: str) -> bool:
        raw = lower.strip(" .,!?:;")
        return raw in YES_WORDS or any(raw.startswith(word + " ") for word in YES_WORDS)

    def _looks_like_denial(self, lower: str) -> bool:
        raw = lower.strip(" .,!?:;")
        return raw in NO_WORDS or any(word in raw for word in NO_WORDS)

    def _boolish(self, *values: Any) -> bool:
        return any(value is True for value in values)

    def _clean_text(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    def _ctx(self, session: CallSession) -> dict[str, Any]:
        pending = session.extra.get("pending_slot") or {}
        appointment = session.slots.get("appointment") or pending
        return {
            "session_id": session.session_id,
            "turns": session.turns,
            "fio": session.slots.get("fio", ""),
            "complaint": session.slots.get("complaint", ""),
            "age": session.slots.get("age", ""),
            "phone": session.slots.get("phone", ""),
            "specialty": session.slots.get("specialty", ""),
            "slot_human": appointment.get("human", ""),
        }

    def _response(self, session: CallSession, text: str) -> dict[str, Any]:
        return {
            "event": "assistant_response",
            "text": text,
            "slots": session.slots,
            "phase": session.phase,
            "status": session.status,
            "question_key": session.question_key,
            "extra": session.extra,
        }
