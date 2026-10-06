from __future__ import annotations

import asyncio
import logging
import sys
import time
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNotFound, TelegramUnauthorizedError
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
    handlers: list[logging.Handler] = [file]
    if sys.stderr is not None:  # при фоновом запуске (pythonw) консоли нет
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        handlers.append(console)
    logging.basicConfig(level=logging.INFO, handlers=handlers)
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)


async def run() -> None:
    cfg = load_config()
    svc = Service(cfg, Calendar(cfg), DB(cfg.db_path))

    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    dp["cfg"] = cfg
    dp["svc"] = svc
    dp.include_router(router)

    try:
        me = await bot.get_me()
    except (TelegramNotFound, TelegramUnauthorizedError):
        await bot.session.close()
        raise SystemExit(
            "Telegram не принял BOT_TOKEN из .env. Скопируйте токен из @BotFather "
            "заново (/mybots → ваш бот → API Token)."
        )
    reminders = asyncio.create_task(reminders_loop(bot, svc, cfg))
    logging.info("Бот @%s запущен", me.username)
    try:
        await dp.start_polling(bot)
    finally:
        reminders.cancel()
        await bot.session.close()


def main() -> None:
    setup_logging()
    while True:
        try:
            asyncio.run(run())
            return
        except KeyboardInterrupt:
            return
        except SystemExit as e:
            # Ошибка настройки (.env, config.yaml, Google) — пишем в bot.log,
            # чтобы её было видно и при фоновом запуске.
            if e.code not in (None, 0):
                logging.error("Бот не запущен: %s", e.code)
            raise
        except Exception:
            # Например, пропал интернет при старте — пробуем снова.
            logging.exception("Бот упал, перезапуск через 15 секунд")
            time.sleep(15)


if __name__ == "__main__":
    main()
