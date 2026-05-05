from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.dialog import DialogManager
from app.questions import QuestionBank
from app.schedule import ScheduleBook
from app.session_store import SessionStore


class FakeLlm:
    async def extract(self, text, slots, specialties):
        return {}


def make_dialog(tmp_path):
    test_settings = replace(settings, data_dir=tmp_path)
    store = SessionStore(test_settings)
    return (
        DialogManager(
            store=store,
            questions=QuestionBank(settings.question_style_path),
            schedule=ScheduleBook(settings),
            llm=FakeLlm(),
            symptom_map_path=settings.symptom_map_path,
        ),
        store,
    )


async def run_flow(dialog, session, turns):
    response = None
    for text in turns:
        response = await dialog.process_text(session, text)
    return response


def test_schedule_shift_has_future_slots():
    book = ScheduleBook(settings)
    assert book.find_slots("терапевт", "в субботу утром", limit=1)


def test_flexible_slot_ignores_demo_schedule():
    book = ScheduleBook(settings)
    slot = book.find_slots("гастроэнтеролог", "в воскресенье в 23:30", limit=1)[0]
    assert slot.source == "test_flexible"
    assert slot.doctor.specialty == "гастроэнтеролог"
    assert slot.starts_at.weekday() == 6
    assert slot.starts_at.hour == 23
    assert slot.starts_at.minute == 30


def test_basic_booking_flow(tmp_path):
    import asyncio

    dialog, store = make_dialog(tmp_path)
    session = store.get_or_create("test-session", "medcenter")
    dialog.start_message(session)
    response = asyncio.run(
        run_flow(
            dialog,
            session,
            [
                "Георгий",
                "болит живот",
                "35 лет",
                "завтра после двух",
                "да",
                "+7 999 111 22 33",
                "да"
            ],
        )
    )
    assert response is not None
    assert session.status == "finalized"
    assert session.slots["specialty"] == "гастроэнтеролог"
    assert session.slots["phone"] == "+79991112233"
    assert session.slots["first_name"] == "Георгий"
    assert response["should_close"] is True
    assert response["text"] == "Спасибо, Георгий, запись создана. Желаем Вам хорошего здоровья. При необходимости перезвоните. До свидания"
    assert session.saved_path


def test_finalized_session_does_not_continue_dialog(tmp_path):
    import asyncio

    dialog, store = make_dialog(tmp_path)
    session = store.get_or_create("closed-session", "medcenter")
    dialog.start_message(session)
    asyncio.run(
        run_flow(
            dialog,
            session,
            [
                "Иванов Иван Иванович",
                "болит живот",
                "40 лет",
                "завтра в 15",
                "да",
                "+7 999 111 22 33",
                "да",
            ],
        )
    )
    response = asyncio.run(dialog.process_text(session, "а еще вопрос"))
    assert session.status == "finalized"
    assert session.slots["first_name"] == "Иван"
    assert response["event"] == "conversation_closed"
    assert response["text"] == ""

def test_emergency_goes_to_operator(tmp_path):
    import asyncio

    dialog, store = make_dialog(tmp_path)
    session = store.get_or_create("urgent-session", "medcenter")
    dialog.start_message(session)
    response = asyncio.run(dialog.process_text(session, "мне очень плохо и болит грудь"))
    assert response["status"] == "needs_operator"
    assert "103" in response["text"]
