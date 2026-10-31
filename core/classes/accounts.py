"""
Аккаунты AnimeSocial: схема таблицы аккаунтов, поиск по логину или почте, @handle, человек для ответа, бан.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Таблица и столбцы определяются при первом обращении (у соцсети — Just_accounts); их можно задать через
AV_USER_TABLE и AV_USER_*_COL. Пароли — core/classes/passwords.py, сессии AsoPlay — core/functions/sessions.py.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import HTTPException

from core.classes.db import connect, db_config
from core.config import animesocial, config

log = logging.getLogger("asoplay.auth")


# ---------- schema introspection ----------
_schema_cache: dict[str, Any] | None = None


def introspect_schema() -> dict[str, Any]:
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
        """Столбец для env_key: заданный в окружении (его нет в таблице — 500), иначе первый из options."""
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


# ---------- user lookup ----------
def lookup_user(identity: str) -> dict[str, Any] | None:
    """Строка аккаунта по логину или почте (ключи — столбцы схемы) или None."""
    schema = introspect_schema()
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
    Используется для публичного профиля. Возвращает serialize_user() или None."""
    handle = (handle or "").strip().lstrip("@")
    if not handle:
        return None

    schema = introspect_schema()
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
        return serialize_user(dict(zip(picked.keys(), row)))

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
    return serialize_user(dict(zip(picked.keys(), row)))


def _handle_for(user_id: int, alias: str | None) -> str:
    """Vanity-handle в стиле соцсети: @alias или @profile{id}."""
    alias = (alias or "").strip()
    if alias and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}", alias):
        return alias
    return f"profile{user_id}"


def serialize_user(row: dict[str, Any]) -> dict[str, Any]:
    """Человек для ответа и сессии: id, логин, почта, имя, alias, @handle, аватары и ссылка на профиль
    в соцсети."""
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


def is_banned(status_col_name: str | None, value: Any) -> bool:
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
