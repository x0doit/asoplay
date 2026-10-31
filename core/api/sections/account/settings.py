"""
Разделы /account/settings и /account/privacy: настройки аккаунта и приватность.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/api/sections/account: вход (__init__.py) подключает части по порядку. Что значат флаги приватности
и как их читают другие разделы — в core/functions/privacy.py.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.classes.clock import utc_now
from core.classes.db import connect
from core.functions.privacy import PRIVACY_FIELDS, read_privacy
from core.functions.sessions import current_user_required

router = APIRouter()


# ---------- settings ----------
# Ключевой момент: поля Optional, т.к. фронт шлёт частичный апдейт.
# Иначе при PUT /account/settings {"autonext": false} Pydantic подставлял бы
# auto_add_lists=True (дефолт), и сервер тихо включал автодобавление обратно —
# даже если пользователь его только что выключил из другого тоггла.
class SettingsIn(BaseModel):
    """Настройки аккаунта; меняется только присланное поле."""

    autonext: bool | None = None
    auto_add_lists: bool | None = None
    extra: dict[str, Any] | None = None


@router.get("/settings")
def settings_get(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, Any]:
    """GET /account/settings — настройки аккаунта (без строки в базе — значения по умолчанию)."""
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
    """PUT /account/settings — сохранить присланные поля, остальные не трогать."""
    now = utc_now()

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
# Присланное поле меняется, отсутствующее остаётся как было: страница, открытая до появления
# новых настроек, не сбросит их своим запросом.
class PrivacyIn(BaseModel):
    """Флаги приватности; меняется только присланное поле."""

    hide_lists: bool | None = None
    hide_activity: bool | None = None
    hide_presence: bool | None = None
    show_watching: bool | None = None


@router.get("/privacy")
def privacy_get(user: dict[str, Any] = Depends(current_user_required)) -> dict[str, bool]:
    """GET /account/privacy — флаги приватности."""
    return read_privacy(user["id"])


@router.put("/privacy")
def privacy_put(
    payload: PrivacyIn,
    user: dict[str, Any] = Depends(current_user_required),
) -> dict[str, bool]:
    """PUT /account/privacy — сохранить присланные флаги; присутствие друзьям меняется сразу."""
    given = {f: getattr(payload, f) for f in PRIVACY_FIELDS if getattr(payload, f) is not None}
    insert = [1 if given.get(f) else 0 for f in PRIVACY_FIELDS]
    updates = [f"{f}=VALUES({f})" for f in given] + ["updated_at=VALUES(updated_at)"]
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"""INSERT INTO aviev_privacy (user_id, {', '.join(PRIVACY_FIELDS)}, updated_at)
                VALUES (%s, {', '.join(['%s'] * len(PRIVACY_FIELDS))}, %s)
                ON DUPLICATE KEY UPDATE {', '.join(updates)}""",
            (user["id"], *insert, utc_now()),
        )
        conn.commit()
    if "hide_presence" in given or "show_watching" in given:
        # Друзья на сайте сразу увидят «вышел» или перестанут видеть, что он смотрит
        from core.functions import realtime
        realtime.privacy_changed(int(user["id"]))
    return read_privacy(user["id"])
