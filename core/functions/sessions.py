"""
Сессии AsoPlay: создать, проверить, закрыть; cookie сессии и «текущий пользователь» для маршрутов.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Сессии лежат в aviev_sessions; токен — 64 hex-знака в cookie HttpOnly, JavaScript его не видит. Сессия, которой
не пользовались SESSION_TTL_DAYS, закрывается при первом обращении. Модуль в functions, а не в core/auth.py: сессии
нужны логике входа через соцсеть (social_login) и живому соединению (realtime). После части K (конец шага 5) решим,
поднимать ли cookie и current_user_* в core/auth.py, когда маршруты уйдут из functions.
"""
from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from fastapi import Cookie, Depends, HTTPException, Request, Response

from core.classes import accounts
from core.classes.clock import utc_now
from core.classes.db import connect
from core.config import config
from core.functions import rate_limit

COOKIE_NAME = "aviev_session"


# ---------- session storage ----------
def create_session(user_id: int, request: Request) -> str:
    """Новая сессия человека: случайный токен из 64 hex-знаков, строка в aviev_sessions с браузером и IP."""
    token = secrets.token_hex(32)  # 64 hex chars
    now = utc_now()
    ua = (request.headers.get("user-agent") or "")[:255]
    ip = rate_limit.client_ip(request)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO `aviev_sessions`
                (`token`, `user_id`, `created_at`, `last_seen_at`, `user_agent`, `ip`, `revoked`)
                VALUES (%s, %s, %s, %s, %s, %s, 0)""",
            (token, user_id, now, now, ua, ip),
        )
        conn.commit()
    return token


def touch_session(token: str) -> dict[str, Any] | None:
    """Resolve a session token to a user. Returns None for revoked/expired/missing."""
    if not token or len(token) != 64:
        return None
    cutoff = utc_now() - timedelta(days=config.SESSION_TTL_DAYS)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT user_id, last_seen_at, revoked FROM `aviev_sessions`
                WHERE `token` = %s LIMIT 1""",
            (token,),
        )
        row = cur.fetchone()
        if not row:
            return None
        user_id, last_seen_at, revoked = row
        if revoked:
            return None
        if last_seen_at and last_seen_at < cutoff:
            cur.execute(
                "UPDATE `aviev_sessions` SET `revoked` = 1 WHERE `token` = %s",
                (token,),
            )
            conn.commit()
            return None
        cur.execute(
            "UPDATE `aviev_sessions` SET `last_seen_at` = %s WHERE `token` = %s",
            (utc_now(), token),
        )
        conn.commit()

        schema = accounts.introspect_schema()
        select_cols = ["id_col", "login_col", "email_col", "name_col", "avatar_col", "handle_col"]
        picked = {k: schema[k] for k in select_cols if schema.get(k)}
        sql_cols = ", ".join(f"`{v}`" for v in picked.values())
        cur.execute(
            f"SELECT {sql_cols} FROM `{schema['user_table']}` WHERE `{schema['id_col']}` = %s LIMIT 1",
            (user_id,),
        )
        urow = cur.fetchone()
        if not urow:
            return None
        data = dict(zip(picked.keys(), urow))
        return accounts.serialize_user(data)


def revoke_session(token: str) -> None:
    """Закрыть сессию по токену; пустой токен — ничего."""
    if not token:
        return
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE `aviev_sessions` SET `revoked` = 1 WHERE `token` = %s",
            (token,),
        )
        conn.commit()


def revoke_user_sessions(user_id: int) -> None:
    """Страницу заблокировали в Anime Social: все сессии человека в AsoPlay и их связи с соцсетью — закрыть."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE `aviev_sessions` SET `revoked` = 1 WHERE `user_id` = %s", (user_id,))
        cur.execute("DELETE FROM `aviev_as_sessions` WHERE `user_id` = %s", (user_id,))
        conn.commit()


# ---------- FastAPI dependencies ----------
def current_user_optional(
    request: Request,
    token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> dict[str, Any] | None:
    """Человек по cookie сессии или None: cookie нет, сессия закрыта или база недоступна."""
    if not token:
        return None
    try:
        return touch_session(token)
    except HTTPException:
        return None


def current_user_required(
    user: dict[str, Any] | None = Depends(current_user_optional),
) -> dict[str, Any]:
    """Человек по cookie сессии; нет — 401 «auth required»."""
    if not user:
        raise HTTPException(401, "auth required")
    return user


# ---------- cookie ----------
def set_session_cookie(resp: Response, token: str) -> None:
    """Поставить cookie сессии: HttpOnly, Secure и SameSite из настроек, срок — SESSION_TTL_DAYS, путь /."""
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=config.SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        path="/",
    )


def clear_session_cookie(resp: Response) -> None:
    """Снять cookie сессии."""
    resp.delete_cookie(COOKIE_NAME, path="/")
