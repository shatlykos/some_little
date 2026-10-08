"""Проверка доступа: показывает все календари аккаунта (с идентификаторами)
и проверяет, что каждый календарь из config.yaml доступен.

Запуск:  python list_calendars.py   (или check.bat)
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding="utf-8")

from bot.config import load_config  # noqa: E402
from bot.gcal import Calendar  # noqa: E402
from bot.google_auth import is_service_account  # noqa: E402


async def main() -> None:
    cfg = load_config()
    if is_service_account(cfg.google_credentials):
        with open(cfg.google_credentials, encoding="utf-8") as f:
            email = json.load(f)["client_email"]
        print(f"Сервисный аккаунт: {email}")
        print("Этот адрес нужно добавить в «Открыть доступ» каждого календаря.\n")

    cal = Calendar(cfg)
    items = await cal.calendar_list()
    print("Ваши календари (скопируйте нужные id в config.yaml):")
    for c in items:
        print(f"  {c.get('summary', '')!r:35} id: {c['id']}")
    if not items:
        print("  (список пуст)")

    print("\nПроверка календарей из config.yaml:")
    now = datetime.now(cfg.tz)
    checks = [(cfg.events_calendar_name, cfg.events_calendar_id)] + [
        (r.name, r.calendar_id) for r in cfg.resources
    ]
    ok = True
    for name, cid in checks:
        try:
            events = await cal.list_events(cid, now, now + timedelta(days=7))
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"  ❌ {name} ({cid}): {e}")
            continue
        print(f"  ✅ {name}: доступен, событий на 7 дней: {len(events)}")
        for e in events:
            when = e.start.strftime("%d.%m") + (" весь день" if e.all_day else e.start.strftime(" %H:%M"))
            note = ""
            if e.from_bot:
                note = "  (бронь через бота)"
            elif cid == cfg.events_calendar_id:
                res = cal.resource_by_location(e.location)
                note = (f"  → помещение: {res.name}" if res
                        else "  → место не указано: помещение не занимает")
            print(f"       {when}  {e.summary}{note}")
    print("\nВсё готово!" if ok else "\nИсправьте calendar_id в config.yaml для календарей с ❌.")


asyncio.run(main())
