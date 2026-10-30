"""
Раздел /account/progress: позиции в сериях и «не смотрел».

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/api/sections/account: вход (__init__.py) подключает части по порядку.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, conint

from core.classes.clock import utc_now
from core.classes.db import connect, current_user_required
from core.functions.progress import progress_rows_for_title, upsert_episode_progress
from core.functions.watch_history import (
    adjust_auto_list_status_after_unwatch, history_row_for_title, resync_watch_history_from_latest_progress,
    sync_watch_history_from_progress,
)

router = APIRouter()


class ProgressIn(BaseModel):
    """Позиция в серии от плеера и что показывать в «Смотрю»."""

    episode_num: conint(ge=1, le=10_000)
    seconds: conint(ge=0, le=200_000)
    duration: conint(ge=0, le=200_000) = 0
    title: str = Field("", max_length=500)
    poster_url: str = Field("", max_length=500)
    episodes_total: conint(ge=0, le=10_000) = 0


@router.get("/progress/{mal_id}")
def progress_for_title(
    mal_id: int,
    user: dict[str, Any] = Depends(current_user_required),
) -> list[dict[str, Any]]:
    """GET /account/progress/{mal_id} — позиции во всех сериях тайтла."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT episode_num, seconds, duration, updated_at
                FROM aviev_episode_progress
                WHERE user_id=%s AND mal_id=%s
                ORDER BY episode_num""",
            (user["id"], mal_id),
        )
        rows = cur.fetchall()
    return [
        {
            "episode_num": int(r[0]),
            "seconds": int(r[1] or 0),
            "duration": int(r[2] or 0),
            "updated_at": r[3].isoformat() if r[3] else None,
        }
        for r in rows
    ]


@router.put("/progress/{mal_id}")
def progress_upsert(
    mal_id: int,
    payload: ProgressIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    """PUT /account/progress/{mal_id} — записать позицию в серии и обновить «Смотрю»."""
    stamp = utc_now()
    with connect() as conn:
        upsert_episode_progress(
            conn,
            user["id"],
            mal_id,
            payload.episode_num,
            payload.seconds,
            payload.duration,
            updated_at=stamp,
        )
        sync_watch_history_from_progress(
            conn,
            user["id"],
            mal_id,
            episode_num=payload.episode_num,
            title=payload.title,
            poster_url=payload.poster_url,
            episodes_total=int(payload.episodes_total or 0),
            updated_at=stamp,
        )
        conn.commit()
    return {"ok": True}


@router.delete("/progress/{mal_id}/{episode_num}")
def progress_unwatch_episode(
    mal_id: int,
    episode_num: int,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    """DELETE /account/progress/{mal_id}/{episode_num} — «не смотрел»: стереть серию из прогресса, дневника,
    «Смотрю» и поправить авто-статус в списках."""
    if episode_num < 1 or episode_num > 10_000:
        raise HTTPException(400, "episode_num out of range")

    with connect() as conn:
        cur = conn.cursor()
        old_history = history_row_for_title(conn, user["id"], mal_id) or {}
        episodes_total = int(old_history.get("episodes_total") or 0)

        cur.execute(
            """DELETE FROM aviev_episode_progress
                WHERE user_id=%s AND mal_id=%s AND episode_num=%s""",
            (user["id"], mal_id, episode_num),
        )
        removed_progress = int(cur.rowcount or 0)

        cur.execute(
            """DELETE FROM aviev_activity
                WHERE user_id=%s AND mal_id=%s
                  AND kind IN ('watch_start', 'watch_continue')
                  AND meta=%s""",
            (user["id"], mal_id, f"ep={episode_num}"),
        )
        removed_activity = int(cur.rowcount or 0)

        watch = resync_watch_history_from_latest_progress(conn, user["id"], mal_id)
        entry = adjust_auto_list_status_after_unwatch(
            conn,
            user["id"],
            mal_id,
            removed_episode=episode_num,
            episodes_total=episodes_total,
        )
        progress = progress_rows_for_title(conn, user["id"], mal_id)
        conn.commit()

    return {
        "ok": True,
        "removed_episode": episode_num,
        "removed_progress": removed_progress,
        "removed_activity": removed_activity,
        "progress": progress,
        "watch": watch,
        "entry": entry,
    }
