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
from aiogram.methods import AnswerCallbackQuery, GetMe, TelegramMethod
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

    def add(self, cid, start, end, summary="", location="", from_bot=False):
        eid = f"e{next(self._ids)}"
        self.events.setdefault(cid, {})[eid] = CalEvent(eid, summary, location, start, end, False, from_bot)
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
            from_bot="booking_id" in body["extendedProperties"]["private"],
        )

    def _patch_sync(self, cid, eid, body):
        e = self.events[cid][eid]
        self.events[cid][eid] = CalEvent(e.id, body.get("summary", e.summary), e.location, e.start, e.end, False, e.from_bot)

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
        if isinstance(method, GetMe):
            return User(id=1, is_bot=True, first_name="booking", username="galaxybooking_bot")
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
        Resource("workshop", "Творческая мастерская", "🎨", "cal-workshop", timedelta(hours=1), ("мастерская",),
                 "📺 В аренду входит телевизор."),
        Resource("piano", "Фортепиано", "🎹", "cal-piano", timedelta(minutes=30), ("фортепиано",)),
    )
    cfg = Config(
        bot_token="1:x", admin_id=ADMIN, tz=TZ, google_credentials=Path("x"), google_token=Path("t"),
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
        assert "🎭 16:00–17:00 — Анна Груз" in day_text  # внесено вручную — видно
        assert "🎭 18:00–19:00 — Максим" in day_text
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
        await press(dp, bot, CLIENT, kb.RepeatCB(weeks=1))
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
        day2 = session.sent[-1].text
        assert "🔴 17:05–17:55 — ⏳ Анна (заявка)" in day2  # бронь из бота видна всем

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
        (first,) = await svc.create(nb(CLIENT))
        assert (first.start, first.end) == (datetime(2026, 10, 7, 10, tzinfo=TZ), datetime(2026, 10, 7, 10, 30, tzinfo=TZ))
        with pytest.raises(SlotTaken):
            await svc.create(nb(CLIENT2))
        # Следующие полчаса: соседняя бронь вплотную → 10:35–11:00
        nxt = NewBooking(CLIENT2, None, piano, datetime(2026, 10, 7, 10, 30, tzinfo=TZ),
                         timedelta(minutes=30), "Б", "+995", 1, "")
        (second,) = await svc.create(nxt)
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
    cal.add("cal-piano", datetime(2026, 10, 8, 10, tzinfo=TZ), datetime(2026, 10, 8, 11, tzinfo=TZ),
            "⏳ Секрет (заявка)", from_bot=True)
    cal.add("cal-workshop", datetime(2026, 10, 8, 19, tzinfo=TZ), datetime(2026, 10, 8, 20, tzinfo=TZ),
            "Лекция Вадим")
    cal.add("cal-workshop", datetime(2026, 10, 8, 12, tzinfo=TZ), datetime(2026, 10, 8, 13, tzinfo=TZ),
            "Анна", from_bot=True)

    async def scenario():
        await send(dp, bot, CLIENT, kb.BTN_AFISHA)
        assert "Выберите помещение" in session.sent[-1].text
        labels = [b.text for b in buttons(session.last_markup())]
        assert labels[-1] == "🏢 Все помещения" and "🎹 Фортепиано" in labels
        await press(dp, bot, CLIENT, kb.AfishaCB(key=kb.AFISHA_ALL))
        text = session.sent[-1].text
        assert "Афиша: все помещения" in text
        assert "Концерт · 🎹 Фортепиано" in text
        assert "Лекция Вадим · 🎨 Творческая мастерская" in text  # из календаря помещения
        assert "Секрет" not in text and "Анна" not in text  # брони клиентов из бота — не в афише

        await press(dp, bot, CLIENT, kb.AfishaCB(key="workshop"))
        text = session.sent[-1].text
        assert "Афиша: 🎨 Творческая мастерская" in text
        assert "Лекция Вадим" in text and "Концерт" not in text
        await press(dp, bot, CLIENT, kb.AfishaCB(key=kb.AFISHA_MENU))
        assert "Выберите помещение" in session.sent[-1].text
        await press(dp, bot, CLIENT, kb.AfishaCB(key="piano"))
        assert "Концерт" in session.sent[-1].text and "Лекция" not in session.sent[-1].text

        await send(dp, bot, CLIENT, kb.BTN_VIEW)
        await press(dp, bot, CLIENT, kb.DayCB(mode="v", day="20261008"))
        text = session.sent[-1].text
        assert "🎭 19:00–21:00 — Концерт" in text
        assert "🔴 10:00–11:00 — ⏳ Секрет (заявка)" in text
        assert "🎭 19:00–20:00 — Лекция Вадим" in text and "🔴 12:00–13:00 — Анна" in text
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



def test_contact_can_be_telegram_username(env):
    cfg, cal, svc, session, bot, dp = env
    Clock.now = datetime(2026, 10, 6, 12, 0, tzinfo=TZ)

    async def scenario():
        await send(dp, bot, CLIENT, kb.BTN_BOOK)
        await press(dp, bot, CLIENT, kb.ResCB(key="piano"))
        await press(dp, bot, CLIENT, kb.DayCB(mode="b", day="20261007"))
        await press(dp, bot, CLIENT, kb.StartCB(hhmm="1000"))
        await press(dp, bot, CLIENT, kb.DurCB(minutes=30))
        await send(dp, bot, CLIENT, "Катя Д")
        await send(dp, bot, CLIENT, "просто текст")
        assert "Не получилось распознать" in session.sent[-1].text
        await send(dp, bot, CLIENT, "@psihology_ot_Apsitis")
        assert "Сколько будет человек" in session.sent[-1].text
        await send(dp, bot, CLIENT, "2")
        await send(dp, bot, CLIENT, kb.BTN_SKIP)
        await press(dp, bot, CLIENT, kb.RepeatCB(weeks=1))
        assert "📞 @psihology_ot_Apsitis" in session.sent[-1].text
        await press(dp, bot, CLIENT, kb.ConfirmCB(ok=True))
        assert svc.db.get(1).phone == "@psihology_ot_Apsitis"

    asyncio.run(scenario())


async def _book(dp, bot, uid, contact, people="3", weeks=1, start="1800", day="20261012"):
    await send(dp, bot, uid, kb.BTN_BOOK)
    await press(dp, bot, uid, kb.ResCB(key="workshop"))
    await press(dp, bot, uid, kb.DayCB(mode="b", day=day))
    await press(dp, bot, uid, kb.StartCB(hhmm=start))
    await press(dp, bot, uid, kb.DurCB(minutes=120))
    await send(dp, bot, uid, "Катя Д.")
    await send(dp, bot, uid, contact)
    await send(dp, bot, uid, people)
    await send(dp, bot, uid, "Психология")
    await press(dp, bot, uid, kb.RepeatCB(weeks=weeks))
    await press(dp, bot, uid, kb.ConfirmCB(ok=True))


def test_booking_for_partner(env):
    cfg, cal, svc, session, bot, dp = env
    Clock.now = datetime(2026, 10, 9, 12, 0, tzinfo=TZ)
    PARTNER, LATE = 4000, 5000

    async def scenario():
        await send(dp, bot, PARTNER, "/start")  # партнёр уже знаком с ботом
        svc.db.remember_user(PARTNER, "katya_psy")
        await _book(dp, bot, ADMIN, "@Katya_Psy", people="12")
        b = svc.db.get(1)
        assert b.user_id == PARTNER and b.booked_by == ADMIN and b.people == 12
        assert any("На вас оформлена заявка" in t for t in session.texts(PARTNER))
        card = [t for t in session.texts(ADMIN) if "Новая заявка" in t][-1]
        assert "Telegram:" not in card
        await press(dp, bot, ADMIN, kb.AdminCB(action="ok", id=1))
        assert any("подтверждена" in t for t in session.texts(PARTNER))
        # партнёр видит бронь в «Мои брони» и может отменить
        await send(dp, bot, PARTNER, kb.BTN_MINE)
        assert any("Творческая мастерская" in t for t in session.texts(PARTNER)[-2:])

        # ник, который ещё не писал боту: бронь у админа, перейдёт при первом сообщении
        await _book(dp, bot, ADMIN, "@newbie_user", start="1000")
        b2 = svc.db.get(2)
        assert b2.user_id == ADMIN and b2.contact_username == "newbie_user"
        note = [t for t in session.texts(ADMIN) if "ещё не открывал этого бота" in t]
        assert note and "t.me/galaxybooking_bot" in note[-1]
        msg = Message(message_id=1, date=datetime.now(), chat=Chat(id=LATE, type="private"),
                      from_user=User(id=LATE, is_bot=False, first_name="N", username="Newbie_User"),
                      text="/start")
        await dp.feed_update(bot, Update(update_id=99999, message=msg))
        assert svc.db.get(2).user_id == LATE
        assert any("На вас оформлена бронь" in t for t in session.texts(LATE))

    asyncio.run(scenario())


def test_weekly_series(env):
    cfg, cal, svc, session, bot, dp = env
    Clock.now = datetime(2026, 10, 9, 12, 0, tzinfo=TZ)
    # 19 октября в 18:00 уже занято — эту дату серия должна пропустить
    cal.add("cal-workshop", datetime(2026, 10, 19, 18, tzinfo=TZ), datetime(2026, 10, 19, 19, tzinfo=TZ), "Занято")

    async def scenario():
        await send(dp, bot, CLIENT, kb.BTN_BOOK)
        await press(dp, bot, CLIENT, kb.ResCB(key="workshop"))
        await press(dp, bot, CLIENT, kb.DayCB(mode="b", day="20261012"))
        await press(dp, bot, CLIENT, kb.StartCB(hhmm="1800"))
        await press(dp, bot, CLIENT, kb.DurCB(minutes=120))
        await send(dp, bot, CLIENT, "Анна")
        await send(dp, bot, CLIENT, "+995555123456")
        await send(dp, bot, CLIENT, "7")
        await send(dp, bot, CLIENT, kb.BTN_SKIP)
        labels = [b.text for b in buttons(session.last_markup())]
        assert labels[0] == "Один раз" and "🔁 Раз в неделю — 4 недели (≈ месяц)" in labels
        await press(dp, bot, CLIENT, kb.RepeatCB(weeks=4))
        preview = session.sent[-1].text
        assert "❌ пн 19 окт — занято, пропустим" in preview and "✅ пн 2 ноя" in preview
        await press(dp, bot, CLIENT, kb.ConfirmCB(ok=True))
        items = svc.db.series(1)
        assert [b.start.day for b in items] == [12, 26, 2]
        assert "оформлено 3 из 4 недель" in [t for t in session.texts(CLIENT) if "Заявка #1" in t][-1]
        await press(dp, bot, ADMIN, kb.AdminCB(action="ok", id=1))
        assert all(b.status == dbm.APPROVED for b in svc.db.series(1))
        approved = [t for t in session.texts(CLIENT) if "подтверждена" in t][-1]
        assert "📺 В аренду входит телевизор." in approved and "еду и напитки" in approved
        assert "раз в неделю, 3 недели" in approved
        assert all(e.summary == "Анна" for e in cal.events["cal-workshop"].values() if e.from_bot)
        # отмена одной даты серии не трогает остальные
        await press(dp, bot, CLIENT, kb.CancelCB(action="yes", id=items[1].id))
        assert [b.status for b in svc.db.series(1)] == [dbm.APPROVED, dbm.CANCELLED, dbm.APPROVED]

    asyncio.run(scenario())
