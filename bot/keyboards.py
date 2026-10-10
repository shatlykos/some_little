from __future__ import annotations

from datetime import date, datetime, timedelta

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import Resource
from . import recurrence as rec
from .fmt import day_short, duration, hm, span
from .slots import Slot

# ---------- главное меню ----------

BTN_BOOK = "📅 Забронировать"
BTN_VIEW = "👀 Занятость на день"
BTN_AFISHA = "🎭 Афиша"
BTN_MINE = "📋 Мои брони"
BTN_CANCEL = "❌ Отмена"
BTN_SKIP = "Пропустить"

DAYS_PER_PAGE = 7


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BOOK), KeyboardButton(text=BTN_VIEW)],
            [KeyboardButton(text=BTN_AFISHA), KeyboardButton(text=BTN_MINE)],
        ],
        resize_keyboard=True,
    )


def reply(*buttons: KeyboardButton | str) -> ReplyKeyboardMarkup:
    rows = [[b if isinstance(b, KeyboardButton) else KeyboardButton(text=b)] for b in buttons]
    rows.append([KeyboardButton(text=BTN_CANCEL)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def people_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=str(n)) for n in range(1, 6)],
            [KeyboardButton(text=str(n)) for n in range(6, 11)],
            [KeyboardButton(text=BTN_CANCEL)],
        ],
        resize_keyboard=True,
    )


def phone_kb(username: str | None = None) -> ReplyKeyboardMarkup:
    buttons: list[KeyboardButton | str] = [
        KeyboardButton(text="📱 Отправить мой номер", request_contact=True)
    ]
    if username:
        buttons.append(f"@{username}")
    return reply(*buttons)


# ---------- callback data ----------

class ResCB(CallbackData, prefix="r"):
    key: str
    day: str = ""  # YYYYMMDD — если задан, сразу к выбору времени


class DayCB(CallbackData, prefix="d"):
    mode: str  # "b" — бронь (конкретное помещение), "v" — обзор всех помещений
    day: str  # YYYYMMDD
    page: int = -1  # >= 0 — это кнопка листания, а не выбор дня


class StartCB(CallbackData, prefix="s"):
    hhmm: str


class DurCB(CallbackData, prefix="u"):
    minutes: int


class NavCB(CallbackData, prefix="n"):
    to: str  # "res" | "days" | "starts" | "vdays"


class RepeatCB(CallbackData, prefix="w"):
    freq: str  # recurrence.NONE / DAILY / WEEKLY / MONTHLY / YEARLY / CUSTOM


class RepeatDayCB(CallbackData, prefix="wd"):
    day: int  # 0=пн … 6=вс; -1 — «Готово»


class RepeatCountCB(CallbackData, prefix="wn"):
    n: int


class ConfirmCB(CallbackData, prefix="c"):
    ok: bool


class AdminCB(CallbackData, prefix="a"):
    action: str  # "ok" | "no"
    id: int


class AfishaCB(CallbackData, prefix="f"):
    key: str  # ключ помещения, AFISHA_ALL — все помещения, AFISHA_MENU — выбор помещения


AFISHA_ALL = "all"
AFISHA_MENU = "menu"


class CancelCB(CallbackData, prefix="x"):
    action: str  # "ask" | "yes" | "no"
    id: int


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def parse_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


# ---------- инлайн-клавиатуры ----------

def resources_kb(resources: tuple[Resource, ...]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in resources:
        kb.button(text=f"{r.title} (от {duration(r.min_duration)})", callback_data=ResCB(key=r.key))
    kb.adjust(1)
    return kb.as_markup()


def days_kb(days: list[date], mode: str, page: int, back_to: str | None) -> InlineKeyboardMarkup:
    pages = max(1, (len(days) + DAYS_PER_PAGE - 1) // DAYS_PER_PAGE)
    page = max(0, min(page, pages - 1))
    chunk = days[page * DAYS_PER_PAGE:(page + 1) * DAYS_PER_PAGE]
    kb = InlineKeyboardBuilder()
    for i, d in enumerate(chunk):
        label = day_short(d)
        if page == 0 and i == 0:
            label = f"Сегодня, {label}"
        elif page == 0 and i == 1:
            label = f"Завтра, {label}"
        kb.button(text=label, callback_data=DayCB(mode=mode, day=ymd(d)))
    kb.adjust(1, 1, 2, 2, 2) if page == 0 else kb.adjust(2)
    nav = []
    anchor = ymd(days[0])
    if page > 0:
        nav.append(InlineKeyboardButton(text="◀️ Раньше", callback_data=DayCB(mode=mode, day=anchor, page=page - 1).pack()))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="Позже ▶️", callback_data=DayCB(mode=mode, day=anchor, page=page + 1).pack()))
    if nav:
        kb.row(*nav)
    if back_to:
        kb.row(InlineKeyboardButton(text="⬅️ Назад", callback_data=NavCB(to=back_to).pack()))
    return kb.as_markup()


def starts_kb(starts: list[datetime]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for s in starts:
        kb.button(text=hm(s), callback_data=StartCB(hhmm=s.strftime("%H%M")))
    kb.adjust(4)
    kb.row(InlineKeyboardButton(text="⬅️ Другой день", callback_data=NavCB(to="days").pack()))
    return kb.as_markup()


def durations_kb(options: list[tuple[timedelta, Slot]]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for d, slot in options:
        text = duration(d)
        if (slot.start, slot.end) != (slot.nominal_start, slot.nominal_end):
            text += f" ({span(slot.start, slot.end)})"
        kb.button(text=text, callback_data=DurCB(minutes=int(d.total_seconds() // 60)))
    kb.adjust(2)
    kb.row(InlineKeyboardButton(text="⬅️ Другое время", callback_data=NavCB(to="starts").pack()))
    return kb.as_markup()


def back_kb(text: str, to: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=text, callback_data=NavCB(to=to).pack())]]
    )


def overview_kb(resources: tuple[Resource, ...], day: date) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in resources:
        kb.button(text=f"➕ {r.title}", callback_data=ResCB(key=r.key, day=ymd(day)))
    kb.button(text="⬅️ Другой день", callback_data=NavCB(to="vdays"))
    kb.adjust(1)
    return kb.as_markup()


def afisha_menu_kb(resources: tuple[Resource, ...]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for r in resources:
        kb.button(text=r.title, callback_data=AfishaCB(key=r.key))
    kb.button(text="🏢 Все помещения", callback_data=AfishaCB(key=AFISHA_ALL))
    kb.adjust(*([2] * (len(resources) // 2)), *([1] * (len(resources) % 2)), 1)
    return kb.as_markup()


def afisha_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
        text="⬅️ Другое помещение", callback_data=AfishaCB(key=AFISHA_MENU).pack())]])


def my_booking_kb(booking_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="❌ Отменить бронь", callback_data=CancelCB(action="ask", id=booking_id))
    return kb.as_markup()


def repeat_kb(start: date) -> InlineKeyboardMarkup:
    """Варианты повтора — как в Google Calendar."""
    kb = InlineKeyboardBuilder()
    for freq in (rec.NONE, rec.DAILY, rec.WEEKLY, rec.MONTHLY, rec.YEARLY):
        kb.button(text=rec.label(freq, start), callback_data=RepeatCB(freq=freq))
    kb.button(text="Другое…", callback_data=RepeatCB(freq=rec.CUSTOM))
    kb.adjust(1)
    return kb.as_markup()


def repeat_days_kb(selected: tuple[int, ...]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for i, name in enumerate(rec.WD_SHORT):
        kb.button(text=("✅ " if i in selected else "") + name, callback_data=RepeatDayCB(day=i))
    kb.button(text="Готово ➡️", callback_data=RepeatDayCB(day=-1))
    kb.adjust(4, 3, 1)
    return kb.as_markup()


def repeat_count_kb(freq: str, start: datetime, weekdays: tuple[int, ...]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for n in rec.COUNT_OPTIONS[freq]:
        kb.button(text=rec.count_button(freq, start, n, weekdays), callback_data=RepeatCountCB(n=n))
    kb.adjust(1)
    return kb.as_markup()


def confirm_kb() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Отправить заявку", callback_data=ConfirmCB(ok=True))
    kb.button(text="❌ Отменить", callback_data=ConfirmCB(ok=False))
    kb.adjust(1)
    return kb.as_markup()


def admin_kb(booking_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=AdminCB(action="ok", id=booking_id))
    kb.button(text="❌ Отклонить", callback_data=AdminCB(action="no", id=booking_id))
    kb.adjust(2)
    return kb.as_markup()


def cancel_ask_kb(booking_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="Да, отменить", callback_data=CancelCB(action="yes", id=booking_id))
    kb.button(text="Нет", callback_data=CancelCB(action="no", id=booking_id))
    kb.adjust(2)
    return kb.as_markup()
