# -*- coding: utf-8 -*-
"""
AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Activity log — события пользователя (watch_start / watch_continue / list_add /
list_move / favorite / unfavorite / rate / complete / list_remove). Складываются
в `aviev_activity`, отсюда считается contribution-график профиля.

`record_event` пишет синхронно в текущем соединении. Вызывающий код должен
сам вызвать `conn.commit()` когда готов.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends

from server.animesocial import connect, current_user_required

log = logging.getLogger("animeviev.activity")

router = APIRouter(prefix="/account/activity")


ALLOWED_KINDS = {
    "watch_start", "watch_continue", "list_add", "list_move", "list_remove",
    "favorite", "unfavorite", "rate", "complete",
}

# Группировка kind'ов для UI-фильтра активности. «Все» = без фильтра.
KIND_GROUPS = {
    "watch":     ["watch_start", "watch_continue", "complete"],
    "lists":     ["list_add", "list_move", "list_remove"],
    "favorites": ["favorite", "unfavorite"],
    "rate":      ["rate"],
}

# Для этих событий дедуп должен быть «один раз в день на тайтл», а не по
# конкретному значению meta. Иначе 5 переносов «watching→dropped→watching→…»
# спамят активность. Пишем их с пустой meta — уникальный индекс
# (user, day, kind, mal_id, meta(64)) схлопнёт дубликаты на уровне INSERT IGNORE.
DEDUP_BY_TITLE_PER_DAY = {
    "list_add", "list_move", "list_remove",
    "favorite", "unfavorite", "complete", "rate",
}


def record_event(conn, user_id: int, *, kind: str, mal_id: int | None = None,
                 meta: str | None = None) -> None:
    """Пишет событие в aviev_activity.

    Для watch_* дедуп по (user, day, kind, mal_id, ep) — разные серии
    считаются отдельно, но пауза/перемотка на одной серии в тот же день — нет.

    Для list_* / favorite / complete / rate дедуп просто по (user, day, kind,
    mal_id) — активность не спамится при перекидывании тайтла между списками.
    """
    if kind not in ALLOWED_KINDS:
        log.debug("record_event: unknown kind=%s", kind)
        return
    stored_meta = "" if kind in DEDUP_BY_TITLE_PER_DAY else (meta or "")
    now = datetime.utcnow()
    cur = conn.cursor()
    cur.execute(
        """INSERT IGNORE INTO aviev_activity
            (user_id, day, kind, mal_id, meta, at)
            VALUES (%s, %s, %s, %s, %s, %s)""",
        (user_id, now.date(), kind, mal_id, stored_meta[:255], now),
    )


ALLOWED_DAYS = (30, 90, 180, 365)
DEFAULT_DAYS = 30


def _clamp_days(days: int | None) -> int:
    if not days or days <= 0:
        return DEFAULT_DAYS
    for d in ALLOWED_DAYS:
        if days <= d:
            return d
    return 365


def _resolve_kinds(group: str | None) -> list[str] | None:
    """Map группы UI → список kind'ов для WHERE-фильтра.
    None → без фильтра (все события)."""
    if not group or group == "all":
        return None
    kinds = KIND_GROUPS.get(group)
    return list(kinds) if kinds else None


def _fetch_recent(conn, user_id: int, *, offset: int = 0, limit: int = 10,
                  group: str | None = None, day: str | None = None) -> list[dict[str, Any]]:
    """Постраничная выборка recent events с enrich тайтлом. Возвращает список
    items; вызывающий сам решает has_more по len(items) == limit+1.
    Для экономии запросов мы забираем limit+1 элементов, обрезаем до limit,
    и по факту len==limit+1 решаем, есть ли следующая страница.

    `day` — опциональный YYYY-MM-DD для фильтрации активности по одному дню
    (клик по клетке GitHub-grid'а). Комбинируется с group.
    """
    import re as _re

    cur = conn.cursor()
    kinds = _resolve_kinds(group)
    where = ["user_id=%s"]
    params: list[Any] = [user_id]
    if kinds:
        placeholders = ",".join(["%s"] * len(kinds))
        where.append(f"kind IN ({placeholders})")
        params.extend(kinds)
    if day:
        where.append("day=%s")
        params.append(day)
    sql = (
        f"""SELECT kind, mal_id, meta, at FROM aviev_activity
            WHERE {' AND '.join(where)}
            ORDER BY at DESC LIMIT %s OFFSET %s"""
    )
    cur.execute(sql, (*params, limit, offset))
    rows = cur.fetchall()

    # Enrich: тайтл из user_lists → title_pages
    titles: dict[int, str] = {}
    mal_ids = list({int(r[1]) for r in rows if r[1] is not None})
    if mal_ids:
        p = ",".join(["%s"] * len(mal_ids))
        cur.execute(
            f"""SELECT l.mal_id, l.title FROM aviev_user_lists l
                WHERE l.user_id=%s AND l.mal_id IN ({p})
                  AND l.title IS NOT NULL AND l.title <> ''""",
            (user_id, *mal_ids),
        )
        for mid, t in cur.fetchall():
            titles[int(mid)] = t
        missing = [m for m in mal_ids if m not in titles]
        if missing:
            p2 = ",".join(["%s"] * len(missing))
            cur.execute(
                f"""SELECT mal_id, title_ru, title_en FROM aviev_title_pages
                    WHERE mal_id IN ({p2})""",
                tuple(missing),
            )
            for mid, ru, en in cur.fetchall():
                titles[int(mid)] = (ru or en or "").strip()

    out = []
    for (k, mal, m, at) in rows:
        mid = int(mal) if mal is not None else None
        meta = m or ""
        ep = None
        if "ep=" in meta:
            mo = _re.search(r"ep=(\d+)", meta)
            ep = int(mo.group(1)) if mo else None
        out.append({
            "kind": k,
            "mal_id": mid,
            "meta": meta,
            "at": at.isoformat() if at else None,
            "title": titles.get(mid, "") if mid else "",
            "episode_num": ep,
        })
    return out


def fetch_recent_paged(user_id: int, *, offset: int, limit: int,
                       group: str | None, day: str | None = None) -> dict[str, Any]:
    """Обёртка над _fetch_recent — открывает коннект, считает has_more."""
    limit = max(1, min(limit, 50))
    offset = max(0, offset)
    # Мягкая валидация day: ожидаем YYYY-MM-DD, иначе игнорируем.
    if day:
        import re as _re
        if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            day = None
    with connect() as conn:
        items = _fetch_recent(conn, user_id, offset=offset, limit=limit + 1,
                              group=group, day=day)
    has_more = len(items) > limit
    return {"items": items[:limit], "has_more": has_more, "offset": offset, "limit": limit,
            "group": group or "all", "day": day}


def _graph_for_user(user_id: int, days: int = DEFAULT_DAYS) -> dict[str, Any]:
    """Возвращает агрегат за последние `days` дней: грид по дням + summary +
    последние 20 событий в виде списка (c тайтлами и эпизодами, если есть)."""
    days = _clamp_days(days)
    now = datetime.utcnow()
    start = (now - timedelta(days=days - 1)).date()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT day, COUNT(*) AS n FROM aviev_activity
                WHERE user_id=%s AND day >= %s
                GROUP BY day""",
            (user_id, start),
        )
        by_day = {d.isoformat(): int(n) for d, n in cur.fetchall()}
        cur.execute(
            """SELECT kind, COUNT(*) FROM aviev_activity
                WHERE user_id=%s AND day >= %s
                GROUP BY kind""",
            (user_id, start),
        )
        by_kind = {k: int(n) for k, n in cur.fetchall()}
        recent = _fetch_recent(conn, user_id, offset=0, limit=10, group=None)

    cells = []
    total = 0
    max_on_day = 0
    active_days = 0
    streak_current = 0
    streak_best = 0
    cursor = start
    today = now.date()
    while cursor <= today:
        n = by_day.get(cursor.isoformat(), 0)
        cells.append({"d": cursor.isoformat(), "n": n})
        total += n
        if n > max_on_day:
            max_on_day = n
        if n > 0:
            active_days += 1
            streak_current += 1
            if streak_current > streak_best:
                streak_best = streak_current
        else:
            streak_current = 0
        cursor += timedelta(days=1)

    return {
        "period_days": days,
        "from": start.isoformat(),
        "to": today.isoformat(),
        "days": cells,
        "totals": {
            "events": total,
            "active_days": active_days,
            "max_on_day": max_on_day,
            "streak_current": streak_current,
            "streak_best": streak_best,
            "by_kind": by_kind,
        },
        "recent": recent,
    }


# ---------- routes ----------
from fastapi import Query


@router.get("")
def activity_my(
    days: int = Query(default=DEFAULT_DAYS, ge=1, le=365),
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    return _graph_for_user(user["id"], days=days)


@router.get("/recent")
def activity_my_recent(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50),
    group: str = Query(default="all"),
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    return fetch_recent_paged(user["id"], offset=offset, limit=limit,
                              group=group, day=date)


def graph_for_user_public(user_id: int, days: int = DEFAULT_DAYS) -> dict[str, Any]:
    """Без auth — используется public-профилем, который уже проверил
    приватность в вызывающем коде."""
    return _graph_for_user(user_id, days=days)
