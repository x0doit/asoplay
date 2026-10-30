"""
Админ-API: журнал действий администраторов — запись и список для панели.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/admin: вход router.py подключает части по порядку. Запись делают части, которые что-то меняют, — в той
же транзакции, что и само изменение. Здесь же время журнала: UTC без пояса в базе, Unix-секунды в ответах.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends

from core.admin import auth
from core.admin.auth import _after, _page, admin_user, ok
from core.classes.db import connect
from core.functions import site_settings

router = APIRouter()


def _now() -> datetime:
    """Сейчас по UTC — так время хранится в таблицах админ-API."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def unix(value: datetime | None) -> int | None:
    """Время из базы (UTC) — в Unix-секунды для панели."""
    return int(value.replace(tzinfo=timezone.utc).timestamp()) if value else None


def audit(cur: Any, user_id: int, action: str, target: str, details: dict[str, Any] | None = None) -> None:
    """Записать действие в той же транзакции, что и само изменение. Не влезает в 2000 символов — вместо
    обрезанного (и уже не JSON) — что именно меняли."""
    text = json.dumps(details or {}, ensure_ascii=False)
    if len(text) > 2000:
        text = json.dumps({"changed": sorted(details or {})}, ensure_ascii=False)[:2000]
    cur.execute(
        "INSERT INTO aviev_admin_audit (user_id, action, target, details, at) VALUES (%s, %s, %s, %s, %s)",
        (user_id, action, target[:128], text, _now()),
    )


@router.get("/audit")
def audit_list(cursor: str = "", _admin: int = Depends(admin_user)) -> dict[str, Any]:
    """GET /api/admin/audit — журнал, свежие записи первыми, по странице за раз."""
    after = _after(cursor)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, action, target, details, at FROM aviev_admin_audit"
            + (" WHERE id < %s" if after else "") + " ORDER BY id DESC LIMIT %s",
            (after, auth.PAGE + 1) if after else (auth.PAGE + 1,),
        )
        rows = cur.fetchall()
    items = [{"id": int(i), "user_id": int(u), "action": a, "target": t, "details": site_settings.parse_json(d) or {},
              "at": unix(at)} for i, u, a, t, d, at in rows]
    return ok(_page(items))
