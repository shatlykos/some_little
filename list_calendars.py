"""Проверка доступа: показывает календари, которые видит сервисный аккаунт,
и проверяет, что каждый календарь из config.yaml доступен.

Запуск:  python list_calendars.py
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")

from bot.config import load_config  # noqa: E402
from bot.gcal import Calendar  # noqa: E402


async def main() -> None:
    cfg = load_config()
    with open(cfg.google_credentials, encoding="utf-8") as f:
        email = json.load(f)["client_email"]
    print(f"Сервисный аккаунт: {email}")
    print("Этот адрес нужно добавить в «Открыть доступ» каждого календаря.\n")

    cal = Calendar(cfg)
    items = await cal.calendar_list()
    print("Календари в списке сервисного аккаунта:")
    for c in items:
        print(f"  {c.get('summary')!r:40} id = {c['id']}")
    if not items:
        print("  (пусто — это нормально, если календари расшарены, но ещё не добавлены в список)")

    print("\nПроверка календарей из config.yaml:")
    now = datetime.now(cfg.tz)
    checks = [(cfg.events_calendar_name, cfg.events_calendar_id)] + [
        (r.name, r.calendar_id) for r in cfg.resources
    ]
    ok = True
    for name, cid in checks:
        try:
            events = await cal.list_events(cid, now, now + timedelta(days=7))
            print(f"  ✅ {name}: доступен, событий на 7 дней: {len(events)}")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"  ❌ {name} ({cid}): {e}")
    print("\nВсё готово!" if ok else "\nИсправьте ошибки выше (см. README, шаг 3).")


asyncio.run(main())
