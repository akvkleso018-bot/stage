"""Общая логика профиля: репутация, уровни, формат даты."""
from __future__ import annotations

import datetime as dt

from bot.config import TZ

REP_LEVELS = [
    (0, "Новичок"),
    (100, "Исполнитель"),
    (300, "Профи"),
    (700, "Мастер дел"),
    (1500, "Легенда"),
]

MONTHS_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def reputation_of(stats: dict) -> int:
    """Очки: в срок +10, с опозданием +3, просрочено -5 (не ниже 0)."""
    return max(0, stats["on_time"] * 10 + stats["late"] * 3 - stats["missed"] * 5)


def level_of(rep: int) -> tuple[tuple[int, str], tuple[int, str] | None]:
    """((минимум, название) текущего уровня, следующий уровень или None)."""
    cur = REP_LEVELS[0]
    nxt: tuple[int, str] | None = None
    for min_rep, title in REP_LEVELS:
        if rep >= min_rep:
            cur = (min_rep, title)
        else:
            nxt = (min_rep, title)
            break
    return cur, nxt


def fmt_date(ts: int) -> str:
    d = dt.datetime.fromtimestamp(ts, TZ)
    return f"{d.day} {MONTHS_RU[d.month - 1]} {d.year}"


def week_bounds(now: dt.datetime | None = None) -> tuple[int, int]:
    """Границы текущей недели (с понедельника 00:00) в unix-секундах."""
    now = now or dt.datetime.now(TZ)
    monday = (now - dt.timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(monday.timestamp()), int((monday + dt.timedelta(days=7)).timestamp())


def plural(n: int, one: str, few: str, many: str) -> str:
    a, b = abs(n) % 100, abs(n) % 10
    if 10 < a < 20:
        return many
    if 1 < b < 5:
        return few
    if b == 1:
        return one
    return many
