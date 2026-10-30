"""
Раздел /account/history: «Продолжить просмотр» — лента «Смотрю».

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
from core.functions.progress import upsert_episode_progress
from core.functions.watch_history import sync_watch_history_from_progress

router = APIRouter()


class HistoryIn(BaseModel):
    """Точка просмотра для «Смотрю»: серия, позиция в ней и что показывать в карточке."""

    mal_id: conint(ge=1, le=10_000_000)
    title: str = Field("", max_length=500)
    poster_url: str = Field("", max_length=500)
    last_episode: conint(ge=1, le=10_000) = 1
    episode_seconds: conint(ge=0, le=200_000) = 0
    episode_duration: conint(ge=0, le=200_000) = 0
    episodes_total: conint(ge=0, le=10_000) = 0


@router.get("/history")
def history_list(user: dict[str, Any] = Depends(current_user_required)) -> list[dict[str, Any]]:
    """GET /account/history — «Продолжить просмотр»: последняя точка каждого тайтла, свежие первыми."""
    # Источник правды для "Продолжить просмотр" — latest episode_progress по
    # тайтлу. Порог в 5 минут действует на сам тайтл: если пользователь уже
    # однажды реально посмотрел >= 5 минут, фид продолжает показывать и более
    # свежую точку (например, 2 минуты следующей серии). Это также позволяет
    # восстановить фид из сохранённого progress, даже если watch_history ранее
    # не успел записаться.
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """WITH latest_progress AS (
                    SELECT mal_id, episode_num, seconds, duration, updated_at
                    FROM (
                        SELECT mal_id, episode_num, seconds, duration, updated_at,
                               ROW_NUMBER() OVER (
                                   PARTITION BY mal_id
                                   ORDER BY updated_at DESC, episode_num DESC
                               ) AS rn
                        FROM aviev_episode_progress
                        WHERE user_id=%s
                    ) ranked
                    WHERE rn = 1
                ),
                qualified_titles AS (
                    SELECT DISTINCT mal_id
                    FROM aviev_episode_progress
                    WHERE user_id=%s
                      AND (seconds >= 300 OR (duration > 0 AND seconds >= duration * 0.92))
                    UNION DISTINCT
                    SELECT mal_id
                    FROM aviev_watch_history
                    WHERE user_id=%s
                      AND (episode_seconds >= 300 OR (episode_duration > 0 AND episode_seconds >= episode_duration * 0.92))
                )
                SELECT p.mal_id,
                       p.episode_num,
                       p.seconds,
                       p.duration,
                       IFNULL(h.episodes_total, 0) AS episodes_total,
                       COALESCE(NULLIF(h.title, ''), NULLIF(ul.title, ''), '') AS title,
                       COALESCE(NULLIF(h.poster_url, ''), NULLIF(ul.poster_url, ''), '') AS poster_url,
                       p.updated_at
                FROM latest_progress p
                JOIN qualified_titles q ON q.mal_id = p.mal_id
                LEFT JOIN aviev_watch_history h
                       ON h.user_id=%s AND h.mal_id=p.mal_id
                LEFT JOIN aviev_user_lists ul
                       ON ul.user_id=%s AND ul.mal_id=p.mal_id
                ORDER BY p.updated_at DESC""",
            (user["id"], user["id"], user["id"], user["id"], user["id"]),
        )
        rows = cur.fetchall()
    return [
        {
            "mal_id": int(r[0]),
            "last_episode": int(r[1] or 1),
            "episode_seconds": int(r[2] or 0),
            "episode_duration": int(r[3] or 0),
            "episodes_total": int(r[4] or 0),
            "title": r[5] or "",
            "poster_url": r[6] or "",
            "updated_at": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]


@router.put("/history/{mal_id}")
def history_upsert(
    mal_id: int,
    payload: HistoryIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    """PUT /account/history/{mal_id} — записать точку просмотра; «Смотрю» обновится по прогрессу."""
    if mal_id != payload.mal_id:
        raise HTTPException(400, "mal_id mismatch")
    stamp = utc_now()
    with connect() as conn:
        upsert_episode_progress(
            conn,
            user["id"],
            mal_id,
            payload.last_episode,
            payload.episode_seconds,
            payload.episode_duration,
            updated_at=stamp,
        )
        sync_watch_history_from_progress(
            conn,
            user["id"],
            mal_id,
            episode_num=payload.last_episode,
            title=payload.title,
            poster_url=payload.poster_url,
            episodes_total=int(payload.episodes_total or 0),
            updated_at=stamp,
        )
        conn.commit()
    return {"ok": True}


@router.delete("/history/{mal_id}")
def history_remove(mal_id: int, user: dict[str, Any] = Depends(current_user_required)) -> dict[str, Any]:
    """DELETE /account/history/{mal_id} — убрать тайтл из «Смотрю» вместе с его прогрессом."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM aviev_watch_history WHERE user_id=%s AND mal_id=%s",
            (user["id"], mal_id),
        )
        cur.execute(
            "DELETE FROM aviev_episode_progress WHERE user_id=%s AND mal_id=%s",
            (user["id"], mal_id),
        )
        conn.commit()
    return {"ok": True}


@router.delete("/history")
def history_clear_all(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, Any]:
    """Полная очистка «Продолжить просмотр» текущего пользователя.
    Удаляет и watch_history, и episode_progress. Списки (aviev_user_lists)
    не трогает — это отдельная модель и очищается через /my/lists."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM aviev_watch_history WHERE user_id=%s", (user["id"],))
        n_history = cur.rowcount
        cur.execute("DELETE FROM aviev_episode_progress WHERE user_id=%s", (user["id"],))
        n_progress = cur.rowcount
        conn.commit()
    return {"ok": True, "history_cleared": int(n_history or 0), "progress_cleared": int(n_progress or 0)}
