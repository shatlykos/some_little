"""Бизнес-логика: расписание, создание заявок, подтверждение, отмена."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from . import db as dbm
from .config import Config, Resource
from .db import DB, Booking
from .fmt import day_long, q, span
from .gcal import Calendar
from .slots import (
    Busy,
    Slot,
    available_durations,
    available_starts,
    fit_booking,
    free_windows,
    merge_busy,
)

log = logging.getLogger(__name__)


@dataclass
class NewBooking:
    user_id: int
    username: str | None
    resource: Resource
    nominal_start: datetime
    duration: timedelta
    name: str
    phone: str
    people: int
    comment: str


class SlotTaken(Exception):
    """Пока клиент заполнял данные, время заняли."""


class Service:
    def __init__(self, cfg: Config, cal: Calendar, db: DB):
        self.cfg = cfg
        self.cal = cal
        self.db = db
        # Один процесс бота → этого замка достаточно, чтобы два клиента
        # не забронировали одно и то же время одновременно.
        self._lock = asyncio.Lock()

    # ---------- время ----------

    def now(self) -> datetime:
        return datetime.now(self.cfg.tz)

    def today(self) -> date:
        return self.now().date()

    def bookable_days(self) -> list[date]:
        t = self.today()
        return [t + timedelta(days=i) for i in range(self.cfg.horizon_days)]

    def is_bookable_day(self, d: date) -> bool:
        return 0 <= (d - self.today()).days < self.cfg.horizon_days

    def at(self, d: date, t: time) -> datetime:
        return datetime.combine(d, t, self.cfg.tz)

    # ---------- расписание ----------

    async def day(self, resource: Resource, d: date) -> tuple[list[Busy], datetime, datetime]:
        busy = await self.cal.busy_for_day(resource, d)
        day_open, day_close = self.cal.day_bounds(d)
        return busy, day_open, day_close

    async def starts(self, resource: Resource, d: date) -> list[datetime]:
        busy, o, c = await self.day(resource, d)
        return available_starts(
            busy, o, c, self.cfg.step, resource.min_duration, self.cfg.gap,
            not_before=self.now(),
        )

    async def durations(self, resource: Resource, start: datetime) -> list[tuple[timedelta, Slot]]:
        busy, o, c = await self.day(resource, start.date())
        if start < self.now():
            return []
        return available_durations(
            start, busy, o, c, self.cfg.step, resource.min_duration, self.cfg.gap
        )

    def describe_day(self, resource: Resource, d: date, busy: list[Busy],
                     day_open: datetime, day_close: datetime, header: bool = True) -> str:
        """Текст расписания на день: занято / мероприятия / свободные окна."""
        # Сегодня свободным считаем время только с текущего момента (округляем до 5 мин).
        free_from = day_open
        if d == self.today():
            n = self.now().replace(second=0, microsecond=0)
            n += timedelta(minutes=(-n.minute) % 5)
            free_from = max(day_open, n)

        rows: list[tuple[datetime, str]] = []
        for b in merge_busy(busy):
            if b.end <= day_open or b.start >= day_close:
                continue
            if b.title:
                rows.append((b.start, f"🎭 {span(b.start, b.end)} — {q(b.title)}"))
            else:
                rows.append((b.start, f"🔴 {span(b.start, b.end)} — занято"))
        has_free = False
        if free_from < day_close:
            for a, z in free_windows(busy, free_from, day_close):
                if z - a >= resource.min_duration:
                    rows.append((a, f"🟢 {span(a, z)} — свободно"))
                    has_free = True
        rows.sort(key=lambda r: r[0])

        lines = [f"<b>{resource.title}</b>", f"<i>{day_long(d)}</i>"] if header else []
        lines += [text for _, text in rows]
        if not has_free:
            lines.append("Свободного времени нет")
        return "\n".join(lines)

    # ---------- заявки ----------

    def _event_body(self, b: NewBooking | Booking, booking_id: int, slot_start: datetime,
                    slot_end: datetime, pending: bool) -> dict:
        name = b.name
        summary = f"⏳ {name} (заявка)" if pending else name
        username = getattr(b, "username", None)
        desc = [
            f"Телефон: {b.phone}",
            f"Человек: {b.people}",
        ]
        if b.comment:
            desc.append(f"Комментарий: {b.comment}")
        if username:
            desc.append(f"Telegram: @{username}")
        desc.append(f"Заявка #{booking_id} из Telegram-бота")
        return {
            "summary": summary,
            "description": "\n".join(desc),
            "start": {"dateTime": slot_start.isoformat(), "timeZone": str(self.cfg.tz)},
            "end": {"dateTime": slot_end.isoformat(), "timeZone": str(self.cfg.tz)},
            "extendedProperties": {"private": {"booking_id": str(booking_id)}},
        }

    async def create(self, nb: NewBooking) -> Booking:
        """Повторно проверяет время и ставит в календарь заявку «⏳ ожидает»."""
        async with self._lock:
            if nb.nominal_start < self.now():
                raise SlotTaken
            busy, o, c = await self.day(nb.resource, nb.nominal_start.date())
            slot = fit_booking(nb.nominal_start, nb.duration, busy, o, c, self.cfg.gap)
            if slot is None:
                raise SlotTaken
            booking_id = self.db.create(
                user_id=nb.user_id,
                username=nb.username,
                resource_key=nb.resource.key,
                calendar_id=nb.resource.calendar_id,
                start=slot.start,
                end=slot.end,
                name=nb.name,
                phone=nb.phone,
                people=nb.people,
                comment=nb.comment,
                status=dbm.PENDING,
                created_at=self.now(),
            )
            try:
                event_id = await self.cal.insert_event(
                    nb.resource.calendar_id,
                    self._event_body(nb, booking_id, slot.start, slot.end, pending=True),
                )
            except Exception:
                self.db.set_status(booking_id, dbm.CANCELLED)
                raise
            self.db.set_event(booking_id, event_id)
        return self.db.get(booking_id)

    async def approve(self, booking_id: int) -> Booking | None:
        b = self.db.get(booking_id)
        if not b or b.status != dbm.PENDING or not b.event_id:
            return None
        if not await self.cal.event_exists(b.calendar_id, b.event_id):
            self.db.set_status(b.id, dbm.CANCELLED)
            return None
        await self.cal.patch_event(
            b.calendar_id, b.event_id,
            {"summary": self._event_body(b, b.id, b.start, b.end, pending=False)["summary"]},
        )
        if not self.db.set_status(b.id, dbm.APPROVED, only_if=(dbm.PENDING,)):
            return None
        # Напоминания, время которых уже прошло, не отправляем задним числом.
        now = self.now()
        b = self.db.get(b.id)
        for td in self.cfg.reminders:
            if now >= b.start - td:
                self.db.mark_reminded(b, reminder_tag(td))
        return b

    async def reject(self, booking_id: int) -> Booking | None:
        b = self.db.get(booking_id)
        if not b or not self.db.set_status(b.id, dbm.REJECTED, only_if=(dbm.PENDING,)):
            return None
        if b.event_id:
            await self.cal.delete_event(b.calendar_id, b.event_id)
        return self.db.get(b.id)

    async def cancel_by_client(self, booking_id: int, user_id: int) -> Booking | None:
        b = self.db.get(booking_id)
        if not b or b.user_id != user_id:
            return None
        if not self.db.set_status(b.id, dbm.CANCELLED):
            return None
        if b.event_id:
            await self.cal.delete_event(b.calendar_id, b.event_id)
        return self.db.get(b.id)


def reminder_tag(td: timedelta) -> str:
    return f"{int(td.total_seconds() // 60)}m"
