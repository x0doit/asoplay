"""
История «Смотрю» по прогрессу серий и авто-статус тайтла в списках после «не смотрел».

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Функции без маршрутов для разделов аккаунта (core/api/sections): соединение с базой приходит от раздела, коммит —
тоже его. Пороги «досмотрено» и «попадает в историю» — в core/functions/progress.py.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from core.classes.clock import utc_now
from core.functions.progress import auto_watching_qualified, history_qualified, progress_done


def sync_watch_history_from_progress(
    conn,
    user_id: int,
    mal_id: int,
    *,
    episode_num: int,
    title: str = "",
    poster_url: str = "",
    episodes_total: int = 0,
    updated_at: datetime | None = None,
) -> None:
    """Обновить «Смотрю» по только что записанной позиции, если тайтл уже прошёл порог истории."""
    cur = conn.cursor()
    cur.execute(
        """SELECT seconds, duration
            FROM aviev_episode_progress
            WHERE user_id=%s AND mal_id=%s AND episode_num=%s
            LIMIT 1""",
        (user_id, mal_id, episode_num),
    )
    row = cur.fetchone()
    if not row:
        return
    episode_seconds = int(row[0] or 0)
    episode_duration = int(row[1] or 0)
    cur.execute(
        """SELECT 1
            FROM aviev_episode_progress
            WHERE user_id=%s AND mal_id=%s
              AND (seconds >= 300 OR (duration > 0 AND seconds >= duration * 0.92))
            LIMIT 1""",
        (user_id, mal_id),
    )
    qualified = bool(cur.fetchone()) or history_qualified(episode_seconds, episode_duration)
    if not qualified:
        return
    stamp = updated_at or utc_now()
    cur.execute(
        """INSERT INTO aviev_watch_history
            (user_id, mal_id, last_episode, episode_seconds, episode_duration,
             episodes_total, title, poster_url, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                last_episode=VALUES(last_episode),
                episode_seconds=VALUES(episode_seconds),
                episode_duration=GREATEST(episode_duration, VALUES(episode_duration)),
                episodes_total=IF(VALUES(episodes_total)>0,
                                  VALUES(episodes_total), episodes_total),
                title=IF(VALUES(title)<>'', VALUES(title), title),
                poster_url=IF(VALUES(poster_url)<>'', VALUES(poster_url), poster_url),
                updated_at=VALUES(updated_at)""",
        (
            user_id,
            mal_id,
            episode_num,
            episode_seconds,
            episode_duration,
            episodes_total,
            title,
            poster_url,
            stamp,
        ),
    )


def history_row_for_title(conn, user_id: int, mal_id: int) -> dict[str, Any] | None:
    """Строка «Смотрю» этого тайтла или None."""
    cur = conn.cursor()
    cur.execute(
        """SELECT mal_id, last_episode, episode_seconds, episode_duration,
                  episodes_total, title, poster_url, updated_at
            FROM aviev_watch_history
            WHERE user_id=%s AND mal_id=%s
            LIMIT 1""",
        (user_id, mal_id),
    )
    r = cur.fetchone()
    if not r:
        return None
    return {
        "mal_id": int(r[0]),
        "last_episode": int(r[1] or 1),
        "episode_seconds": int(r[2] or 0),
        "episode_duration": int(r[3] or 0),
        "episodes_total": int(r[4] or 0),
        "title": r[5] or "",
        "poster_url": r[6] or "",
        "updated_at": r[7].isoformat() if r[7] else None,
    }


def _list_entry_for_title(conn, user_id: int, mal_id: int) -> dict[str, Any] | None:
    """Запись тайтла в списках человека или None."""
    cur = conn.cursor()
    cur.execute(
        """SELECT mal_id, status, status_source, is_favorite,
                  title, poster_url, added_at, updated_at
            FROM aviev_user_lists
            WHERE user_id=%s AND mal_id=%s
            LIMIT 1""",
        (user_id, mal_id),
    )
    r = cur.fetchone()
    if not r:
        return None
    return {
        "mal_id": int(r[0]),
        "status": r[1],
        "status_source": r[2],
        "is_favorite": bool(r[3]),
        "title": r[4] or "",
        "poster_url": r[5] or "",
        "added_at": r[6].isoformat() if r[6] else None,
        "updated_at": r[7].isoformat() if r[7] else None,
    }


def resync_watch_history_from_latest_progress(conn, user_id: int, mal_id: int) -> dict[str, Any] | None:
    """Пересобрать «Смотрю» по самой свежей оставшейся позиции; тайтл ниже порога истории — убрать из неё."""
    cur = conn.cursor()
    old_history = history_row_for_title(conn, user_id, mal_id) or {}
    list_entry = _list_entry_for_title(conn, user_id, mal_id) or {}
    episodes_total = int(old_history.get("episodes_total") or 0)
    title = old_history.get("title") or list_entry.get("title") or ""
    poster_url = old_history.get("poster_url") or list_entry.get("poster_url") or ""

    cur.execute(
        """SELECT episode_num, seconds, duration, updated_at
            FROM aviev_episode_progress
            WHERE user_id=%s AND mal_id=%s
            ORDER BY updated_at DESC, episode_num DESC
            LIMIT 1""",
        (user_id, mal_id),
    )
    latest = cur.fetchone()
    cur.execute(
        """SELECT 1
            FROM aviev_episode_progress
            WHERE user_id=%s AND mal_id=%s
              AND (seconds >= 300 OR (duration > 0 AND seconds >= duration * 0.92))
            LIMIT 1""",
        (user_id, mal_id),
    )
    qualified = bool(cur.fetchone())
    if not latest or not qualified:
        cur.execute(
            "DELETE FROM aviev_watch_history WHERE user_id=%s AND mal_id=%s",
            (user_id, mal_id),
        )
        return None

    episode_num = int(latest[0] or 1)
    episode_seconds = int(latest[1] or 0)
    episode_duration = int(latest[2] or 0)
    stamp = latest[3] or utc_now()
    cur.execute(
        """INSERT INTO aviev_watch_history
            (user_id, mal_id, last_episode, episode_seconds, episode_duration,
             episodes_total, title, poster_url, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                last_episode=VALUES(last_episode),
                episode_seconds=VALUES(episode_seconds),
                episode_duration=VALUES(episode_duration),
                episodes_total=VALUES(episodes_total),
                title=IF(VALUES(title)<>'', VALUES(title), title),
                poster_url=IF(VALUES(poster_url)<>'', VALUES(poster_url), poster_url),
                updated_at=VALUES(updated_at)""",
        (
            user_id,
            mal_id,
            episode_num,
            episode_seconds,
            episode_duration,
            episodes_total,
            title,
            poster_url,
            stamp,
        ),
    )
    return history_row_for_title(conn, user_id, mal_id)


def adjust_auto_list_status_after_unwatch(
    conn,
    user_id: int,
    mal_id: int,
    *,
    removed_episode: int,
    episodes_total: int,
) -> dict[str, Any] | None:
    """После «не смотрел» поправить статус, который список ставил сам: «Просмотрено» → «Смотрю» → без статуса.
    Статус, выбранный человеком, не трогается."""
    entry = _list_entry_for_title(conn, user_id, mal_id)
    if not entry or entry.get("status_source") != "auto":
        return entry

    cur = conn.cursor()
    cur.execute(
        """SELECT episode_num, seconds, duration
            FROM aviev_episode_progress
            WHERE user_id=%s AND mal_id=%s
            ORDER BY episode_num DESC""",
        (user_id, mal_id),
    )
    rows = [(int(r[0] or 0), int(r[1] or 0), int(r[2] or 0)) for r in cur.fetchall()]
    has_watching_progress = any(auto_watching_qualified(seconds, duration) for _, seconds, duration in rows)
    still_completed = bool(
        episodes_total > 0
        and any(ep >= episodes_total and progress_done(seconds, duration) for ep, seconds, duration in rows)
    )

    if still_completed:
        target_status = "completed"
    elif has_watching_progress:
        target_status = "watching"
    else:
        target_status = None

    now = utc_now()
    if target_status:
        cur.execute(
            """UPDATE aviev_user_lists
                SET status=%s, status_source='auto', updated_at=%s
                WHERE user_id=%s AND mal_id=%s""",
            (target_status, now, user_id, mal_id),
        )
    else:
        cur.execute(
            """UPDATE aviev_user_lists
                SET status=NULL, status_source='auto', updated_at=%s
                WHERE user_id=%s AND mal_id=%s""",
            (now, user_id, mal_id),
        )
        cur.execute(
            """DELETE FROM aviev_user_lists
                WHERE user_id=%s AND mal_id=%s
                  AND is_favorite=0 AND status IS NULL""",
            (user_id, mal_id),
        )

    if removed_episode >= episodes_total > 0 and not still_completed:
        cur.execute(
            """DELETE FROM aviev_activity
                WHERE user_id=%s AND mal_id=%s AND kind='complete'""",
            (user_id, mal_id),
        )
    if not has_watching_progress:
        cur.execute(
            """DELETE FROM aviev_activity
                WHERE user_id=%s AND mal_id=%s
                  AND kind='list_add' AND meta='watching:auto'""",
            (user_id, mal_id),
        )
    return _list_entry_for_title(conn, user_id, mal_id)
