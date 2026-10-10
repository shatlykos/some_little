"""Форматирование дат и текстов на русском."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from html import escape

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]


def hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def span(a: datetime, b: datetime) -> str:
    return f"{hm(a)}–{hm(b)}"


def day_long(d: date) -> str:
    return f"{WEEKDAYS[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}"


def day_short(d: date) -> str:
    return f"{WEEKDAYS_SHORT[d.weekday()]} {d.day} {MONTHS_SHORT[d.month - 1]}"


def duration(td: timedelta) -> str:
    minutes = int(td.total_seconds() // 60)
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} ч {m} мин"
    if h:
        return f"{h} ч"
    return f"{m} мин"


def q(text: str | None) -> str:
    """Экранирование пользовательского текста для parse_mode=HTML."""
    return escape(text or "")


def weeks_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "неделя"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return "недели"
    return "недель"
