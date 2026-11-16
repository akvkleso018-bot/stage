"""Конфигурация бота. Все секреты — только через .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
ADMIN_ID: int = int(os.getenv("ADMIN_ID", "0"))

WEBAPP_URL: str = os.getenv("WEBAPP_URL", "https://example.com/webapp")
HOST: str = os.getenv("HOST", "0.0.0.0")
PORT: int = int(os.getenv("PORT", "8080"))

TIMEZONE: str = os.getenv("TIMEZONE", "Europe/Moscow")
TZ = ZoneInfo(TIMEZONE)

# ---------- подписки ----------
# tier -> period -> {"stars": цена, "days": длительность (0 = навсегда)}
SUBSCRIPTION_PLANS: dict = {
    "regular": {
        "week":    {"stars": 150,  "days": 7},
        "month":   {"stars": 400,  "days": 30},
        "year":    {"stars": 1300, "days": 365},
        "forever": {"stars": 3000, "days": 0},
    },
    "elite": {
        "week":    {"stars": 350,  "days": 7},
        "month":   {"stars": 800,  "days": 30},
        "year":    {"stars": 2500, "days": 365},
        "forever": {"stars": 4500, "days": 0},
    },
}
PERIOD_TITLES = {"week": "неделя", "month": "месяц", "year": "год", "forever": "навсегда"}
TIER_TITLES = {"regular": "Обычная", "elite": "Elite"}

# Лимиты задач в день: без подписки / Обычная / Elite (безлимит)
FREE_TASK_LIMIT: int = int(os.getenv("FREE_TASK_LIMIT", "2"))
REGULAR_TASK_LIMIT: int = int(os.getenv("REGULAR_TASK_LIMIT", "6"))

DEV_BYPASS_AUTH: bool = _bool("DEV_BYPASS_AUTH")

# Секрет для админ-панельного API (админ-бот -> основной бот)
PANEL_SECRET: str = os.getenv("PANEL_SECRET", "")

# Токен АДМИН-БОТА: если задан — он стартует в том же процессе (второй Telegram-бот)
ADMIN_BOT_TOKEN: str = os.getenv("ADMIN_BOT_TOKEN", "")

DB_PATH: str = os.getenv("DB_PATH", str(BASE_DIR / "data" / "bot.db"))

if not BOT_TOKEN or ":" not in BOT_TOKEN:
    raise SystemExit("BOT_TOKEN не задан — заполни файл .env")
