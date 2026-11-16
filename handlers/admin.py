"""Админ-команды: статистика, выдача/отзыв премиума, рассылка."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Router
from aiogram.filters import BaseFilter, Command
from aiogram.types import Message

from bot import config as cfg

log = logging.getLogger(__name__)
router = Router()


class IsAdmin(BaseFilter):
    async def __call__(self, message: Message) -> bool:
        return bool(message.from_user and message.from_user.id == cfg.ADMIN_ID)


@router.message(Command("admin"), IsAdmin())
async def cmd_admin(message: Message, db) -> None:
    s = await db.stats()
    await message.answer(
        "<b>Админ-панель</b>\n\n"
        f"Пользователей: <b>{s['users']}</b>\n"
        f"Обычная: <b>{s['regular']}</b> · Elite: <b>{s['elite']}</b>\n"
        f"Задач всего: <b>{s['tasks_total']}</b>\n"
        f"Активных на сегодня: <b>{s['tasks_open']}</b>\n"
        f"Выполнено сегодня: <b>{s['done_today']}</b>\n"
        f"Заработано: <b>{s['stars']} Stars</b>"
    )


@router.message(Command("grant"), IsAdmin())
async def cmd_grant(message: Message, db, bot: Bot) -> None:
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer(
            "Использование: <code>/grant &lt;user_id&gt; [дней]</code>\n"
            "Тариф по умолчанию — Обычная. Для Elite: <code>/grantelite &lt;user_id&gt; [дней]</code>"
        )
        return
    uid = int(parts[1])
    days = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 30
    await db.add_subscription(uid, "regular", days)
    await message.answer(f"Обычная подписка выдана <code>{uid}</code> на {days} дн.")
    try:
        await bot.send_message(uid, "Тебе выдана подписка «Обычная»! Проверь приложение.")
    except Exception:
        pass


@router.message(Command("grantelite"), IsAdmin())
async def cmd_grant_elite(message: Message, db, bot: Bot) -> None:
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: <code>/grantelite &lt;user_id&gt; [дней]</code> (0 = навсегда)")
        return
    uid = int(parts[1])
    days = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 30
    await db.add_subscription(uid, "elite", days)
    label = "навсегда" if days <= 0 else f"на {days} дн."
    await message.answer(f"Elite выдан <code>{uid}</code> {label}.")
    try:
        await bot.send_message(uid, "Тебе выдана подписка <b>Elite</b>! Проверь приложение.")
    except Exception:
        pass


@router.message(Command("revoke"), IsAdmin())
async def cmd_revoke(message: Message, db) -> None:
    parts = message.text.split()
    if len(parts) < 2 or not parts[1].lstrip("-").isdigit():
        await message.answer("Использование: <code>/revoke &lt;user_id&gt;</code>")
        return
    await db.revoke_premium(int(parts[1]))
    await message.answer(f"Премиум отозван у <code>{parts[1]}</code>.")


@router.message(Command("broadcast"), IsAdmin())
async def cmd_broadcast(message: Message, bot: Bot, db) -> None:
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Использование: <code>/broadcast &lt;текст&gt;</code>")
        return
    ids = await db.all_user_ids()
    sent = 0
    for uid in ids:
        try:
            await bot.send_message(uid, parts[1])
            sent += 1
        except Exception:
            pass
        await asyncio.sleep(0.05)
    await message.answer(f"Рассылка отправлена: {sent}/{len(ids)}.")
