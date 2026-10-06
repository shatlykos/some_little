"""Расчёт свободного времени. Чистая логика без Telegram и Google — легко тестировать.

Правила:
* Клиент выбирает время начала на сетке (шаг STEP, например 30 минут) и
  длительность (кратна шагу, не меньше минимума ресурса).
* Если соседняя бронь стоит вплотную (заканчивается ровно перед началом или
  начинается ровно после конца), бронь автоматически сдвигается/укорачивается,
  чтобы между ними был перерыв GAP минут.
  Пример: заняты 16:00–17:00 и 18:00–19:00, клиент выбирает 17:00 на 1 час
  → получает 17:05–17:55.
* Если соседей вплотную нет — время остаётся как выбрано (17:00–18:00).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class Busy:
    start: datetime
    end: datetime
    title: str | None = None  # None — показываем клиенту просто «Занято»
    event: bool = False  # мероприятие (создано вручную), а не бронь клиента из бота


@dataclass(frozen=True)
class Slot:
    start: datetime  # реальное начало (с учётом перерыва)
    end: datetime  # реальный конец (с учётом перерыва)
    nominal_start: datetime  # выбранное на сетке
    nominal_end: datetime


def fit_booking(
    nominal_start: datetime,
    duration: timedelta,
    busy: list[Busy],
    day_open: datetime,
    day_close: datetime,
    gap: timedelta,
) -> Slot | None:
    """Пытается поставить бронь [start, start+duration]. Возвращает Slot или None."""
    nominal_end = nominal_start + duration
    if nominal_start < day_open or nominal_end > day_close:
        return None

    start, end = nominal_start, nominal_end
    for b in busy:
        # Соседняя бронь закончилась меньше чем за GAP до начала → сдвигаем начало.
        if nominal_start - gap < b.end <= nominal_start:
            start = max(start, b.end + gap)
        # Соседняя бронь начинается меньше чем через GAP после конца → укорачиваем.
        if nominal_end <= b.start < nominal_end + gap:
            end = min(end, b.start - gap)

    if end <= start:
        return None
    for b in busy:
        if b.start < end and b.end > start:  # пересечение
            return None
    return Slot(start, end, nominal_start, nominal_end)


def grid(day_open: datetime, day_close: datetime, step: timedelta) -> list[datetime]:
    out = []
    t = day_open
    while t < day_close:
        out.append(t)
        t += step
    return out


def available_starts(
    busy: list[Busy],
    day_open: datetime,
    day_close: datetime,
    step: timedelta,
    min_duration: timedelta,
    gap: timedelta,
    not_before: datetime | None = None,
) -> list[datetime]:
    """Времена начала на сетке, с которых помещается хотя бы минимальная бронь."""
    result = []
    for s in grid(day_open, day_close, step):
        if not_before is not None and s < not_before:
            continue
        if fit_booking(s, min_duration, busy, day_open, day_close, gap):
            result.append(s)
    return result


def available_durations(
    nominal_start: datetime,
    busy: list[Busy],
    day_open: datetime,
    day_close: datetime,
    step: timedelta,
    min_duration: timedelta,
    gap: timedelta,
) -> list[tuple[timedelta, Slot]]:
    """Все длительности (от минимума, с шагом step), которые помещаются с этого начала."""
    result = []
    d = min_duration
    while nominal_start + d <= day_close:
        slot = fit_booking(nominal_start, d, busy, day_open, day_close, gap)
        if slot is None:
            break  # дальше будет только хуже — упёрлись в чужую бронь
        result.append((d, slot))
        d += step
    return result


def merge_busy(busy: list[Busy]) -> list[Busy]:
    """Сортирует занятость. Анонимные пересекающиеся интервалы склеивает,
    интервалы с названием (мероприятия) оставляет как есть."""
    items = sorted(busy, key=lambda b: (b.start, b.end))
    merged: list[Busy] = []
    for b in items:
        if (
            merged
            and b.title is None
            and merged[-1].title is None
            and b.start <= merged[-1].end
        ):
            last = merged[-1]
            merged[-1] = Busy(last.start, max(last.end, b.end))
        else:
            merged.append(b)
    return merged


def free_windows(
    busy: list[Busy], day_open: datetime, day_close: datetime
) -> list[tuple[datetime, datetime]]:
    """Свободные промежутки в рабочем дне."""
    windows = []
    cursor = day_open
    for b in sorted(busy, key=lambda b: b.start):
        if b.end <= day_open or b.start >= day_close:
            continue
        if b.start > cursor:
            windows.append((cursor, min(b.start, day_close)))
        cursor = max(cursor, b.end)
    if cursor < day_close:
        windows.append((cursor, day_close))
    return windows
