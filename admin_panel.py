"""Админ-бот: работает ВНУТРИ процесса основного бота, читает базу напрямую.

Включается, если задан ADMIN_BOT_TOKEN. Отвечает только ADMIN_ID.
"""
from __future__ import annotations

import asyncio
import html as html_mod
import logging
import time
from datetime import datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot import config as cfg
from bot.db import Database
from bot.profile import week_bounds

log = logging.getLogger(__name__)
router = Router()

START_TS = time.time()
FOREVER_TS = 4102444800


@router.message.middleware
@router.callback_query.middleware
async def admin_only(handler, event, data):
    user = getattr(event, "from_user", None)
    if not user or user.id != cfg.ADMIN_ID:
        if isinstance(event, CallbackQuery):
            await event.answer("⛔ Нет доступа", show_alert=True)
        else:
            await event.answer("⛔ Нет доступа.")
        return
    return await handler(event, data)

MENU = (
    "🛡 <b>Панель администратора</b>\n\n"
    "Вся статистика бота «Бот твоих дел» — кнопки ниже.\n\n"
    "Команды:\n"
    "/stats — сводка\n"
    "/payments — последние платежи\n"
    "/suggestions — идеи пользователей\n"
    "/user &lt;id&gt; — карточка игрока\n"
    "/broadcast &lt;текст&gt; — рассылка всем\n"
    "/ping — статус системы"
)

BC_TEXTS: dict[int, str] = {}


def esc(s) -> str:
    return html_mod.escape(str(s or ""))


def fmt_dt(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="p:stats")],
        [InlineKeyboardButton(text="💳 Платежи", callback_data="p:pay")],
        [InlineKeyboardButton(text="💡 Идеи", callback_data="p:sug")],
    ])


def suggestion_kb(sid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Принять", callback_data=f"p:ok:{sid}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"p:no:{sid}"),
    ]])


async def stats_text(db: Database) -> str:
    day_start, _ = db.day_bounds()
    ws, _ = week_bounds()
    s = await db.stats()
    lines = [
        "📊 <b>Статистика бота твоих дел</b>",
        "",
        f"👥 Пользователей: <b>{s['users']}</b>",
        f"   • новых сегодня: {await db.new_users_count(day_start)}"
        f" · за неделю: {await db.new_users_count(ws)}",
        f"👑 Подписок: <b>{s['premium']}</b>",
        f"   • Обычная: {s['regular']} · Elite: {s['elite']}",
        "",
        "📋 Задачи:",
        f"   • всего: <b>{s['tasks_total']}</b>",
        f"   • активных сегодня: <b>{s['tasks_open']}</b>",
        f"   • выполнено сегодня: <b>{s['done_today']}</b>",
        "",
        "⭐ Доход:",
        f"   • всего: <b>{s['stars']} Stars</b>",
        f"   • сегодня: <b>{await db.stars_sum(day_start)}</b>"
        f" · за неделю: <b>{await db.stars_sum(ws)}</b>",
        "",
        f"💡 Идей на рассмотрении: <b>{len(await db.active_suggestions(limit=100))}</b>",
        f"⏱ Аптайм панели: {fmt_uptime()}",
    ]
    return "\n".join(lines)


def fmt_uptime() -> str:
    secs = int(time.time() - START_TS)
    d, r = divmod(secs, 86400)
    h, r = divmod(r, 3600)
    m, _ = divmod(r, 60)
    return (f"{d} д " if d else "") + f"{h} ч {m} мин"


async def payments_text(db: Database) -> str:
    pays = await db.recent_payments(15)
    if not pays:
        return "💳 Платежей пока не было."
    lines = ["💳 <b>Последние платежи</b>", ""]
    for p in pays:
        who = esc(p.get("first_name") or "Игрок")
        tag = f" (@{esc(p['username'])})" if p.get("username") else ""
        plan = f" · {esc(p['plan'].replace(':', ' · '))}" if p.get("plan") else ""
        lines.append(
            f"• <b>{who}</b>{tag}{plan} — <b>+{p['stars']}⭐</b> ({fmt_dt(p['created_at'])})\n"
            f"   id: <code>{p['user_id']}</code>"
        )
    return "\n".join(lines)


async def user_text(db: Database, uid: int) -> str:
    u = await db.user_detail(uid)
    if not u:
        return f"Пользователь <code>{uid}</code> не найден."
    tier = {"regular": "Обычная", "elite": "Elite", "none": "—"}[u["tier"]]
    if u["premium_until"] >= FOREVER_TS:
        until = "навсегда"
    elif u["premium_until"]:
        until = fmt_dt(u["premium_until"])
    else:
        until = "—"
    return "\n".join([
        f"👤 <b>{esc(u['first_name'] or 'Игрок')}</b>"
        + (f" (@{esc(u['username'])})" if u.get("username") else ""),
        f"id: <code>{u['telegram_id']}</code>",
        "",
        f"👑 Подписка: <b>{tier}</b> · до: {until}",
        f"📋 Задач: всего {u['tasks']['total']}"
        f" · активных {u['tasks']['active']}"
        f" · выполнено {u['tasks']['completed']}",
        f"💳 Платежей: {u['payments']['n']} на <b>{u['payments']['stars']}⭐</b>",
        f"📅 С нами с: {fmt_dt(u['created_at'])}",
    ])


# ---------- команды ----------

@router.message(CommandStart())
async def cmd_start(m: Message, db: Database):
    await m.answer(MENU, reply_markup=kb_main())


@router.message(Command("stats"))
async def cmd_stats(m: Message, db: Database):
    await m.answer(await stats_text(db), reply_markup=kb_main())


@router.message(Command("payments"))
async def cmd_payments(m: Message, db: Database):
    await m.answer(await payments_text(db))


@router.message(Command("suggestions"))
async def cmd_suggestions(m: Message, db: Database):
    sugs = await db.active_suggestions()
    if not sugs:
        await m.answer("💡 Активных идей нет.")
        return
    await m.answer(f"💡 Активных идей: <b>{len(sugs)}</b>")
    for sg in sugs[:10]:
        who = esc(sg.get("first_name") or "Игрок")
        tag = f" (@{esc(sg['username'])})" if sg.get("username") else ""
        await m.answer(
            f"💡 <b>#{sg['id']}</b> от {who}{tag} ({fmt_dt(sg['created_at'])}):\n\n«{esc(sg['text'])}»",
            reply_markup=suggestion_kb(sg["id"]),
        )


@router.message(Command("user"))
async def cmd_user(m: Message, db: Database):
    parts = (m.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await m.answer("Использование: <code>/user &lt;id&gt;</code>")
        return
    await m.answer(await user_text(db, int(parts[1])))


@router.message(Command("broadcast"))
async def cmd_broadcast(m: Message):
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование: <code>/broadcast &lt;текст&gt;</code>")
        return
    text = parts[1][:4000]
    bc_id = abs(hash(text)) % 10**9
    BC_TEXTS[bc_id] = text
    confirm = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📣 Отправить всем", callback_data=f"p:bc:{bc_id}")
    ]])
    await m.answer(
        "Отправить всем пользователям?\n\n" + html_mod.escape(text[:1000]),
        reply_markup=confirm,
    )


@router.message(Command("ping"))
async def cmd_ping(m: Message, db: Database):
    try:
        await db.get_user(0)
        db_ok = True
    except Exception:
        db_ok = False
    await m.answer(
        f"🟢 Все системы работают\n"
        f"База данных: {'ок' if db_ok else 'ОШИБКА'}\n"
        f"Аптайм контейнера: {fmt_uptime()}"
    )


# ---------- колбэки ----------

@router.callback_query(F.data.startswith("p:"))
async def on_press(cb: CallbackQuery, db: Database, main_bot: Bot, admin_bot: Bot):
    parts = cb.data.split(":")
    act = parts[1]

    if act == "stats":
        await cb.message.edit_text(await stats_text(db), reply_markup=kb_main())
        await cb.answer("Обновлено")
    elif act == "pay":
        await cb.message.edit_text(await payments_text(db))
        await cb.answer()
    elif act == "sug":
        sugs = await db.active_suggestions()
        if not sugs:
            await cb.message.edit_text("💡 Активных идей нет.")
        else:
            await cb.message.edit_text(f"💡 Активных идей: <b>{len(sugs)}</b>")
            for sg in sugs[:10]:
                await admin_bot.send_message(
                    cb.from_user.id,
                    f"💡 <b>#{sg['id']}</b> от {esc(sg.get('first_name') or 'Игрок')}:\n\n«{esc(sg['text'])}»",
                    reply_markup=suggestion_kb(sg["id"]),
                )
        await cb.answer()
    elif act in ("ok", "no"):
        sid = int(parts[2])
        ok_status = await db.set_suggestion_status(sid, "done" if act == "ok" else "declined")
        await cb.answer("Принято ✅" if act == "ok" else "Отклонено ❌")
        if ok_status:
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
    elif act == "bc":
        text = BC_TEXTS.get(int(parts[2]), "")
        if not text:
            await cb.answer("Текст рассылки устарел — отправь /broadcast заново", show_alert=True)
            return
        sent, failed = 0, 0
        for uid in await db.all_user_ids():
            try:
                await main_bot.send_message(uid, text)
                sent += 1
            except Exception:
                failed += 1
            await asyncio.sleep(0.05)
        await cb.message.edit_text(f"📣 Рассылка завершена: {sent} доставлено, {failed} не удалось.")
        await cb.answer()
    else:
        await cb.answer()
