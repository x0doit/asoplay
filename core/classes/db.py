# -*- coding: utf-8 -*-
"""
AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Bridge to the AnimeSocial database.

- Reads MySQL coordinates from core/config/db.php (the ecosystem-wide PHP
  config). Values can be overridden with AV_DB_HOST / AV_DB_USER / AV_DB_PASS /
  AV_DB_NAME / AV_DB_PORT env vars for non-OpenServer dev setups.
- Uses the existing `Just_*` users table for authentication. The table and
  column names are auto-detected on first use, with AV_USER_TABLE / AV_USER_*
  env overrides when auto-detect does not match.
- Creates and validates sessions in the project's own `aviev_sessions` table.
  The auth cookie is HTTP-only; tokens are NEVER stored in JavaScript-visible
  storage, and passwords are NEVER kept in plain text.
- Registration is intentionally NOT implemented here — /auth/register replies
  with a redirect to the social-network signup URL (AV_REGISTER_URL).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Iterator

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from core.classes.clock import utc_now
from core.config import animesocial, config
from core.functions import rate_limit

log = logging.getLogger("asoplay.auth")

COOKIE_NAME = "aviev_session"


def _register_url() -> str:
    """Back-compat: AV_REGISTER_URL env wins over animesocial.json for legacy
    deployments. Otherwise pull from the single-source-of-truth config."""
    override = config.register_url_override()
    return override or animesocial.register_url()


# ---------- connection pool ----------
_cfg_cache: dict[str, Any] | None = None
_pymysql = None  # lazy import — pymysql is optional


def _pymysql_module():
    global _pymysql
    if _pymysql is None:
        try:
            import pymysql  # type: ignore
        except ImportError as exc:
            raise HTTPException(
                503,
                "AnimeSocial DB client not available: pip install pymysql",
            ) from exc
        _pymysql = pymysql
    return _pymysql


def db_config() -> dict[str, Any]:
    global _cfg_cache
    if _cfg_cache is None:
        _cfg_cache = config.db_settings()
    return _cfg_cache


@contextmanager
def connect() -> Iterator[Any]:
    cfg = db_config()
    pymysql = _pymysql_module()
    # Retry only the connect step across host aliases ("MySQL-8.0" is only
    # resolvable inside OpenServer Panel). Errors from within the yielded
    # block propagate to the caller unchanged.
    last_err: Exception | None = None
    conn = None
    for host in dict.fromkeys([cfg["host"], "127.0.0.1", "localhost"]):
        try:
            conn = pymysql.connect(
                host=host,
                user=cfg["user"],
                password=cfg["password"],
                database=cfg["database"],
                port=cfg["port"],
                charset="utf8mb4",
                connect_timeout=4,
                autocommit=False,
            )
            break
        except Exception as exc:
            last_err = exc
            conn = None
    if conn is None:
        raise HTTPException(503, f"AnimeSocial DB unreachable: {last_err}")
    try:
        yield conn
    finally:
        conn.close()


def health() -> dict[str, Any]:
    """Quick read-only probe. Used by /health. Never raises."""
    try:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.execute("SHOW TABLES LIKE 'aviev_sessions'")
            has_sessions = cur.fetchone() is not None
            return {"ok": True, "aviev_schema_present": has_sessions}
    except HTTPException as exc:
        return {"ok": False, "error": exc.detail}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------- schema introspection ----------
_schema_cache: dict[str, Any] | None = None


def _introspect_schema() -> dict[str, Any]:
    """Pick the AnimeSocial users-table layout: table name + essential columns.

    Respects env overrides (AV_USER_TABLE / AV_USER_ID_COL / AV_USER_LOGIN_COL /
    AV_USER_EMAIL_COL / AV_USER_HASH_COL / AV_USER_SALT_COL / AV_USER_NAME_COL)
    when auto-detection picks the wrong table / column.
    """
    global _schema_cache
    if _schema_cache is not None:
        return _schema_cache

    override_table = config.user_table()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SHOW TABLES")
        all_tables = [row[0] for row in cur.fetchall()]

        if override_table:
            if override_table not in all_tables:
                raise HTTPException(500, f"AV_USER_TABLE='{override_table}' not found")
            user_table = override_table
        else:
            prefix = db_config()["prefix"]
            # AnimeSocial's users table is `Just_accounts`. Other Just_*_accounts
            # tables (e.g. Just_apps_accounts, Just_auth_accounts) also match
            # the generic regex, so we rank by exactness: a direct name like
            # `Just_users` or `Just_accounts` always wins over compound names.
            preferred = [f"{prefix}_{n}" for n in ("accounts", "users", "members", "account", "user", "member")]
            user_table = next((t for t in preferred if t in all_tables), None)
            if not user_table:
                candidates = sorted(
                    (t for t in all_tables
                     if t.startswith(prefix) and re.search(r"(user|account|member)", t, re.I)),
                    key=len,
                )
                if not candidates:
                    raise HTTPException(
                        500,
                        "No users-like table found. Set AV_USER_TABLE env var.",
                    )
                user_table = candidates[0]

        cur.execute(f"DESCRIBE `{user_table}`")
        columns = [row[0] for row in cur.fetchall()]

    def _pick(env_key: str, options: list[str], optional: bool = False) -> str | None:
        forced = config.user_column(env_key)
        if forced:
            if forced not in columns:
                raise HTTPException(500, f"{env_key}='{forced}' absent from `{user_table}`")
            return forced
        for opt in options:
            if opt in columns:
                return opt
        if optional:
            return None
        raise HTTPException(500, f"Could not auto-detect column for {env_key} in `{user_table}`")

    schema = {
        "user_table": user_table,
        "id_col": _pick("AV_USER_ID_COL", ["id", "user_id", "uid"]),
        "login_col": _pick(
            "AV_USER_LOGIN_COL", ["login", "username", "user_name", "nickname", "nick"], optional=True
        ),
        "email_col": _pick("AV_USER_EMAIL_COL", ["email", "mail", "e_mail"], optional=True),
        "hash_col": _pick("AV_USER_HASH_COL", ["password", "passwd", "password_hash", "hash", "pwd"]),
        "salt_col": _pick("AV_USER_SALT_COL", ["salt", "password_salt", "user_salt"], optional=True),
        "name_col": _pick(
            "AV_USER_NAME_COL", ["name", "display_name", "full_name", "nickname", "nick", "login", "username"],
            optional=True,
        ),
        # `banned` wins over `status` / `is_active` because it has unambiguous
        # ban semantics (1 = blocked); `status` in AnimeSocial is actually an
        # unrelated profile-mood field. Override via AV_USER_STATUS_COL.
        "status_col": _pick(
            "AV_USER_STATUS_COL",
            ["banned", "is_banned", "blocked", "is_active", "status", "state"],
            optional=True,
        ),
        "avatar_col": _pick(
            "AV_USER_AVATAR_COL", ["avatar", "avatar_url", "photo", "userpic"], optional=True
        ),
        # Vanity username для URL вида /@edward — в AnimeSocial это колонка
        # `alias`. Если есть — профиль становится /@{alias}, иначе /@profile{id}.
        "handle_col": _pick(
            "AV_USER_HANDLE_COL", ["alias", "username", "nickname", "slug"], optional=True
        ),
    }
    _schema_cache = schema
    log.info("AnimeSocial schema detected: %s", schema)
    return schema


# ---------- password verification ----------
def _verify_password(plain: str, stored_hash: str, salt: str | None) -> bool:
    """Try the configured verifier first; in 'auto' mode, dispatch by hash shape."""
    stored = (stored_hash or "").strip()
    if not stored:
        return False

    strategies: list[str]
    if config.AUTH_VERIFIER == "auto":
        if stored.startswith(("$2a$", "$2b$", "$2y$")):
            strategies = ["bcrypt"]
        elif stored.startswith("$argon2"):
            strategies = ["argon2"]
        elif re.fullmatch(r"[0-9a-fA-F]{64}", stored):
            strategies = ["sha256"]
        elif re.fullmatch(r"[0-9a-fA-F]{40}", stored):
            strategies = ["sha1"]
        elif re.fullmatch(r"[0-9a-fA-F]{32}", stored):
            strategies = ["dle", "md5_salt", "md5"]
        else:
            strategies = ["plain"]
    else:
        strategies = [config.AUTH_VERIFIER]

    for how in strategies:
        try:
            if _check_one(how, plain, stored, salt):
                return True
        except Exception as exc:
            log.debug("verifier %s raised: %s", how, exc)
    return False


def _check_one(how: str, plain: str, stored: str, salt: str | None) -> bool:
    if how == "bcrypt":
        try:
            import bcrypt  # type: ignore
        except ImportError:
            return False
        return bcrypt.checkpw(plain.encode("utf-8"), stored.encode("utf-8"))

    if how == "argon2":
        try:
            from argon2 import PasswordHasher  # type: ignore
            from argon2.exceptions import VerifyMismatchError  # type: ignore
        except ImportError:
            return False
        try:
            PasswordHasher().verify(stored, plain)
            return True
        except VerifyMismatchError:
            return False

    if how == "sha256":
        return hmac.compare_digest(hashlib.sha256(plain.encode("utf-8")).hexdigest(), stored.lower())

    if how == "sha1":
        return hmac.compare_digest(hashlib.sha1(plain.encode("utf-8")).hexdigest(), stored.lower())

    if how == "md5":
        return hmac.compare_digest(hashlib.md5(plain.encode("utf-8")).hexdigest(), stored.lower())

    if how == "md5_salt" and salt:
        digest = hashlib.md5((plain + salt).encode("utf-8")).hexdigest()
        return hmac.compare_digest(digest, stored.lower())

    if how == "dle" and salt:
        # DataLife Engine style: md5(md5(password) + salt)
        inner = hashlib.md5(plain.encode("utf-8")).hexdigest()
        outer = hashlib.md5((inner + salt).encode("utf-8")).hexdigest()
        return hmac.compare_digest(outer, stored.lower())

    if how == "plain":
        return hmac.compare_digest(plain, stored)

    return False


# ---------- user lookup ----------
def _lookup_user(identity: str) -> dict[str, Any] | None:
    schema = _introspect_schema()
    ident_cols = [schema["login_col"], schema["email_col"]]
    ident_cols = [c for c in ident_cols if c]
    if not ident_cols:
        raise HTTPException(500, "Neither login nor email column detected on users table")

    select_cols = ["id_col", "hash_col", "salt_col", "name_col", "login_col", "email_col",
                   "avatar_col", "status_col", "handle_col"]
    picked = {k: schema[k] for k in select_cols if schema.get(k)}
    sql_cols = ", ".join(f"`{v}`" for v in picked.values())
    where = " OR ".join([f"`{c}` = %s" for c in ident_cols])
    params = [identity] * len(ident_cols)

    with connect() as conn:
        cur = conn.cursor()
        cur.execute(f"SELECT {sql_cols} FROM `{schema['user_table']}` WHERE {where} LIMIT 1", params)
        row = cur.fetchone()

    if not row:
        return None

    out: dict[str, Any] = {}
    for key, col in zip(picked.keys(), row):
        out[key] = col
    return out


def resolve_handle(handle: str) -> dict[str, Any] | None:
    """Превращает @alias или @profile{id} в запись пользователя.
    Используется для публичного профиля. Возвращает _serialize_user() или None."""
    handle = (handle or "").strip().lstrip("@")
    if not handle:
        return None

    schema = _introspect_schema()
    select_cols = [
        "id_col", "login_col", "email_col", "name_col", "avatar_col",
        "handle_col", "status_col", "hash_col", "salt_col",
    ]
    picked = {k: schema[k] for k in select_cols if schema.get(k)}
    sql_cols = ", ".join(f"`{v}`" for v in picked.values())

    # /@profile{id} — прямой резолв по user_id.
    m = re.match(r"^profile(\d+)$", handle)
    if m:
        uid = int(m.group(1))
        with connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"SELECT {sql_cols} FROM `{schema['user_table']}` "
                f"WHERE `{schema['id_col']}` = %s LIMIT 1",
                (uid,),
            )
            row = cur.fetchone()
        if not row:
            return None
        return _serialize_user(dict(zip(picked.keys(), row)))

    # /@alias — только если в схеме есть handle-колонка.
    if not schema.get("handle_col"):
        return None
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {sql_cols} FROM `{schema['user_table']}` "
            f"WHERE `{schema['handle_col']}` = %s LIMIT 1",
            (handle,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _serialize_user(dict(zip(picked.keys(), row)))


def _handle_for(user_id: int, alias: str | None) -> str:
    """Vanity-handle в стиле соцсети: @alias или @profile{id}."""
    alias = (alias or "").strip()
    if alias and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", alias):
        return alias
    return f"profile{user_id}"


def _serialize_user(row: dict[str, Any]) -> dict[str, Any]:
    user_id = int(row["id_col"])
    photo = row.get("avatar_col")
    alias = row.get("handle_col")
    handle = _handle_for(user_id, alias)
    return {
        "id": user_id,
        "login": row.get("login_col"),
        "email": row.get("email_col"),
        "name": row.get("name_col") or row.get("login_col") or row.get("email_col"),
        "alias": alias if isinstance(alias, str) and alias else None,
        "handle": handle,
        "profile_path": f"/@{handle}",
        "avatar": animesocial.avatar_url(user_id, photo, "medium"),
        "avatar_small": animesocial.avatar_url(user_id, photo, "small"),
        "profile_url": animesocial.profile_url(user_id),
    }


def _is_banned(status_col_name: str | None, value: Any) -> bool:
    """True when the user should be refused login. AnimeSocial's `Just_accounts`
    uses a `banned` column where 1 = banned; other schemas may store
    `status='banned'` or `is_active=0`. Env override: AV_USER_BANNED_VALUES
    (comma-separated)."""
    if value is None or not status_col_name:
        return False
    env = config.user_banned_values()
    if env:
        banned_set = {v.strip().lower() for v in env.split(",") if v.strip()}
    elif status_col_name.lower() in ("banned", "blocked", "is_banned", "is_blocked"):
        banned_set = {"1", "true", "yes", "on"}
    else:
        banned_set = {"0", "false", "no", "banned", "blocked", "disabled", "inactive"}
    return str(value).strip().lower() in banned_set


# ---------- session storage ----------
def create_session(user_id: int, request: Request) -> str:
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

        schema = _introspect_schema()
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
        return _serialize_user(data)


def revoke_session(token: str) -> None:
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
    if not token:
        return None
    try:
        return touch_session(token)
    except HTTPException:
        return None


def current_user_required(
    user: dict[str, Any] | None = Depends(current_user_optional),
) -> dict[str, Any]:
    if not user:
        raise HTTPException(401, "auth required")
    return user


# ---------- routes ----------
router = APIRouter(prefix="/auth")


class LoginIn(BaseModel):
    login: str = Field(..., max_length=200)
    password: str = Field(..., max_length=500)


def set_session_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=config.SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=config.COOKIE_SECURE,
        samesite=config.COOKIE_SAMESITE,
        path="/",
    )


def _clear_cookie(resp: Response) -> None:
    resp.delete_cookie(COOKIE_NAME, path="/")


# Перебор паролей Anime Social через эту форму: лимиты как у входа на сайте — не больше
# 20 неудачных попыток с одного адреса и 8 на один логин за 15 минут
LOGIN_WINDOW = 15 * 60
_login_by_ip = rate_limit.SlidingWindow(20, LOGIN_WINDOW)
_login_by_identity = rate_limit.SlidingWindow(8, LOGIN_WINDOW)


@router.post("/login")
def auth_login(payload: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    from core.functions import site_settings
    if not site_settings.feature("password_login"):
        # Вход по почте выключили в панели Anime Social
        raise HTTPException(403, "password_login_disabled")
    identity = payload.login.strip()
    ip = rate_limit.client_ip(request)
    identity_key = identity.lower()
    wait = max(_login_by_ip.retry_after(ip), _login_by_identity.retry_after(identity_key))
    if wait:
        raise HTTPException(429, "too many login attempts", headers={"Retry-After": str(wait)})
    if not identity or not payload.password:
        raise HTTPException(401, "invalid credentials")
    # as_login сам импортирует этот модуль, поэтому импорт здесь, а не наверху
    from core.functions import social_login
    if social_login.enabled():
        # Пароль проверяет соцсеть, и вошедший по почте получает ту же сессию AsoPlay в соцсети,
        # что и вошедший кнопкой; вход только по почте — как на сайте соцсети
        signed = social_login.password_login(identity, payload.password, request)
        if signed is None:
            _login_by_ip.hit(ip)
            _login_by_identity.hit(identity_key)
            raise HTTPException(401, "invalid credentials")
        _login_by_identity.reset(identity_key)
        token, user = signed
        set_session_cookie(response, token)
        return {"ok": True, "user": user}
    row = _lookup_user(identity)
    # Блокировку называем только тому, кто знает пароль: иначе форма выдаёт, что аккаунт есть
    if not row or not _verify_password(payload.password, row["hash_col"], row.get("salt_col")):
        _login_by_ip.hit(ip)
        _login_by_identity.hit(identity_key)
        raise HTTPException(401, "invalid credentials")
    _login_by_identity.reset(identity_key)
    schema = _introspect_schema()
    if _is_banned(schema.get("status_col"), row.get("status_col")):
        raise HTTPException(403, "account is blocked")
    user = _serialize_user(row)
    token = create_session(user["id"], request)
    set_session_cookie(response, token)
    return {"ok": True, "user": user}


@router.post("/logout")
def auth_logout(
    request: Request,
    response: Response,
    token: str | None = Cookie(default=None, alias=COOKIE_NAME),
) -> dict[str, Any]:
    if token:
        # as_login сам импортирует этот модуль, поэтому импорт здесь, а не наверху
        from core.functions import realtime, social_login
        social_login.end_session(token, rate_limit.client_ip(request), request.headers.get("user-agent") or "")
        # Вкладки этой сессии переподключаются гостями
        realtime.drop_session(token)
        try:
            revoke_session(token)
        except HTTPException:
            pass
    _clear_cookie(response)
    return {"ok": True}


@router.get("/me")
def auth_me(user: dict[str, Any] | None = Depends(current_user_optional)) -> dict[str, Any]:
    return {"authenticated": bool(user), "user": user}


@router.get("/register")
def auth_register_redirect() -> Response:
    """Registration is on the social network side. We just redirect there."""
    return Response(status_code=302, headers={"Location": _register_url()})


@router.get("/config")
def auth_config() -> dict[str, Any]:
    """Public config used on every page boot. Contains everything the
    frontend needs to render AnimeSocial links and avatars: URLs, templates,
    and a fallback avatar. Single source of truth — edit core/config/animesocial.json
    (or set AV_ANIMESOCIAL_SITE_URL) rather than hardcoding URLs in JS."""
    from core.classes import as_api
    from core.functions import social_login
    social = animesocial.public_view()
    return {
        "register_url": _register_url(),  # keep legacy field for old clients
        "session_ttl_days": config.SESSION_TTL_DAYS,
        "cookie_secure": config.COOKIE_SECURE,
        "social": social,
        "as_login": social_login.enabled(),
        # Браузер сам спрашивает у соцсети, кто в ней вошёл («Имя Фамилия, это вы?»)
        "as_api_url": as_api.base_url(),
    }
