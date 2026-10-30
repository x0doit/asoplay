"""
Раздел /account/lists: записи списков — чтение, статус, избранное, удаление.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/api/sections/lists: вход (__init__.py) подключает части по порядку.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, constr

from core.classes.clock import utc_now
from core.classes.db import connect, current_user_required
from core.functions.activity import record_event
from core.functions.lists import STATUSES, fetch_entry, mal_id_exists, row_to_public, upsert_status

router = APIRouter()


# ---------- schemas ----------
class ListItemIn(BaseModel):
    """Статус тайтла от человека (пусто — убрать статус) и что показывать в карточке."""

    status: constr(strip_whitespace=True, min_length=0, max_length=16) | None = None
    title: str = Field("", max_length=500)
    poster_url: str = Field("", max_length=500)


class FavoriteIn(BaseModel):
    """Добавить в избранное или убрать оттуда."""

    is_favorite: bool
    title: str = Field("", max_length=500)
    poster_url: str = Field("", max_length=500)


# ---------- CRUD ----------
@router.get("")
def list_all(
    status: str | None = Query(default=None),
    user: dict[str, Any] = Depends(current_user_required),
) -> list[dict[str, Any]]:
    """GET /account/lists — записи списков: все, со статусом или избранное (status=favorite)."""
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
    return [row_to_public(r) for r in rows]


@router.get("/counts")
def list_counts(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, int]:
    """GET /account/lists/counts — сколько тайтлов в каждом статусе и в избранном."""
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
    """GET /account/lists/{mal_id} — запись тайтла (без записи — пустая)."""
    with connect() as conn:
        entry = fetch_entry(conn, user["id"], mal_id)
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
    """PUT /account/lists/{mal_id}/status — поставить или убрать статус вручную."""
    status = (payload.status or "").strip() or None
    if status is not None and status not in STATUSES:
        raise HTTPException(400, f"status must be one of {STATUSES} or null")
    # Не даём записать в списки мёртвый MAL ID (например, /anime/2 давно
    # удалён, а кто-то тыкнул «В список» на страничке до фикса).
    if not await mal_id_exists(mal_id):
        raise HTTPException(404, "this MAL id no longer exists")
    with connect() as conn:
        prev = fetch_entry(conn, user["id"], mal_id)
        upsert_status(
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
        return fetch_entry(conn, user["id"], mal_id) or {}


@router.put("/{mal_id}/favorite")
async def set_favorite(
    mal_id: int,
    payload: FavoriteIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    """PUT /account/lists/{mal_id}/favorite — добавить в избранное или убрать оттуда."""
    if payload.is_favorite and not await mal_id_exists(mal_id):
        raise HTTPException(404, "this MAL id no longer exists")
    now = utc_now()
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
        return fetch_entry(conn, user["id"], mal_id) or {}


@router.delete("/{mal_id}")
def list_remove(
    mal_id: int,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    """DELETE /account/lists/{mal_id} — убрать тайтл из списков совсем (и статус, и избранное)."""
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
