"""
Админ-API: обзор — версия и источники, люди, просмотры, популярное за неделю, кто сейчас на сайте.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/admin: вход router.py подключает части по порядку. STARTED_AT и loaded_sources ставит core.py при сборке
приложения: что знает только он — когда запущен процесс и какие источники загрузились.
"""
from __future__ import annotations

import re
import time
from datetime import timedelta
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends

from core.admin import titles
from core.admin.auth import admin_user, ok
from core.classes.clock import utc_now
from core.classes.db import connect
from core.config import env
from core.functions import site_settings, source_health

router = APIRouter()

# Когда запущен процесс — ставит core.py при старте
STARTED_AT = time.time()
# Какие источники загрузились в этом процессе — ставит core.py
loaded_sources: Any = lambda: []


@lru_cache(maxsize=1)
def _version() -> str:
    """Версия сборки — та же, что в адресах файлов сайта (index.html, app.js?v=…)."""
    try:
        text = (env.ROOT / "index.html").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    found = re.search(r"app\.js\?v=([\w.-]+)", text)
    return found.group(1) if found else "unknown"


def _summary() -> dict[str, Any]:
    """Обзор для первой страницы раздела в панели."""
    now = utc_now()
    day, week, month = now - timedelta(days=1), now - timedelta(days=7), now - timedelta(days=30)
    loaded = set(loaded_sources())
    sources = []
    for source in site_settings.sources_view():
        window = source_health.window(source["id"])
        sources.append({
            "id": source["id"], "name": source["name"], "enabled": source["enabled"],
            "ok": source["id"] in loaded and source_health.is_available(source["id"]),
            "fail_rate_24h": window["fail_rate_24h"], "latency_ms": window["latency_ms"],
            "requests_24h": window["requests_24h"],
        })
    with connect() as conn:
        cur = conn.cursor()
        # «Активен» — была открыта вкладка AsoPlay (сессия отмечается при каждом запросе и раз в 5 минут)
        cur.execute(
            """SELECT COUNT(DISTINCT user_id),
                      COUNT(DISTINCT CASE WHEN last_seen_at >= %s THEN user_id END),
                      COUNT(DISTINCT CASE WHEN last_seen_at >= %s THEN user_id END),
                      COUNT(DISTINCT CASE WHEN last_seen_at >= %s THEN user_id END)
                 FROM aviev_sessions""",
            (day, week, month),
        )
        total, active_1d, active_7d, active_30d = (int(x or 0) for x in cur.fetchone())
        cur.execute("""SELECT COUNT(*) FROM (SELECT user_id FROM aviev_sessions GROUP BY user_id
                                             HAVING MIN(created_at) >= %s) AS fresh""", (week,))
        new_7d = int(cur.fetchone()[0])
        # Серия засчитана, когда её смотрели дольше 10 секунд (watch_continue)
        cur.execute("""SELECT COUNT(DISTINCT user_id, mal_id, meta) FROM aviev_activity
                        WHERE day >= %s AND kind = 'watch_continue' AND at >= %s""", (day.date(), day))
        episodes_1d = int(cur.fetchone()[0])
        cur.execute("SELECT COALESCE(SUM(seconds), 0) FROM aviev_watch_time WHERE hour > %s", (day,))
        minutes_1d = int(cur.fetchone()[0]) // 60
        cur.execute(
            """SELECT mal_id, COUNT(DISTINCT user_id) AS viewers FROM aviev_activity
                WHERE day >= %s AND kind IN ('watch_start', 'watch_continue') AND mal_id IS NOT NULL
                GROUP BY mal_id ORDER BY viewers DESC, mal_id LIMIT 10""",
            (week.date(),),
        )
        top = [(int(m), int(v)) for m, v in cur.fetchall()]
        names = titles._names_for(cur, [m for m, _ in top])
        cur.execute("SELECT COUNT(*) FROM aviev_reports WHERE status = 'open'")
        reports_open = int(cur.fetchone()[0])
    from core.functions import realtime
    return {
        "version": _version(),
        "started_at": int(STARTED_AT),
        "health": {"ok": any(s["enabled"] and s["ok"] for s in sources), "sources": sources},
        "users": {"total": total, "active_1d": active_1d, "active_7d": active_7d, "active_30d": active_30d,
                  "new_7d": new_7d},
        "watch": {"episodes_1d": episodes_1d, "minutes_1d": minutes_1d},
        "top_titles_7d": [{"mal_id": m, "title": names.get(m, ""), "viewers": v, "hidden": site_settings.hidden(m)}
                          for m, v in top],
        "online": {"users": len(realtime.hub.by_user), "tabs": len(realtime.hub.conns)},
        "reports_open": reports_open,
    }


@router.get("/summary")
def summary(_admin: int = Depends(admin_user)) -> dict[str, Any]:
    """GET /api/admin/summary — обзор."""
    return ok(_summary())
