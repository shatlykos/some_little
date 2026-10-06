from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Resource:
    key: str
    name: str
    emoji: str
    calendar_id: str
    min_duration: timedelta
    aliases: tuple[str, ...]

    @property
    def title(self) -> str:
        return f"{self.emoji} {self.name}"


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_id: int
    tz: ZoneInfo
    google_credentials: Path
    google_token: Path
    open_time: time
    close_time: time
    step: timedelta
    gap: timedelta
    horizon_days: int
    reminders: tuple[timedelta, ...]
    events_calendar_id: str
    events_calendar_name: str
    resources: tuple[Resource, ...]
    db_path: Path

    def resource(self, key: str) -> Resource:
        for r in self.resources:
            if r.key == key:
                return r
        raise KeyError(key)


def _parse_time(value: str) -> time:
    h, m = str(value).split(":")
    return time(int(h), int(m))


def load_config(path: Path | None = None) -> Config:
    load_dotenv(ROOT / ".env")
    path = path or ROOT / "config.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    token = os.environ.get("BOT_TOKEN", "").strip()
    admin = os.environ.get("ADMIN_ID", "").strip()
    if not token or not admin:
        raise SystemExit("Заполните BOT_TOKEN и ADMIN_ID в файле .env")

    resources = tuple(
        Resource(
            key=r["key"],
            name=r["name"],
            emoji=r.get("emoji", "•"),
            calendar_id=r["calendar_id"],
            min_duration=timedelta(minutes=int(r["min_minutes"])),
            aliases=tuple(a.lower() for a in r.get("aliases", [r["name"]])),
        )
        for r in raw["resources"]
    )
    creds = ROOT / raw.get("google_credentials", "credentials.json")
    google_token = ROOT / raw.get("google_token", "token.json")

    return Config(
        bot_token=token,
        admin_id=int(admin),
        tz=ZoneInfo(raw.get("timezone", "Asia/Tbilisi")),
        google_credentials=creds,
        google_token=google_token,
        open_time=_parse_time(raw.get("open_time", "09:00")),
        close_time=_parse_time(raw.get("close_time", "22:00")),
        step=timedelta(minutes=int(raw.get("step_minutes", 30))),
        gap=timedelta(minutes=int(raw.get("gap_minutes", 5))),
        horizon_days=int(raw.get("booking_horizon_days", 30)),
        reminders=tuple(
            timedelta(hours=float(h)) for h in raw.get("reminders_hours", [24, 2])
        ),
        events_calendar_id=raw["events_calendar"]["calendar_id"],
        events_calendar_name=raw["events_calendar"].get("name", "Афиша"),
        resources=resources,
        db_path=ROOT / "bookings.db",
    )
