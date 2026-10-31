"""
Разделы /account/ratings и /account/dub-prefs: оценки тайтлов и любимые озвучки.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/api/sections/account: вход (__init__.py) подключает части по порядку.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, conint

from core.classes.clock import utc_now
from core.classes.db import connect
from core.functions.activity import record_event
from core.functions.sessions import current_user_required

router = APIRouter()


# ---------- ratings ----------
class RatingIn(BaseModel):
    """Оценка тайтла от 1 до 10."""

    score: conint(ge=1, le=10)


@router.get("/ratings")
def ratings_all(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, int]:
    """GET /account/ratings — все оценки человека: номер MAL → оценка."""
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
    """PUT /account/ratings/{mal_id} — поставить или изменить оценку."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO aviev_title_ratings (user_id, mal_id, score, set_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE score=VALUES(score), set_at=VALUES(set_at)""",
            (user["id"], mal_id, payload.score, utc_now()),
        )
        # activity: оценка/перестановка оценки — 1 событие в день на тайтл
        # (dedup в record_event по (user, day, kind, mal_id)).
        record_event(conn, user["id"], kind="rate", mal_id=mal_id)
        conn.commit()
    return {"ok": True}


@router.delete("/ratings/{mal_id}")
def rating_clear(mal_id: int, user: dict[str, Any] = Depends(current_user_required)) -> dict[str, Any]:
    """DELETE /account/ratings/{mal_id} — убрать оценку."""
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
    """Любимая озвучка тайтла — её нормализованное имя."""

    dub_norm: str = Field(..., min_length=1, max_length=128)


@router.get("/dub-prefs")
def dub_prefs_all(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, str]:
    """GET /account/dub-prefs — любимые озвучки: номер MAL → озвучка."""
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
    """PUT /account/dub-prefs/{mal_id} — запомнить озвучку, выбранную для тайтла."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO aviev_dub_prefs (user_id, mal_id, dub_norm, updated_at)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE dub_norm=VALUES(dub_norm), updated_at=VALUES(updated_at)""",
            (user["id"], mal_id, payload.dub_norm, utc_now()),
        )
        conn.commit()
    return {"ok": True}
