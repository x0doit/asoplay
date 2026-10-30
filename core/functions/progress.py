"""
Прогресс серий: позиция в серии, «досмотрено» и пороги, после которых тайтл попадает в «Смотрю» и в списки.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Функции без маршрутов для разделов аккаунта (core/api/sections): соединение с базой приходит от раздела, коммит —
тоже его. Здесь же метка времени записей аккаунта — UTC без пояса, как в таблицах.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from core.classes.clock import utc_now


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
) -> None:
    """Записать позицию в серии (только вперёд: берётся большее из старого и нового)."""
    stamp = updated_at or utc_now()
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO aviev_episode_progress
            (user_id, mal_id, episode_num, seconds, duration, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                seconds=GREATEST(seconds, VALUES(seconds)),
                duration=GREATEST(duration, VALUES(duration)),
                updated_at=VALUES(updated_at)""",
        (user_id, mal_id, episode_num, seconds, duration, stamp),
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
    """Позиции во всех сериях тайтла по порядку серий."""
    cur = conn.cursor()
    cur.execute(
        """SELECT episode_num, seconds, duration, updated_at
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
        }
        for r in cur.fetchall()
    ]
