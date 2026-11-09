"""
Подключение к базе AnimeSocial: настройки из core/config/db.php, соединение и проверка для /health.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Координаты MySQL — из core/config/db.php (общий PHP-конфиг экосистемы); для разработки вне OpenServer их задают
AV_DB_HOST, AV_DB_USER, AV_DB_PASS, AV_DB_NAME и AV_DB_PORT. Соединение пробует адреса по очереди: имя «MySQL-8.0»
знает только OpenServer. pymysql — необязательный пакет, без него 503. Подробности сбоя (текст ошибки MySQL, в нём
бывает имя пользователя базы) — только в журнал, наружу — 503 «database unavailable». Аккаунты —
core/classes/accounts.py, сессии AsoPlay — core/functions/sessions.py, вход — core/api/sections/auth.py.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

from fastapi import HTTPException

from core.config import config

log = logging.getLogger("asoplay.db")
UNAVAILABLE = "database unavailable"

# ---------- connection pool ----------
_cfg_cache: dict[str, Any] | None = None
_pymysql = None  # lazy import — pymysql is optional


def _pymysql_module():
    """pymysql по требованию: пакета нет — 503."""
    global _pymysql
    if _pymysql is None:
        try:
            import pymysql  # type: ignore
        except ImportError as exc:
            log.error("AnimeSocial DB client not available: pip install pymysql")
            raise HTTPException(503, UNAVAILABLE) from exc
        _pymysql = pymysql
    return _pymysql


def db_config() -> dict[str, Any]:
    """Настройки базы из core/config/db.php и окружения; читаются один раз."""
    global _cfg_cache
    if _cfg_cache is None:
        _cfg_cache = config.db_settings()
    return _cfg_cache


@contextmanager
def connect() -> Iterator[Any]:
    """Соединение с базой: адрес из настроек, потом 127.0.0.1 и localhost; не вышло — 503. Закрывается
    на выходе из with, фиксировать изменения — commit() вызывающего."""
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
        log.warning("AnimeSocial DB unreachable: %s", last_err)  # текст pymysql — только в журнал
        raise HTTPException(503, UNAVAILABLE)
    try:
        yield conn
    finally:
        conn.close()


def health() -> dict[str, Any]:
    """Короткая проверка базы для /health (он открыт всем): ошибок не бросает, текст сбоя — только в журнал."""
    try:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.execute("SHOW TABLES LIKE 'aviev_sessions'")
            has_sessions = cur.fetchone() is not None
            return {"ok": True, "aviev_schema_present": has_sessions}
    except HTTPException:
        return {"ok": False, "error": "unavailable"}  # подробность connect() уже записал в журнал
    except Exception as exc:  # noqa: BLE001 — /health не падает
        log.warning("Database health check failed: %s", exc)
        return {"ok": False, "error": "unavailable"}


# ---------- части для SQL IN ----------
_SQL_CHUNK = 1000


def chunks(ids: Iterable[int], size: int = _SQL_CHUNK) -> Iterable[list[int]]:
    """Id без повторов, по возрастанию, частями по size — для SQL IN (…)."""
    ordered = sorted(set(ids))
    for i in range(0, len(ordered), size):
        yield ordered[i:i + size]
