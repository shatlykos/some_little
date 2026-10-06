"""Обёртка над Google Calendar API.

Библиотека googleapiclient синхронная и не потокобезопасная, поэтому каждый
вызов выполняется в отдельном потоке (asyncio.to_thread) со своим объектом
service. Описание API берётся из пакета (static discovery), без запроса в сеть.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .config import Config, Resource
from .google_auth import load_credentials
from .slots import Busy

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class CalEvent:
    id: str
    summary: str
    location: str
    start: datetime
    end: datetime
    all_day: bool


class Calendar:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tz: ZoneInfo = cfg.tz
        self._creds = load_credentials(cfg.google_credentials, cfg.google_token)

    def _service(self):
        return build(
            "calendar", "v3", credentials=self._creds, cache_discovery=False
        )

    # ---------- низкоуровневые вызовы ----------

    def _parse_dt(self, value: dict) -> tuple[datetime, bool]:
        if "dateTime" in value:
            return datetime.fromisoformat(value["dateTime"]).astimezone(self.tz), False
        d = date.fromisoformat(value["date"])
        return datetime.combine(d, time(0), self.tz), True

    def _list_sync(self, calendar_id: str, t_min: datetime, t_max: datetime) -> list[CalEvent]:
        svc = self._service()
        out: list[CalEvent] = []
        page = None
        while True:
            resp = (
                svc.events()
                .list(
                    calendarId=calendar_id,
                    timeMin=t_min.isoformat(),
                    timeMax=t_max.isoformat(),
                    singleEvents=True,  # повторяющиеся события разворачиваются
                    orderBy="startTime",
                    maxResults=2500,
                    pageToken=page,
                )
                .execute()
            )
            for e in resp.get("items", []):
                if e.get("status") == "cancelled":
                    continue
                if e.get("transparency") == "transparent":  # помечено «Свободен»
                    continue
                start, all_day = self._parse_dt(e["start"])
                end, _ = self._parse_dt(e["end"])
                out.append(
                    CalEvent(
                        id=e["id"],
                        summary=e.get("summary", "") or "",
                        location=e.get("location", "") or "",
                        start=start,
                        end=end,
                        all_day=all_day,
                    )
                )
            page = resp.get("nextPageToken")
            if not page:
                return out

    def _insert_sync(self, calendar_id: str, body: dict) -> str:
        ev = self._service().events().insert(calendarId=calendar_id, body=body).execute()
        return ev["id"]

    def _patch_sync(self, calendar_id: str, event_id: str, body: dict) -> None:
        self._service().events().patch(
            calendarId=calendar_id, eventId=event_id, body=body
        ).execute()

    def _delete_sync(self, calendar_id: str, event_id: str) -> None:
        try:
            self._service().events().delete(
                calendarId=calendar_id, eventId=event_id
            ).execute()
        except HttpError as e:
            if e.resp.status not in (404, 410):  # уже удалено — не ошибка
                raise

    def _exists_sync(self, calendar_id: str, event_id: str) -> bool:
        try:
            ev = self._service().events().get(
                calendarId=calendar_id, eventId=event_id
            ).execute()
        except HttpError as e:
            if e.resp.status in (404, 410):
                return False
            raise
        return ev.get("status") != "cancelled"

    def _calendar_list_sync(self) -> list[dict]:
        return self._service().calendarList().list().execute().get("items", [])

    # ---------- асинхронный интерфейс ----------

    async def list_events(self, calendar_id: str, t_min: datetime, t_max: datetime) -> list[CalEvent]:
        return await asyncio.to_thread(self._list_sync, calendar_id, t_min, t_max)

    async def insert_event(self, calendar_id: str, body: dict) -> str:
        return await asyncio.to_thread(self._insert_sync, calendar_id, body)

    async def patch_event(self, calendar_id: str, event_id: str, body: dict) -> None:
        await asyncio.to_thread(self._patch_sync, calendar_id, event_id, body)

    async def delete_event(self, calendar_id: str, event_id: str) -> None:
        await asyncio.to_thread(self._delete_sync, calendar_id, event_id)

    async def event_exists(self, calendar_id: str, event_id: str) -> bool:
        return await asyncio.to_thread(self._exists_sync, calendar_id, event_id)

    async def calendar_list(self) -> list[dict]:
        return await asyncio.to_thread(self._calendar_list_sync)

    # ---------- бизнес-уровень ----------

    def day_bounds(self, day: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(day, self.cfg.open_time, self.tz),
            datetime.combine(day, self.cfg.close_time, self.tz),
        )

    @staticmethod
    def location_matches(resource: Resource, location: str) -> bool:
        loc = location.lower()
        return any(alias in loc for alias in resource.aliases)

    async def afisha(self, t_min: datetime, t_max: datetime) -> list[CalEvent]:
        return await self.list_events(self.cfg.events_calendar_id, t_min, t_max)

    async def busy_for_day(self, resource: Resource, day: date) -> list[Busy]:
        """Занятость ресурса за сутки: его собственный календарь (анонимно)
        + мероприятия из афиши, где в «Месте» указан этот ресурс (с названием)."""
        t_min = datetime.combine(day, time(0), self.tz)
        t_max = t_min + timedelta(days=1)
        own, events = await asyncio.gather(
            self.list_events(resource.calendar_id, t_min, t_max),
            self.afisha(t_min, t_max),
        )
        busy = [Busy(e.start, e.end) for e in own]
        busy += [
            Busy(e.start, e.end, e.summary or "Мероприятие")
            for e in events
            if self.location_matches(resource, e.location)
        ]
        return busy
