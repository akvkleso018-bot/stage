"""Монетизация через Telegram Stars: /premium, инвойсы, оплата подписок."""
from __future__ import annotations

from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    PreCheckoutQuery,
    WebAppInfo,
)

from bot import config as cfg

router = Router()

SUBSCRIPTION_TEXT = (
    "Подписки «Бота твоих дел»:\n\n"
    "<b>Обычная</b> — 6 задач в день, Режим паники вечером и жёсткие напоминания:\n"
    "• неделя — 150 ⭐ · месяц — 400 ⭐\n"
    "• год — 1300 ⭐ · навсегда — 3000 ⭐\n\n"
    "<b>Elite</b> — безлимит задач, всё то же + максимум свободы:\n"
    "• неделя — 350 ⭐ · месяц — 800 ⭐\n"
    "• год — 2500 ⭐ · навсегда — 4500 ⭐\n\n"
    "Без подписки — 2 задачи в день.\n"
    "Выбери тариф и период в приложении:"
    "\n\n——— English ———\n\n"
    "Plans of “Your Deals Bot”:\n\n"
    "<b>Standard</b> — 6 tasks a day, evening Panic mode and hard reminders:\n"
    "• week — 150 ⭐ · month — 400 ⭐\n"
    "• year — 1300 ⭐ · forever — 3000 ⭐\n\n"
    "<b>Elite</b> — unlimited tasks, plus everything above:\n"
    "• week — 350 ⭐ · month — 800 ⭐\n"
    "• year — 2500 ⭐ · forever — 4500 ⭐\n\n"
    "No subscription — 2 tasks a day.\n"
    "Pick a plan and period in the app:"
)

SUBS_KB = InlineKeyboardMarkup(inline_keyboard=[[
    InlineKeyboardButton(text="Выбрать подписку", web_app=WebAppInfo(url=cfg.WEBAPP_URL))
]])


@router.message(Command("premium"))
async def cmd_premium(message: Message) -> None:
    await message.answer(SUBSCRIPTION_TEXT, reply_markup=SUBS_KB)


@router.callback_query(F.data == "buy_premium")
async def cb_buy_premium(cb: CallbackQuery) -> None:
    await cb.answer()
    await cb.message.answer(SUBSCRIPTION_TEXT, reply_markup=SUBS_KB)


@router.pre_checkout_query()
async def pre_checkout(query: PreCheckoutQuery, bot: Bot) -> None:
    await bot.answer_pre_checkout_query(query.id, ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message, bot: Bot, db) -> None:
    sp = message.successful_payment

    # payload: sub:<tier>:<period>:<uid>
    tier, period = "regular", "month"
    parts = sp.invoice_payload.split(":")
    if len(parts) >= 3 and parts[0] == "sub":
        tier, period = parts[1], parts[2]
    plan = cfg.SUBSCRIPTION_PLANS.get(tier, {}).get(period) or {"days": 30}

    tier = tier if tier in cfg.SUBSCRIPTION_PLANS else "regular"
    await db.add_subscription(message.from_user.id, tier, plan["days"])
    await db.add_payment(
        message.from_user.id, sp.total_amount, sp.telegram_payment_charge_id,
        plan=f"{tier}:{period}",
    )

    row = await db.get_user(message.from_user.id)
    tier_title = cfg.TIER_TITLES.get(tier, tier)
    period_title = cfg.PERIOD_TITLES.get(period, period)
    until_line = ""
    if plan["days"] <= 0:
        until_line = "Действует: <b>навсегда</b>\n——— English ———\nValid: <b>forever</b>"
    elif row and row["premium_until"]:
        until = datetime.fromtimestamp(row["premium_until"], cfg.TZ).strftime("%d.%m.%Y")
        until_line = f"Действует до: <b>{until}</b>\n——— English ———\nValid until: <b>{until}</b>"

    if tier == "elite":
        features = ("Безлимит задач, Режим паники вечером и жёсткие напоминания — твои.\n\n"
                    "——— English ———\n\n"
                    "Unlimited tasks, evening Panic mode and hard reminders are yours.")
    else:
        features = ("6 задач в день, Режим паники вечером и жёсткие напоминания — твои.\n\n"
                    "——— English ———\n\n"
                    "6 tasks a day, evening Panic mode and hard reminders are yours.")

    await message.answer(
        f"Подписка <b>{tier_title}</b> ({period_title}) активирована!\n"
        f"——— English ———\n"
        f"Subscription <b>{tier_title}</b> ({period_title}) activated!\n\n"
        + features + "\n\n" + until_line,
        reply_markup=SUBS_KB,
    )
    try:
        await bot.send_message(
            cfg.ADMIN_ID,
            f"Новая подписка <b>{tier_title}</b> ({period_title}) за <b>{sp.total_amount} Stars</b>:\n"
            f"пользователь <code>{message.from_user.id}</code> "
            f"(@{message.from_user.username}) — {message.from_user.full_name}",
        )
    except Exception:
        pass
