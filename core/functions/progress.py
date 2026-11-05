"""
Прогресс серий: позиция в серии, «досмотрено» и пороги, после которых тайтл попадает в «Смотрю» и в списки.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Функции без маршрутов для разделов аккаунта (core/api/sections): соединение с базой приходит от раздела, коммит —
тоже его. Здесь же метка времени записей аккаунта — UTC без пояса, как в таблицах. Серия, впервые ставшая
досмотренной, — событие для соцсети (core/functions/as_events.py) в той же транзакции; серии, которые отмечаются
досмотренными сами, потому что досмотрена следующая, событий не дают.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from core.classes.clock import utc_now
from core.functions import as_events, title_completion


def _mark_previous_episodes_done_if_needed(
    conn,
    user_id: int,
    mal_id: int,
    episode_num: int,
    seconds: int,
    duration: int,
    updated_at: datetime,
) -> None:
    """Серия досмотрена — значит, и все серии до неё отмечаются досмотренными."""
    if episode_num <= 1 or duration <= 0:
        return
    if not (seconds >= duration * 0.92 or seconds >= max(0, duration - 90)):
        return
    stamp_duration = max(duration, 1)
    cur = conn.cursor()
    for ep in range(1, episode_num):
        cur.execute(
            """INSERT INTO aviev_episode_progress
                (user_id, mal_id, episode_num, seconds, duration, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    seconds=GREATEST(seconds, %s),
                    duration=GREATEST(duration, %s),
                    updated_at=VALUES(updated_at)""",
            (
                user_id,
                mal_id,
                ep,
                stamp_duration,
                stamp_duration,
                updated_at,
                stamp_duration,
                stamp_duration,
            ),
        )


def upsert_episode_progress(
    conn,
    user_id: int,
    mal_id: int,
    episode_num: int,
    seconds: int,
    duration: int,
    updated_at: datetime | None = None,
) -> bool:
    """Записать позицию в серии: seconds — дальняя точка (берётся большее из старого и нового, по ней «досмотрено»),
    last_seconds — где остановился (последняя присланная позиция, для «Продолжить с»). True — серия только что
    впервые стала досмотренной: тогда в очереди соцсети событие episode, а у последней серии по каталогу — и
    completed (core/functions/title_completion.py)."""
    stamp = updated_at or utc_now()
    cur = conn.cursor()
    cur.execute(
        "SELECT seconds, duration FROM aviev_episode_progress WHERE user_id=%s AND mal_id=%s AND episode_num=%s",
        (user_id, mal_id, episode_num),
    )
    before = cur.fetchone()
    cur.execute(
        """INSERT INTO aviev_episode_progress
            (user_id, mal_id, episode_num, seconds, duration, updated_at, last_seconds)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                seconds=GREATEST(seconds, VALUES(seconds)),
                duration=GREATEST(duration, VALUES(duration)),
                updated_at=VALUES(updated_at),
                last_seconds=VALUES(last_seconds)""",
        (user_id, mal_id, episode_num, seconds, duration, stamp, seconds),
    )
    _mark_previous_episodes_done_if_needed(
        conn,
        user_id,
        mal_id,
        episode_num,
        seconds,
        duration,
        stamp,
    )
    old_seconds, old_duration = (int(before[0]), int(before[1])) if before else (0, 0)
    if before and progress_done(old_seconds, old_duration):
        return False
    if not progress_done(max(old_seconds, seconds), max(old_duration, duration)):
        return False
    as_events.record(conn, user_id, "episode", mal_id, episode=episode_num, at=stamp)
    # Последняя серия по каталогу сервера — и досмотр тайтла: факт просмотра, не правило списков
    title_completion.episode_done(conn, user_id, mal_id, episode_num, stamp)
    return True


def progress_done(seconds: int, duration: int) -> bool:
    """Серия досмотрена: 92 % или последние полторы минуты."""
    return duration > 0 and (seconds >= duration * 0.92 or seconds >= max(0, duration - 90))


def history_qualified(seconds: int, duration: int) -> bool:
    """Тайтл попадает в «Продолжить просмотр»: смотрели 5 минут или серия досмотрена."""
    return seconds >= 300 or (duration > 0 and seconds >= duration * 0.92)


def auto_watching_qualified(seconds: int, duration: int) -> bool:
    """Тайтл сам встаёт в «Смотрю»: смотрели 10 минут или серия досмотрена."""
    return seconds >= 600 or (duration > 0 and seconds >= duration * 0.92)


def progress_rows_for_title(conn, user_id: int, mal_id: int) -> list[dict[str, Any]]:
    """Позиции во всех сериях тайтла по порядку серий; last_seconds у строк до миграции 2026-11-05 — дальняя точка."""
    cur = conn.cursor()
    cur.execute(
        """SELECT episode_num, seconds, duration, updated_at, last_seconds
            FROM aviev_episode_progress
            WHERE user_id=%s AND mal_id=%s
            ORDER BY episode_num""",
        (user_id, mal_id),
    )
    return [
        {
            "episode_num": int(r[0]),
            "seconds": int(r[1] or 0),
            "duration": int(r[2] or 0),
            "updated_at": r[3].isoformat() if r[3] else None,
            "last_seconds": int(r[1] or 0) if r[4] is None else int(r[4]),
        }
        for r in cur.fetchall()
    ]
