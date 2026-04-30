# -*- coding: utf-8 -*-
"""
AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Domain: пользовательские списки тайтлов.

Одна таблица `aviev_user_lists` держит и основной статус (watching / planned /
completed / dropped / postponed), и отдельный флаг is_favorite. Каждая
запись помечена `status_source` = manual | auto — manual побеждает auto.

Автоматические правила:
    - на 10-й минуте просмотра тайтл попадает в watching (auto),
    - на последней серии — в completed (auto),
    - через 30 дней простоя watching → dropped (auto).
Все три правила подчиняются настройке `auto_add_lists` в aviev_account_settings.

Activity-логирование живёт в `server.activity_log` и вызывается отсюда, чтобы
contribution-график имел честный источник событий.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, conint, constr

import json as _json

from server.animesocial import connect, current_user_required
from server.activity_log import record_event
from server.proxies import cached_fetch

log = logging.getLogger("animeviev.user_lists")

router = APIRouter(prefix="/account/lists")


STATUSES = ("watching", "planned", "completed", "dropped", "postponed")
AUTO_STATUS_ORDER = {None: 0, "watching": 1, "completed": 2, "dropped": 2}


async def _mal_id_exists(mal_id: int) -> bool:
    """Проверяет, существует ли запись в MyAnimeList. Подтягивает Jikan,
    кэш у нас уже стоит (1 ч per-id), так что проверка практически бесплатная.

    Возвращает True при HTTP 200, False при 404, True при 5xx — потому что
    временная недоступность Jikan не повод блокировать пользователя. Это
    «сильная валидация для откровенных мертвецов, мягкая для флейковой сети».
    """
    try:
        _, _, status = await cached_fetch(
            "GET", f"https://api.jikan.moe/v4/anime/{mal_id}",
            ttl=3600, timeout=6,
        )
    except Exception:
        return True
    if status == 404:
        return False
    return True


def _now() -> datetime:
    return datetime.utcnow()


# ---------- helpers ----------
def _row_to_public(row: tuple) -> dict[str, Any]:
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


def _auto_add_enabled(conn, user_id: int) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT auto_add_lists FROM aviev_account_settings WHERE user_id=%s",
        (user_id,),
    )
    row = cur.fetchone()
    if not row:
        return True  # default ON
    return bool(row[0])


def _fetch_entry(conn, user_id: int, mal_id: int) -> dict[str, Any] | None:
    cur = conn.cursor()
    cur.execute(
        """SELECT user_id, mal_id, status, status_source, is_favorite,
                  title, poster_url, added_at, updated_at
            FROM aviev_user_lists WHERE user_id=%s AND mal_id=%s""",
        (user_id, mal_id),
    )
    row = cur.fetchone()
    return _row_to_public(row) if row else None


def _upsert_status(conn, user_id: int, mal_id: int, status: str | None,
                   source: str, title: str = "", poster_url: str = "") -> None:
    now = _now()
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


# ---------- schemas ----------
class ListItemIn(BaseModel):
    status: constr(strip_whitespace=True, min_length=0, max_length=16) | None = None
    title: str = Field("", max_length=500)
    poster_url: str = Field("", max_length=500)


class FavoriteIn(BaseModel):
    is_favorite: bool
    title: str = Field("", max_length=500)
    poster_url: str = Field("", max_length=500)


class ProgressEventIn(BaseModel):
    """Событие от плеера: «пользователь смотрел эпизод». Сервер сам решит,
    включать ли auto-rules."""
    mal_id: conint(ge=1, le=10_000_000)
    episode_num: conint(ge=1, le=10_000)
    seconds: conint(ge=0, le=200_000)
    duration: conint(ge=0, le=200_000) = 0
    episodes_total: conint(ge=0, le=10_000) = 0
    title: str = Field("", max_length=500)
    poster_url: str = Field("", max_length=500)


# ---------- CRUD ----------
@router.get("")
def list_all(
    status: str | None = Query(default=None),
    user: dict[str, Any] = Depends(current_user_required),
) -> list[dict[str, Any]]:
    with connect() as conn:
        cur = conn.cursor()
        if status == "favorite":
            cur.execute(
                """SELECT user_id, mal_id, status, status_source, is_favorite,
                          title, poster_url, added_at, updated_at
                    FROM aviev_user_lists
                    WHERE user_id=%s AND is_favorite=1
                    ORDER BY updated_at DESC""",
                (user["id"],),
            )
        elif status in STATUSES:
            cur.execute(
                """SELECT user_id, mal_id, status, status_source, is_favorite,
                          title, poster_url, added_at, updated_at
                    FROM aviev_user_lists
                    WHERE user_id=%s AND status=%s
                    ORDER BY updated_at DESC""",
                (user["id"], status),
            )
        else:
            cur.execute(
                """SELECT user_id, mal_id, status, status_source, is_favorite,
                          title, poster_url, added_at, updated_at
                    FROM aviev_user_lists
                    WHERE user_id=%s AND (status IS NOT NULL OR is_favorite=1)
                    ORDER BY updated_at DESC""",
                (user["id"],),
            )
        rows = cur.fetchall()
    return [_row_to_public(r) for r in rows]


@router.get("/counts")
def list_counts(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    out["favorite"] = 0
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT status, COUNT(*) FROM aviev_user_lists
                WHERE user_id=%s AND status IS NOT NULL
                GROUP BY status""",
            (user["id"],),
        )
        for status, n in cur.fetchall():
            if status in out:
                out[status] = int(n)
        cur.execute(
            "SELECT COUNT(*) FROM aviev_user_lists WHERE user_id=%s AND is_favorite=1",
            (user["id"],),
        )
        out["favorite"] = int(cur.fetchone()[0] or 0)
    return out


@router.get("/{mal_id}")
def list_entry(
    mal_id: int,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    with connect() as conn:
        entry = _fetch_entry(conn, user["id"], mal_id)
    if not entry:
        return {
            "mal_id": mal_id,
            "status": None,
            "status_source": None,
            "is_favorite": False,
            "title": "",
            "poster_url": "",
        }
    return entry


@router.put("/{mal_id}/status")
async def set_status(
    mal_id: int,
    payload: ListItemIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    status = (payload.status or "").strip() or None
    if status is not None and status not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES} or null")
    # Не даём записать в списки мёртвый MAL ID (например, /anime/2 давно
    # удалён, а кто-то тыкнул «В список» на страничке до фикса).
    if not await _mal_id_exists(mal_id):
        raise HTTPException(404, "this MAL id no longer exists")
    with connect() as conn:
        prev = _fetch_entry(conn, user["id"], mal_id)
        _upsert_status(
            conn, user["id"], mal_id, status,
            source="manual",
            title=payload.title, poster_url=payload.poster_url,
        )
        conn.commit()
        kind = "list_move" if prev and prev.get("status") else "list_add"
        if status is None:
            kind = "list_remove"
        record_event(
            conn, user["id"],
            kind=kind, mal_id=mal_id,
            meta=status or "cleared",
        )
        conn.commit()
        return _fetch_entry(conn, user["id"], mal_id) or {}


@router.put("/{mal_id}/favorite")
async def set_favorite(
    mal_id: int,
    payload: FavoriteIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    if payload.is_favorite and not await _mal_id_exists(mal_id):
        raise HTTPException(404, "this MAL id no longer exists")
    now = _now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO aviev_user_lists
                (user_id, mal_id, status, status_source, is_favorite,
                 title, poster_url, added_at, updated_at)
                VALUES (%s, %s, NULL, 'manual', %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    is_favorite=VALUES(is_favorite),
                    title=IF(VALUES(title)<>'', VALUES(title), title),
                    poster_url=IF(VALUES(poster_url)<>'', VALUES(poster_url), poster_url),
                    updated_at=VALUES(updated_at)""",
            (
                user["id"], mal_id, 1 if payload.is_favorite else 0,
                payload.title, payload.poster_url, now, now,
            ),
        )
        conn.commit()
        record_event(
            conn, user["id"],
            kind="favorite" if payload.is_favorite else "unfavorite",
            mal_id=mal_id,
        )
        conn.commit()
        return _fetch_entry(conn, user["id"], mal_id) or {}


@router.delete("/{mal_id}")
def list_remove(
    mal_id: int,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "DELETE FROM aviev_user_lists WHERE user_id=%s AND mal_id=%s",
            (user["id"], mal_id),
        )
        conn.commit()
        record_event(conn, user["id"], kind="list_remove", mal_id=mal_id)
        conn.commit()
    return {"ok": True}


# ---------- auto rules ----------
@router.post("/progress-event")
def progress_event(
    event: ProgressEventIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    """Плеер присылает события «продолжаю смотреть». Здесь живёт вся auto-логика
    — мы не заставляем клиента её считать. Возвращаем обновлённое состояние
    тайтла, чтобы плеер мог сразу отразить авто-переход."""
    now = _now()
    applied: list[str] = []
    with connect() as conn:
        cur = conn.cursor()

        # 1) Прогресс эпизода пишем всегда — нужен для «resume at position».
        cur.execute(
            """INSERT INTO aviev_episode_progress
                (user_id, mal_id, episode_num, seconds, duration, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    seconds=GREATEST(seconds, VALUES(seconds)),
                    duration=GREATEST(duration, VALUES(duration)),
                    updated_at=VALUES(updated_at)""",
            (user["id"], event.mal_id, event.episode_num,
             event.seconds, event.duration, now),
        )

        # Если серия фактически досмотрена (>=92% или до конца <90 сек), помечаем
        # все предыдущие серии как «досмотрено» — чтобы в списке эпизодов они
        # были подкрашены как пройденные. По ТЗ: если недосмотрел серию N, но
        # завершил N+1 — N автоматически помечается как досмотренная.
        episode_done = (
            event.duration > 0
            and (event.seconds >= event.duration * 0.92
                 or event.seconds >= max(0, event.duration - 90))
        )
        if episode_done and event.episode_num > 1:
            stamp_duration = max(event.duration, 1)
            for ep in range(1, event.episode_num):
                cur.execute(
                    """INSERT INTO aviev_episode_progress
                        (user_id, mal_id, episode_num, seconds, duration, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            seconds=GREATEST(seconds, %s),
                            duration=GREATEST(duration, %s),
                            updated_at=VALUES(updated_at)""",
                    (user["id"], event.mal_id, ep,
                     stamp_duration, stamp_duration, now,
                     stamp_duration, stamp_duration),
                )

        # watch_history = фид «Продолжить просмотр». Туда попадает только
        # реальный просмотр от 5 минут; «просто открыл страницу» не учитывается.
        if event.seconds >= 300:
            cur.execute(
                """INSERT INTO aviev_watch_history
                    (user_id, mal_id, last_episode, episode_seconds, episode_duration,
                     episodes_total, title, poster_url, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        last_episode=VALUES(last_episode),
                        episode_seconds=VALUES(episode_seconds),
                        episode_duration=VALUES(episode_duration),
                        episodes_total=IF(VALUES(episodes_total)>0,
                                          VALUES(episodes_total), episodes_total),
                        title=IF(VALUES(title)<>'', VALUES(title), title),
                        poster_url=IF(VALUES(poster_url)<>'', VALUES(poster_url), poster_url),
                        updated_at=VALUES(updated_at)""",
                (
                    user["id"], event.mal_id, event.episode_num,
                    event.seconds, event.duration, event.episodes_total,
                    event.title, event.poster_url, now,
                ),
            )

        current = _fetch_entry(conn, user["id"], event.mal_id)
        auto_ok = _auto_add_enabled(conn, user["id"])

        # 2) auto-rule: 10 минут → watching (auto), если нет ручного статуса.
        #    «Ручной watching» тоже уважаем: просто не трогаем.
        crossed_ten_min = event.seconds >= 600
        if auto_ok and crossed_ten_min:
            prev_status = current.get("status") if current else None
            prev_source = current.get("status_source") if current else None
            if prev_status is None and prev_source != "manual":
                _upsert_status(
                    conn, user["id"], event.mal_id,
                    status="watching", source="auto",
                    title=event.title, poster_url=event.poster_url,
                )
                applied.append("watching")
                record_event(
                    conn, user["id"], kind="list_add",
                    mal_id=event.mal_id, meta="watching:auto",
                )

        # 3) auto-rule: последняя серия → completed (auto), если не было manual
        #    completed/dropped/postponed.
        is_last = (
            event.episodes_total > 0 and event.episode_num >= event.episodes_total
            and event.duration > 30
            and (event.seconds >= event.duration * 0.85
                 or event.seconds >= event.duration - 60)
        )
        if auto_ok and is_last:
            entry = _fetch_entry(conn, user["id"], event.mal_id)
            prev_status = entry.get("status") if entry else None
            prev_source = entry.get("status_source") if entry else None
            unlocked = (
                prev_status in (None, "watching")
                and not (prev_source == "manual" and prev_status in ("dropped", "postponed", "completed"))
            )
            if unlocked and prev_status != "completed":
                _upsert_status(
                    conn, user["id"], event.mal_id,
                    status="completed", source="auto",
                    title=event.title, poster_url=event.poster_url,
                )
                applied.append("completed")
                record_event(
                    conn, user["id"], kind="complete",
                    mal_id=event.mal_id, meta="auto",
                )

        # 4) activity: базовое событие «продолжаю смотреть» c эпизодом в meta.
        record_event(
            conn, user["id"],
            kind="watch_continue" if event.seconds > 10 else "watch_start",
            mal_id=event.mal_id,
            meta=f"ep={event.episode_num}",
        )

        conn.commit()
        entry = _fetch_entry(conn, user["id"], event.mal_id) or {}
    return {"applied": applied, "entry": entry}


# ---------- cron-friendly hook ----------
def sweep_dropped_after_30_days(conn) -> int:
    """Переводит auto-watching без движения >30 дней в auto-dropped.

    Возвращает количество изменённых строк. Вызывается из
    `server.user_lists.sweep_all_once()` или любой другой планировщик-ручкой.
    Manual-статусы никогда не трогает.
    """
    cutoff = _now() - timedelta(days=30)
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
            (_now(), user_id, mal_id),
        )
        if cur.rowcount:
            count += 1
            record_event(conn, user_id, kind="list_move",
                         mal_id=mal_id, meta="watching->dropped:auto")
    conn.commit()
    return count


@router.post("/sweep")
def manual_sweep(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, Any]:
    """Диагностическая ручка — пользователь может запустить auto-dropped сам.
    Выполняется только для его записей, не для всей базы."""
    cutoff = _now() - timedelta(days=30)
    with connect() as conn:
        cur = conn.cursor()
        if not _auto_add_enabled(conn, user["id"]):
            return {"applied": 0, "note": "auto_add_lists disabled"}
        cur.execute(
            """UPDATE aviev_user_lists
                SET status='dropped', status_source='auto', updated_at=%s
                WHERE user_id=%s
                  AND status='watching'
                  AND status_source='auto'
                  AND updated_at < %s""",
            (_now(), user["id"], cutoff),
        )
        n = cur.rowcount
        conn.commit()
    return {"applied": int(n)}
