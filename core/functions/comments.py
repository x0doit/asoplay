"""
Комментарии AsoPlay в соцсети (clients.md, раздел 12): какие объекты можно комментировать, запрос к соцсети токеном
человека, ответы гостям из короткого кеша, отказы соцсети — короткими кодами.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Маршруты — core/api/sections/comments.py, события веток вкладкам — core/functions/realtime_comments.py. Токены
соцсети в браузер не попадают. Истёк токен доступа (401 token_expired) — одно обновление и повтор; снова 401 —
связи нет. Гостевые ответы (без токена) живут в памяти GUEST_TTL секунд; ответы с liked и can не кешируются.
Текст отказов соцсети — в журнал, наружу — её код.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Request

from core.classes import as_api, as_sessions
from core.functions import rate_limit

log = logging.getLogger("asoplay.comments")

# Тайтл и серия: asoplay:anime:52991, asoplay:episode:52991.6
OBJECT = re.compile(r"^asoplay:(?:anime:(\d{1,9})|episode:(\d{1,9})\.(\d{1,5}))$")
GUEST_TTL = 10.0
GUEST_ENTRIES = 512
_REFUSALS = {400, 403, 404, 409, 422}
_guest: OrderedDict[str, tuple[float, Any]] = OrderedDict()
_guest_lock = threading.Lock()


def parse_object(key: str) -> tuple[int, int | None] | None:
    """(mal_id, номер серии или None) по ключу объекта AsoPlay; чужой или испорченный ключ — None."""
    m = OBJECT.match(key or "")
    if not m:
        return None
    if m.group(1):
        return int(m.group(1)), None
    return int(m.group(2)), int(m.group(3))


def query(params: dict[str, Any]) -> str:
    """Строка запроса без пустых параметров, по порядку имён: так перестановка не плодит промахи кеша."""
    return urlencode(sorted((k, str(v)) for k, v in params.items() if v is not None and v != ""))


def _who(request: Request) -> tuple[str, str]:
    """Адрес и браузер посетителя — соцсеть считает по ним свои лимиты."""
    return rate_limit.client_ip(request), request.headers.get("user-agent") or ""


def token_of(request: Request, session: str | None) -> str | None:
    """Токен соцсети этого человека или None: гость, связи нет, вход через соцсеть выключен."""
    if not session or not as_sessions.enabled():
        return None
    user_ip, user_agent = _who(request)
    return as_sessions.access_token(session, user_ip, user_agent)


def _refusal(exc: as_api.ASApiError, session: str | None) -> HTTPException:
    """Отказ соцсети — короткий код наружу; текст — в журнал."""
    if exc.status == 401 and exc.code != "invalid_client":
        if session:
            as_sessions.forget_if_dead(session, exc)  # завершённая сессия соцсети — связь удаляется
        return HTTPException(401, "as_link_required")
    if exc.status == 429 or exc.code == "rate_limited":
        return HTTPException(429, exc.code or "rate_limited", headers={"Retry-After": str(exc.retry_after or 60)})
    if exc.status in _REFUSALS:
        return HTTPException(exc.status, exc.code)
    log.warning("Anime Social comments request failed: %s %s %s", exc.status, exc.code, exc.message)
    return HTTPException(503, "comments_unavailable")


def social_call(request: Request, session: str | None, method: str, path: str, body: dict[str, Any] | None = None,
                *, token: str | None = None) -> Any:
    """Запрос к соцсети с токеном человека или без. Истёк токен доступа — одно обновление и повтор."""
    user_ip, user_agent = _who(request)
    for attempt in (0, 1):
        try:
            return as_api.call(method, path, token=token, body=body, user_ip=user_ip, user_agent=user_agent)
        except as_api.ASApiError as exc:
            if token and session and not attempt and exc.status == 401 and exc.code == "token_expired":
                token = as_sessions.access_token(session, user_ip, user_agent, stale=token)
                if token:
                    continue
            raise _refusal(exc, session) from exc
    raise AssertionError("unreachable")


def _as_guest(request: Request, path: str, key: str) -> Any:
    """Ответ без токена; повтор за GUEST_TTL — из памяти, не больше GUEST_ENTRIES записей."""
    now = time.monotonic()
    with _guest_lock:
        hit = _guest.get(key)
        if hit and hit[0] > now:
            _guest.move_to_end(key)
            return hit[1]
    data = social_call(request, None, "GET", path)
    with _guest_lock:
        _guest[key] = (now + GUEST_TTL, data)
        _guest.move_to_end(key)
        while len(_guest) > GUEST_ENTRIES:
            _guest.popitem(last=False)
    return data


def read(request: Request, session: str | None, path: str, params: dict[str, Any] | None = None, *,
         cache: bool = True) -> Any:
    """Чтение: вошедшему со связью — его токеном, без кеша; связи нет или она больше не работает — как гостю
    (комментарии открыты всем), из кеша, если cache. Один комментарий после события читается без кеша."""
    key = query(params or {})
    full = f"{path}?{key}" if key else path
    token = token_of(request, session)
    if token:
        try:
            return social_call(request, session, "GET", full, token=token)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
    return _as_guest(request, full, full) if cache else social_call(request, None, "GET", full)


def write(request: Request, session: str | None, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """Действие человека: без связи с соцсетью — 401 as_link_required."""
    token = token_of(request, session)
    if not token:
        raise HTTPException(401, "as_link_required")
    return social_call(request, session, method, path, body, token=token)
