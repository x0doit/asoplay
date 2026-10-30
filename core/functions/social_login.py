# -*- coding: utf-8 -*-
"""
AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Вход через Anime Social: OAuth 2.0 + PKCE поверх API v2 соцсети
(socialnetwork.local/docs/api/README.md, «Вход через сайт»).

  GET /auth/as/start?next=/путь  → страница соцсети «Войти в AsoPlay как …?»
  GET /auth/as/callback          ← code (или error=access_denied) → обмен на токены,
                                   сессия AsoPlay, возврат на next

Кто уже вошёл в AsoPlay по почте, тем же входом связывает с соцсетью свою сессию; если в
соцсети открыт другой аккаунт, вход отклоняется (?as=other), а не подменяет человека.
Форма почты тоже входит через соцсеть (password_login → POST /auth/login): пароль проверяет она,
и сессия соцсети появляется у всех, а не только у вошедших кнопкой.

code_verifier не покидает сервер: он лежит в памяти процесса до 10 минут под ключом
state, а state ещё и в cookie этого браузера — чужой код в чужой браузер не
подставить. Сессия устройства Anime Social после обмена хранится в
aviev_as_sessions (токены зашифрованы, core/classes/token_box.py) и завершается вместе с
выходом из AsoPlay.

Включается AV_AS_LOGIN=1 при заданном ключе AV_TOKEN_KEY. Адрес возврата должен
быть разрешён в движке соцсети; по умолчанию это {схема}://{хост}/auth/as/callback
текущего запроса, AV_AS_REDIRECT_URI его заменяет.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
import threading
import time
from collections import OrderedDict
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from core.classes import as_api, db, token_box
from core.classes.clock import utc_now
from core.config import animesocial, config
from core.functions import rate_limit

log = logging.getLogger("asoplay.as_login")

FLOW_COOKIE = "aviev_as_flow"
FLOW_TTL = 600
# Токен доступа обновляем заранее, за минуту до конца
_REFRESH_MARGIN = 60
# После этих ответов сессия соцсети больше не работает — связь с ней удаляется
_DEAD_SESSION = {"invalid_refresh_token", "session_expired", "session_revoked", "unauthorized"}

router = APIRouter(prefix="/auth/as")


def enabled() -> bool:
    flag = config.as_login_flag()
    return flag and as_api.configured() and token_box.available()


# ---------- незавершённые входы ----------
class _FlowStore:
    """state → {verifier, next}: живёт FLOW_TTL секунд и выдаётся один раз."""

    def __init__(self, ttl: float, max_items: int = 10_000, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self.max_items = max_items
        self._clock = clock
        self._items: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._lock = threading.Lock()

    def put(self, state: str, data: dict[str, Any]) -> None:
        now = self._clock()
        with self._lock:
            while self._items and (len(self._items) >= self.max_items or next(iter(self._items.values()))[0] <= now):
                self._items.popitem(last=False)
            self._items[state] = (now + self.ttl, data)

    def take(self, state: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._items.pop(state, None)
        if item is None or item[0] <= self._clock():
            return None
        return item[1]


_flows = _FlowStore(FLOW_TTL)


def _safe_next(value: str) -> str:
    """Только путь этого сайта: «//хост», «/\\хост» и адреса со схемой увели бы на чужой сайт."""
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")) or any(c in value for c in "\r\n"):
        return "/"
    return value[:500]


def redirect_uri(request: Request) -> str:
    override = config.as_redirect_uri()
    if override:
        return override
    scheme = request.url.scheme
    if rate_limit.from_local_proxy(request):
        forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
        if forwarded in ("http", "https"):
            scheme = forwarded
    host = request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}/auth/as/callback"


def _authorize_url() -> str:
    return as_api.site_url() + "/auth/app"


def _finish(location: str) -> RedirectResponse:
    resp = RedirectResponse(location, status_code=302)
    resp.delete_cookie(FLOW_COOKIE, path="/auth/as")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _with_as(path: str, outcome: str) -> str:
    """next с отметкой ?as=… — чем кончился вход; страница покажет сообщение."""
    return f"{path}{'&' if '?' in path else '?'}as={outcome}"


def _session_user(session: str) -> dict[str, Any] | None:
    if not session:
        return None
    try:
        return db.touch_session(session)
    except Exception:  # noqa: BLE001 — база недоступна: дальше вход всё равно не откроется
        return None


@router.get("/start")
def as_start(request: Request, next: str = "/") -> RedirectResponse:
    if not enabled():
        raise HTTPException(404, "not found")
    from core.functions import site_settings
    if not site_settings.feature("as_login"):
        # Вход через Anime Social выключили в панели: обратно, с объяснением на странице
        return RedirectResponse(_with_as(_safe_next(next), "disabled"), status_code=302)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    _flows.put(state, {"verifier": verifier, "next": _safe_next(next)})
    query = urlencode({"client_id": as_api.client_id(), "redirect_uri": redirect_uri(request),
                       "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    resp = RedirectResponse(f"{_authorize_url()}?{query}", status_code=302)
    resp.set_cookie(FLOW_COOKIE, state, max_age=FLOW_TTL, httponly=True, secure=config.COOKIE_SECURE,
                    samesite="lax", path="/auth/as")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/callback")
def as_callback(request: Request, code: str = "", state: str = "", error: str = "",
                flow_state: str | None = Cookie(default=None, alias=FLOW_COOKIE)) -> RedirectResponse:
    if not enabled():
        raise HTTPException(404, "not found")
    flow = _flows.take(state) if state and flow_state and hmac.compare_digest(state, flow_state) else None
    if flow is None:
        return _finish("/login?as=expired")
    # Уже вошедший в AsoPlay (по почте) связывает с соцсетью ту же сессию — например, со страницы
    # «Друзья»; неудача возвращает его туда же, а не на страницу входа
    current = request.cookies.get(db.COOKIE_NAME) or ""
    signed_in = _session_user(current)

    def fail(outcome: str) -> RedirectResponse:
        return _finish(_with_as(flow["next"], outcome) if signed_in else f"/login?as={outcome}")

    if error or not code:
        return fail("denied" if error == "access_denied" else "error")
    user_ip, user_agent = rate_limit.client_ip(request), request.headers.get("user-agent") or ""
    try:
        result = as_api.exchange_code(code, flow["verifier"], user_ip, user_agent)
    except as_api.ASApiError as exc:
        log.warning("Anime Social code exchange failed: %s %s", exc.status, exc.code)
        return fail("error")
    parsed = _auth_result(result)
    if parsed is None:
        return fail("error")
    tokens, uid, user = parsed
    if user.get("banned"):
        _logout_quietly(str(tokens["access_token"]), user_ip, user_agent)
        return fail("banned")
    if signed_in:
        if int(signed_in["id"]) != uid:
            # В соцсети открыт другой аккаунт: молча менять того, кто вошёл в AsoPlay, нельзя
            _logout_quietly(str(tokens["access_token"]), user_ip, user_agent)
            return fail("other")
        try:
            _link(current, uid, tokens, user_ip, user_agent)
        except Exception:  # noqa: BLE001 — как и при входе: связи нет, хвосты убираем
            log.exception("Could not link the session to Anime Social")
            _logout_quietly(str(tokens["access_token"]), user_ip, user_agent)
            return fail("error")
        return _finish(flow["next"])
    session = _open_session(uid, tokens, request, user_ip, user_agent)
    if session is None:
        return _finish("/login?as=error")
    resp = _finish(flow["next"])
    db.set_session_cookie(resp, session)
    return resp


def _auth_result(result: Any) -> tuple[dict[str, Any], int, dict[str, Any]] | None:
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


def _open_session(uid: int, tokens: dict[str, Any], request: Request, user_ip: str, user_agent: str) -> str | None:
    """Сессия AsoPlay с привязанной сессией соцсети. Не вышло — None, сессия соцсети отозвана."""
    session = None
    try:
        session = db.create_session(uid, request)
        _insert(session, uid, tokens)
        return session
    except Exception:  # noqa: BLE001 — база недоступна или нет таблицы: вход не открываем, хвосты убираем
        log.exception("Could not open a session after sign-in with Anime Social")
        _logout_quietly(str(tokens["access_token"]), user_ip, user_agent)
        if session:
            try:
                db.revoke_session(session)
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
    parsed = _auth_result(result)
    if parsed is None:
        raise HTTPException(503, "Anime Social is unavailable")
    tokens, uid, user = parsed
    if user.get("banned"):
        _logout_quietly(str(tokens["access_token"]), user_ip, user_agent)
        raise HTTPException(403, "account is blocked")
    session = _open_session(uid, tokens, request, user_ip, user_agent)
    profile = _session_user(session) if session else None
    if session and not profile:
        # Аккаунта соцсети нет в базе AsoPlay: такой вход не открываем
        log.error("Anime Social account %s is missing from the AsoPlay database", uid)
        end_session(session, user_ip, user_agent)
        db.revoke_session(session)
    if not profile:
        raise HTTPException(503, "Anime Social is unavailable")
    return session, profile


# ---------- «Имя Фамилия, это вы?» на сайте Anime Social ----------
def _origin(url: str) -> str:
    parts = urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""


def _social_origins() -> set[str]:
    """Адреса сайта соцсети, которым можно узнать, кто вошёл в AsoPlay в этом браузере."""
    origins = {_origin(as_api.site_url())}
    origins.update(_origin(o) for o in config.as_origins())
    return {o for o in origins if o}


@router.get("/whoami")
def as_whoami(request: Request, user: dict[str, Any] | None = Depends(db.current_user_optional)) -> JSONResponse:
    """Кто вошёл в AsoPlay в этом браузере. Читать ответ из другого сайта (CORS с cookie) можно
    только сайту Anime Social; в ответе имя и аватар, без почты и логина."""
    if not enabled():
        raise HTTPException(404, "not found")
    body: dict[str, Any] = {"logged_in": bool(user)}
    if user:
        # Без фото — null, а не картинка-заглушка: сайт сам нарисует инициалы
        avatar = user.get("avatar") or None
        if avatar == animesocial.avatar_url(user["id"], None):
            avatar = None
        body["user"] = {"id": user["id"], "name": user["name"], "avatar": avatar}
    resp = JSONResponse(body, headers={"Cache-Control": "no-store", "Vary": "Origin"})
    origin = request.headers.get("origin") or ""
    if origin in _social_origins():
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    return resp


_NONCE = re.compile(r"^[A-Za-z0-9._~\-]{16,128}$")


@router.get("/to-social")
def as_to_social(request: Request, next: str = "/", nonce: str = "",
                 session: str | None = Cookie(default=None, alias=db.COOKIE_NAME)) -> RedirectResponse:
    """«Продолжить под этим аккаунтом» на сайте Anime Social: вход на сайт тем аккаунтом, которым
    браузер вошёл в AsoPlay (POST /me/web-login токеном его сессии). Нет сессии или связи с
    соцсетью — просто на ту же страницу сайта."""
    if not enabled():
        raise HTTPException(404, "not found")
    path = _safe_next(next)
    target = as_api.site_url() + path
    user_ip, user_agent = rate_limit.client_ip(request), request.headers.get("user-agent") or ""
    user = db.touch_session(session) if session else None
    token = access_token(session, user_ip, user_agent) if user else None
    if token:
        try:
            link = str((as_api.web_login(token, path, nonce if _NONCE.match(nonce) else "", user_ip, user_agent) or {})
                       .get("url") or "")
            # Ссылка — только на сайт соцсети, даже если ответ испорчен
            if link.startswith(as_api.site_url() + "/"):
                target = link
        except as_api.ASApiError as exc:
            log.warning("Anime Social web-login failed: %s %s", exc.status, exc.code)
    resp = RedirectResponse(target, status_code=302)
    resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------- сессии устройства Anime Social ----------
def _context(session: str, field: str) -> str:
    return f"aviev_as_sessions:{session}:{field}"


def _expiry(tokens: dict[str, Any], field: str, lifetime: int) -> int:
    """Срок из ответа соцсети (Unix), а без него — сейчас + lifetime секунд."""
    try:
        return int(tokens.get(field) or 0) or int(time.time()) + lifetime
    except (TypeError, ValueError):
        return int(time.time()) + lifetime


def _sealed(session: str, tokens: dict[str, Any]) -> tuple[bytes, int, bytes, int]:
    return (token_box.seal(str(tokens["access_token"]), _context(session, "access")),
            _expiry(tokens, "access_expires_at", int(tokens.get("expires_in") or 3600)),
            token_box.seal(str(tokens["refresh_token"]), _context(session, "refresh")),
            _expiry(tokens, "refresh_expires_at", 90 * 86400))


def _insert(session: str, uid: int, tokens: dict[str, Any]) -> None:
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


def _link(session: str, uid: int, tokens: dict[str, Any], user_ip: str = "", user_agent: str = "") -> None:
    """Связь сессии AsoPlay с новой сессией соцсети; прежняя сессия соцсети, если была, отзывается."""
    previous = access_token(session, user_ip, user_agent)
    if previous:
        _logout_quietly(previous, user_ip, user_agent)
    _delete(session)
    _insert(session, uid, tokens)


def _logout_quietly(token: str, user_ip: str = "", user_agent: str = "") -> None:
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
                _logout_quietly(token, user_ip, user_agent)
        _delete(session)
    except Exception:  # noqa: BLE001 — выход из AsoPlay должен пройти в любом случае
        log.exception("Could not end the Anime Social session")
