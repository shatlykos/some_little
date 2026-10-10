"""Повторяющиеся брони — как в Google Calendar: каждый день, каждую неделю,
каждый месяц (в N-й день недели), каждый год или по выбранным дням недели."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .fmt import MONTHS, day_short

NONE, DAILY, WEEKLY, MONTHLY, YEARLY, CUSTOM = "none", "daily", "weekly", "monthly", "yearly", "custom"
MAX_OCCURRENCES = 100

WD_SHORT = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
WD_PLURAL = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]
WD_ACC = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
WD_GENDER = ["m", "m", "f", "m", "f", "f", "n"]
ORDINALS = {  # N-й (5 = последний) в нужном роде, винительный падеж
    "m": ["первый", "второй", "третий", "четвёртый", "последний"],
    "f": ["первую", "вторую", "третью", "четвёртую", "последнюю"],
    "n": ["первое", "второе", "третье", "четвёртое", "последнее"],
}

# Варианты «сколько раз» для каждого типа повтора: (значение, единица)
COUNT_OPTIONS = {
    DAILY: (5, 7, 14, 30),
    WEEKLY: (4, 8, 12, 26),
    MONTHLY: (3, 6, 12),
    YEARLY: (2, 3, 5),
    CUSTOM: (4, 8, 12, 26),  # недель
}


def _plural(n: int, one: str, few: str, many: str) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def count_word(freq: str, n: int) -> str:
    """«5 раз», «4 недели», «3 месяца» — что означает число для этого повтора."""
    if freq == CUSTOM:
        return f"{n} {_plural(n, 'неделя', 'недели', 'недель')}"
    return f"{n} {_plural(n, 'раз', 'раза', 'раз')}"


def nth_in_month(d: date) -> int:
    """Какой по счёту это день недели в месяце: 1..4, 5 — последний."""
    n = (d.day - 1) // 7 + 1
    return 5 if n == 5 or (d + timedelta(days=7)).month != d.month else n


def label(freq: str, start: date, weekdays: tuple[int, ...] = ()) -> str:
    wd = start.weekday()
    if freq == DAILY:
        return "Каждый день"
    if freq == WEEKLY:
        return f"Каждую неделю (по {WD_PLURAL[wd]})"
    if freq == MONTHLY:
        n = nth_in_month(start)
        ordinal = ORDINALS[WD_GENDER[wd]][n - 1]
        prep = "во" if ordinal.startswith("втор") else "в"
        return f"Каждый месяц ({prep} {ordinal} {WD_ACC[wd]})"
    if freq == YEARLY:
        return f"Каждый год ({start.day} {MONTHS[start.month - 1]})"
    if freq == CUSTOM:
        days = ", ".join(WD_SHORT[i] for i in sorted(weekdays)) or "—"
        return f"Каждую неделю: {days}"
    return "Не повторяется"


def _add_months(d: date, months: int) -> tuple[int, int]:
    m = d.month - 1 + months
    return d.year + m // 12, m % 12 + 1


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date | None:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    if n == 5:  # последний
        nxt = date(year + month // 12, month % 12 + 1, 1)
        last = nxt - timedelta(days=1)
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    d = first + timedelta(days=offset + 7 * (n - 1))
    return d if d.month == month else None


def occurrences(start: datetime, freq: str, count: int,
                weekdays: tuple[int, ...] = ()) -> list[datetime]:
    """Даты серии, начиная с start (сама start — первая). Не больше MAX_OCCURRENCES."""
    if freq == NONE or count <= 1 and freq != CUSTOM:
        return [start]
    d0 = start.date()
    days: list[date] = []
    if freq == DAILY:
        days = [d0 + timedelta(days=k) for k in range(count)]
    elif freq == WEEKLY:
        days = [d0 + timedelta(weeks=k) for k in range(count)]
    elif freq == MONTHLY:
        n, wd = nth_in_month(d0), d0.weekday()
        for k in range(count):
            y, m = _add_months(d0, k)
            d = _nth_weekday(y, m, wd, n)
            if d:
                days.append(d)
    elif freq == YEARLY:
        for k in range(count):
            try:
                days.append(d0.replace(year=d0.year + k))
            except ValueError:  # 29 февраля
                pass
    elif freq == CUSTOM:
        week0 = d0 - timedelta(days=d0.weekday())
        wds = sorted(set(weekdays)) or [d0.weekday()]
        for k in range(count):
            for wd in wds:
                d = week0 + timedelta(weeks=k, days=wd)
                if d >= d0:
                    days.append(d)
    days = days[:MAX_OCCURRENCES]
    return [start.replace(year=d.year, month=d.month, day=d.day) for d in days]


def count_button(freq: str, start: datetime, n: int, weekdays: tuple[int, ...] = ()) -> str:
    last = occurrences(start, freq, n, weekdays)[-1]
    return f"{count_word(freq, n)} — до {day_short(last.date())}"
