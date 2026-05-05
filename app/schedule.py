from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings


DOCTOR_RE = re.compile(r"^\|\s*(d\d+)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")
DATE_RE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})")
SLOT_RE = re.compile(r"^-\s+\*\*(d\d+)\s+\([^)]*\)\*\*:\s*(.+)$")

WEEKDAYS = {
    "понедельник": 0,
    "понедельника": 0,
    "вторник": 1,
    "вторника": 1,
    "среда": 2,
    "среду": 2,
    "четверг": 3,
    "четверга": 3,
    "пятница": 4,
    "пятницу": 4,
    "суббота": 5,
    "субботу": 5,
    "воскресенье": 6,
}

MONTHS_RU = [
    "",
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

HOUR_WORDS = {
    "один": 1,
    "одного": 1,
    "два": 2,
    "двух": 2,
    "три": 3,
    "трех": 3,
    "четыре": 4,
    "четырех": 4,
    "пять": 5,
    "пяти": 5,
    "шесть": 6,
    "шести": 6,
    "семь": 7,
    "семи": 7,
    "восемь": 8,
    "восьми": 8,
    "девять": 9,
    "девяти": 9,
    "десять": 10,
    "десяти": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
}

SPECIALTY_ALIASES = {
    "лор": "лор",
    "отоларинголог": "лор",
    "оториноларинголог": "лор",
    "ухо горло нос": "лор",
    "зубной": "стоматолог",
    "стоматолог": "стоматолог",
    "кардио": "кардиолог",
}


@dataclass(frozen=True)
class Doctor:
    id: str
    fio: str
    specialty: str
    cabinet: str


@dataclass(frozen=True)
class ScheduleSlot:
    doctor: Doctor
    starts_at: datetime
    source: str = "schedule"

    def asdict(self) -> dict:
        return {
            "doctor_id": self.doctor.id,
            "doctor_fio": self.doctor.fio,
            "specialty": self.doctor.specialty,
            "cabinet": self.doctor.cabinet,
            "starts_at": self.starts_at.isoformat(timespec="minutes"),
            "human": self.human(),
            "source": self.source,
        }

    def human(self) -> str:
        d = self.starts_at
        return f"{d.day} {MONTHS_RU[d.month]} в {d:%H:%M}, {self.doctor.specialty}, {self.doctor.fio}"


class ScheduleBook:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.tz = ZoneInfo(settings.timezone)
        self.doctors: dict[str, Doctor] = {}
        self.slots: list[ScheduleSlot] = []
        self._load(settings.schedule_markdown_path)

    def _load(self, path: Path) -> None:
        text = path.read_text(encoding="utf-8")
        source_slots: list[tuple[date, str, str]] = []
        current_date: date | None = None

        for line in text.splitlines():
            doc_match = DOCTOR_RE.match(line)
            if doc_match and doc_match.group(1) != "ID":
                doctor_id, fio, specialty, cabinet = [part.strip() for part in doc_match.groups()]
                self.doctors[doctor_id] = Doctor(doctor_id, fio, specialty.lower(), cabinet)
                continue

            date_match = DATE_RE.match(line)
            if date_match:
                current_date = date.fromisoformat(date_match.group(1))
                continue

            slot_match = SLOT_RE.match(line)
            if slot_match and current_date:
                doctor_id = slot_match.group(1)
                times = [t.strip() for t in slot_match.group(2).split(",") if t.strip()]
                for raw_time in times:
                    if re.match(r"^\d{1,2}:\d{2}$", raw_time):
                        source_slots.append((current_date, doctor_id, raw_time))

        if not source_slots:
            return

        first_source = min(item[0] for item in source_slots)
        target_first = self._next_same_weekday(first_source.weekday())
        offset = target_first - first_source

        seen: set[tuple[str, datetime]] = set()
        for source_day, doctor_id, raw_time in source_slots:
            doctor = self.doctors.get(doctor_id)
            if not doctor:
                continue
            shifted_day = source_day + offset
            hh, mm = [int(part) for part in raw_time.split(":")]
            starts_at = datetime.combine(shifted_day, time(hh, mm), tzinfo=self.tz)
            key = (doctor_id, starts_at)
            if key in seen:
                continue
            seen.add(key)
            self.slots.append(ScheduleSlot(doctor=doctor, starts_at=starts_at))

        self.slots.sort(key=lambda slot: slot.starts_at)

    def _next_same_weekday(self, weekday: int) -> date:
        today = datetime.now(self.tz).date()
        delta = (weekday - today.weekday()) % 7
        if delta == 0:
            delta = 7
        return today + timedelta(days=delta)

    def specialties(self) -> list[str]:
        return sorted({doctor.specialty for doctor in self.doctors.values()})

    def normalize_specialty(self, text: str | None) -> str | None:
        if not text:
            return None
        raw = text.lower().strip()
        for alias, specialty in SPECIALTY_ALIASES.items():
            if alias in raw:
                return specialty
        for specialty in self.specialties():
            if specialty in raw:
                return specialty
        return raw if raw in self.specialties() else None

    def find_slots(self, specialty: str | None, desired_time_text: str | None, limit: int = 3) -> list[ScheduleSlot]:
        now = datetime.now(self.tz)
        specialty = self.normalize_specialty(specialty)
        dates = self._target_dates(desired_time_text)
        window = self._target_time_window(desired_time_text)

        # Test MVP mode: any requested day/time is bookable. Keep real doctors,
        # but do not block the flow on the demo schedule being sparse or shifted.
        flexible_slots = self._flexible_requested_slots(specialty, desired_time_text, limit)
        if flexible_slots:
            return flexible_slots

        candidates = [slot for slot in self.slots if slot.starts_at >= now]
        if specialty:
            candidates = [slot for slot in candidates if slot.doctor.specialty == specialty]
        if dates:
            date_set = set(dates)
            candidates = [slot for slot in candidates if slot.starts_at.date() in date_set]
        if window:
            start_h, end_h = window
            candidates = [slot for slot in candidates if start_h <= slot.starts_at.hour + slot.starts_at.minute / 60 <= end_h]

        if not candidates and dates:
            candidates = [slot for slot in self.slots if slot.starts_at >= now]
            if specialty:
                candidates = [slot for slot in candidates if slot.doctor.specialty == specialty]
            if window:
                start_h, end_h = window
                candidates = [slot for slot in candidates if start_h <= slot.starts_at.hour + slot.starts_at.minute / 60 <= end_h]

        return candidates[:limit]

    def _flexible_requested_slots(
        self,
        specialty: str | None,
        desired_time_text: str | None,
        limit: int,
    ) -> list[ScheduleSlot]:
        if not desired_time_text:
            return []
        doctor = self._pick_doctor(specialty)
        if not doctor:
            return []
        start = self._target_start_datetime(desired_time_text)
        return [
            ScheduleSlot(
                doctor=doctor,
                starts_at=start + timedelta(minutes=20 * idx),
                source="test_flexible",
            )
            for idx in range(max(1, limit))
        ]

    def _pick_doctor(self, specialty: str | None) -> Doctor | None:
        doctors = sorted(self.doctors.values(), key=lambda doctor: doctor.id)
        if specialty:
            for doctor in doctors:
                if doctor.specialty == specialty:
                    return doctor
        for doctor in doctors:
            if doctor.specialty == "терапевт":
                return doctor
        return doctors[0] if doctors else None

    def _target_start_datetime(self, text: str) -> datetime:
        today = datetime.now(self.tz).date()
        dates = self._target_dates(text)
        target_date = dates[0] if dates else today
        target_time = self._target_time(text)
        starts_at = datetime.combine(target_date, target_time, tzinfo=self.tz)
        if starts_at < datetime.now(self.tz):
            starts_at += timedelta(days=1)
        return starts_at

    def _target_time(self, text: str) -> time:
        raw = text.lower()
        if "полноч" in raw:
            return time(0, 0)
        if "полден" in raw or "полдн" in raw:
            return time(12, 0)

        hour: int | None = None
        minute = 0
        numeric = re.search(r"\b(?:в|к|на|после|до)?\s*(\d{1,2})(?::(\d{2}))?\b", raw)
        if numeric:
            hour = int(numeric.group(1))
            minute = int(numeric.group(2) or 0)
        else:
            for word, value in HOUR_WORDS.items():
                if re.search(rf"\b{word}\b", raw):
                    hour = value
                    break

        if hour is None:
            if "утр" in raw:
                return time(9, 0)
            if "вечер" in raw:
                return time(18, 0)
            if "днем" in raw or "днём" in raw or "день" in raw or "после" in raw:
                return time(14, 0)
            return time(9, 0)

        if ("вечер" in raw or "после" in raw or "дня" in raw) and hour <= 7:
            hour += 12
        if "ноч" not in raw and hour == 12 and "вечер" not in raw:
            hour = 12
        hour = max(0, min(23, hour))
        minute = max(0, min(59, minute))
        return time(hour, minute)

    def _target_dates(self, text: str | None) -> list[date]:
        if not text:
            return []
        raw = text.lower()
        today = datetime.now(self.tz).date()
        if "послезавтра" in raw:
            return [today + timedelta(days=2)]
        if "завтра" in raw:
            return [today + timedelta(days=1)]
        if "сегодня" in raw:
            return [today]

        exact = re.search(r"\b(\d{1,2})[.\-/](\d{1,2})(?:[.\-/](\d{2,4}))?\b", raw)
        if exact:
            day = int(exact.group(1))
            month = int(exact.group(2))
            year = int(exact.group(3) or today.year)
            if year < 100:
                year += 2000
            try:
                target = date(year, month, day)
                if target < today:
                    target = date(today.year + 1, month, day)
                return [target]
            except ValueError:
                return []

        for word, weekday in WEEKDAYS.items():
            if word in raw:
                delta = (weekday - today.weekday()) % 7
                if delta == 0 or "след" in raw:
                    delta += 7
                return [today + timedelta(days=delta)]
        return []

    def _target_time_window(self, text: str | None) -> tuple[float, float] | None:
        if not text:
            return None
        raw = text.lower()
        if "утр" in raw:
            return (8.0, 12.0)
        if "вечер" in raw:
            return (17.0, 21.0)
        if "днем" in raw or "днём" in raw or "день" in raw:
            return (12.0, 17.0)

        numeric = re.search(r"\b(?:в|к|на|после|до)?\s*(\d{1,2})(?::(\d{2}))?\b", raw)
        hour: int | None = None
        minute = 0
        if numeric:
            hour = int(numeric.group(1))
            minute = int(numeric.group(2) or 0)
        else:
            for word, value in HOUR_WORDS.items():
                if re.search(rf"\b{word}\b", raw):
                    hour = value
                    break

        if hour is None:
            return None
        if ("вечер" in raw or "после" in raw or "дня" in raw) and hour <= 7:
            hour += 12
        if "после" in raw:
            return (float(hour) + minute / 60, 21.0)
        if "до " in raw:
            return (8.0, float(hour) + minute / 60)
        center = float(hour) + minute / 60
        return (max(8.0, center - 0.34), min(21.0, center + 0.34))
