"""
Чтение журнала активности: лента событий постранично и график по дням для профиля.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Запись — core/functions/activity.py, маршруты /account/activity — core/api/sections/activity.py. Публичный профиль
(core/modules/profile_pages.py) зовёт graph_for_user_public и fetch_recent_paged, приватность он проверяет сам.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from core.classes.clock import utc_now
from core.classes.db import connect
from core.functions.activity import KIND_GROUPS

WATCH_EVENT_HAS_PROGRESS_SQL = """(
    a.kind NOT IN ('watch_start', 'watch_continue')
    OR (
        a.meta REGEXP '^ep=[0-9]+$'
        AND EXISTS (
            SELECT 1
            FROM aviev_episode_progress p
            WHERE p.user_id = a.user_id
              AND p.mal_id = a.mal_id
              AND p.episode_num = CAST(SUBSTRING(a.meta, 4) AS UNSIGNED)
        )
    )
)"""


ALLOWED_DAYS = (30, 90, 180, 365)
DEFAULT_DAYS = 30


def _clamp_days(days: int | None) -> int:
    """Период графика: ближайший из ALLOWED_DAYS, не меньше запрошенного."""
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


def _activity_title(value: str | None) -> str:
    """Название тайтла для ленты; испорченное старой записью (знаки «?» вместо букв) — пустое."""
    title = (value or "").strip()
    # Legacy lossy writes stored literal '?' instead of letters; decoding cannot restore them.
    if "\ufffd" in title or (title.count("?") >= 3 and not any(c.isalpha() for c in title)):
        return ""
    return title


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
    where = ["a.user_id=%s", WATCH_EVENT_HAS_PROGRESS_SQL]
    params: list[Any] = [user_id]
    if kinds:
        placeholders = ",".join(["%s"] * len(kinds))
        where.append(f"kind IN ({placeholders})")
        params.extend(kinds)
    if day:
        where.append("day=%s")
        params.append(day)
    sql = (
        f"""SELECT a.kind, a.mal_id, a.meta, a.at FROM aviev_activity a
            WHERE {' AND '.join(where)}
            ORDER BY a.at DESC LIMIT %s OFFSET %s"""
    )
    cur.execute(sql, (*params, limit, offset))
    rows = cur.fetchall()

    # Enrich: тайтл из user_lists → title_pages
    titles: dict[int, str] = {}
    posters: dict[int, str] = {}
    mal_ids = list({int(r[1]) for r in rows if r[1] is not None})
    if mal_ids:
        p = ",".join(["%s"] * len(mal_ids))
        cur.execute(
            f"""SELECT l.mal_id, l.title, l.poster_url FROM aviev_user_lists l
                WHERE l.user_id=%s AND l.mal_id IN ({p})
                """,
            (user_id, *mal_ids),
        )
        for mid, t, poster in cur.fetchall():
            title = _activity_title(t)
            if title:
                titles[int(mid)] = title
            if poster:
                posters[int(mid)] = poster
        missing = [m for m in mal_ids if m not in titles or m not in posters]
        if missing:
            p2 = ",".join(["%s"] * len(missing))
            cur.execute(
                f"""SELECT mal_id, title_ru, title_en, poster_url FROM aviev_title_pages
                    WHERE mal_id IN ({p2})""",
                tuple(missing),
            )
            for mid, ru, en, poster in cur.fetchall():
                titles.setdefault(int(mid), _activity_title(ru) or _activity_title(en))
                posters.setdefault(int(mid), poster or "")

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
            "poster_url": posters.get(mid, "") if mid else "",
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


def graph_for_user(user_id: int, days: int = DEFAULT_DAYS) -> dict[str, Any]:
    """Возвращает агрегат за последние `days` дней: грид по дням + summary +
    последние 20 событий в виде списка (c тайтлами и эпизодами, если есть)."""
    days = _clamp_days(days)
    now = utc_now()
    start = (now - timedelta(days=days - 1)).date()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""SELECT a.day, COUNT(*) AS n FROM aviev_activity a
                WHERE a.user_id=%s AND a.day >= %s
                  AND {WATCH_EVENT_HAS_PROGRESS_SQL}
                GROUP BY a.day""",
            (user_id, start),
        )
        by_day = {d.isoformat(): int(n) for d, n in cur.fetchall()}
        cur.execute(
            f"""SELECT a.kind, COUNT(*) FROM aviev_activity a
                WHERE a.user_id=%s AND a.day >= %s
                  AND {WATCH_EVENT_HAS_PROGRESS_SQL}
                GROUP BY a.kind""",
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


def graph_for_user_public(user_id: int, days: int = DEFAULT_DAYS) -> dict[str, Any]:
    """Без auth — используется public-профилем, который уже проверил
    приватность в вызывающем коде."""
    return graph_for_user(user_id, days=days)
