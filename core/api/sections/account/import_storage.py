"""
Раздел /account/import-*: разовый перенос данных из localStorage старой версии сайта в аккаунт.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/api/sections/account: вход (__init__.py) подключает части по порядку.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field

from core.classes.clock import utc_now
from core.classes.db import connect
from core.functions.sessions import current_user_required

router = APIRouter()


class ImportBlob(BaseModel):
    """Что лежало в localStorage браузера: избранное, просмотр, оценки, озвучки и автопереход."""

    favorites: list[dict[str, Any]] = Field(default_factory=list)
    watch: dict[str, dict[str, Any]] = Field(default_factory=dict)
    ratings: dict[str, int] = Field(default_factory=dict)
    dub_prefs: dict[str, str] = Field(default_factory=dict)
    autonext: bool | None = None


@router.get("/import-marks")
def import_marks(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, str]:
    """GET /account/import-marks — что и когда уже перенесено: вид данных → время."""
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
    """POST /account/import-localstorage — разовый перенос из localStorage старого браузера в аккаунт.
    Повторять безопасно: каждый вид данных отмечается в aviev_import_marks и без ?force=1 второй раз не переносится.
    При расхождении прав сервер, но недостающие строки дописываются."""
    counters = {"favorites": 0, "watch": 0, "ratings": 0, "dub_prefs": 0, "settings": 0}
    now = utc_now()

    with connect() as conn:
        cur = conn.cursor()

        cur.execute(
            "SELECT kind FROM aviev_import_marks WHERE user_id=%s",
            (user["id"],),
        )
        already = {r[0] for r in cur.fetchall()}

        def stamp(kind: str, n: int) -> None:
            """Отметить, что данные этого вида перенесены, и сколько их было."""
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
