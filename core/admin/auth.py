"""
Админ-API, общие правила: подпись и повторы, ошибки и обёртка ответов, страницы списков.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/admin: вход router.py подключает части по порядку, а эта — основа остальных частей. Протокол подписи
описан во входе. PAGE остальные части читают как auth.PAGE — через модуль, а не копией.
"""
from __future__ import annotations

import hashlib
import hmac
import threading
import time
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from core.config import config

MAX_AGE = 60
PAGE = 50
_LOOPBACK = ("127.0.0.1", "::1")


class AdminError(Exception):
    """Ответ панели с ошибкой: {ok: false, error: {code, message}}. message панель показывает под своим текстом:
    коротко, по-русски и без секретов."""

    def __init__(self, status: int, code: str, message: str = ""):
        """Код ответа HTTP, код ошибки для панели и текст для человека (без текста — сам код)."""
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message or code


def error_response(_request: Request, exc: AdminError) -> JSONResponse:
    """Ответ с ошибкой в обёртке API v2: {ok: false, error: {code, message}}."""
    return JSONResponse({"ok": False, "error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


def ok(data: Any) -> dict[str, Any]:
    """Успешный ответ в обёртке API v2: {ok: true, data}."""
    return {"ok": True, "data": data}


def enabled() -> bool:
    """Включено ли админ-API: задан ли общий секрет с панелью."""
    return bool(config.admin_secret())


def sign(secret: bytes, ts: str, method: str, path_with_query: str, admin_user: str, body: bytes) -> str:
    """Подпись запроса панели: HMAC-SHA256 от времени, метода, пути с запросом, id администратора и тела."""
    message = f"{ts}\n{method.upper()}\n{path_with_query}\n{admin_user}\n".encode("utf-8") + body
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


class _Seen:
    """Подписи изменяющих запросов за последние 2·MAX_AGE секунд: повтор перехваченного запроса не пройдёт.
    Чтение повторять можно — панель вправе открыть одну страницу дважды за секунду."""

    def __init__(self) -> None:
        """Подпись → до какого времени её помнить; под замком — запросы панели приходят одновременно."""
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def first(self, signature: str, now: float) -> bool:
        """Впервые ли пришла эта подпись; заодно забыть те, срок которых вышел."""
        with self._lock:
            for key in [k for k, until in self._until.items() if until < now]:
                del self._until[key]
            if signature in self._until:
                return False
            self._until[signature] = now + 2 * MAX_AGE
            return True


_seen = _Seen()


async def admin_user(request: Request) -> int:
    """Зависимость каждого метода: проверить подпись панели и вернуть id администратора."""
    host = request.client.host if request.client else ""
    if host not in _LOOPBACK or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
        raise AdminError(404, "not_found", "Не найдено")
    secret = config.admin_secret()
    if not secret:
        raise AdminError(503, "not_configured", "AV_ADMIN_SECRET не задан в .env AsoPlay")
    ts, _, given = request.headers.get("x-asoplay-admin", "").partition(".")
    if not ts.isdigit() or len(given) != 64:
        raise AdminError(401, "bad_signature", "Нет подписи X-AsoPlay-Admin")
    now = time.time()
    if abs(now - int(ts)) > MAX_AGE:
        raise AdminError(401, "expired", f"Подпись старше {MAX_AGE} секунд")
    user = request.headers.get("x-asoplay-admin-user", "")
    if not user.isdigit() or not 0 < int(user) < 2**32:
        raise AdminError(400, "bad_admin_user", "Нет X-AsoPlay-Admin-User")
    path = request.scope.get("raw_path", b"").decode("latin-1") or request.url.path
    query = request.scope.get("query_string", b"").decode("latin-1")
    expected = sign(secret, ts, request.method, path + (f"?{query}" if query else ""), user, await request.body())
    if not hmac.compare_digest(expected, given.lower()):
        raise AdminError(401, "bad_signature", "Подпись не сходится")
    if request.method != "GET" and not _seen.first(expected, now):
        raise AdminError(401, "replayed", "Этот запрос уже был")
    return int(user)


# ---------- страницы списков ----------
def _after(cursor: str) -> int:
    """Курсор страницы — id последней строки прошлой страницы."""
    if not cursor:
        return 0
    if not cursor.isdigit():
        raise AdminError(422, "validation_failed", "Неверный курсор")
    return int(cursor)


def _page(items: list[dict[str, Any]], key: str = "id") -> dict[str, Any]:
    """Страница списка: не больше PAGE строк и курсор следующей, если строк больше."""
    more = len(items) > PAGE
    items = items[:PAGE]
    return {"items": items, "next_cursor": str(items[-1][key]) if more else None}
