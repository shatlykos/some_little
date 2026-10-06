from __future__ import annotations

import asyncio
import logging
import re
from datetime import time, timedelta

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, ErrorEvent, Message, ReplyKeyboardRemove

from . import db as dbm
from . import keyboards as kb
from .config import Config, Resource
from .db import Booking
from .fmt import day_long, duration, hm, q, span
from .service import NewBooking, Service, SlotTaken

log = logging.getLogger(__name__)
router = Router()

STATUS_TEXT = {
    dbm.PENDING: "⏳ ожидает подтверждения",
    dbm.APPROVED: "✅ подтверждена",
    dbm.REJECTED: "❌ отклонена",
    dbm.CANCELLED: "🚫 отменена",
}


class Book(StatesGroup):
    name = State()
    phone = State()
    people = State()
    comment = State()
    confirm = State()


def booking_card(cfg: Config, b: Booking, with_contacts: bool = True) -> str:
    r = cfg.resource(b.resource_key)
    lines = [
        f"<b>Заявка #{b.id}</b> — {STATUS_TEXT.get(b.status, b.status)}",
        f"{r.title}",
        f"📅 {day_long(b.start.date())}",
        f"🕐 {span(b.start, b.end)} ({duration(b.end - b.start)})",
    ]
    if with_contacts:
        lines += [
            f"👤 {q(b.name)}",
            f"📱 {q(b.phone)}",
            f"👥 {b.people} чел.",
        ]
        if b.comment:
            lines.append(f"💬 {q(b.comment)}")
        if b.username:
            lines.append(f"Telegram: @{q(b.username)}")
    return "\n".join(lines)


async def _resource_from_state(state: FSMContext, cfg: Config) -> Resource | None:
    key = (await state.get_data()).get("resource")
    try:
        return cfg.resource(key) if key else None
    except KeyError:
        return None


async def _expired(cb: CallbackQuery) -> None:
    await cb.answer("Эта кнопка устарела. Начните заново из меню.", show_alert=True)


# ====================== меню ======================

@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext, cfg: Config) -> None:
    await state.clear()
    text = (
        "Здравствуйте! Это бот бронирования <b>Galaxy Book</b>.\n\n"
        "Здесь можно посмотреть, когда свободны помещения, узнать о мероприятиях "
        "и оставить заявку на бронь.\n\n"
        "Минимальное время аренды — 1 час, фортепиано — 30 минут."
    )
    if msg.from_user.id == cfg.admin_id:
        text += "\n\n<i>Вы администратор. /pending — заявки, ожидающие решения.</i>"
    await msg.answer(text, reply_markup=kb.main_menu())


@router.message(Command("cancel"))
@router.message(F.text == kb.BTN_CANCEL)
async def cmd_cancel(msg: Message, state: FSMContext) -> None:
    await state.clear()
    await msg.answer("Отменено.", reply_markup=kb.main_menu())


@router.message(F.text == kb.BTN_BOOK)
async def menu_book(msg: Message, state: FSMContext, cfg: Config) -> None:
    await state.clear()
    await msg.answer("Что хотите забронировать?", reply_markup=kb.resources_kb(cfg.resources))


@router.message(F.text == kb.BTN_VIEW)
async def menu_view(msg: Message, state: FSMContext, svc: Service) -> None:
    await state.clear()
    await msg.answer(
        "Выберите день — покажу занятость всех помещений:",
        reply_markup=kb.days_kb(svc.bookable_days(), mode="v", page=0, back_to=None),
    )


async def _afisha_text(svc: Service, cfg: Config, key: str) -> str:
    now = svc.now()
    items = await svc.cal.afisha(now, now + timedelta(days=cfg.horizon_days))
    items = [i for i in items if i.end > now]
    resource = None
    if key:
        try:
            resource = cfg.resource(key)
        except KeyError:
            key = ""
        items = [i for i in items if resource and i.resource == resource]

    header = f"<b>🎭 Афиша: {resource.title}</b>" if resource else "<b>🎭 Ближайшие мероприятия</b>"
    if not items:
        return header + "\n\nВ ближайшие дни мероприятий не запланировано."
    lines = [header]
    current_day = None
    for i in items:
        d = i.start.date()
        if d != current_day:
            current_day = d
            lines.append(f"\n<b>{day_long(d).capitalize()}</b>")
        when = "весь день" if i.all_day else span(i.start, i.end)
        place = ""
        if not resource:
            if i.resource:
                place = f" · {i.resource.title}"
            elif i.place:
                place = f" · {q(i.place)}"
        lines.append(f"• {when} — {q(i.title)}{place}")
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3900].rsplit("\n", 1)[0] + "\n…"
    return text


@router.message(F.text == kb.BTN_AFISHA)
async def menu_afisha(msg: Message, svc: Service, cfg: Config) -> None:
    await msg.answer(await _afisha_text(svc, cfg, ""), reply_markup=kb.afisha_kb(cfg.resources, ""))


@router.callback_query(kb.AfishaCB.filter())
async def on_afisha_filter(cb: CallbackQuery, callback_data: kb.AfishaCB,
                           svc: Service, cfg: Config) -> None:
    await cb.answer()
    text = await _afisha_text(svc, cfg, callback_data.key)
    try:
        await cb.message.edit_text(text, reply_markup=kb.afisha_kb(cfg.resources, callback_data.key))
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            raise


@router.message(F.text == kb.BTN_MINE)
async def menu_mine(msg: Message, svc: Service, cfg: Config) -> None:
    items = svc.db.user_active(msg.from_user.id, svc.now())
    if not items:
        await msg.answer("У вас нет активных броней.", reply_markup=kb.main_menu())
        return
    await msg.answer(f"Ваши брони ({len(items)}):")
    for b in items:
        await msg.answer(booking_card(cfg, b, with_contacts=False), reply_markup=kb.my_booking_kb(b.id))


# ====================== обзор дня (все помещения) ======================

async def _show_overview(cb: CallbackQuery, svc: Service, cfg: Config, day) -> None:
    await cb.message.edit_text("⏳ Загружаю расписание…")
    results = await asyncio.gather(*(svc.day(r, day) for r in cfg.resources))
    parts = [f"<b>Занятость: {day_long(day)}</b>"]
    for r, (busy, o, c) in zip(cfg.resources, results):
        parts.append(f"\n<b>{r.title}</b>\n" + svc.describe_day(r, day, busy, o, c, header=False))
    parts.append("\n<b>Забронировать:</b>")
    await cb.message.edit_text("\n".join(parts), reply_markup=kb.overview_kb(cfg.resources, day))


# ====================== бронирование ======================

@router.callback_query(kb.ResCB.filter())
async def on_resource(cb: CallbackQuery, callback_data: kb.ResCB, state: FSMContext,
                      svc: Service, cfg: Config) -> None:
    try:
        resource = cfg.resource(callback_data.key)
    except KeyError:
        return await _expired(cb)
    await state.clear()
    await state.update_data(resource=resource.key)
    await cb.answer()
    if callback_data.day:
        day = kb.parse_ymd(callback_data.day)
        if svc.is_bookable_day(day):
            await state.update_data(day=callback_data.day)
            # Новым сообщением, чтобы обзор всех помещений остался на экране.
            return await _show_starts(cb.message.chat.id, cb.bot, svc, resource, day)
    await cb.message.edit_text(
        f"<b>{resource.title}</b>\nВыберите день:",
        reply_markup=kb.days_kb(svc.bookable_days(), mode="b", page=0, back_to="res"),
    )


@router.callback_query(kb.DayCB.filter())
async def on_day(cb: CallbackQuery, callback_data: kb.DayCB, state: FSMContext,
                 svc: Service, cfg: Config) -> None:
    back_to = "res" if callback_data.mode == "b" else None
    if callback_data.page >= 0:  # листание
        await cb.answer()
        return await cb.message.edit_reply_markup(
            reply_markup=kb.days_kb(svc.bookable_days(), callback_data.mode, callback_data.page, back_to)
        )

    day = kb.parse_ymd(callback_data.day)
    if not svc.is_bookable_day(day):
        return await cb.answer("Этот день уже недоступен, выберите другой.", show_alert=True)
    await cb.answer()

    if callback_data.mode == "v":
        return await _show_overview(cb, svc, cfg, day)

    resource = await _resource_from_state(state, cfg)
    if not resource:
        return await _expired(cb)
    await state.update_data(day=callback_data.day)
    await cb.message.edit_text("⏳ Загружаю расписание…")
    await _show_starts(cb.message.chat.id, cb.bot, svc, resource, day, edit=cb.message)


async def _show_starts(chat_id: int, bot: Bot, svc: Service, resource: Resource, day,
                       edit: Message | None = None) -> None:
    busy, o, c = await svc.day(resource, day)
    text = svc.describe_day(resource, day, busy, o, c)
    starts = await svc.starts(resource, day)
    if starts:
        text += "\n\n<b>Выберите время начала:</b>"
        markup = kb.starts_kb(starts)
    else:
        text += "\n\nВ этот день записаться уже нельзя — выберите другой день."
        markup = kb.back_kb("⬅️ Другой день", "days")
    if edit:
        await edit.edit_text(text, reply_markup=markup)
    else:
        await bot.send_message(chat_id, text, reply_markup=markup)


@router.callback_query(kb.NavCB.filter())
async def on_nav(cb: CallbackQuery, callback_data: kb.NavCB, state: FSMContext,
                 svc: Service, cfg: Config) -> None:
    await cb.answer()
    if callback_data.to == "res":
        await state.clear()
        return await cb.message.edit_text(
            "Что хотите забронировать?", reply_markup=kb.resources_kb(cfg.resources)
        )
    if callback_data.to == "vdays":
        return await cb.message.edit_text(
            "Выберите день — покажу занятость всех помещений:",
            reply_markup=kb.days_kb(svc.bookable_days(), mode="v", page=0, back_to=None),
        )
    resource = await _resource_from_state(state, cfg)
    if not resource:
        return await _expired(cb)
    if callback_data.to == "days":
        return await cb.message.edit_text(
            f"<b>{resource.title}</b>\nВыберите день:",
            reply_markup=kb.days_kb(svc.bookable_days(), mode="b", page=0, back_to="res"),
        )
    if callback_data.to == "starts":
        data = await state.get_data()
        if "day" not in data:
            return await _expired(cb)
        await _show_starts(cb.message.chat.id, cb.bot, svc, resource,
                           kb.parse_ymd(data["day"]), edit=cb.message)


@router.callback_query(kb.StartCB.filter())
async def on_start_time(cb: CallbackQuery, callback_data: kb.StartCB, state: FSMContext,
                        svc: Service, cfg: Config) -> None:
    resource = await _resource_from_state(state, cfg)
    data = await state.get_data()
    if not resource or "day" not in data:
        return await _expired(cb)
    day = kb.parse_ymd(data["day"])
    start = svc.at(day, time(int(callback_data.hhmm[:2]), int(callback_data.hhmm[2:])))
    options = await svc.durations(resource, start)
    if not options:
        await cb.answer("Это время уже занято, выберите другое.", show_alert=True)
        return await _show_starts(cb.message.chat.id, cb.bot, svc, resource, day, edit=cb.message)
    await cb.answer()
    await state.update_data(start=callback_data.hhmm)
    text = (
        f"<b>{resource.title}</b>\n"
        f"📅 {day_long(day)}, начало в {hm(start)}\n\n"
        "<b>Выберите длительность:</b>"
    )
    if any((s.start, s.end) != (s.nominal_start, s.nominal_end) for _, s in options):
        text += (
            f"\n\n<i>Рядом есть другая бронь, поэтому время сдвинуто, "
            f"чтобы между бронями был перерыв {int(cfg.gap.total_seconds() // 60)} минут.</i>"
        )
    await cb.message.edit_text(text, reply_markup=kb.durations_kb(options))


@router.callback_query(kb.DurCB.filter())
async def on_duration(cb: CallbackQuery, callback_data: kb.DurCB, state: FSMContext,
                      svc: Service, cfg: Config) -> None:
    resource = await _resource_from_state(state, cfg)
    data = await state.get_data()
    if not resource or "day" not in data or "start" not in data:
        return await _expired(cb)
    day = kb.parse_ymd(data["day"])
    start = svc.at(day, time(int(data["start"][:2]), int(data["start"][2:])))
    chosen = timedelta(minutes=callback_data.minutes)
    slot = next((s for d, s in await svc.durations(resource, start) if d == chosen), None)
    if slot is None:
        await cb.answer("Это время уже занято, выберите другое.", show_alert=True)
        return await _show_starts(cb.message.chat.id, cb.bot, svc, resource, day, edit=cb.message)
    await cb.answer()
    await state.update_data(minutes=callback_data.minutes)
    await state.set_state(Book.name)
    await cb.message.edit_text(
        f"<b>{resource.title}</b>\n📅 {day_long(day)}\n🕐 {span(slot.start, slot.end)}"
    )
    tg_name = (cb.from_user.full_name or "").strip()
    await cb.message.answer(
        "Как вас зовут?",
        reply_markup=kb.reply(tg_name) if tg_name else kb.reply(),
    )


@router.message(Book.name, F.text)
async def ask_phone(msg: Message, state: FSMContext) -> None:
    name = msg.text.strip()
    if not 2 <= len(name) <= 60:
        return await msg.answer("Пожалуйста, введите имя (от 2 до 60 символов).")
    await state.update_data(name=name)
    await state.set_state(Book.phone)
    await msg.answer(
        "Ваш номер телефона? Нажмите кнопку ниже или введите вручную.",
        reply_markup=kb.phone_kb(),
    )


PHONE_RE = re.compile(r"^\+?[\d\s\-()]{7,20}$")


@router.message(Book.phone, F.contact)
@router.message(Book.phone, F.text)
async def ask_people(msg: Message, state: FSMContext) -> None:
    if msg.contact:
        phone = msg.contact.phone_number
        if not phone.startswith("+"):
            phone = "+" + phone
    else:
        phone = msg.text.strip()
        digits = re.sub(r"\D", "", phone)
        if not PHONE_RE.match(phone) or not 7 <= len(digits) <= 15:
            return await msg.answer("Номер выглядит неверно. Пример: +995 555 12 34 56")
    await state.update_data(phone=phone)
    await state.set_state(Book.people)
    await msg.answer("Сколько будет человек?", reply_markup=kb.reply("1", "2", "3"))


@router.message(Book.people, F.text)
async def ask_comment(msg: Message, state: FSMContext) -> None:
    text = msg.text.strip()
    if not text.isdigit() or not 1 <= int(text) <= 500:
        return await msg.answer("Введите количество человек числом, например 5.")
    await state.update_data(people=int(text))
    await state.set_state(Book.comment)
    await msg.answer(
        "Цель брони или комментарий? Например: «репетиция», «день рождения».",
        reply_markup=kb.reply(kb.BTN_SKIP),
    )


@router.message(Book.comment, F.text)
async def ask_confirm(msg: Message, state: FSMContext, svc: Service, cfg: Config) -> None:
    comment = "" if msg.text.strip() == kb.BTN_SKIP else msg.text.strip()[:500]
    await state.update_data(comment=comment)
    data = await state.get_data()
    resource = await _resource_from_state(state, cfg)
    if not resource:
        await state.clear()
        return await msg.answer("Что-то пошло не так, начните заново.", reply_markup=kb.main_menu())
    day = kb.parse_ymd(data["day"])
    start = svc.at(day, time(int(data["start"][:2]), int(data["start"][2:])))
    chosen = timedelta(minutes=data["minutes"])
    slot = next((s for d, s in await svc.durations(resource, start) if d == chosen), None)
    if slot is None:
        await state.clear()
        return await msg.answer(
            "К сожалению, это время уже заняли. Выберите другое.", reply_markup=kb.main_menu()
        )
    await state.set_state(Book.confirm)
    lines = [
        "<b>Проверьте заявку:</b>",
        f"{resource.title}",
        f"📅 {day_long(day)}",
        f"🕐 {span(slot.start, slot.end)}",
        f"👤 {q(data['name'])}",
        f"📱 {q(data['phone'])}",
        f"👥 {data['people']} чел.",
    ]
    if comment:
        lines.append(f"💬 {q(comment)}")
    await msg.answer("Почти готово!", reply_markup=ReplyKeyboardRemove())
    await msg.answer("\n".join(lines), reply_markup=kb.confirm_kb())


@router.callback_query(Book.confirm, kb.ConfirmCB.filter())
async def on_confirm(cb: CallbackQuery, callback_data: kb.ConfirmCB, state: FSMContext,
                     svc: Service, cfg: Config) -> None:
    data = await state.get_data()
    resource = await _resource_from_state(state, cfg)
    await state.clear()
    if not callback_data.ok:
        await cb.answer()
        await cb.message.edit_text("Заявка отменена.")
        return await cb.message.answer("Главное меню:", reply_markup=kb.main_menu())
    if not resource:
        return await _expired(cb)
    await cb.answer("Отправляю…")
    day = kb.parse_ymd(data["day"])
    nb = NewBooking(
        user_id=cb.from_user.id,
        username=cb.from_user.username,
        resource=resource,
        nominal_start=svc.at(day, time(int(data["start"][:2]), int(data["start"][2:]))),
        duration=timedelta(minutes=data["minutes"]),
        name=data["name"],
        phone=data["phone"],
        people=data["people"],
        comment=data.get("comment", ""),
    )
    try:
        booking = await svc.create(nb)
    except SlotTaken:
        await cb.message.edit_text("😔 К сожалению, это время только что заняли. Выберите другое.")
        return await cb.message.answer("Главное меню:", reply_markup=kb.main_menu())

    await cb.message.edit_text(
        booking_card(cfg, booking)
        + "\n\nЗаявка отправлена администратору. Мы напишем вам, когда она будет подтверждена."
    )
    await cb.message.answer("Главное меню:", reply_markup=kb.main_menu())
    try:
        await cb.bot.send_message(
            cfg.admin_id,
            "🔔 <b>Новая заявка</b>\n\n" + booking_card(cfg, booking),
            reply_markup=kb.admin_kb(booking.id),
        )
    except Exception:
        log.exception("Не удалось отправить заявку администратору")


@router.callback_query(kb.ConfirmCB.filter())
async def on_confirm_stale(cb: CallbackQuery) -> None:
    await _expired(cb)


# ====================== отмена клиентом ======================

@router.callback_query(kb.CancelCB.filter())
async def on_client_cancel(cb: CallbackQuery, callback_data: kb.CancelCB,
                           svc: Service, cfg: Config) -> None:
    b = svc.db.get(callback_data.id)
    if not b or b.user_id != cb.from_user.id:
        return await _expired(cb)
    if callback_data.action == "ask":
        await cb.answer()
        return await cb.message.edit_text(
            booking_card(cfg, b, with_contacts=False) + "\n\n<b>Отменить эту бронь?</b>",
            reply_markup=kb.cancel_ask_kb(b.id),
        )
    if callback_data.action == "no":
        await cb.answer("Бронь сохранена")
        return await cb.message.edit_text(booking_card(cfg, b, with_contacts=False))
    cancelled = await svc.cancel_by_client(b.id, cb.from_user.id)
    if not cancelled:
        await cb.answer("Эта бронь уже неактивна.", show_alert=True)
        return await cb.message.edit_text(booking_card(cfg, svc.db.get(b.id), with_contacts=False))
    await cb.answer("Бронь отменена")
    await cb.message.edit_text(booking_card(cfg, cancelled, with_contacts=False))
    try:
        await cb.bot.send_message(
            cfg.admin_id, "🚫 <b>Клиент отменил бронь</b>\n\n" + booking_card(cfg, cancelled)
        )
    except Exception:
        log.exception("Не удалось уведомить администратора об отмене")


# ====================== администратор ======================

@router.message(Command("pending"))
async def cmd_pending(msg: Message, svc: Service, cfg: Config) -> None:
    if msg.from_user.id != cfg.admin_id:
        return
    items = [b for b in svc.db.by_status(dbm.PENDING) if b.end > svc.now()]
    if not items:
        return await msg.answer("Нет заявок, ожидающих решения.")
    for b in items:
        await msg.answer(booking_card(cfg, b), reply_markup=kb.admin_kb(b.id))


@router.callback_query(kb.AdminCB.filter())
async def on_admin(cb: CallbackQuery, callback_data: kb.AdminCB, svc: Service, cfg: Config) -> None:
    if cb.from_user.id != cfg.admin_id:
        return await cb.answer("Недостаточно прав.", show_alert=True)
    if callback_data.action == "ok":
        b = await svc.approve(callback_data.id)
        client_text = "✅ <b>Ваша бронь подтверждена!</b>"
    else:
        b = await svc.reject(callback_data.id)
        client_text = (
            "❌ <b>К сожалению, заявка отклонена.</b>\n"
            "Попробуйте выбрать другое время или свяжитесь с нами."
        )
    if not b:
        current = svc.db.get(callback_data.id)
        await cb.answer("Заявка уже обработана, отменена или удалена из календаря.", show_alert=True)
        if current:
            await cb.message.edit_text(booking_card(cfg, current))
        return
    await cb.answer("Готово")
    await cb.message.edit_text(booking_card(cfg, b))
    try:
        await cb.bot.send_message(
            b.user_id, client_text + "\n\n" + booking_card(cfg, b, with_contacts=False)
        )
    except Exception:
        log.exception("Не удалось уведомить клиента %s", b.user_id)


# ====================== ошибки ======================

@router.errors()
async def on_error(event: ErrorEvent) -> None:
    log.exception("Ошибка при обработке обновления", exc_info=event.exception)
    upd = event.update
    text = "⚠️ Произошла ошибка. Попробуйте ещё раз чуть позже."
    try:
        if upd.callback_query:
            await upd.callback_query.answer(text, show_alert=True)
        elif upd.message:
            await upd.message.answer(text)
    except Exception:
        pass
