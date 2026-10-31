"""
Сессии соцсети у сессий AsoPlay: токены в aviev_as_sessions, обновление, выход; включён ли вход через соцсеть.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

После входа через Anime Social (кнопкой или паролем) у сессии AsoPlay есть своя сессия устройства в соцсети. Её
токены лежат в aviev_as_sessions зашифрованными (core/classes/token_box.py) и обновляются по одному на сессию:
повтор того же токена обновления соцсеть считает кражей. Выход из AsoPlay отзывает её и в соцсети.
"""
from __future__ import annotations

import hashlib
import logging
import threading
import time
from typing import Any

from core.classes import as_api, db, token_box
from core.classes.clock import utc_now
from core.config import config

log = logging.getLogger("asoplay.as_login")

# Токен доступа обновляем заранее, за минуту до конца
_REFRESH_MARGIN = 60
# После этих ответов сессия соцсети больше не работает — связь с ней удаляется
_DEAD_SESSION = {"invalid_refresh_token", "session_expired", "session_revoked", "unauthorized"}


def enabled() -> bool:
    """Вход через соцсеть включён: флаг AV_AS_LOGIN, ключи клиента AsoPlay и ключ шифрования токенов."""
    flag = config.as_login_flag()
    return flag and as_api.configured() and token_box.available()


# ---------- сессии устройства Anime Social ----------
def _context(session: str, field: str) -> str:
    """Привязка шифротекста к строке и полю: сессия AsoPlay и access или refresh."""
    return f"aviev_as_sessions:{session}:{field}"


def _expiry(tokens: dict[str, Any], field: str, lifetime: int) -> int:
    """Срок из ответа соцсети (Unix), а без него — сейчас + lifetime секунд."""
    try:
        return int(tokens.get(field) or 0) or int(time.time()) + lifetime
    except (TypeError, ValueError):
        return int(time.time()) + lifetime


def _sealed(session: str, tokens: dict[str, Any]) -> tuple[bytes, int, bytes, int]:
    """Токены сессии соцсети зашифрованными, со сроками: (access, срок, refresh, срок)."""
    return (token_box.seal(str(tokens["access_token"]), _context(session, "access")),
            _expiry(tokens, "access_expires_at", int(tokens.get("expires_in") or 3600)),
            token_box.seal(str(tokens["refresh_token"]), _context(session, "refresh")),
            _expiry(tokens, "refresh_expires_at", 90 * 86400))


def insert(session: str, uid: int, tokens: dict[str, Any]) -> None:
    """Связать сессию AsoPlay с новой сессией соцсети; токены — зашифрованными."""
    access, access_exp, refresh, refresh_exp = _sealed(session, tokens)
    now = utc_now()
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO `aviev_as_sessions`
                (`session_token`, `user_id`, `as_session_id`, `access_token`, `access_expires_at`,
                 `refresh_token`, `refresh_expires_at`, `created_at`, `updated_at`)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (session, uid, tokens.get("session_id"), access, access_exp, refresh, refresh_exp, now, now),
        )
        conn.commit()


def _update(session: str, tokens: dict[str, Any]) -> None:
    """Записать обновлённые токены сессии соцсети."""
    access, access_exp, refresh, refresh_exp = _sealed(session, tokens)
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """UPDATE `aviev_as_sessions` SET `access_token` = %s, `access_expires_at` = %s,
                `refresh_token` = %s, `refresh_expires_at` = %s, `updated_at` = %s
                WHERE `session_token` = %s""",
            (access, access_exp, refresh, refresh_exp, utc_now(), session),
        )
        conn.commit()


def _load(session: str) -> dict[str, Any] | None:
    """Токены сессии соцсети — расшифрованные, со сроком токена доступа; связи нет или ключ сменился — None."""
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT `access_token`, `access_expires_at`, `refresh_token` FROM `aviev_as_sessions`
                WHERE `session_token` = %s LIMIT 1""",
            (session,),
        )
        row = cur.fetchone()
    if not row:
        return None
    access = token_box.unseal(row[0], _context(session, "access"))
    refresh = token_box.unseal(row[2], _context(session, "refresh"))
    if access is None or refresh is None:
        return None
    return {"access": access, "access_expires_at": int(row[1]), "refresh": refresh}


def _delete(session: str) -> None:
    """Удалить связь сессии AsoPlay с сессией соцсети."""
    with db.connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM `aviev_as_sessions` WHERE `session_token` = %s", (session,))
        conn.commit()


# Обновление — по одному на сессию: повтор того же токена обновления соцсеть считает кражей
_refresh_locks = [threading.Lock() for _ in range(64)]


def access_token(session: str, user_ip: str = "", user_agent: str = "") -> str | None:
    """Действующий токен доступа сессии соцсети, при необходимости обновлённый; None — связи нет."""
    lock = _refresh_locks[int(hashlib.sha256(session.encode("utf-8")).hexdigest()[:8], 16) % len(_refresh_locks)]
    with lock:
        row = _load(session)
        if row is None:
            return None
        if row["access_expires_at"] - _REFRESH_MARGIN > time.time():
            return row["access"]
        try:
            tokens = as_api.refresh(row["refresh"], user_ip, user_agent)
        except as_api.ASApiError as exc:
            if exc.code in _DEAD_SESSION:
                _delete(session)
            elif exc.code == "invalid_client":
                # Ключи AsoPlay не приняты: настройка, а не сессия человека — связь не удаляем
                log.error("Anime Social rejected the client keys (AV_AS_CLIENT_ID / AV_AS_CLIENT_SECRET)")
            else:
                log.warning("Anime Social token refresh failed: %s %s", exc.status, exc.code)
            return None
        _update(session, tokens)
        return str(tokens["access_token"])


def forget_if_dead(session: str, exc: as_api.ASApiError) -> bool:
    """Ответ соцсети на запрос токеном сессии: если её сессия больше не работает (человек завершил
    «AsoPlay» в настройках соцсети, сменил пароль), связь удаляется. True — удалена."""
    if exc.code not in _DEAD_SESSION:
        return False
    try:
        _delete(session)
    except Exception:  # noqa: BLE001
        log.exception("Could not drop a dead Anime Social session")
    return True


def link(session: str, uid: int, tokens: dict[str, Any], user_ip: str = "", user_agent: str = "") -> None:
    """Связь сессии AsoPlay с новой сессией соцсети; прежняя сессия соцсети, если была, отзывается."""
    previous = access_token(session, user_ip, user_agent)
    if previous:
        logout_quietly(previous, user_ip, user_agent)
    _delete(session)
    insert(session, uid, tokens)


def logout_quietly(token: str, user_ip: str = "", user_agent: str = "") -> None:
    """Отозвать сессию соцсети её токеном доступа; отказ соцсети — только в журнал."""
    try:
        as_api.logout(token, user_ip, user_agent)
    except as_api.ASApiError as exc:
        log.info("Anime Social logout failed: %s %s", exc.status, exc.code)


def end_session(session: str, user_ip: str = "", user_agent: str = "") -> None:
    """Выход из AsoPlay отзывает сессию устройства AsoPlay в соцсети (вход на самом сайте соцсети
    остаётся). Сбой соцсети или базы выход не задерживает."""
    try:
        if token_box.available():
            token = access_token(session, user_ip, user_agent)
            if token:
                logout_quietly(token, user_ip, user_agent)
        _delete(session)
    except Exception:  # noqa: BLE001 — выход из AsoPlay должен пройти в любом случае
        log.exception("Could not end the Anime Social session")
