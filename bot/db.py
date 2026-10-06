"""Локальная база заявок (SQLite). Источник правды о занятости — Google Calendar,
здесь хранится только то, чего нет в календаре: кто из Telegram сделал бронь,
статус заявки и какие напоминания уже отправлены."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"
CANCELLED = "cancelled"
ACTIVE = (PENDING, APPROVED)

SCHEMA = """
CREATE TABLE IF NOT EXISTS bookings (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    username      TEXT,
    resource_key  TEXT NOT NULL,
    calendar_id   TEXT NOT NULL,
    event_id      TEXT,
    start         TEXT NOT NULL,
    end           TEXT NOT NULL,
    name          TEXT NOT NULL,
    phone         TEXT NOT NULL,
    people        INTEGER NOT NULL,
    comment       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL,
    reminded      TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bookings_user ON bookings(user_id);
CREATE INDEX IF NOT EXISTS idx_bookings_status ON bookings(status);
"""


@dataclass
class Booking:
    id: int
    user_id: int
    username: str | None
    resource_key: str
    calendar_id: str
    event_id: str | None
    start: datetime
    end: datetime
    name: str
    phone: str
    people: int
    comment: str
    status: str
    reminded: set[str]
    created_at: datetime


class DB:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @staticmethod
    def _row(r: sqlite3.Row) -> Booking:
        return Booking(
            id=r["id"],
            user_id=r["user_id"],
            username=r["username"],
            resource_key=r["resource_key"],
            calendar_id=r["calendar_id"],
            event_id=r["event_id"],
            start=datetime.fromisoformat(r["start"]),
            end=datetime.fromisoformat(r["end"]),
            name=r["name"],
            phone=r["phone"],
            people=r["people"],
            comment=r["comment"],
            status=r["status"],
            reminded=set(filter(None, r["reminded"].split(","))),
            created_at=datetime.fromisoformat(r["created_at"]),
        )

    def create(self, **kw) -> int:
        cols = ", ".join(kw)
        marks = ", ".join("?" for _ in kw)
        values = [v.isoformat() if isinstance(v, datetime) else v for v in kw.values()]
        cur = self.conn.execute(f"INSERT INTO bookings ({cols}) VALUES ({marks})", values)
        self.conn.commit()
        return cur.lastrowid

    def get(self, booking_id: int) -> Booking | None:
        r = self.conn.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        return self._row(r) if r else None

    def set_event(self, booking_id: int, event_id: str) -> None:
        self.conn.execute("UPDATE bookings SET event_id = ? WHERE id = ?", (event_id, booking_id))
        self.conn.commit()

    def set_status(self, booking_id: int, status: str, only_if: tuple[str, ...] = ACTIVE) -> bool:
        """Меняет статус, только если текущий входит в only_if. True — если изменили.
        Защищает от двойного нажатия кнопок (например, «Подтвердить» после отмены)."""
        marks = ", ".join("?" for _ in only_if)
        cur = self.conn.execute(
            f"UPDATE bookings SET status = ? WHERE id = ? AND status IN ({marks})",
            (status, booking_id, *only_if),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def mark_reminded(self, booking: Booking, tag: str) -> None:
        booking.reminded.add(tag)
        self.conn.execute(
            "UPDATE bookings SET reminded = ? WHERE id = ?",
            (",".join(sorted(booking.reminded)), booking.id),
        )
        self.conn.commit()

    def user_active(self, user_id: int, now: datetime) -> list[Booking]:
        rows = self.conn.execute(
            "SELECT * FROM bookings WHERE user_id = ? AND status IN (?, ?) ORDER BY start",
            (user_id, *ACTIVE),
        ).fetchall()
        return [b for b in map(self._row, rows) if b.end > now]

    def by_status(self, status: str) -> list[Booking]:
        rows = self.conn.execute(
            "SELECT * FROM bookings WHERE status = ? ORDER BY start", (status,)
        ).fetchall()
        return [self._row(r) for r in rows]
