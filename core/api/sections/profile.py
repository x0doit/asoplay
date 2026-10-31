"""
Раздел /profile/…: публичные данные профиля — сводка, активность и списки человека по его handle.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Handle — alias из Just_accounts (например, edward), а если alias пуст — profile{user_id}. Приватность решает сервер:
скрыл человек списки (hide_lists) или активность (hide_activity) — посторонний их не получает вовсе, никакого
скрытия в CSS; владелец видит всё. Страница /@handle для поисковиков — core/modules/profile_pages.py.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from core.classes.accounts import resolve_handle
from core.functions import lists
from core.functions.activity_feed import fetch_recent_paged, graph_for_user_public
from core.functions.privacy import read_privacy
from core.functions.sessions import current_user_optional

router = APIRouter()


@router.get("/profile/{handle}/summary")
def profile_summary(
    handle: str,
    days: int = Query(default=30, ge=1, le=365),
    viewer: dict[str, Any] | None = Depends(current_user_optional),
) -> dict[str, Any]:
    """Сводка профиля: человек, его приватность (только hide_lists и hide_activity), счётчики списков и график
    активности за days дней — то, что он не скрыл; владельцу — всё."""
    target = resolve_handle(handle)
    if not target:
        raise HTTPException(404, "profile not found")
    is_owner = bool(viewer and int(viewer["id"]) == int(target["id"]))
    privacy = read_privacy(target["id"])
    out: dict[str, Any] = {
        "user": {k: target[k] for k in (
            "id", "handle", "profile_path", "name", "avatar", "avatar_small",
        )},
        "is_owner": is_owner,
        # Посторонним — только то, что меняет страницу профиля
        "privacy": {k: privacy[k] for k in ("hide_lists", "hide_activity")},
        "counts": None,
        "activity": None,
    }
    if not privacy["hide_lists"] or is_owner:
        out["counts"] = lists.counts_for(target["id"])
    if not privacy["hide_activity"] or is_owner:
        out["activity"] = graph_for_user_public(target["id"], days=days)
    return out


@router.get("/profile/{handle}/activity")
def profile_activity(
    handle: str,
    days: int = Query(default=30, ge=1, le=365),
    viewer: dict[str, Any] | None = Depends(current_user_optional),
) -> dict[str, Any]:
    """График активности за days дней; активность скрыта, а смотрит не владелец, — {"hidden": true}."""
    target = resolve_handle(handle)
    if not target:
        raise HTTPException(404, "profile not found")
    is_owner = bool(viewer and int(viewer["id"]) == int(target["id"]))
    privacy = read_privacy(target["id"])
    if privacy["hide_activity"] and not is_owner:
        return {"hidden": True}
    return graph_for_user_public(target["id"], days=days)


@router.get("/profile/{handle}/activity/recent")
def profile_activity_recent(
    handle: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50),
    group: str = Query(default="all"),
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    viewer: dict[str, Any] | None = Depends(current_user_optional),
) -> dict[str, Any]:
    """Лента активности страницами: offset и limit, группа событий, один день; активность скрыта, а смотрит
    не владелец, — пустая лента с hidden."""
    target = resolve_handle(handle)
    if not target:
        raise HTTPException(404, "profile not found")
    is_owner = bool(viewer and int(viewer["id"]) == int(target["id"]))
    privacy = read_privacy(target["id"])
    if privacy["hide_activity"] and not is_owner:
        return {"hidden": True, "items": [], "has_more": False}
    return fetch_recent_paged(target["id"], offset=offset, limit=limit,
                              group=group, day=date)


@router.get("/profile/{handle}/lists")
def profile_lists(
    handle: str,
    status: str = "watching",
    viewer: dict[str, Any] | None = Depends(current_user_optional),
) -> dict[str, Any]:
    """Записи одного статуса или избранного; списки скрыты, а смотрит не владелец, — пусто с hidden; неизвестный
    статус — 400."""
    target = resolve_handle(handle)
    if not target:
        raise HTTPException(404, "profile not found")
    is_owner = bool(viewer and int(viewer["id"]) == int(target["id"]))
    privacy = read_privacy(target["id"])
    if privacy["hide_lists"] and not is_owner:
        return {"hidden": True, "items": []}
    if status not in lists.STATUSES and status != "favorite":
        raise HTTPException(400, "unknown status")
    return {"hidden": False, "items": lists.items_for(target["id"], status), "is_owner": is_owner}
