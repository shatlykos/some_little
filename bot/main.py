from __future__ import annotations

import asyncio
import logging
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from .config import ROOT, load_config
from .db import DB
from .gcal import Calendar
from .handlers import router
from .reminders import reminders_loop
from .service import Service


def setup_logging() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    file = RotatingFileHandler(ROOT / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    file.setFormatter(fmt)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logging.basicConfig(level=logging.INFO, handlers=[file, console])
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)


async def run() -> None:
    setup_logging()
    cfg = load_config()
    svc = Service(cfg, Calendar(cfg), DB(cfg.db_path))

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp["cfg"] = cfg
    dp["svc"] = svc
    dp.include_router(router)

    reminders = asyncio.create_task(reminders_loop(bot, svc, cfg))
    logging.info("Бот запущен")
    try:
        await dp.start_polling(bot)
    finally:
        reminders.cancel()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit) as e:
        if isinstance(e, SystemExit) and e.code not in (None, 0):
            raise


if __name__ == "__main__":
    main()
