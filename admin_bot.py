"""Админ-бот: полная статистика основного бота через панельное API.

Запуск: python admin_bot.py
Переменные окружения: ADMIN_BOT_TOKEN, ADMIN_ID, PANEL_URL, PANEL_SECRET
"""
from __future__ import annotations

import asyncio
import html as html_mod
import logging
import os
from datetime import datetime

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).resolve().parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)

ADMIN_BOT_TOKEN = os.getenv("ADMIN_BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
PANEL_URL = os.getenv("PANEL_URL", "").rstrip("/")
PANEL_SECRET = os.getenv("PANEL_SECRET", "")

if not ADMIN_BOT_TOKEN or ":" not in ADMIN_BOT_TOKEN:
    raise SystemExit("ADMIN_BOT_TOKEN не задан — заполни .env")
if not PANEL_URL or not PANEL_SECRET:
    raise SystemExit("PANEL_URL и PANEL_SECRET не заданы — заполни .env")

router = Router()
MENU = (
    "🛡 <b>Панель администратора</b>\n\n"
    "Вся статистика бота «Бот твоих дел» — кнопки ниже.\n\n"
    "Команды:\n"
    "/stats — сводка\n"
    "/payments — последние платежи\n"
    "/suggestions — идеи пользователей\n"
    "/user &lt;id&gt; — карточка игрока\n"
    "/broadcast &lt;текст&gt; — рассылка всем\n"
    "/ping — жив ли основной бот"
)


def kb_main() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="p:stats")],
        [InlineKeyboardButton(text="💳 Платежи", callback_data="p:pay")],
        [InlineKeyboardButton(text="💡 Идеи", callback_data="p:sug")],
    ])


def guard(m: Message) -> bool:
    return bool(m.from_user and m.from_user.id == ADMIN_ID)


async def panel(session: aiohttp.ClientSession, method: str, path: str, payload: dict | None = None):
    headers = {"X-Panel-Token": PANEL_SECRET}
    url = PANEL_URL + path
    async with session.request(
        method, url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=60)
    ) as resp:
        try:
            data = await resp.json()
        except Exception:
            data = {"ok": False, "error": f"HTTP {resp.status}"}
        return resp.status, data


def esc(s) -> str:
    return html_mod.escape(str(s or ""))


def fmt_dt(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M")


async def build_stats_text(session: aiohttp.ClientSession) -> str:
    status, data = await panel(session, "GET", "/api/panel/summary")
    if status != 200 or not data.get("ok"):
        return "⚠️ Основной бот не отвечает (" + esc(data.get("error") or status) + ")"
    s = data["stats"]
    lines = [
        "📊 <b>Статистика бота твоих дел</b>",
        "",
        f"👥 Пользователей: <b>{s['users']}</b>",
        f"   • новых сегодня: {s['new_users_today']} · за неделю: {s['new_users_week']}",
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
        f"   • сегодня: <b>{s['stars_today']}</b> · за неделю: <b>{s['stars_week']}</b>",
        "",
        f"💡 Идей на рассмотрении: <b>{data['pending_suggestions']}</b>",
    ]
    return "\n".join(lines)


async def build_payments_text(session: aiohttp.ClientSession) -> str:
    status, data = await panel(session, "GET", "/api/panel/payments?limit=15")
    if status != 200 or not data.get("ok"):
        return "⚠️ Основной бот не отвечает (" + esc(data.get("error") or status) + ")"
    pays = data["payments"] or []
    if not pays:
        return "💳 Платежей пока не было."
    lines = ["💳 <b>Последние платежи</b>", ""]
    for p in pays:
        who = esc(p.get("first_name") or "Игрок")
        tag = f" (@{esc(p['username'])})" if p.get("username") else ""
        plan = f" · {esc(p['plan'].replace(':', ' · '))}" if p.get("plan") else ""
        lines.append(f"• <b>{who}</b>{tag}{plan} — <b>+{p['stars']}⭐</b> ({fmt_dt(p['created_at'])})\n   id: <code>{p['user_id']}</code>")
    return "\n".join(lines)


async def build_suggestions_text(session: aiohttp.ClientSession) -> str:
    status, data = await panel(session, "GET", "/api/panel/suggestions")
    if status != 200 or not data.get("ok"):
        return "⚠️ Основной бот не отвечает (" + esc(data.get("error") or status) + ")"
    sugs = data["suggestions"] or []
    if not sugs:
        return "💡 Активных идей нет."
    return "💡 Активных идей: " + str(len(sugs)) + " — показываю ниже."


def suggestion_kb(sugs: list) -> InlineKeyboardMarkup:
    rows = []
    for s in sugs[:10]:
        rows.append([
            InlineKeyboardButton(text=f"✅ #{s['id']}", callback_data=f"p:ok:{s['id']}"),
            InlineKeyboardButton(text=f"❌ #{s['id']}", callback_data=f"p:no:{s['id']}"),
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def build_user_text(session: aiohttp.ClientSession, uid: int) -> str:
    status, data = await panel(session, "GET", f"/api/panel/user/{uid}")
    if status == 404:
        return f"Пользователь <code>{uid}</code> не найден."
    if status != 200 or not data.get("ok"):
        return "⚠️ Основной бот не отвечает (" + esc(data.get("error") or status) + ")"
    u = data["user"]
    tier = {"regular": "Обычная", "elite": "Elite", "none": "—"}[u["tier"]]
    until = "навсегда" if u["premium_until"] >= 4102444800 else (
        fmt_dt(u["premium_until"]) if u["premium_until"] else "—"
    )
    return "\n".join([
        f"👤 <b>{esc(u['first_name'] or 'Игрок')}</b>"
        + (f" (@{esc(u['username'])})" if u.get("username") else ""),
        f"id: <code>{u['telegram_id']}</code>",
        "",
        f"👑 Подписка: <b>{tier}</b> · до: {until}",
        f"📋 Задач: всего {u['tasks']['total']} · активных {u['tasks']['active']} · выполнено {u['tasks']['completed']}",
        f"💳 Платежей: {u['payments']['n']} на <b>{u['payments']['stars']}⭐</b>",
        f"📅 С нами с: {fmt_dt(u['created_at'])}",
    ])


@router.message(CommandStart())
async def cmd_start(m: Message):
    if not guard(m):
        return
    await m.answer(MENU, reply_markup=kb_main())


@router.message(Command("stats"))
async def cmd_stats(m: Message):
    if not guard(m):
        return
    async with aiohttp.ClientSession() as s:
        await m.answer(await build_stats_text(s), reply_markup=kb_main())


@router.message(Command("payments"))
async def cmd_payments(m: Message):
    if not guard(m):
        return
    async with aiohttp.ClientSession() as s:
        await m.answer(await build_payments_text(s))


@router.message(Command("suggestions"))
async def cmd_suggestions(m: Message):
    if not guard(m):
        return
    async with aiohttp.ClientSession() as s:
        text = await build_suggestions_text(s)
        if "нет" in text and "идей" in text:
            await m.answer(text)
            return
        status, data = await panel(s, "GET", "/api/panel/suggestions")
        for sg in (data.get("suggestions") or [])[:10]:
            who = esc(sg.get("first_name") or "Игрок")
            tag = f" (@{esc(sg['username'])})" if sg.get("username") else ""
            await m.answer(
                f"💡 <b>#{sg['id']}</b> от {who}{tag} ({fmt_dt(sg['created_at'])}):\n\n"
                f"«{esc(sg['text'])}»",
                reply_markup=suggestion_kb([sg]),
            )


@router.message(Command("user"))
async def cmd_user(m: Message):
    if not guard(m):
        return
    parts = (m.text or "").split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await m.answer("Использование: <code>/user &lt;id&gt;</code>")
        return
    async with aiohttp.ClientSession() as s:
        await m.answer(await build_user_text(s, int(parts[1])))


@router.message(Command("broadcast"))
async def cmd_broadcast(m: Message):
    if not guard(m):
        return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await m.answer("Использование: <code>/broadcast &lt;текст&gt;</code>")
        return
    text = parts[1]
    bc_id = abs(hash(text)) % 10**9
    BC_TEXTS[bc_id] = text
    confirm = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="📣 Отправить всем", callback_data="p:bc:" + str(bc_id))
    ]])
    await m.answer(
        "Отправить всем пользователям?\n\n" + html_mod.escape(text[:1000]),
        reply_markup=confirm,
    )


BC_TEXTS: dict[int, str] = {}


@router.message(Command("ping"))
async def cmd_ping(m: Message):
    if not guard(m):
        return
    async with aiohttp.ClientSession() as s:
        status, _ = await panel(s, "GET", "/api/panel/summary")
    await m.answer("🟢 Основной бот на связи" if status == 200 else f"🔴 Нет связи (HTTP {status})")


@router.callback_query(F.data.startswith("p:"))
async def on_press(cb: CallbackQuery):
    if not (cb.from_user and cb.from_user.id == ADMIN_ID):
        await cb.answer("Нет доступа", show_alert=True)
        return
    parts = cb.data.split(":")
    act = parts[1]
    async with aiohttp.ClientSession() as s:
        if act == "stats":
            await cb.message.edit_text(await build_stats_text(s), reply_markup=kb_main())
            await cb.answer("Обновлено")
        elif act == "pay":
            await cb.message.edit_text(await build_payments_text(s))
            await cb.answer()
        elif act == "sug":
            status, data = await panel(s, "GET", "/api/panel/suggestions")
            sugs = data.get("suggestions") or []
            if not sugs:
                await cb.message.edit_text("💡 Активных идей нет.")
            else:
                await cb.message.edit_text("💡 Активных идей: " + str(len(sugs)))
                for sg in sugs[:10]:
                    await cb.message.answer(
                        f"💡 <b>#{sg['id']}</b> от {esc(sg.get('first_name') or 'Игрок')}:\n\n«{esc(sg['text'])}»",
                        reply_markup=suggestion_kb([sg]),
                    )
            await cb.answer()
        elif act in ("ok", "no"):
            sid = parts[2]
            status, data = await panel(
                s, "POST", f"/api/panel/suggestions/{sid}/status",
                {"status": "done" if act == "ok" else "declined"},
            )
            await cb.answer("Принято ✅" if act == "ok" else "Отклонено ❌" if status == 200 else "Ошибка")
            if status == 200:
                try:
                    await cb.message.edit_reply_markup(reply_markup=None)
                except Exception:
                    pass
        elif act == "bc":
            text = BC_TEXTS.get(int(parts[2]), "")
            if not text:
                await cb.answer("Текст рассылки устарел — отправь /broadcast заново", show_alert=True)
                return
            status, data = await panel(s, "POST", "/api/panel/broadcast", {"text": text})
            if status == 200 and data.get("ok"):
                await cb.message.edit_text(f"📣 Рассылка завершена: {data['sent']} доставлено, {data['failed']} не удалось.")
            else:
                await cb.message.edit_text("⚠️ Рассылка не удалась: " + esc(data.get("error") or status))
            await cb.answer()
        else:
            await cb.answer()


@router.message()
async def fallback(m: Message):
    if not guard(m):
        return
    # /broadcast с текстом длиннее одного слова попадает сюда через cmd? нет — Command ловит.
    await m.answer("Не понял команду. Жми кнопки 👇", reply_markup=kb_main())


async def main() -> None:
    bot = Bot(ADMIN_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
