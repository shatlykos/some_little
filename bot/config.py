from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# Тексты по умолчанию — работают, даже если в config.yaml их нет.
DEFAULT_RULES = "🍽 Пожалуйста, не приносите с собой еду и напитки."
DEFAULT_NOTES = {"workshop": "📺 В аренду входит телевизор."}


@dataclass(frozen=True)
class Resource:
    key: str
    name: str
    emoji: str
    calendar_id: str
    min_duration: timedelta
    aliases: tuple[str, ...]
    note: str = ""  # что входит в аренду и т. п. — показывается клиенту

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
    rules: str = DEFAULT_RULES  # общие правила — показываются клиенту

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
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        raise SystemExit(f"Не найден {path.name}. Запустите install.bat") from None
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (строка {mark.line + 1})" if mark else ""
        raise SystemExit(
            f"Ошибка в {path.name}{where}: проверьте кавычки и отступы рядом с этой строкой. "
            "Отступы — только пробелами, как в config.example.yaml."
        ) from None

    token = os.environ.get("BOT_TOKEN", "").strip().strip("'\"").strip()
    admin = os.environ.get("ADMIN_ID", "").strip().strip("'\"").strip()
    if not token or not admin:
        raise SystemExit("Заполните BOT_TOKEN и ADMIN_ID в файле .env")
    if not re.fullmatch(r"\d{5,}:[A-Za-z0-9_-]{30,}", token) or token.startswith("123456789:"):
        raise SystemExit(
            "BOT_TOKEN в .env записан неверно. Скопируйте токен из @BotFather целиком, "
            "вида 7712345678:AAH..., без пробелов и кавычек."
        )
    if not admin.isdigit():
        raise SystemExit("ADMIN_ID в .env должен состоять только из цифр (ваш Id из @userinfobot).")

    resources = tuple(
        Resource(
            key=r["key"],
            name=r["name"],
            emoji=r.get("emoji", "•"),
            calendar_id=str(r["calendar_id"]).strip(),
            min_duration=timedelta(minutes=int(r["min_minutes"])),
            aliases=tuple(a.lower() for a in r.get("aliases", [r["name"]])),
            note=str(r.get("note", DEFAULT_NOTES.get(r["key"], "")) or "").strip(),
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
        events_calendar_id=str(raw["events_calendar"]["calendar_id"]).strip(),
        events_calendar_name=raw["events_calendar"].get("name", "Афиша"),
        resources=resources,
        db_path=ROOT / "bookings.db",
        rules=str(raw.get("rules", DEFAULT_RULES) or "").strip(),
    )
