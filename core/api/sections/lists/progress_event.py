"""
Раздел /account/lists: события плеера — прогресс, «Смотрю», авто-правила списков, время просмотра — и ручной
прогон правила 30 дней.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/api/sections/lists: вход (__init__.py) подключает части по порядку. Сами правила описаны
в core/functions/lists.py.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, conint

from core.classes.clock import utc_now
from core.classes.db import connect
from core.functions import lists_sweep
from core.functions.activity import record_event
from core.functions.lists import auto_add_enabled, fetch_entry, sweep_dropped_after_30_days, upsert_status
from core.functions.progress import auto_watching_qualified, history_qualified
from core.functions.sessions import current_user_required
from core.functions.watch_time import watched_seconds

log = logging.getLogger("asoplay.user_lists")

router = APIRouter()


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


# ---------- auto rules ----------
@router.post("/progress-event")
def progress_event(
    event: ProgressEventIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    """Плеер присылает события «продолжаю смотреть». Здесь живёт вся auto-логика
    — мы не заставляем клиента её считать. Возвращаем обновлённое состояние
    тайтла, чтобы плеер мог сразу отразить авто-переход."""
    now = utc_now()
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

        # watch_history = фид «Продолжить просмотр». Туда попадает реальный
        # просмотр от 5 минут или честно завершённая короткая серия; «просто
        # открыл страницу» не учитывается.
        if history_qualified(event.seconds, event.duration):
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

        current = fetch_entry(conn, user["id"], event.mal_id)
        auto_ok = auto_add_enabled(conn, user["id"])

        # 2) auto-rule: 10 минут или завершённая серия → watching (auto),
        #    если нет ручного статуса. Так короткие серии не выпадают из
        #    списков только потому, что физически короче 10 минут.
        cur.execute(
            """SELECT 1
                FROM aviev_episode_progress
                WHERE user_id=%s AND mal_id=%s
                  AND (seconds >= 600 OR (duration > 0 AND seconds >= duration * 0.92))
                LIMIT 1""",
            (user["id"], event.mal_id),
        )
        has_watching_signal = bool(cur.fetchone()) or auto_watching_qualified(
            event.seconds,
            event.duration,
        )
        if auto_ok and has_watching_signal:
            prev_status = current.get("status") if current else None
            prev_source = current.get("status_source") if current else None
            if prev_status is None and prev_source != "manual":
                upsert_status(
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
            entry = fetch_entry(conn, user["id"], event.mal_id)
            prev_status = entry.get("status") if entry else None
            prev_source = entry.get("status_source") if entry else None
            unlocked = (
                prev_status in (None, "watching")
                and not (prev_source == "manual" and prev_status in ("dropped", "postponed", "completed"))
            )
            if unlocked and prev_status != "completed":
                upsert_status(
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

        # 5) сколько посмотрели за этот час — для «Обзора» в панели
        watched = watched_seconds(int(user["id"]), event.mal_id, event.episode_num, event.seconds)
        if watched:
            try:
                cur.execute(
                    """INSERT INTO aviev_watch_time (hour, seconds) VALUES (%s, %s)
                        ON DUPLICATE KEY UPDATE seconds = seconds + VALUES(seconds)""",
                    (now.replace(minute=0, second=0, microsecond=0), watched),
                )
            except Exception:  # noqa: BLE001 — статистика не должна мешать сохранить прогресс
                log.exception("Could not count the watch time")

        conn.commit()
        entry = fetch_entry(conn, user["id"], event.mal_id) or {}
    return {"applied": applied, "entry": entry}


@router.post("/sweep")
def manual_sweep(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, Any]:
    """Диагностическая ручка — пользователь может запустить auto-dropped сам.
    Выполняется только для его записей, не для всей базы; условие — как у прохода по расписанию."""
    with connect() as conn:
        if not auto_add_enabled(conn, user["id"]):
            return {"applied": 0, "note": "auto_add_lists disabled"}
        n = sweep_dropped_after_30_days(conn, utc_now(), user_id=int(user["id"]), on_batch=lists_sweep.announce)
    return {"applied": int(n)}
