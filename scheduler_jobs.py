"""APScheduler: Режим паники (премиум), жёсткие напоминания, вежливый ремаиндер."""
from __future__ import annotations

import html as html_mod
import logging
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from bot import config as cfg
from bot.profile import week_bounds

log = logging.getLogger(__name__)

PANIC_TEXTS = [
    "Вечер подкрался незаметно... А задача «{task}» всё ещё не сделана. Есть время — доделай!",
    "Так, серьёзно?! «{task}» до сих пор в списке. День тает на глазах. Двигайся!",
    "СТОП. «{task}» смотрит на тебя с укором. До конца дня — {left}. Хватит прокрастинировать!",
    "РЕЖИМ ПАНИКИ АКТИВИРОВАН. «{task}» — не сделана. Активных задач: {n}. Время уходит: {left}!",
    "Ты правда собираешься лечь спать с задачей «{task}» в незакрытом виде? {left} до конца дня!",
    "ПОСЛЕДНИЙ РУБЕЖ. «{task}» — горит! Осталось {left}. Если не сейчас — то когда?!",
    "ФИНАЛЬНЫЙ ОТСЧЁТ: {left} на «{task}». Беги. Лети. Но сделай!",
    "Ночной кошмар включён: «{task}» пойдёт с тобой в завтрашний день, если ты не сделаешь это СЕЙЧАС. {left}!",
]

URGENT_TEXTS = [
    "Час истины! До дедлайна задачи «{task}» осталось {left}. Соберись!",
    "КОД КРАСНЫЙ: «{task}» — горит! {left} до дедлайна. Начинай прямо сейчас!",
]

FREE_REMINDER = (
    "Пока ты был занят: «{task}» всё ещё не сделано.\n\n"
    "Хочешь, чтобы вечером бот жёстко напоминал о невыполненном, пока не сделаешь? "
    "Включи подписку в приложении."
    "\n\n——— English ———\n\n"
    "While you were busy: “{task}” is still not done.\n\n"
    "Want the bot to firmly remind you in the evening until it's done? "
    "Enable a plan in the app."
)


def esc(s: str) -> str:
    return html_mod.escape(s)


def _left_to_midnight(now: datetime) -> str:
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    minutes = max(0, int((end - now).total_seconds() // 60))
    h, m = minutes // 60, minutes % 60
    return f"{h} ч {m} мин" if h else f"{m} мин"


async def panic_tick(bot: Bot, db) -> None:
    """Каждые 15 минут вечером: эмоциональный спам по премиум-пользователям."""
    now = datetime.now(cfg.TZ)
    if now.hour < 20:
        return
    today = now.strftime("%Y-%m-%d")
    left = _left_to_midnight(now)

    for row in await db.premium_incomplete_today():
        tasks = await db.incomplete_today(row["telegram_id"])
        if not tasks:
            continue
        count = await db.bump_panic(row["telegram_id"], today)
        idx = min(count, len(PANIC_TEXTS)) - 1
        text = PANIC_TEXTS[idx].format(task=esc(tasks[0]["text"]), left=left, n=len(tasks))
        try:
            await bot.send_message(row["telegram_id"], text)
            log.info("panic -> %s (tick %s)", row["telegram_id"], count)
        except TelegramForbiddenError:
            continue
        except Exception:
            log.exception("panic send failed for %s", row["telegram_id"])


async def deadline_tick(bot: Bot, db) -> None:
    """Каждые 5 минут: жёсткое напоминание за час до дедлайна (премиум)."""
    now_ts = int(datetime.now(cfg.TZ).timestamp())
    for task in await db.tasks_due_soon():
        try:
            if task["premium_until"] > now_ts:  # жёсткие напоминания — для премиума
                minutes = max(1, (task["deadline"] - now_ts) // 60)
                text = URGENT_TEXTS[task["id"] % len(URGENT_TEXTS)].format(
                    task=esc(task["text"]), left=f"{minutes} мин"
                )
                await bot.send_message(task["user_id"], text)
        except TelegramForbiddenError:
            pass
        except Exception:
            log.exception("urgent send failed for task %s", task["id"])
        finally:
            await db.mark_urgent(task["id"])


async def free_reminder(bot: Bot, db) -> None:
    """19:00 — один вежливый ремаиндер бесплатным пользователям + апселл Премиума."""
    today = datetime.now(cfg.TZ).strftime("%Y-%m-%d")
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Открыть планировщик", web_app=WebAppInfo(url=cfg.WEBAPP_URL))
    ]])
    for user in await db.free_reminder_users():
        tasks = await db.incomplete_today(user["telegram_id"])
        if not tasks:
            await db.set_free_notified(user["telegram_id"], today)
            continue
        try:
            await bot.send_message(
                user["telegram_id"],
                FREE_REMINDER.format(task=esc(tasks[0]["text"])),
                reply_markup=kb,
            )
        except TelegramForbiddenError:
            pass
        except Exception:
            log.exception("free reminder failed for %s", user["telegram_id"])
        else:
            await db.set_free_notified(user["telegram_id"], today)


async def weekly_award(bot: Bot, db) -> None:
    """Понедельник 00:05 — награда топ-3 прошлой недели: +3/+2/+1 день Обычной."""
    from bot.profile import plural

    ws, _ = week_bounds()
    prev_ws = ws - 7 * 86400
    rewards = {1: 3, 2: 2, 3: 1}
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}

    top = await db.weekly_leaderboard(prev_ws, ws, limit=3)
    for i, r in enumerate(top):
        place = i + 1
        if r["on_time"] * 10 + r["late"] * 3 <= 0:
            break
        days = rewards[place]
        if not await db.add_week_award(prev_ws, place, r["telegram_id"], days):
            continue  # уже награждён (перезапуск задачи)
        await db.add_subscription(r["telegram_id"], "regular", days)
        name = r["first_name"] or "Игрок"
        try:
            await bot.send_message(
                r["telegram_id"],
                f"{medals[place]} <b>{place} место</b> в честном топе недели!\n\n"
                f"Ты заработал <b>+{days} {plural(days, 'день', 'дня', 'дней')}</b> "
                f"подписки «Обычная». Так держать!",
            )
        except TelegramForbiddenError:
            pass
        except Exception:
            log.exception("award send failed for %s", r["telegram_id"])
        log.info("weekly award: place %s -> %s (+%sd)", place, r["telegram_id"], days)


def setup_scheduler(bot: Bot, db) -> AsyncIOScheduler:
    import pytz

    tz = pytz.timezone(cfg.TIMEZONE)
    scheduler = AsyncIOScheduler(timezone=tz)
    scheduler.add_job(
        panic_tick, IntervalTrigger(minutes=15),
        args=(bot, db), id="panic", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        deadline_tick, IntervalTrigger(minutes=5),
        args=(bot, db), id="deadline", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        free_reminder, CronTrigger(hour=19, minute=0, timezone=tz),
        args=(bot, db), id="free_reminder", max_instances=1, coalesce=True,
    )
    scheduler.add_job(
        weekly_award, CronTrigger(day_of_week="mon", hour=0, minute=5, timezone=tz),
        args=(bot, db), id="weekly_award", max_instances=1, coalesce=True,
    )
    return scheduler
