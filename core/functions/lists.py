"""
Списки тайтлов: статусы, избранное и правила, которые ставят статус сами.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Одна таблица `aviev_user_lists` держит и основной статус (watching / planned /
completed / dropped / postponed), и отдельный флаг is_favorite. Каждая
запись помечена `status_source` = manual | auto — manual побеждает auto.

Автоматические правила:
    - на 10-й минуте просмотра тайтл попадает в watching (auto),
    - на последней серии — в completed (auto),
    - через 30 дней простоя watching → dropped (auto).
Все три правила подчиняются настройке `auto_add_lists` в aviev_account_settings. Первые два считает раздел
/account/lists/progress-event (core/api/sections/lists), третье — sweep_dropped_after_30_days ниже.

Activity-логирование живёт в `core.functions.activity` и вызывается отсюда, чтобы
contribution-график имел честный источник событий.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from core.classes.clock import utc_now
from core.classes.jikan import catalog as jikan_catalog
from core.functions.activity import record_event

STATUSES = ("watching", "planned", "completed", "dropped", "postponed")


async def mal_id_exists(mal_id: int) -> bool:
    """Проверяет, существует ли запись в MyAnimeList. Подтягивает Jikan,
    кэш у нас уже стоит (1 ч per-id), так что проверка практически бесплатная.

    Возвращает True при HTTP 200, False при 404, True при 5xx — потому что
    временная недоступность Jikan не повод блокировать пользователя. Это
    «сильная валидация для откровенных мертвецов, мягкая для флейковой сети».
    """
    try:
        _, _, status = await jikan_catalog.fetch(f"anime/{mal_id}", timeout=6)
    except Exception:
        return True
    if status == 404:
        return False
    return True


# ---------- helpers ----------
def row_to_public(row: tuple) -> dict[str, Any]:
    """Строка aviev_user_lists — в запись списка для ответа."""
    (user_id, mal_id, status, source, is_fav, title, poster, added_at, updated_at) = row
    return {
        "mal_id": int(mal_id),
        "status": status,
        "status_source": source,
        "is_favorite": bool(is_fav),
        "title": title or "",
        "poster_url": poster or "",
        "added_at": added_at.isoformat() if added_at else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
    }


def auto_add_enabled(conn, user_id: int) -> bool:
    """Включены ли у человека автоматические правила списков (по умолчанию — да)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT auto_add_lists FROM aviev_account_settings WHERE user_id=%s",
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        return True  # default ON
    return bool(row[0])


def fetch_entry(conn, user_id: int, mal_id: int) -> dict[str, Any] | None:
    """Запись тайтла в списках человека или None."""
    cur = conn.cursor()
    cur.execute(
        """SELECT user_id, mal_id, status, status_source, is_favorite,
                  title, poster_url, added_at, updated_at
            FROM aviev_user_lists WHERE user_id=%s AND mal_id=%s""",
        (user_id, mal_id),
    )
    row = cur.fetchone()
    return row_to_public(row) if row else None


def upsert_status(conn, user_id: int, mal_id: int, status: str | None,
                  source: str, title: str = "", poster_url: str = "") -> None:
    """Поставить статус тайтла (manual или auto); избранное не трогается."""
    now = utc_now()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO aviev_user_lists
            (user_id, mal_id, status, status_source, is_favorite,
             title, poster_url, added_at, updated_at)
            VALUES (%s, %s, %s, %s, 0, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                status=VALUES(status),
                status_source=VALUES(status_source),
                title=IF(VALUES(title)<>'', VALUES(title), title),
                poster_url=IF(VALUES(poster_url)<>'', VALUES(poster_url), poster_url),
                updated_at=VALUES(updated_at)""",
        (user_id, mal_id, status, source, title, poster_url, now, now),
    )


# ---------- cron-friendly hook ----------
def sweep_dropped_after_30_days(conn) -> int:
    """Переводит auto-watching без движения >30 дней в auto-dropped — по всей базе.

    Возвращает количество изменённых строк. Планировщика пока нет: вызвать можно откуда угодно, а ручной прогон
    для одного человека делает POST /account/lists/sweep. Manual-статусы никогда не трогает.
    """
    cutoff = utc_now() - timedelta(days=30)
    cur = conn.cursor()
    cur.execute(
        """SELECT user_id, mal_id FROM aviev_user_lists
            WHERE status='watching'
              AND status_source='auto'
              AND updated_at < %s""",
        (cutoff,),
    )
    rows = cur.fetchall()
    count = 0
    for user_id, mal_id in rows:
        cur.execute(
            """UPDATE aviev_user_lists
                SET status='dropped', status_source='auto', updated_at=%s
                WHERE user_id=%s AND mal_id=%s
                  AND status='watching' AND status_source='auto'""",
            (utc_now(), user_id, mal_id),
        )
        if cur.rowcount:
            count += 1
            record_event(conn, user_id, kind="list_move",
                         mal_id=mal_id, meta="watching->dropped:auto")
    conn.commit()
    return count
