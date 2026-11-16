"""Слой SQLite (aiosqlite): пользователи, задачи, платежи."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta

import aiosqlite

from bot.config import TZ

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id      INTEGER PRIMARY KEY,
    username         TEXT,
    first_name       TEXT,
    premium_until    INTEGER DEFAULT 0,
    tier             TEXT    DEFAULT 'none',
    public_id        TEXT    DEFAULT '',
    panic_count      INTEGER DEFAULT 0,
    panic_date       TEXT    DEFAULT '',
    free_notified_on TEXT    DEFAULT '',
    created_at       INTEGER
);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    text            TEXT    NOT NULL,
    deadline        INTEGER NOT NULL,
    done            INTEGER DEFAULT 0,
    created_at      INTEGER,
    completed_at    INTEGER,
    urgent_notified INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_tasks_user     ON tasks (user_id, done);
CREATE INDEX IF NOT EXISTS idx_tasks_deadline ON tasks (deadline);

CREATE TABLE IF NOT EXISTS payments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    stars      INTEGER NOT NULL,
    plan       TEXT    DEFAULT '',
    charge_id  TEXT,
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS suggestions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    username   TEXT,
    first_name TEXT,
    text       TEXT NOT NULL,
    status     TEXT DEFAULT 'new',
    created_at INTEGER
);

CREATE TABLE IF NOT EXISTS friends (
    user_id    INTEGER NOT NULL,
    friend_id  INTEGER NOT NULL,
    created_at INTEGER,
    PRIMARY KEY (user_id, friend_id)
);

CREATE TABLE IF NOT EXISTS friend_requests (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user  INTEGER NOT NULL,
    to_user    INTEGER NOT NULL,
    created_at INTEGER,
    UNIQUE (from_user, to_user)
);

CREATE TABLE IF NOT EXISTS awards (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start INTEGER NOT NULL,
    place      INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    days       INTEGER NOT NULL,
    created_at INTEGER,
    UNIQUE (week_start, place)
);
"""

# «Навсегда» = 2100-01-01 UTC — со всеми существующими проверками работает как бессрочность
FOREVER_TS = 4102444800


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def init(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self._migrate()
        await self.conn.commit()

    async def _migrate(self) -> None:
        """Добавляет колонки тарифов к старым базам и переносит старый премиум."""
        async def add_column(table: str, column: str, ddl: str) -> None:
            cur = await self.conn.execute(f"PRAGMA table_info({table})")
            cols = {r[1] for r in await cur.fetchall()}
            await cur.close()
            if column not in cols:
                await self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

        await add_column("users", "tier", "TEXT NOT NULL DEFAULT 'none'")
        await add_column("users", "public_id", "TEXT NOT NULL DEFAULT ''")
        await add_column("payments", "plan", "TEXT NOT NULL DEFAULT ''")
        # у кого уже был активный премиум до ввода тарифов — считаем «Обычной»
        await self.conn.execute(
            "UPDATE users SET tier = 'regular' WHERE tier = 'none' AND premium_until > ?",
            (int(time.time()),),
        )
        # старые односторонние «друзья»: взаимные пары остаются дружбой,
        # односторонние превращаются в заявки (их можно принять/отклонить)
        await self.conn.execute(
            "INSERT OR IGNORE INTO friend_requests (from_user, to_user, created_at) "
            "SELECT f.user_id, f.friend_id, f.created_at FROM friends f "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM friends g WHERE g.user_id = f.friend_id AND g.friend_id = f.user_id)"
        )
        await self.conn.execute(
            "DELETE FROM friends "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM friends g WHERE g.user_id = friends.friend_id AND g.friend_id = friends.user_id)"
        )
        await self.conn.commit()
        await self._ensure_public_ids()

    async def _ensure_public_ids(self) -> None:
        """Анонимный публичный ID для профилей (без telegram_id и username)."""
        import secrets

        rows = await self._all("SELECT telegram_id FROM users WHERE public_id = ''")
        for r in rows:
            while True:
                pid = secrets.token_hex(4)
                dup = await self._one("SELECT 1 FROM users WHERE public_id = ?", (pid,))
                if not dup:
                    break
            await self.conn.execute(
                "UPDATE users SET public_id = ? WHERE telegram_id = ?", (pid, r["telegram_id"])
            )
        if rows:
            await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    # ---------- helpers ----------

    async def _one(self, sql: str, args: tuple = ()):
        cur = await self.conn.execute(sql, args)
        row = await cur.fetchone()
        await cur.close()
        return row

    async def _all(self, sql: str, args: tuple = ()):
        cur = await self.conn.execute(sql, args)
        rows = await cur.fetchall()
        await cur.close()
        return rows

    @staticmethod
    def day_bounds() -> tuple[int, int]:
        """Границы «сегодня» (в TZ бота) в unix-секундах."""
        now = datetime.now(TZ)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(start.timestamp()), int((start + timedelta(days=1)).timestamp())

    # ---------- users ----------

    async def upsert_user(self, tg_id: int, username: str | None = None, first_name: str | None = None) -> None:
        await self.conn.execute(
            """INSERT INTO users (telegram_id, username, first_name, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(telegram_id) DO UPDATE SET
                   username   = COALESCE(excluded.username, username),
                   first_name = COALESCE(excluded.first_name, first_name)""",
            (tg_id, username, first_name, int(time.time())),
        )
        await self.conn.commit()

    async def get_user(self, tg_id: int):
        return await self._one("SELECT * FROM users WHERE telegram_id = ?", (tg_id,))

    @staticmethod
    def tier_of(row) -> str:
        """Активный тариф пользователя: 'none' | 'regular' | 'elite'."""
        if not row:
            return "none"
        try:
            tier = row["tier"]
        except (KeyError, IndexError):
            tier = "regular"
        if row["premium_until"] and row["premium_until"] > int(time.time()):
            return tier if tier in ("regular", "elite") else "regular"
        return "none"

    @staticmethod
    def is_premium_row(row) -> bool:
        return Database.tier_of(row) != "none"

    async def add_premium_days(self, tg_id: int, days: int) -> None:
        """Совместимость: выдача «Обычной» на N дней (админ-команды)."""
        await self.add_subscription(tg_id, "regular", days)

    async def add_subscription(self, tg_id: int, tier: str, days: int) -> None:
        """Активирует/продлевает подписку. days <= 0 — навсегда."""
        row = await self.get_user(tg_id)
        now_ts = int(time.time())
        if days <= 0:
            until = FOREVER_TS
        else:
            base = max(now_ts, row["premium_until"] if row else 0)
            if base >= FOREVER_TS:
                base = now_ts
            until = base + days * 86400
        await self.conn.execute(
            "UPDATE users SET tier = ?, premium_until = ? WHERE telegram_id = ?",
            (tier, until, tg_id),
        )
        await self.conn.commit()

    async def revoke_premium(self, tg_id: int) -> None:
        await self.conn.execute(
            "UPDATE users SET premium_until = 0, tier = 'none' WHERE telegram_id = ?",
            (tg_id,),
        )
        await self.conn.commit()

    async def add_payment(self, user_id: int, stars: int, charge_id: str, plan: str = "") -> None:
        await self.conn.execute(
            "INSERT INTO payments (user_id, stars, plan, charge_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, stars, plan, charge_id, int(time.time())),
        )
        await self.conn.commit()

    # ---------- публичные профили, друзья, топ ----------

    async def user_by_public_id(self, public_id: str):
        return await self._one("SELECT * FROM users WHERE public_id = ?", (public_id,))

    async def weekly_leaderboard(
        self, week_start: int, week_end: int, ids: list[int] | None = None, limit: int = 50
    ):
        """Честный топ недели: очки только за реально сделанные задачи
        (в срок ×10, с опозданием ×3). Накрутить добавлением задач нельзя."""
        params: list = [week_start, week_end] * 3
        where = ""
        if ids is not None:
            if not ids:
                return []
            marks = ",".join("?" for _ in ids)
            where = f"WHERE u.telegram_id IN ({marks})"
            params = [week_start, week_end] * 3 + list(ids)
        rows = await self._all(
            f"""SELECT u.telegram_id, u.public_id, u.first_name, u.tier,
                  COALESCE(SUM(CASE WHEN t.done = 1 AND t.completed_at >= ? AND t.completed_at < ?
                                    THEN 1 ELSE 0 END), 0) AS done_cnt,
                  COALESCE(SUM(CASE WHEN t.done = 1 AND t.completed_at >= ? AND t.completed_at < ?
                                    AND t.completed_at <= t.deadline THEN 1 ELSE 0 END), 0) AS on_time,
                  COALESCE(SUM(CASE WHEN t.done = 1 AND t.completed_at >= ? AND t.completed_at < ?
                                    AND t.completed_at > t.deadline THEN 1 ELSE 0 END), 0) AS late
                FROM users u LEFT JOIN tasks t ON t.user_id = u.telegram_id
                {where}
                GROUP BY u.telegram_id
                HAVING on_time + late > 0
                ORDER BY (on_time * 10 + late * 3) DESC, done_cnt DESC, u.telegram_id ASC
                LIMIT ?""",
            (*params, limit),
        )
        return [dict(r) for r in rows]

    async def friends_ids(self, user_id: int) -> list[int]:
        rows = await self._all("SELECT friend_id FROM friends WHERE user_id = ?", (user_id,))
        return [r["friend_id"] for r in rows]

    async def count_friends(self, user_id: int) -> int:
        row = await self._one("SELECT COUNT(*) AS c FROM friends WHERE user_id = ?", (user_id,))
        return row["c"]

    async def is_friend(self, user_id: int, friend_id: int) -> bool:
        return bool(await self._one(
            "SELECT 1 FROM friends WHERE user_id = ? AND friend_id = ?", (user_id, friend_id)
        ))

    async def add_friend(self, user_id: int, friend_id: int) -> bool:
        """Дружба взаимная: строки в обе стороны."""
        if user_id == friend_id:
            return False
        for a, b in ((user_id, friend_id), (friend_id, user_id)):
            await self.conn.execute(
                "INSERT OR IGNORE INTO friends (user_id, friend_id, created_at) VALUES (?, ?, ?)",
                (a, b, int(time.time())),
            )
        await self.conn.commit()
        return True

    async def remove_friend(self, user_id: int, friend_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM friends WHERE (user_id = ? AND friend_id = ?) OR (user_id = ? AND friend_id = ?)",
            (user_id, friend_id, friend_id, user_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # --- заявки в друзья ---

    async def exists_request(self, from_user: int, to_user: int) -> bool:
        return bool(await self._one(
            "SELECT 1 FROM friend_requests WHERE from_user = ? AND to_user = ?",
            (from_user, to_user),
        ))

    async def add_request(self, from_user: int, to_user: int) -> bool:
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO friend_requests (from_user, to_user, created_at) VALUES (?, ?, ?)",
            (from_user, to_user, int(time.time())),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def delete_request(self, from_user: int, to_user: int) -> None:
        await self.conn.execute(
            "DELETE FROM friend_requests WHERE from_user = ? AND to_user = ?",
            (from_user, to_user),
        )
        await self.conn.commit()

    async def incoming_requests(self, user_id: int) -> list[dict]:
        rows = await self._all(
            """SELECT u.public_id, u.first_name, u.tier
               FROM friend_requests r JOIN users u ON u.telegram_id = r.from_user
               WHERE r.to_user = ? ORDER BY r.created_at DESC""",
            (user_id,),
        )
        return [dict(r) for r in rows]

    async def outgoing_requests(self, user_id: int) -> list[dict]:
        rows = await self._all(
            """SELECT u.public_id, u.first_name, u.tier
               FROM friend_requests r JOIN users u ON u.telegram_id = r.to_user
               WHERE r.from_user = ? ORDER BY r.created_at DESC""",
            (user_id,),
        )
        return [dict(r) for r in rows]

    async def incoming_requests_count(self, user_id: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS c FROM friend_requests WHERE to_user = ?", (user_id,)
        )
        return row["c"]

    async def friends_with_profiles(self, user_id: int) -> list[dict]:
        rows = await self._all(
            """SELECT u.public_id, u.first_name, u.tier, u.premium_until
               FROM friends f JOIN users u ON u.telegram_id = f.friend_id
               WHERE f.user_id = ? ORDER BY f.created_at DESC""",
            (user_id,),
        )
        return [dict(r) for r in rows]

    async def add_week_award(self, week_start: int, place: int, user_id: int, days: int) -> bool:
        cur = await self.conn.execute(
            """INSERT OR IGNORE INTO awards (week_start, place, user_id, days, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (week_start, place, user_id, days, int(time.time())),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def week_winners(self, week_start: int) -> list[dict]:
        rows = await self._all(
            """SELECT a.place, a.days, a.user_id, u.public_id, u.first_name
               FROM awards a JOIN users u ON u.telegram_id = a.user_id
               WHERE a.week_start = ? ORDER BY a.place ASC""",
            (week_start,),
        )
        return [dict(r) for r in rows]

    # ---------- tasks ----------

    async def add_task(self, user_id: int, text: str, deadline: int) -> int:
        cur = await self.conn.execute(
            "INSERT INTO tasks (user_id, text, deadline, created_at) VALUES (?, ?, ?, ?)",
            (user_id, text, deadline, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def complete_task(self, task_id: int, user_id: int) -> bool:
        cur = await self.conn.execute(
            "UPDATE tasks SET done = 1, completed_at = ? WHERE id = ? AND user_id = ? AND done = 0",
            (int(time.time()), task_id, user_id),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def delete_task(self, task_id: int, user_id: int) -> bool:
        cur = await self.conn.execute(
            "DELETE FROM tasks WHERE id = ? AND user_id = ?", (task_id, user_id)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def today_tasks(self, user_id: int, start_ts: int | None = None, end_ts: int | None = None):
        """Незавершённые (на сегодня + просроченные) и выполненные сегодня.

        start_ts/end_ts — границы «сегодня» в часовом поясе устройства
        пользователя (приходят из Mini App); None -> границы по TZ сервера."""
        if start_ts is None or end_ts is None:
            s, e = self.day_bounds()
            start_ts = s if start_ts is None else start_ts
            end_ts = e if end_ts is None else end_ts
        pending = await self._all(
            "SELECT * FROM tasks WHERE user_id = ? AND done = 0 AND deadline < ? ORDER BY deadline ASC",
            (user_id, end_ts),
        )
        done = await self._all(
            "SELECT * FROM tasks WHERE user_id = ? AND done = 1 AND completed_at >= ? ORDER BY completed_at DESC",
            (user_id, start_ts),
        )
        return [dict(r) for r in pending], [dict(r) for r in done]

    async def upcoming_tasks(self, user_id: int, after_ts: int, limit: int = 20):
        """Незавершённые задачи с дедлайном в будущем (не сегодня)."""
        rows = await self._all(
            "SELECT * FROM tasks WHERE user_id = ? AND done = 0 AND deadline >= ? ORDER BY deadline ASC LIMIT ?",
            (user_id, after_ts, limit),
        )
        return [dict(r) for r in rows]

    async def count_created_today(self, user_id: int, start_ts: int | None = None) -> int:
        if start_ts is None:
            start_ts, _ = self.day_bounds()
        row = await self._one(
            "SELECT COUNT(*) AS c FROM tasks WHERE user_id = ? AND created_at >= ?",
            (user_id, start_ts),
        )
        return row["c"]

    async def incomplete_today(self, user_id: int):
        _, end_ts = self.day_bounds()
        rows = await self._all(
            "SELECT * FROM tasks WHERE user_id = ? AND done = 0 AND deadline < ? ORDER BY deadline",
            (user_id, end_ts),
        )
        return [dict(r) for r in rows]

    # ---------- scheduler ----------

    async def premium_incomplete_today(self):
        now_ts = int(time.time())
        _, end_ts = self.day_bounds()
        rows = await self._all(
            """SELECT u.telegram_id FROM users u
               WHERE u.premium_until > ?
                 AND EXISTS (SELECT 1 FROM tasks t
                             WHERE t.user_id = u.telegram_id AND t.done = 0 AND t.deadline < ?)""",
            (now_ts, end_ts),
        )
        return [dict(r) for r in rows]

    async def bump_panic(self, tg_id: int, today_str: str) -> int:
        row = await self._one(
            "SELECT panic_count, panic_date FROM users WHERE telegram_id = ?", (tg_id,)
        )
        count = (row["panic_count"] + 1) if row and row["panic_date"] == today_str else 1
        await self.conn.execute(
            "UPDATE users SET panic_count = ?, panic_date = ? WHERE telegram_id = ?",
            (count, today_str, tg_id),
        )
        await self.conn.commit()
        return count

    async def tasks_due_soon(self, window_sec: int = 3600):
        now_ts = int(time.time())
        rows = await self._all(
            """SELECT t.id, t.user_id, t.text, t.deadline, u.premium_until
               FROM tasks t JOIN users u ON u.telegram_id = t.user_id
               WHERE t.done = 0 AND t.urgent_notified = 0
                 AND t.deadline > ? AND t.deadline <= ?""",
            (now_ts, now_ts + window_sec),
        )
        return [dict(r) for r in rows]

    async def mark_urgent(self, task_id: int) -> None:
        await self.conn.execute("UPDATE tasks SET urgent_notified = 1 WHERE id = ?", (task_id,))
        await self.conn.commit()

    async def free_reminder_users(self):
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        now_ts = int(time.time())
        _, end_ts = self.day_bounds()
        rows = await self._all(
            """SELECT DISTINCT u.telegram_id, u.first_name FROM users u
               JOIN tasks t ON t.user_id = u.telegram_id AND t.done = 0 AND t.deadline < ?
               WHERE u.premium_until <= ? AND u.free_notified_on != ?""",
            (end_ts, now_ts, today),
        )
        return [dict(r) for r in rows]

    async def set_free_notified(self, tg_id: int, today_str: str) -> None:
        await self.conn.execute(
            "UPDATE users SET free_notified_on = ? WHERE telegram_id = ?", (today_str, tg_id)
        )
        await self.conn.commit()

    # ---------- profile ----------

    async def profile_stats(self, user_id: int) -> dict:
        now_ts = int(time.time())
        row = await self._one(
            """SELECT
                 COALESCE(SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END), 0)                                AS completed,
                 COALESCE(SUM(CASE WHEN done = 1 AND completed_at <= deadline THEN 1 ELSE 0 END), 0)   AS on_time,
                 COALESCE(SUM(CASE WHEN done = 1 AND completed_at >  deadline THEN 1 ELSE 0 END), 0)   AS late,
                 COALESCE(SUM(CASE WHEN done = 0 AND deadline <  ? THEN 1 ELSE 0 END), 0)              AS missed,
                 COALESCE(SUM(CASE WHEN done = 0 AND deadline >= ? THEN 1 ELSE 0 END), 0)              AS active
               FROM tasks WHERE user_id = ?""",
            (now_ts, now_ts, user_id),
        )
        return dict(row)

    async def last_payment(self, user_id: int):
        return await self._one(
            "SELECT stars, created_at FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        )

    async def recent_payments(self, limit: int = 10):
        rows = await self._all(
            """SELECT p.user_id, p.stars, p.plan, p.created_at, u.username, u.first_name
               FROM payments p LEFT JOIN users u ON u.telegram_id = p.user_id
               ORDER BY p.created_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    # ---------- suggestions ----------

    async def add_suggestion(self, user_id: int, username: str | None, first_name: str | None, text: str) -> int:
        cur = await self.conn.execute(
            "INSERT INTO suggestions (user_id, username, first_name, text, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, username, first_name, text, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def pending_suggestions_count(self, user_id: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS c FROM suggestions WHERE user_id = ? AND status = 'new'",
            (user_id,),
        )
        return row["c"]

    async def active_suggestions(self, limit: int = 30):
        rows = await self._all(
            "SELECT * FROM suggestions WHERE status = 'new' ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in rows]

    async def set_suggestion_status(self, sid: int, status: str) -> bool:
        if status not in ("done", "declined"):
            return False
        cur = await self.conn.execute(
            "UPDATE suggestions SET status = ? WHERE id = ?", (status, sid)
        )
        await self.conn.commit()
        return cur.rowcount > 0

    # ---------- admin ----------

    async def all_user_ids(self) -> list[int]:
        rows = await self._all("SELECT telegram_id FROM users")
        return [r["telegram_id"] for r in rows]

    async def stars_sum(self, since_ts: int | None = None) -> int:
        if since_ts is None:
            row = await self._one("SELECT COALESCE(SUM(stars), 0) AS c FROM payments")
        else:
            row = await self._one(
                "SELECT COALESCE(SUM(stars), 0) AS c FROM payments WHERE created_at >= ?",
                (since_ts,),
            )
        return row["c"]

    async def new_users_count(self, since_ts: int) -> int:
        row = await self._one(
            "SELECT COUNT(*) AS c FROM users WHERE created_at >= ?", (since_ts,)
        )
        return row["c"]

    async def user_detail(self, uid: int) -> dict | None:
        row = await self.get_user(uid)
        if not row:
            return None
        tasks = await self._one(
            """SELECT
                 COUNT(*)                                                                          AS total,
                 COALESCE(SUM(CASE WHEN done = 0 THEN 1 ELSE 0 END), 0)                            AS active,
                 COALESCE(SUM(CASE WHEN done = 1 THEN 1 ELSE 0 END), 0)                            AS completed
               FROM tasks WHERE user_id = ?""",
            (uid,),
        )
        pays = await self._one(
            "SELECT COUNT(*) AS n, COALESCE(SUM(stars), 0) AS stars FROM payments WHERE user_id = ?",
            (uid,),
        )
        return dict(
            telegram_id=row["telegram_id"],
            username=row["username"],
            first_name=row["first_name"],
            tier=Database.tier_of(row),
            premium_until=row["premium_until"],
            created_at=row["created_at"],
            tasks=dict(tasks),
            payments=dict(pays),
        )

    async def stats(self) -> dict:
        now_ts = int(time.time())
        start_ts, end_ts = self.day_bounds()
        users = (await self._one("SELECT COUNT(*) AS c FROM users"))["c"]
        regular = (await self._one(
            "SELECT COUNT(*) AS c FROM users WHERE tier = 'regular' AND premium_until > ?", (now_ts,)
        ))["c"]
        elite = (await self._one(
            "SELECT COUNT(*) AS c FROM users WHERE tier = 'elite' AND premium_until > ?", (now_ts,)
        ))["c"]
        tasks_total = (await self._one("SELECT COUNT(*) AS c FROM tasks"))["c"]
        tasks_open = (await self._one(
            "SELECT COUNT(*) AS c FROM tasks WHERE done = 0 AND deadline < ?", (end_ts,)
        ))["c"]
        done_today = (await self._one(
            "SELECT COUNT(*) AS c FROM tasks WHERE done = 1 AND completed_at >= ?", (start_ts,)
        ))["c"]
        stars = (await self._one("SELECT COALESCE(SUM(stars), 0) AS c FROM payments"))["c"]
        return {
            "users": users,
            "premium": regular + elite,
            "regular": regular,
            "elite": elite,
            "tasks_total": tasks_total,
            "tasks_open": tasks_open,
            "done_today": done_today,
            "stars": stars,
        }
