"""
Журнал активности человека (/account/activity…): график по дням и лента событий — только для вошедших.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Чтение — core/functions/activity_feed.py. Запись событий — core/functions/activity.py: её зовут разделы, где человек
что-то делает.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from core.functions.activity_feed import DEFAULT_DAYS, fetch_recent_paged, graph_for_user
from core.functions.sessions import current_user_required

router = APIRouter(prefix="/account/activity")


@router.get("")
def activity_my(
    days: int = Query(default=DEFAULT_DAYS, ge=1, le=365),
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    """GET /account/activity — график за days дней и последние события."""
    return graph_for_user(user["id"], days=days)


@router.get("/recent")
def activity_my_recent(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50),
    group: str = Query(default="all"),
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, Any]:
    """GET /account/activity/recent — лента событий постранично, с фильтром по группе и дню."""
    return fetch_recent_paged(user["id"], offset=offset, limit=limit,
                              group=group, day=date)
