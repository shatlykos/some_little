"""Сквозной тест: настоящие обработчики aiogram, фейковый Telegram и фейковый
Google Calendar. Проходим путь клиента и администратора целиком."""
from __future__ import annotations

import asyncio
import itertools
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import AnswerCallbackQuery, TelegramMethod
from aiogram.types import CallbackQuery, Chat, Contact, Message, Update, User

from bot import db as dbm
from bot import keyboards as kb
from bot.config import Config, Resource
from bot.db import DB
from bot.gcal import CalEvent, Calendar
from bot.handlers import router
from bot.reminders import _tick
from bot.service import Service, SlotTaken, NewBooking

TZ = ZoneInfo("Asia/Tbilisi")
ADMIN = 1000
CLIENT = 2000
CLIENT2 = 3000
DAY = date(2026, 10, 7)


# ---------------- фейки ----------------

class FakeCalendar(Calendar):
    def __init__(self, cfg):
        self.cfg = cfg
        self.tz = cfg.tz
        self.events: dict[str, dict[str, CalEvent]] = {}
        self._ids = itertools.count(1)

    def add(self, cid, start, end, summary="", location=""):
        eid = f"e{next(self._ids)}"
        self.events.setdefault(cid, {})[eid] = CalEvent(eid, summary, location, start, end, False)
        return eid

    def _list_sync(self, cid, t_min, t_max):
        return sorted(
            (e for e in self.events.get(cid, {}).values() if e.end > t_min and e.start < t_max),
            key=lambda e: e.start,
        )

    def _insert_sync(self, cid, body):
        return self.add(
            cid,
            datetime.fromisoformat(body["start"]["dateTime"]),
            datetime.fromisoformat(body["end"]["dateTime"]),
            body["summary"],
        )

    def _patch_sync(self, cid, eid, body):
        e = self.events[cid][eid]
        self.events[cid][eid] = CalEvent(e.id, body.get("summary", e.summary), e.location, e.start, e.end, False)

    def _delete_sync(self, cid, eid):
        self.events.get(cid, {}).pop(eid, None)

    def _exists_sync(self, cid, eid):
        return eid in self.events.get(cid, {})


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent: list[TelegramMethod] = []
        self._mid = itertools.count(100)

    async def make_request(self, bot, method, timeout=None):
        self.sent.append(method)
        if isinstance(method, AnswerCallbackQuery):
            return True
        chat_id = getattr(method, "chat_id", None) or CLIENT
        return Message(
            message_id=next(self._mid),
            date=datetime.now(),
            chat=Chat(id=chat_id, type="private"),
            text=getattr(method, "text", None) or "",
        )

    async def stream_content(self, *a, **kw):  # pragma: no cover
        yield b""

    async def close(self):
        pass

    def texts(self, chat_id=None):
        return [
            m.text for m in self.sent
            if getattr(m, "text", None) and (chat_id is None or getattr(m, "chat_id", chat_id) == chat_id)
        ]

    def last_markup(self):
        for m in reversed(self.sent):
            if getattr(m, "reply_markup", None) is not None and hasattr(m.reply_markup, "inline_keyboard"):
                return m.reply_markup
        raise AssertionError("нет инлайн-клавиатуры")

    def alerts(self):
        return [m.text for m in self.sent if isinstance(m, AnswerCallbackQuery) and m.text]


class Clock:
    now = datetime(2026, 10, 6, 12, 0, tzinfo=TZ)


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    res = (
        Resource("workshop", "Творческая мастерская", "🎨", "cal-workshop", timedelta(hours=1), ("мастерская",)),
        Resource("piano", "Фортепиано", "🎹", "cal-piano", timedelta(minutes=30), ("фортепиано",)),
    )
    cfg = Config(
        bot_token="1:x", admin_id=ADMIN, tz=TZ, google_credentials=Path("x"),
        open_time=time(9), close_time=time(22), step=timedelta(minutes=30),
        gap=timedelta(minutes=5), horizon_days=30,
        reminders=(timedelta(hours=24), timedelta(hours=2)),
        events_calendar_id="cal-events", events_calendar_name="Galaxy Book",
        resources=res, db_path=tmp_path / "b.db",
    )
    cal = FakeCalendar(cfg)
    svc = Service(cfg, cal, DB(cfg.db_path))
    monkeypatch.setattr(svc, "now", lambda: Clock.now)
    session = FakeSession()
    bot = Bot("1:x", session=session, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp["cfg"] = cfg
    dp["svc"] = svc
    # router — модульный синглтон; в тестах подключаем его к новому диспетчеру
    router._parent_router = None
    dp.include_router(router)
    return cfg, cal, svc, session, bot, dp


_uid = itertools.count(1)


def user(uid):
    return User(id=uid, is_bot=False, first_name="Анна", username=f"user{uid}")


async def send(dp, bot, uid, text=None, contact=None):
    msg = Message(
        message_id=next(_uid), date=datetime.now(), chat=Chat(id=uid, type="private"),
        from_user=user(uid), text=text, contact=contact,
    )
    await dp.feed_update(bot, Update(update_id=next(_uid), message=msg))


async def press(dp, bot, uid, data):
    data = data.pack() if hasattr(data, "pack") else data
    msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=uid, type="private"), text="x")
    cq = CallbackQuery(id=str(next(_uid)), from_user=user(uid), chat_instance="c", message=msg, data=data)
    await dp.feed_update(bot, Update(update_id=next(_uid), callback_query=cq))


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


# ---------------- тесты ----------------

def test_full_booking_flow(env):
    cfg, cal, svc, session, bot, dp = env
    # Как в примере: мастерская занята 16–17 и 18–19, плюс мероприятие из афиши в 20–21.
    cal.add("cal-workshop", datetime(2026, 10, 7, 16, tzinfo=TZ), datetime(2026, 10, 7, 17, tzinfo=TZ), "Анна Груз")
    cal.add("cal-workshop", datetime(2026, 10, 7, 18, tzinfo=TZ), datetime(2026, 10, 7, 19, tzinfo=TZ), "Максим")
    cal.add("cal-events", datetime(2026, 10, 7, 20, tzinfo=TZ), datetime(2026, 10, 7, 21, tzinfo=TZ),
            "Мастер-класс по керамике", "Творческая мастерская")

    async def scenario():
        await send(dp, bot, CLIENT, "/start")
        await send(dp, bot, CLIENT, kb.BTN_BOOK)
        await press(dp, bot, CLIENT, kb.ResCB(key="workshop"))
        await press(dp, bot, CLIENT, kb.DayCB(mode="b", day=kb.ymd(DAY)))

        day_text = session.sent[-1].text
        assert "🔴 16:00–17:00 — занято" in day_text
        assert "Анна" not in day_text and "Максим" not in day_text  # имена скрыты
        assert "🎭 20:00–21:00 — Мастер-класс по керамике" in day_text
        starts = [b.text for b in buttons(session.last_markup()) if ":" in b.text]
        assert "17:00" in starts and "16:00" not in starts and "20:00" not in starts

        await press(dp, bot, CLIENT, kb.StartCB(hhmm="1700"))
        durs = [b.text for b in buttons(session.last_markup())]
        assert durs[0] == "1 ч (17:05–17:55)"
        assert len([d for d in durs if "ч" in d or "мин" in d]) == 1  # дальше занято

        await press(dp, bot, CLIENT, kb.DurCB(minutes=60))
        await send(dp, bot, CLIENT, "Анна")
        await send(dp, bot, CLIENT, contact=Contact(phone_number="995555123456", first_name="Анна"))
        await send(dp, bot, CLIENT, "4")
        await send(dp, bot, CLIENT, "Репетиция <b>")
        assert "17:05–17:55" in session.sent[-1].text
        assert "&lt;b&gt;" in session.sent[-1].text  # HTML экранирован
        await press(dp, bot, CLIENT, kb.ConfirmCB(ok=True))

        b = svc.db.get(1)
        assert b.status == dbm.PENDING
        assert (b.start, b.end) == (datetime(2026, 10, 7, 17, 5, tzinfo=TZ), datetime(2026, 10, 7, 17, 55, tzinfo=TZ))
        assert b.phone == "+995555123456" and b.people == 4
        ev = cal.events["cal-workshop"][b.event_id]
        assert ev.summary == "⏳ Анна (заявка)"
        assert any("Новая заявка" in t for t in session.texts(ADMIN))

        # Второй клиент уже не видит 17:00
        await send(dp, bot, CLIENT2, kb.BTN_BOOK)
        await press(dp, bot, CLIENT2, kb.ResCB(key="workshop"))
        await press(dp, bot, CLIENT2, kb.DayCB(mode="b", day=kb.ymd(DAY)))
        starts2 = [b.text for b in buttons(session.last_markup()) if ":" in b.text]
        assert "17:00" not in starts2

        # Не-админ не может подтвердить
        await press(dp, bot, CLIENT, kb.AdminCB(action="ok", id=1))
        assert svc.db.get(1).status == dbm.PENDING

        await press(dp, bot, ADMIN, kb.AdminCB(action="ok", id=1))
        assert svc.db.get(1).status == dbm.APPROVED
        assert cal.events["cal-workshop"][b.event_id].summary == "Анна"
        assert any("подтверждена" in t for t in session.texts(CLIENT))

        # Повторное нажатие — без изменений
        await press(dp, bot, ADMIN, kb.AdminCB(action="no", id=1))
        assert svc.db.get(1).status == dbm.APPROVED

        # Напоминания: за 24 ч уже прошло на момент подтверждения → его нет; за 2 ч — придёт
        n_before = len(session.texts(CLIENT))
        Clock.now = datetime(2026, 10, 7, 15, 10, tzinfo=TZ)
        await _tick(bot, svc, cfg)
        reminders = session.texts(CLIENT)[n_before:]
        assert len(reminders) == 1 and "Напоминание" in reminders[0]
        await _tick(bot, svc, cfg)
        assert len(session.texts(CLIENT)) == n_before + 1  # не дублируется

        # Клиент отменяет бронь
        await send(dp, bot, CLIENT, kb.BTN_MINE)
        await press(dp, bot, CLIENT, kb.CancelCB(action="ask", id=1))
        await press(dp, bot, CLIENT, kb.CancelCB(action="yes", id=1))
        assert svc.db.get(1).status == dbm.CANCELLED
        assert b.event_id not in cal.events["cal-workshop"]
        assert any("отменил" in t for t in session.texts(ADMIN))

        # Чужую бронь отменить нельзя
        await press(dp, bot, CLIENT2, kb.CancelCB(action="yes", id=1))
        assert "Эта кнопка устарела" in session.alerts()[-1]

    Clock.now = datetime(2026, 10, 6, 12, 0, tzinfo=TZ)
    asyncio.run(scenario())


def test_reject_and_double_booking(env):
    cfg, cal, svc, session, bot, dp = env
    piano = cfg.resource("piano")
    Clock.now = datetime(2026, 10, 6, 12, 0, tzinfo=TZ)

    def nb(uid):
        return NewBooking(uid, None, piano, datetime(2026, 10, 7, 10, tzinfo=TZ), timedelta(minutes=30),
                          "Тест", "+995", 1, "")

    async def scenario():
        first = await svc.create(nb(CLIENT))
        assert (first.start, first.end) == (datetime(2026, 10, 7, 10, tzinfo=TZ), datetime(2026, 10, 7, 10, 30, tzinfo=TZ))
        with pytest.raises(SlotTaken):
            await svc.create(nb(CLIENT2))
        # Следующие полчаса: соседняя бронь вплотную → 10:35–11:00
        nxt = NewBooking(CLIENT2, None, piano, datetime(2026, 10, 7, 10, 30, tzinfo=TZ),
                         timedelta(minutes=30), "Б", "+995", 1, "")
        second = await svc.create(nxt)
        assert (second.start, second.end) == (datetime(2026, 10, 7, 10, 35, tzinfo=TZ), datetime(2026, 10, 7, 11, tzinfo=TZ))

        await press(dp, bot, ADMIN, kb.AdminCB(action="no", id=first.id))
        assert svc.db.get(first.id).status == dbm.REJECTED
        assert first.event_id not in cal.events["cal-piano"]
        assert any("отклонена" in t for t in session.texts(CLIENT))

        # Админ удалил событие в календаре вручную → подтвердить нельзя
        cal._delete_sync("cal-piano", second.event_id)
        await press(dp, bot, ADMIN, kb.AdminCB(action="ok", id=second.id))
        assert svc.db.get(second.id).status == dbm.CANCELLED

    asyncio.run(scenario())


def test_overview_and_afisha(env):
    cfg, cal, svc, session, bot, dp = env
    Clock.now = datetime(2026, 10, 6, 12, 0, tzinfo=TZ)
    cal.add("cal-events", datetime(2026, 10, 8, 19, tzinfo=TZ), datetime(2026, 10, 8, 21, tzinfo=TZ),
            "Концерт", "Фортепиано")
    cal.add("cal-piano", datetime(2026, 10, 8, 10, tzinfo=TZ), datetime(2026, 10, 8, 11, tzinfo=TZ), "Секрет")

    async def scenario():
        await send(dp, bot, CLIENT, kb.BTN_AFISHA)
        assert "Концерт · Фортепиано" in session.sent[-1].text

        await send(dp, bot, CLIENT, kb.BTN_VIEW)
        await press(dp, bot, CLIENT, kb.DayCB(mode="v", day="20261008"))
        text = session.sent[-1].text
        assert "🎭 19:00–21:00 — Концерт" in text and "🔴 10:00–11:00 — занято" in text
        assert "Секрет" not in text
        # Из обзора — сразу к выбору времени конкретного помещения
        await press(dp, bot, CLIENT, kb.ResCB(key="piano", day="20261008"))
        starts = [b.text for b in buttons(session.last_markup()) if ":" in b.text]
        assert "10:00" not in starts and "11:00" in starts and "19:00" not in starts

        # Сегодня: время до текущего момента не предлагается
        await send(dp, bot, CLIENT, kb.BTN_BOOK)
        await press(dp, bot, CLIENT, kb.ResCB(key="piano"))
        await press(dp, bot, CLIENT, kb.DayCB(mode="b", day="20261006"))
        starts = [b.text for b in buttons(session.last_markup()) if ":" in b.text]
        assert starts[0] == "12:00"
        assert "🟢 12:00–22:00 — свободно" in session.sent[-1].text

        # Листание дней
        await press(dp, bot, CLIENT, kb.DayCB(mode="b", day="20261006", page=1))
        days = [b.text for b in buttons(session.last_markup())]
        assert any("13 окт" in d for d in days)

    asyncio.run(scenario())
