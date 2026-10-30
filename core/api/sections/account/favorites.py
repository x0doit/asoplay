"""
Раздел /account/favorites: избранное человека.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/api/sections/account: вход (__init__.py) подключает части по порядку.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, conint

from core.classes.db import connect, current_user_required
from core.functions.progress import _now

router = APIRouter()


class FavoriteIn(BaseModel):
    """Тайтл в избранное: номер MAL, название и постер на момент добавления."""

    mal_id: conint(ge=1, le=10_000_000)
    title: str = Field("", max_length=500)
    poster_url: str = Field("", max_length=500)


# /account/favorites/* остаются для обратной совместимости, но под капотом
# теперь работают поверх aviev_user_lists.is_favorite — чтобы не было двух
# параллельных источников правды. Старая таблица aviev_favorites остаётся
# только как archival слой (в pass-2 миграции данные из неё уже перенесены).
@router.get("/favorites")
def favorites_list(user: dict[str, Any] = Depends(current_user_required)) -> list[dict[str, Any]]:
    """GET /account/favorites — избранное, последние добавленные первыми."""
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
    """PUT /account/favorites/{mal_id} — добавить тайтл в избранное."""
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
    """DELETE /account/favorites/{mal_id} — убрать тайтл из избранного."""
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
