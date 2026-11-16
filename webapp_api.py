"""HTTP API РґР»СЏ Mini App (aiohttp) + РІР°Р»РёРґР°С†РёСЏ Telegram initData."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html as html_mod
import json
import logging
import time
from datetime import datetime
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot
from aiogram.types import LabeledPrice

from bot import config as cfg
from bot.db import Database
from bot.profile import level_of, reputation_of, week_bounds

log = logging.getLogger(__name__)
WEBAPP_DIR = cfg.BASE_DIR / "webapp"


# ---------- Р°РІС‚РѕСЂРёР·Р°С†РёСЏ Mini App ----------

def validate_init_data(init_data: str, token: str):
    """РџСЂРѕРІРµСЂСЏРµС‚ РїРѕРґРїРёСЃСЊ Telegram WebApp initData (Р°Р»РіРѕСЂРёС‚Рј РёР· РѕС„РёС†РёР°Р»СЊРЅРѕР№ РґРѕРєСѓРјРµРЅС‚Р°С†РёРё)."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, received_hash):
        return None
    try:
        user = json.loads(pairs.get("user", "{}"))
        return user if "id" in user else None
    except Exception:
        return None


_seen_users: set[int] = set()


@web.middleware
async def no_cache_middleware(request: web.Request, handler):
    """РњРёРЅРё-РїСЂРёР»РѕР¶РµРЅРёРµ РІСЃРµРіРґР° РґРѕР»Р¶РЅРѕ РіСЂСѓР·РёС‚СЊ СЃРІРµР¶РёРµ С„Р°Р№Р»С‹ вЂ” РєСЌС€ Р·Р°РїСЂРµС‰С‘РЅ."""
    response = await handler(request)
    if request.path == "/webapp" or request.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@web.middleware
async def auth_middleware(request: web.Request, handler):
    # /api/panel/* — свой доступ по секрету (админ-бот), initData не нужен
    if request.path.startswith("/api/panel"):
        if not panel_ok(request):
            return web.json_response({"ok": False, "error": "forbidden"}, status=403)
        return await handler(request)

    if request.path.startswith("/api"):
        raw = request.headers.get("X-Init-Data", "")
        tg_user = validate_init_data(raw, cfg.BOT_TOKEN)
        if tg_user:
            request["user_id"] = int(tg_user["id"])
            request["tg_user"] = tg_user
        elif cfg.DEV_BYPASS_AUTH:
            dev_id = int(request.headers.get("X-Dev-User", "1"))
            request["user_id"] = dev_id
            request["tg_user"] = {"id": dev_id, "first_name": "Dev", "username": "dev"}
        else:
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

        db: Database = request.app["db"]
        if request["user_id"] not in _seen_users:
            u = request["tg_user"]
            await db.upsert_user(request["user_id"], u.get("username"), u.get("first_name"))
            _seen_users.add(request["user_id"])
    return await handler(request)


# ---------- СѓС‚РёР»РёС‚С‹ РѕС‚РІРµС‚РѕРІ ----------

def ok(**data) -> web.Response:
    return web.json_response({"ok": True, **data})


def err(message: str, status: int = 400) -> web.Response:
    return web.json_response({"ok": False, "error": message}, status=status)


def is_premium_row(row) -> bool:
    return Database.is_premium_row(row)


def panic_active() -> bool:
    """Р’РµС‡РµСЂРЅРёР№ РёРЅС‚РµСЂРІР°Р», РєРѕРіРґР° Р±РѕС‚ В«Р±РѕРјР±РёС‚В» РїРѕ РїСЂРµРјРёСѓРј-РїРѕР»СЊР·РѕРІР°С‚РµР»СЏРј."""
    return datetime.now(cfg.TZ).hour >= 20


# ---------- API-С…РµРЅРґР»РµСЂС‹ ----------

async def tasks_get(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    uid = request["user_id"]

    # РіСЂР°РЅРёС†С‹ В«СЃРµРіРѕРґРЅСЏВ» РІ РїРѕСЏСЃРµ СѓСЃС‚СЂРѕР№СЃС‚РІР° РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ (РёР· Mini App)
    day_start = None
    try:
        day_start = int(request.query.get("day_start", "") or 0) or None
    except ValueError:
        day_start = None
    if day_start is None:
        day_start, day_end = db.day_bounds()
    else:
        day_end = day_start + 86400

    pending, done_today = await db.today_tasks(uid, day_start, day_end)
    upcoming = await db.upcoming_tasks(uid, day_end)
    row = await db.get_user(uid)
    tier = Database.tier_of(row)
    task_limit = 0 if tier == "elite" else (
        cfg.REGULAR_TASK_LIMIT if tier == "regular" else cfg.FREE_TASK_LIMIT
    )
    return ok(
        pending=pending,
        upcoming=upcoming,
        done_today=done_today,
        premium=tier != "none",
        tier=tier,
        premium_until=row["premium_until"] if row else 0,
        task_limit=task_limit,
        created_today=await db.count_created_today(uid, day_start),
        panic_active=panic_active(),
        is_admin=(uid == cfg.ADMIN_ID),
        friend_requests=await db.incoming_requests_count(uid),
    )


async def admin_stats(request: web.Request) -> web.Response:
    """РЎС‚Р°С‚РёСЃС‚РёРєР° Р±РѕС‚Р° вЂ” С‚РѕР»СЊРєРѕ РґР»СЏ Р°РґРјРёРЅР° (РїСЂРѕРІРµСЂРєР° РЅР° СЃРµСЂРІРµСЂРµ)."""
    if request.get("user_id") != cfg.ADMIN_ID:
        return err("forbidden", 403)
    db: Database = request.app["db"]
    return ok(
        stats=await db.stats(),
        payments=await db.recent_payments(10),
    )


# ---------- РїСЂРµРґР»РѕР¶РµРЅРёСЏ РёРґРµР№ ----------

MAX_PENDING_SUGGESTIONS = 3


async def suggest_post(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    bot: Bot = request.app["bot"]
    uid = request["user_id"]
    u = request["tg_user"]

    try:
        data = await request.json()
    except Exception:
        return err("bad_json")
    text = str(data.get("text", "")).strip()[:500]
    if len(text) < 3:
        return err("too_short")

    if await db.pending_suggestions_count(uid) >= MAX_PENDING_SUGGESTIONS:
        return err("too_many_pending", 429)

    sid = await db.add_suggestion(uid, u.get("username"), u.get("first_name"), text)

    safe_text = html_mod.escape(text)
    who = html_mod.escape(u.get("first_name") or "")
    tag = f" (@{html_mod.escape(u.get('username') or '')})" if u.get("username") else ""
    try:
        await bot.send_message(
            cfg.ADMIN_ID,
            f"РќРѕРІР°СЏ РёРґРµСЏ РґР»СЏ <b>Р‘РѕС‚Р° С‚РІРѕРёС… РґРµР»</b>\n\n"
            f"В«{safe_text}В»\n\n"
            f"РћС‚: {who}{tag} В· <code>{uid}</code>",
        )
    except Exception:
        log.warning("РќРµ СѓРґР°Р»РѕСЃСЊ СѓРІРµРґРѕРјРёС‚СЊ Р°РґРјРёРЅР° Рѕ РЅРѕРІРѕР№ РёРґРµРµ #%s", sid)

    return ok(suggestion_id=sid)


async def admin_suggestions(request: web.Request) -> web.Response:
    if request.get("user_id") != cfg.ADMIN_ID:
        return err("forbidden", 403)
    return ok(suggestions=await request.app["db"].active_suggestions())


async def admin_suggestion_status(request: web.Request) -> web.Response:
    if request.get("user_id") != cfg.ADMIN_ID:
        return err("forbidden", 403)
    db: Database = request.app["db"]
    try:
        sid = int(request.match_info["sid"])
        data = await request.json()
    except Exception:
        return err("bad_json")
    if not await db.set_suggestion_status(sid, str(data.get("status", ""))):
        return err("bad_status_or_not_found", 404)
    return ok()


# ---------- РїСЂРѕС„РёР»СЊ, С‚РѕРї, РґСЂСѓР·СЊСЏ ----------

def _profile_payload(row, stats: dict, rep: int, friends_count: int) -> dict:
    (cur_min, cur_title), nxt = level_of(rep)
    return dict(
        stats=stats,
        rep=rep,
        level=cur_title,
        next_level=nxt[1] if nxt else None,
        next_rep=nxt[0] if nxt else None,
        tier=Database.tier_of(row),
        premium_until=row["premium_until"] if row else 0,
        friends_count=friends_count,
        member_since=row["created_at"] if row else 0,
    )


async def profile_get(request: web.Request) -> web.Response:
    """РЎРІРѕР№ РїСЂРѕС„РёР»СЊ (РїРѕ РєРѕРјР°РЅРґРµ РёР· РїСЂРёР»РѕР¶РµРЅРёСЏ РІРјРµСЃС‚Рѕ СЃС‚Р°СЂРѕРіРѕ /profile)."""
    db: Database = request.app["db"]
    uid = request["user_id"]
    row = await db.get_user(uid)
    stats = await db.profile_stats(uid)
    rep = reputation_of(stats)
    return ok(
        name=(row["first_name"] if row else "") or "РРіСЂРѕРє",
        **_profile_payload(row, stats, rep, await db.count_friends(uid)),
    )


async def public_profile_get(request: web.Request) -> web.Response:
    """Р§СѓР¶РѕР№ РїСЂРѕС„РёР»СЊ РїРѕ public_id: РёРјСЏ Рё СЃС‚Р°С‚РёСЃС‚РёРєР° РІРёРґРЅС‹, РєРѕРЅС‚Р°РєС‚РѕРІ РЅРµС‚."""
    db: Database = request.app["db"]
    uid = request["user_id"]
    row = await db.user_by_public_id(request.match_info["public_id"])
    if not row:
        return err("not_found", 404)
    other = row["telegram_id"]
    if await db.is_friend(uid, other):
        rel = "friends"
    elif await db.exists_request(uid, other):
        rel = "outgoing"
    elif await db.exists_request(other, uid):
        rel = "incoming"
    else:
        rel = "none"
    stats = await db.profile_stats(other)
    rep = reputation_of(stats)
    payload = _profile_payload(row, stats, rep, await db.count_friends(other))
    return ok(
        name=(row["first_name"] or "РРіСЂРѕРє"),
        rel=rel,
        **payload,
    )


async def leaderboard_get(request: web.Request) -> web.Response:
    """Р§РµСЃС‚РЅС‹Р№ С‚РѕРї РЅРµРґРµР»Рё. РђРЅРѕРЅРёРјРЅРѕ: С‚РѕР»СЊРєРѕ РёРјСЏ Рё РѕС‡РєРё, Р±РµР· РєРѕРЅС‚Р°РєС‚РѕРІ."""
    db: Database = request.app["db"]
    uid = request["user_id"]
    scope = request.query.get("scope", "all")
    ws, we = week_bounds()

    ids = None
    if scope == "friends":
        ids = (await db.friends_ids(uid)) + [uid]
    rows = await db.weekly_leaderboard(ws, we, ids=ids, limit=50)

    out, my_row = [], None
    for i, r in enumerate(rows):
        item = dict(
            place=i + 1,
            public_id=r["public_id"],
            name=(r["first_name"] or "РРіСЂРѕРє"),
            points=r["on_time"] * 10 + r["late"] * 3,
            on_time=r["on_time"],
            late=r["late"],
            is_me=(r["telegram_id"] == uid),
            is_friend=await db.is_friend(uid, r["telegram_id"]),
        )
        if item["is_me"]:
            my_row = item
        out.append(item)

    prev_ws = ws - 7 * 86400
    winners = [
        dict(place=w["place"], name=(w["first_name"] or "РРіСЂРѕРє"),
             public_id=w["public_id"], days=w["days"])
        for w in await db.week_winners(prev_ws)
    ]
    return ok(
        scope="friends" if scope == "friends" else "all",
        rows=out,
        me=my_row,
        winners=winners,
    )


async def friend_add(request: web.Request) -> web.Response:
    """РћС‚РїСЂР°РІРёС‚СЊ Р·Р°СЏРІРєСѓ РІ РґСЂСѓР·СЊСЏ. Р•СЃР»Рё РІС‚РѕСЂРѕР№ СѓР¶Рµ РїСЂРёСЃР»Р°Р» Р·Р°СЏРІРєСѓ вЂ” СЃРєР»РµРёРІР°РµРј РІ РґСЂСѓР¶Р±Сѓ."""
    db: Database = request.app["db"]
    bot: Bot = request.app["bot"]
    uid = request["user_id"]
    me = request["tg_user"]
    row = await db.user_by_public_id(request.match_info["public_id"])
    if not row:
        return err("not_found", 404)
    other = row["telegram_id"]
    if other == uid:
        return err("self")

    if await db.is_friend(uid, other) or await db.is_friend(other, uid):
        return ok(status="friends")

    if await db.exists_request(other, uid):
        await db.delete_request(other, uid)
        await db.add_friend(uid, other)
        try:
            await bot.send_message(
                other,
                f"🤝 <b>{html_mod.escape(me.get('first_name') or 'Игрок')}</b> принял твою заявку в друзья!\n"
                f"——— English ———\n"
                f"🤝 <b>{html_mod.escape(me.get('first_name') or 'Player')}</b> accepted your friend request!",
            )
        except Exception:
            pass
        return ok(status="accepted")

    await db.add_request(uid, other)
    try:
        await bot.send_message(
            other,
            f"🤝 Игрок <b>{html_mod.escape(me.get('first_name') or 'Игрок')}</b> "
            f"хочет добавить тебя в друзья!\n\n"
            f"Открой приложение → Профиль, чтобы принять или отклонить заявку.\n"
            f"——— English ———\n"
            f"🤝 Player <b>{html_mod.escape(me.get('first_name') or 'Player')}</b> "
            f"wants to add you as a friend!\n\n"
            f"Open the app → Profile to accept or decline the request.",
        )
    except Exception:
        pass
    return ok(status="sent")


async def friend_accept(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    bot: Bot = request.app["bot"]
    uid = request["user_id"]
    me = request["tg_user"]
    row = await db.user_by_public_id(request.match_info["public_id"])
    if not row:
        return err("not_found", 404)
    other = row["telegram_id"]
    if not await db.exists_request(other, uid):
        return err("no_request", 404)
    await db.delete_request(other, uid)
    await db.add_friend(uid, other)
    try:
        await bot.send_message(
            other,
            f"🤝 <b>{html_mod.escape(me.get('first_name') or 'Игрок')}</b> принял твою заявку в друзья!\n"
            f"——— English ———\n"
            f"🤝 <b>{html_mod.escape(me.get('first_name') or 'Player')}</b> accepted your friend request!",
        )
    except Exception:
        pass
    return ok()


async def friend_decline(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    uid = request["user_id"]
    row = await db.user_by_public_id(request.match_info["public_id"])
    if not row:
        return err("not_found", 404)
    await db.delete_request(row["telegram_id"], uid)
    return ok()


async def friend_remove(request: web.Request) -> web.Response:
    """РЈРґР°Р»СЏРµС‚ РґСЂСѓР¶Р±Сѓ РР›Р РѕС‚РјРµРЅСЏРµС‚/РѕС‚РєР»РѕРЅСЏРµС‚ Р·Р°СЏРІРєСѓ РјРµР¶РґСѓ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏРјРё."""
    db: Database = request.app["db"]
    uid = request["user_id"]
    row = await db.user_by_public_id(request.match_info["public_id"])
    if not row:
        return err("not_found", 404)
    other = row["telegram_id"]
    if await db.exists_request(uid, other):
        await db.delete_request(uid, other)
        return ok(status="cancelled")
    if await db.exists_request(other, uid):
        await db.delete_request(other, uid)
        return ok(status="declined")
    await db.remove_friend(uid, other)
    return ok(status="removed")


async def friends_get(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    uid = request["user_id"]
    return ok(
        friends=await db.friends_with_profiles(uid),
        incoming=await db.incoming_requests(uid),
        outgoing=await db.outgoing_requests(uid),
    )


# ---------- панельное API (админ-бот) ----------

def _week_start_ts() -> int:
    from bot.profile import week_bounds
    return week_bounds()[0]


async def panel_summary(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    day_start, _ = db.day_bounds()
    stats = await db.stats()
    return ok(
        stats=dict(
            stats,
            stars_today=await db.stars_sum(day_start),
            stars_week=await db.stars_sum(_week_start_ts()),
            new_users_today=await db.new_users_count(day_start),
            new_users_week=await db.new_users_count(_week_start_ts()),
        ),
        pending_suggestions=len(await db.active_suggestions(limit=100)),
    )


async def panel_payments(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    try:
        limit = int(request.query.get("limit", "15"))
    except ValueError:
        limit = 15
    return ok(payments=await db.recent_payments(max(1, min(limit, 50))))


async def panel_suggestions(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    return ok(suggestions=await db.active_suggestions())


async def panel_suggestion_status(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    try:
        sid = int(request.match_info["sid"])
        data = await request.json()
    except Exception:
        return err("bad_json")
    if not await db.set_suggestion_status(sid, str(data.get("status", ""))):
        return err("bad_status_or_not_found", 404)
    return ok()


async def panel_broadcast(request: web.Request) -> web.Response:
    """Рассылка от ОСНОВНОГО бота (его знают все пользователи)."""
    bot: Bot = request.app["bot"]
    db: Database = request.app["db"]
    try:
        data = await request.json()
    except Exception:
        return err("bad_json")
    text = str(data.get("text", "")).strip()[:4000]
    if not text:
        return err("empty_text")

    sent, failed = 0, 0
    for uid in await db.all_user_ids():
        try:
            await bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    return ok(sent=sent, failed=failed)


async def panel_user(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    try:
        uid = int(request.match_info["uid"])
    except ValueError:
        return err("bad_id")
    detail = await db.user_detail(uid)
    if not detail:
        return err("not_found", 404)
    return ok(user=detail)


async def tasks_post(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    uid = request["user_id"]
    try:
        data = await request.json()
    except Exception:
        return err("bad_json")

    text = str(data.get("text", "")).strip()[:256]
    if not text:
        return err("empty_text")

    now_ts = int(time.time())
    try:
        deadline = int(data.get("deadline_ts") or 0)
    except (TypeError, ValueError):
        deadline = 0
    if deadline <= now_ts:
        _, end_ts = db.day_bounds()
        deadline = end_ts - 1  # РїРѕ СѓРјРѕР»С‡Р°РЅРёСЋ вЂ” РєРѕРЅРµС† СЃРµРіРѕРґРЅСЏС€РЅРµРіРѕ РґРЅСЏ

    row = await db.get_user(uid)
    tier = Database.tier_of(row)
    if tier != "elite":  # Сѓ Elite вЂ” Р±РµР·Р»РёРјРёС‚
        limit = cfg.REGULAR_TASK_LIMIT if tier == "regular" else cfg.FREE_TASK_LIMIT
        created = await db.count_created_today(uid)
        if created >= limit:
            return err("limit_reached", 403)

    task_id = await db.add_task(uid, text, deadline)
    return ok(task_id=task_id)


async def tasks_done(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    uid = request["user_id"]
    task_id = int(request.match_info["task_id"])
    if await db.complete_task(task_id, uid):
        return ok()
    return err("not_found", 404)


async def tasks_delete(request: web.Request) -> web.Response:
    db: Database = request.app["db"]
    uid = request["user_id"]
    task_id = int(request.match_info["task_id"])
    if await db.delete_task(task_id, uid):
        return ok()
    return err("not_found", 404)


async def subscription_buy(request: web.Request) -> web.Response:
    """РЎРѕР·РґР°С‘С‚ СЃС‡С‘С‚ РІ Telegram Stars РґР»СЏ РІС‹Р±СЂР°РЅРЅРѕРіРѕ С‚Р°СЂРёС„Р° Рё РїРµСЂРёРѕРґР°."""
    bot: Bot = request.app["bot"]
    uid = request["user_id"]
    try:
        data = await request.json()
    except Exception:
        return err("bad_json")

    tier = str(data.get("tier", ""))
    period = str(data.get("period", ""))
    plan = cfg.SUBSCRIPTION_PLANS.get(tier, {}).get(period)
    if not plan:
        return err("bad_plan")

    tier_title = cfg.TIER_TITLES.get(tier, tier)
    period_title = cfg.PERIOD_TITLES.get(period, period)
    title = f"РџРѕРґРїРёСЃРєР° {tier_title} вЂ” {period_title}"
    if plan["days"] > 0:
        description = f"Р”РѕСЃС‚СѓРї РЅР° {plan['days']} РґРЅ.: Р»РёРјРёС‚ Р·Р°РґР°С‡, Р РµР¶РёРј РїР°РЅРёРєРё, РЅР°РїРѕРјРёРЅР°РЅРёСЏ"
    else:
        description = "Р‘РµСЃСЃСЂРѕС‡РЅС‹Р№ РґРѕСЃС‚СѓРї: Р»РёРјРёС‚ Р·Р°РґР°С‡, Р РµР¶РёРј РїР°РЅРёРєРё, РЅР°РїРѕРјРёРЅР°РЅРёСЏ"

    link = await bot.create_invoice_link(
        title=title,
        description=description,
        payload=f"sub:{tier}:{period}:{uid}",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=plan["stars"])],
        provider_token="",
    )
    return ok(invoice_link=link)


async def index(_request: web.Request) -> web.Response:
    resp = web.FileResponse(WEBAPP_DIR / "index.html")
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return resp


async def root(_request: web.Request) -> web.Response:
    raise web.HTTPFound("/webapp")


def build_app(bot: Bot, db: Database) -> web.Application:
    app = web.Application(middlewares=[no_cache_middleware, auth_middleware])
    app["bot"] = bot
    app["db"] = db

    app.router.add_get("/", root)
    app.router.add_get("/webapp", index)
    # РІРµСЂСЃРёРѕРЅРёСЂРѕРІР°РЅРЅС‹Р№ РїСѓС‚СЊ: Р»СЋР±РѕР№ РєСЌС€ (WebView, Cloudflare) РЅРµ СЃРјРѕР¶РµС‚ РѕС‚РґР°С‚СЊ СЃС‚Р°СЂСЊС‘
    app.router.add_static("/static/v26/", WEBAPP_DIR)
    app.router.add_static("/static/", WEBAPP_DIR)

    app.router.add_get("/api/me", tasks_get)  # СЃРѕРІРјРµСЃС‚РёРјРѕРµ РёРјСЏ: /api/me -> С‚Рµ Р¶Рµ РґР°РЅРЅС‹Рµ
    app.router.add_get("/api/tasks", tasks_get)
    app.router.add_post("/api/tasks", tasks_post)
    app.router.add_post("/api/tasks/{task_id}/done", tasks_done)
    app.router.add_delete("/api/tasks/{task_id}", tasks_delete)
    app.router.add_post("/api/subscription/buy", subscription_buy)
    app.router.add_post("/api/suggest", suggest_post)
    app.router.add_get("/api/profile", profile_get)
    app.router.add_get("/api/leaderboard", leaderboard_get)
    app.router.add_get("/api/profile/{public_id}", public_profile_get)
    app.router.add_get("/api/friends", friends_get)
    app.router.add_post("/api/friends/{public_id}", friend_add)
    app.router.add_delete("/api/friends/{public_id}", friend_remove)
    app.router.add_post("/api/friends/{public_id}/accept", friend_accept)
    app.router.add_post("/api/friends/{public_id}/decline", friend_decline)

    # панельное API для админ-бота (доступ по X-Panel-Token)
    app.router.add_get("/api/panel/summary", panel_summary)
    app.router.add_get("/api/panel/payments", panel_payments)
    app.router.add_get("/api/panel/suggestions", panel_suggestions)
    app.router.add_post("/api/panel/suggestions/{sid}/status", panel_suggestion_status)
    app.router.add_post("/api/panel/broadcast", panel_broadcast)
    app.router.add_get("/api/panel/user/{uid}", panel_user)
    app.router.add_get("/api/admin/stats", admin_stats)
    app.router.add_get("/api/admin/suggestions", admin_suggestions)
    app.router.add_post("/api/admin/suggestions/{sid}/status", admin_suggestion_status)
    return app
