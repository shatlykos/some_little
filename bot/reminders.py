"""Фоновая задача: напоминания клиентам (по умолчанию за 24 ч и за 2 ч)."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from aiogram import Bot

from . import db as dbm
from .config import Config
from .fmt import day_long, duration, span
from .service import Service, reminder_tag

log = logging.getLogger(__name__)
CHECK_EVERY_SECONDS = 60


async def reminders_loop(bot: Bot, svc: Service, cfg: Config) -> None:
    while True:
        try:
            await _tick(bot, svc, cfg)
        except Exception:
            log.exception("Ошибка в напоминаниях")
        await asyncio.sleep(CHECK_EVERY_SECONDS)


async def _tick(bot: Bot, svc: Service, cfg: Config) -> None:
    now = svc.now()
    for b in svc.db.by_status(dbm.APPROVED):
        if b.start <= now:
            continue
        # Берём самое «близкое» напоминание, время которого наступило,
        # а более ранние помечаем, чтобы не слать два сообщения подряд.
        due = [td for td in cfg.reminders if now >= b.start - td and reminder_tag(td) not in b.reminded]
        if not due:
            continue
        # Если администратор удалил событие прямо в Google Calendar — бронь отменена.
        if b.event_id and not await svc.cal.event_exists(b.calendar_id, b.event_id):
            svc.db.set_status(b.id, dbm.CANCELLED)
            continue
        for td in due:
            svc.db.mark_reminded(b, reminder_tag(td))
        r = cfg.resource(b.resource_key)
        left_min = int((b.start - now).total_seconds() // 60)
        left_min -= left_min % 5
        days = (b.start.date() - now.date()).days
        when = {0: "Сегодня", 1: "Завтра"}.get(days, "Скоро")
        text = (
            f"⏰ <b>Напоминание</b>\n"
            f"{when} у вас бронь: {r.title}\n"
            f"📅 {day_long(b.start.date())}\n"
            f"🕐 {span(b.start, b.end)}"
        )
        if left_min >= 5:
            text += f"\n\nДо начала: {duration(timedelta(minutes=left_min))}."
        text += "\nЕсли планы изменились — отмените бронь в разделе «📋 Мои брони»."
        try:
            await bot.send_message(b.user_id, text)
        except Exception:
            log.exception("Не удалось отправить напоминание клиенту %s", b.user_id)
