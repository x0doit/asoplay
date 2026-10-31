"""
Вход через Anime Social: незавершённые входы (PKCE), сессия AsoPlay со связанной сессией соцсети, вход паролем.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

OAuth 2.0 + PKCE поверх API v2 соцсети (socialnetwork.local/docs/api/README.md, «Вход через сайт»). code_verifier
не покидает сервер: он лежит в памяти процесса до 10 минут под ключом state, а state — ещё и в cookie браузера,
чужой код в чужой браузер не подставить. Форма почты тоже входит через соцсеть (password_login → POST /auth/login):
пароль проверяет она, и сессия соцсети появляется у всех. Маршруты — core/api/sections/social_login.py, сессии
соцсети — core/classes/as_sessions.py.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from typing import Any, Callable

from fastapi import HTTPException, Request

from core.classes import as_api, as_sessions
from core.functions import rate_limit, sessions

log = logging.getLogger("asoplay.as_login")

FLOW_TTL = 600


# ---------- незавершённые входы ----------
class _FlowStore:
    """state → {verifier, next}: живёт FLOW_TTL секунд и выдаётся один раз."""

    def __init__(self, ttl: float, max_items: int = 10_000, clock: Callable[[], float] = time.monotonic):
        """ttl — сколько живёт незавершённый вход; больше max_items сразу не держим, старые вытесняются."""
        self.ttl = ttl
        self.max_items = max_items
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, state: str, data: dict[str, Any]) -> None:
        """Запомнить вход под ключом state; заодно выбросить просроченные и лишние."""
        now = self._clock()
        with self._lock:
            while self._items and (len(self._items) >= self.max_items or next(iter(self._items.values()))[0] <= now):
                self._items.popitem(last=False)
            self._items[state] = (now + self.ttl, data)

    def take(self, state: str) -> dict[str, Any] | None:
        """Забрать вход по state, один раз; просроченный — None."""
        with self._lock:
            item = self._items.pop(state, None)
        if item is None or item[0] <= self._clock():
            return None
        return item[1]


flows = _FlowStore(FLOW_TTL)


def session_user(session: str) -> dict[str, Any] | None:
    """Человек по сессии AsoPlay или None: сессии нет, она закрыта или база недоступна."""
    if not session:
        return None
    try:
        return sessions.touch_session(session)
    except Exception:  # noqa: BLE001 — база недоступна: дальше вход всё равно не откроется
        return None


def auth_result(result: Any) -> tuple[dict[str, Any], int, dict[str, Any]] | None:
    """Ответ входа соцсети {tokens, user} → (токены, id, пользователь); испорченный ответ — None."""
    tokens = result.get("tokens") if isinstance(result, dict) else None
    user = result.get("user") if isinstance(result, dict) else None
    try:
        uid = int((user or {}).get("id") or 0)
    except (TypeError, ValueError):
        uid = 0
    if not uid or not isinstance(tokens, dict) or not tokens.get("access_token") or not tokens.get("refresh_token"):
        return None
    return tokens, uid, user


def open_session(uid: int, tokens: dict[str, Any], request: Request, user_ip: str, user_agent: str) -> str | None:
    """Сессия AsoPlay с привязанной сессией соцсети. Не вышло — None, сессия соцсети отозвана."""
    session = None
    try:
        session = sessions.create_session(uid, request)
        as_sessions.insert(session, uid, tokens)
        return session
    except Exception:  # noqa: BLE001 — база недоступна или нет таблицы: вход не открываем, хвосты убираем
        log.exception("Could not open a session after sign-in with Anime Social")
        as_sessions.logout_quietly(str(tokens["access_token"]), user_ip, user_agent)
        if session:
            try:
                sessions.revoke_session(session)
            except Exception:  # noqa: BLE001
                log.exception("Could not revoke the half-open session")
        return None


# ---------- вход по почте и паролю через соцсеть ----------
def password_login(email: str, password: str, request: Request) -> tuple[str, dict[str, Any]] | None:
    """Форма почты AsoPlay. Пароль проверяет соцсеть (POST /auth/login с ключами клиента), и у человека
    появляется сессия AsoPlay в соцсети, как после входа кнопкой: «онлайн на AsoPlay», уведомления о
    сериях и события просмотра работают и для него. Возвращает (сессия, пользователь как в /auth/me);
    None — неверная почта или пароль. Блокировку называет только тому, кто знает пароль (403), лимиты
    соцсети — 429 с её Retry-After, соцсеть не отвечает — 503."""
    user_ip, user_agent = rate_limit.client_ip(request), request.headers.get("user-agent") or ""
    try:
        result = as_api.password_login(email, password, user_ip, user_agent)
    except as_api.ASApiError as exc:
        if exc.code in ("invalid_credentials", "validation_failed"):
            return None
        if exc.code == "rate_limited":
            raise HTTPException(429, "too many login attempts",
                                headers={"Retry-After": str(exc.retry_after or 900)}) from exc
        if exc.code == "invalid_client":
            log.error("Anime Social rejected the client keys (AV_AS_CLIENT_ID / AV_AS_CLIENT_SECRET)")
        else:
            log.warning("Anime Social password login failed: %s %s", exc.status, exc.code)
        raise HTTPException(503, "Anime Social is unavailable") from exc
    parsed = auth_result(result)
    if parsed is None:
        raise HTTPException(503, "Anime Social is unavailable")
    tokens, uid, user = parsed
    if user.get("banned"):
        as_sessions.logout_quietly(str(tokens["access_token"]), user_ip, user_agent)
        raise HTTPException(403, "account is blocked")
    session = open_session(uid, tokens, request, user_ip, user_agent)
    profile = session_user(session) if session else None
    if session and not profile:
        # Аккаунта соцсети нет в базе AsoPlay: такой вход не открываем
        log.error("Anime Social account %s is missing from the AsoPlay database", uid)
        as_sessions.end_session(session, user_ip, user_agent)
        sessions.revoke_session(session)
    if not profile:
        raise HTTPException(503, "Anime Social is unavailable")
    return session, profile
