# -*- coding: utf-8 -*-
"""
AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Per-account data API: favorites, watch history, per-episode progress, 1..10
title ratings, saved dub preferences, and miscellaneous account settings.

Everything here is behind `current_user_required`: the frontend must either
be logged in or accept an HTTP 401 and show the guest auth-gate.

All writes are serialized through `server.animesocial.connect()` — the
same pool that auth uses. No ORM; tables are small and shape-stable, so
hand-written SQL stays readable and easy to audit.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field, conint

from server.animesocial import connect, current_user_required
from server.activity_log import record_event

log = logging.getLogger("animeviev.account")

router = APIRouter(prefix="/account")


def _now() -> datetime:
    return datetime.utcnow()


def _mark_previous_episodes_done_if_needed(
    conn,
    user_id: int,
    mal_id: int,
    episode_num: int,
    seconds: int,
    duration: int,
    updated_at: datetime,
) -> None:
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


def _upsert_episode_progress(
    conn,
    user_id: int,
    mal_id: int,
    episode_num: int,
    seconds: int,
    duration: int,
    updated_at: datetime | None = None,
) -> None:
    stamp = updated_at or _now()
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


def _sync_watch_history_from_progress(
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
            WHERE user_id=%s AND mal_id=%s AND seconds >= 300
            LIMIT 1""",
        (user_id, mal_id),
    )
    qualified = bool(cur.fetchone()) or episode_seconds >= 300
    if not qualified:
        return
    stamp = updated_at or _now()
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


# ---------- favorites ----------
class FavoriteIn(BaseModel):
    mal_id: conint(ge=1, le=10_000_000)
    title: str = Field("", max_length=500)
    poster_url: str = Field("", max_length=500)


# /account/favorites/* остаются для обратной совместимости, но под капотом
# теперь работают поверх aviev_user_lists.is_favorite — чтобы не было двух
# параллельных источников правды. Старая таблица aviev_favorites остаётся
# только как archival слой (в pass-2 миграции данные из неё уже перенесены).
@router.get("/favorites")
def favorites_list(user: dict[str, Any] = Depends(current_user_required)) -> list[dict[str, Any]]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT mal_id, title, poster_url, added_at
                FROM aviev_user_lists
                WHERE user_id=%s AND is_favorite=1
                ORDER BY added_at DESC""",
            (user["id"],),
        )
        rows = cur.fetchall()
    return [
        {
            "mal_id": int(r[0]),
            "title": r[1] or "",
            "poster_url": r[2] or "",
            "added_at": r[3].isoformat() if r[3] else None,
        }
        for r in rows
    ]


@router.put("/favorites/{mal_id}")
def favorite_add(
    mal_id: int,
    payload: FavoriteIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    if mal_id != payload.mal_id:
        raise HTTPException(400, "mal_id mismatch")
    now = _now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO aviev_user_lists
                (user_id, mal_id, status, status_source, is_favorite,
                 title, poster_url, added_at, updated_at)
                VALUES (%s, %s, NULL, 'manual', 1, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    is_favorite=1,
                    title=IF(VALUES(title)<>'', VALUES(title), title),
                    poster_url=IF(VALUES(poster_url)<>'', VALUES(poster_url), poster_url),
                    updated_at=VALUES(updated_at)""",
            (user["id"], mal_id, payload.title, payload.poster_url, now, now),
        )
        conn.commit()
    return {"ok": True}


@router.delete("/favorites/{mal_id}")
def favorite_remove(mal_id: int, user: dict[str, Any] = Depends(current_user_required)) -> dict[str, Any]:
    now = _now()
    with connect() as conn:
        cur = conn.cursor()
        # Снимаем флаг is_favorite. Если у записи не было статуса, она становится
        # «пустой» (status=NULL И is_favorite=0) — удаляем полностью, чтобы
        # не держать «призраки» в списках.
        cur.execute(
            """UPDATE aviev_user_lists SET is_favorite=0, updated_at=%s
                WHERE user_id=%s AND mal_id=%s""",
            (now, user["id"], mal_id),
        )
        cur.execute(
            """DELETE FROM aviev_user_lists
                WHERE user_id=%s AND mal_id=%s
                  AND is_favorite=0 AND status IS NULL""",
            (user["id"], mal_id),
        )
        conn.commit()
    return {"ok": True}


# ---------- watch history (the "Смотрю" feed) ----------
class HistoryIn(BaseModel):
    mal_id: conint(ge=1, le=10_000_000)
    title: str = Field("", max_length=500)
    poster_url: str = Field("", max_length=500)
    last_episode: conint(ge=1, le=10_000) = 1
    episode_seconds: conint(ge=0, le=200_000) = 0
    episode_duration: conint(ge=0, le=200_000) = 0
    episodes_total: conint(ge=0, le=10_000) = 0


@router.get("/history")
def history_list(user: dict[str, Any] = Depends(current_user_required)) -> list[dict[str, Any]]:
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
                    WHERE user_id=%s AND seconds >= 300
                    UNION DISTINCT
                    SELECT mal_id
                    FROM aviev_watch_history
                    WHERE user_id=%s AND episode_seconds >= 300
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
    if mal_id != payload.mal_id:
        raise HTTPException(400, "mal_id mismatch")
    stamp = _now()
    with connect() as conn:
        _upsert_episode_progress(
            conn,
            user["id"],
            mal_id,
            payload.last_episode,
            payload.episode_seconds,
            payload.episode_duration,
            updated_at=stamp,
        )
        _sync_watch_history_from_progress(
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


# ---------- episode progress ----------
class ProgressIn(BaseModel):
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
    stamp = _now()
    with connect() as conn:
        _upsert_episode_progress(
            conn,
            user["id"],
            mal_id,
            payload.episode_num,
            payload.seconds,
            payload.duration,
            updated_at=stamp,
        )
        _sync_watch_history_from_progress(
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


# ---------- ratings ----------
class RatingIn(BaseModel):
    score: conint(ge=1, le=10)


@router.get("/ratings")
def ratings_all(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, int]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT mal_id, score FROM aviev_title_ratings WHERE user_id=%s",
            (user["id"],),
        )
        return {str(r[0]): int(r[1]) for r in cur.fetchall()}


@router.put("/ratings/{mal_id}")
def rating_set(
    mal_id: int,
    payload: RatingIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO aviev_title_ratings (user_id, mal_id, score, set_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE score=VALUES(score), set_at=VALUES(set_at)""",
            (user["id"], mal_id, payload.score, _now()),
        )
        # activity: оценка/перестановка оценки — 1 событие в день на тайтл
        # (dedup в record_event по (user, day, kind, mal_id)).
        record_event(conn, user["id"], kind="rate", mal_id=mal_id)
        conn.commit()
    return {"ok": True}


@router.delete("/ratings/{mal_id}")
def rating_clear(mal_id: int, user: dict[str, Any] = Depends(current_user_required)) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM aviev_title_ratings WHERE user_id=%s AND mal_id=%s",
            (user["id"], mal_id),
        )
        # activity: снятие оценки — такой же kind, той же дедупом, тоже попадает
        # в ленту. «Оценил / изменил оценку / убрал оценку» = один общий факт.
        record_event(conn, user["id"], kind="rate", mal_id=mal_id)
        conn.commit()
    return {"ok": True}


# ---------- dub preferences ----------
class DubPrefIn(BaseModel):
    dub_norm: str = Field(..., min_length=1, max_length=128)


@router.get("/dub-prefs")
def dub_prefs_all(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, str]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT mal_id, dub_norm FROM aviev_dub_prefs WHERE user_id=%s",
            (user["id"],),
        )
        return {str(r[0]): r[1] for r in cur.fetchall()}


@router.put("/dub-prefs/{mal_id}")
def dub_prefs_set(
    mal_id: int,
    payload: DubPrefIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO aviev_dub_prefs (user_id, mal_id, dub_norm, updated_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE dub_norm=VALUES(dub_norm), updated_at=VALUES(updated_at)""",
            (user["id"], mal_id, payload.dub_norm, _now()),
        )
        conn.commit()
    return {"ok": True}


# ---------- settings ----------
# Ключевой момент: поля Optional, т.к. фронт шлёт частичный апдейт.
# Иначе при PUT /account/settings {"autonext": false} Pydantic подставлял бы
# auto_add_lists=True (дефолт), и сервер тихо включал автодобавление обратно —
# даже если пользователь его только что выключил из другого тоггла.
class SettingsIn(BaseModel):
    autonext: bool | None = None
    auto_add_lists: bool | None = None
    extra: dict[str, Any] | None = None


@router.get("/settings")
def settings_get(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT autonext, auto_add_lists, settings_json
                FROM aviev_account_settings WHERE user_id=%s""",
            (user["id"],),
        )
        row = cur.fetchone()
    if not row:
        return {"autonext": True, "auto_add_lists": True, "extra": {}}
    extra = {}
    if row[2]:
        try:
            extra = json.loads(row[2])
        except (TypeError, json.JSONDecodeError):
            extra = {}
    return {
        "autonext": bool(row[0]),
        "auto_add_lists": bool(row[1]),
        "extra": extra,
    }


@router.put("/settings")
def settings_put(
    payload: SettingsIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    now = _now()

    # Собираем UPDATE динамически по тем полям, которые пользователь прислал.
    # Для INSERT (новая строка) используем дефолты: autonext=1, auto_add=1.
    insert_autonext = 1 if payload.autonext is None else (1 if payload.autonext else 0)
    insert_auto_add = 1 if payload.auto_add_lists is None else (1 if payload.auto_add_lists else 0)
    insert_extra = json.dumps(payload.extra or {}, ensure_ascii=False)

    update_sets: list[str] = []
    update_vals: list[Any] = []
    if payload.autonext is not None:
        update_sets.append("autonext=%s")
        update_vals.append(1 if payload.autonext else 0)
    if payload.auto_add_lists is not None:
        update_sets.append("auto_add_lists=%s")
        update_vals.append(1 if payload.auto_add_lists else 0)
    if payload.extra is not None:
        update_sets.append("settings_json=%s")
        update_vals.append(insert_extra)
    update_sets.append("updated_at=%s")
    update_vals.append(now)

    sql = (
        "INSERT INTO aviev_account_settings "
        "(user_id, autonext, auto_add_lists, settings_json, updated_at) "
        "VALUES (%s, %s, %s, %s, %s) "
        f"ON DUPLICATE KEY UPDATE {', '.join(update_sets)}"
    )
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            sql,
            (user["id"], insert_autonext, insert_auto_add, insert_extra, now,
             *update_vals),
        )
        conn.commit()
    return {"ok": True}


# ---------- privacy ----------
class PrivacyIn(BaseModel):
    hide_lists: bool = False
    hide_activity: bool = False


@router.get("/privacy")
def privacy_get(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, bool]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT hide_lists, hide_activity FROM aviev_privacy WHERE user_id=%s",
            (user["id"],),
        )
        row = cur.fetchone()
    if not row:
        return {"hide_lists": False, "hide_activity": False}
    return {"hide_lists": bool(row[0]), "hide_activity": bool(row[1])}


@router.put("/privacy")
def privacy_put(
    payload: PrivacyIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, bool]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO aviev_privacy (user_id, hide_lists, hide_activity, updated_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    hide_lists=VALUES(hide_lists),
                    hide_activity=VALUES(hide_activity),
                    updated_at=VALUES(updated_at)""",
            (
                user["id"], 1 if payload.hide_lists else 0,
                1 if payload.hide_activity else 0, _now(),
            ),
        )
        conn.commit()
    return {"hide_lists": payload.hide_lists, "hide_activity": payload.hide_activity}


def read_privacy(user_id: int) -> dict[str, bool]:
    """Доступ без auth-gate — для рендера публичного профиля."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT hide_lists, hide_activity FROM aviev_privacy WHERE user_id=%s",
            (user_id,),
        )
        row = cur.fetchone()
    if not row:
        return {"hide_lists": False, "hide_activity": False}
    return {"hide_lists": bool(row[0]), "hide_activity": bool(row[1])}


# ---------- one-time localStorage import ----------
class ImportBlob(BaseModel):
    favorites: list[dict[str, Any]] = Field(default_factory=list)
    watch: dict[str, dict[str, Any]] = Field(default_factory=dict)
    ratings: dict[str, int] = Field(default_factory=dict)
    dub_prefs: dict[str, str] = Field(default_factory=dict)
    autonext: bool | None = None


@router.get("/import-marks")
def import_marks(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, str]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT kind, imported_at FROM aviev_import_marks WHERE user_id=%s",
            (user["id"],),
        )
        return {r[0]: r[1].isoformat() for r in cur.fetchall()}


@router.post("/import-localstorage")
def import_localstorage(
    blob: ImportBlob = Body(...),
    force: bool = Query(default=False),
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    """One-time merger from the user's old browser localStorage into their
    account. Idempotent: we stamp aviev_import_marks once per kind, and skip
    re-imports unless ?force=1. Conflicts are resolved "server wins, but we
    still fill missing rows"."""
    counters = {"favorites": 0, "watch": 0, "ratings": 0, "dub_prefs": 0, "settings": 0}
    now = _now()

    with connect() as conn:
        cur = conn.cursor()

        cur.execute(
            "SELECT kind FROM aviev_import_marks WHERE user_id=%s",
            (user["id"],),
        )
        already = {r[0] for r in cur.fetchall()}

        def stamp(kind: str, n: int) -> None:
            cur.execute(
                """INSERT INTO aviev_import_marks (user_id, kind, imported_at, n_items)
                    VALUES (%s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE imported_at=VALUES(imported_at), n_items=VALUES(n_items)""",
                (user["id"], kind, now, n),
            )

        if force or "favorites" not in already:
            # LS-импорт избранного теперь идёт прямо в новую модель
            # aviev_user_lists.is_favorite=1 (без промежуточного aviev_favorites).
            for fav in blob.favorites:
                try:
                    mal_id = int(fav.get("mal_id") or fav.get("id"))
                except (TypeError, ValueError):
                    continue
                title = (fav.get("title") or "")[:500]
                poster = (fav.get("cover") or fav.get("poster_url") or "")[:500]
                cur.execute(
                    """INSERT INTO aviev_user_lists
                        (user_id, mal_id, status, status_source, is_favorite,
                         title, poster_url, added_at, updated_at)
                        VALUES (%s, %s, NULL, 'manual', 1, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            is_favorite=1,
                            title=IF(VALUES(title)<>'' AND title='',
                                    VALUES(title), title),
                            poster_url=IF(VALUES(poster_url)<>'' AND poster_url='',
                                    VALUES(poster_url), poster_url)""",
                    (user["id"], mal_id, title, poster, now, now),
                )
                counters["favorites"] += 1
            stamp("favorites", counters["favorites"])

        if force or "watch" not in already:
            for key, v in blob.watch.items():
                try:
                    mal_id = int(key)
                except (TypeError, ValueError):
                    continue
                ep = int(v.get("ep") or 1)
                sec = int(v.get("time") or 0)
                dur = int(v.get("duration") or 0)
                title = (v.get("title") or "")[:500]
                cover = (v.get("cover") or "")[:500]
                cur.execute(
                    """INSERT INTO aviev_watch_history
                        (user_id, mal_id, last_episode, episode_seconds, episode_duration,
                         title, poster_url, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            last_episode=IF(aviev_watch_history.episode_seconds=0,
                                            VALUES(last_episode), aviev_watch_history.last_episode),
                            episode_seconds=GREATEST(aviev_watch_history.episode_seconds, VALUES(episode_seconds)),
                            episode_duration=GREATEST(aviev_watch_history.episode_duration, VALUES(episode_duration)),
                            title=IF(aviev_watch_history.title='', VALUES(title), aviev_watch_history.title),
                            poster_url=IF(aviev_watch_history.poster_url='', VALUES(poster_url), aviev_watch_history.poster_url)""",
                    (user["id"], mal_id, ep, sec, dur, title, cover, now),
                )
                counters["watch"] += 1
                if ep and (sec or dur):
                    cur.execute(
                        """INSERT IGNORE INTO aviev_episode_progress
                            (user_id, mal_id, episode_num, seconds, duration, updated_at)
                            VALUES (%s, %s, %s, %s, %s, %s)""",
                        (user["id"], mal_id, ep, sec, dur, now),
                    )
            stamp("watch", counters["watch"])

        if force or "ratings" not in already:
            for key, score in blob.ratings.items():
                try:
                    mal_id = int(key)
                    score = int(score)
                except (TypeError, ValueError):
                    continue
                if not (1 <= score <= 10):
                    continue
                cur.execute(
                    """INSERT IGNORE INTO aviev_title_ratings
                        (user_id, mal_id, score, set_at) VALUES (%s, %s, %s, %s)""",
                    (user["id"], mal_id, score, now),
                )
                counters["ratings"] += 1
            stamp("ratings", counters["ratings"])

        if force or "dub_prefs" not in already:
            for key, norm in blob.dub_prefs.items():
                try:
                    mal_id = int(key)
                except (TypeError, ValueError):
                    continue
                cur.execute(
                    """INSERT IGNORE INTO aviev_dub_prefs
                        (user_id, mal_id, dub_norm, updated_at) VALUES (%s, %s, %s, %s)""",
                    (user["id"], mal_id, (norm or "")[:128], now),
                )
                counters["dub_prefs"] += 1
            stamp("dub_prefs", counters["dub_prefs"])

        if blob.autonext is not None and (force or "settings" not in already):
            cur.execute(
                """INSERT INTO aviev_account_settings (user_id, autonext, settings_json, updated_at)
                    VALUES (%s, %s, '{}', %s)
                    ON DUPLICATE KEY UPDATE updated_at=VALUES(updated_at)""",
                (user["id"], 1 if blob.autonext else 0, now),
            )
            counters["settings"] = 1
            stamp("settings", 1)

        conn.commit()

    return {"ok": True, "imported": counters}
