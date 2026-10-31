"""
Вход через Anime Social, маршруты /auth/as: начало входа, возврат со страницы соцсети, «кто вошёл», переход на сайт.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

  GET /auth/as/start?next=/путь  → страница соцсети «Войти в AsoPlay как …?»
  GET /auth/as/callback          ← code (или error=access_denied) → обмен на токены, сессия AsoPlay, возврат на next
  GET /auth/as/whoami            ← сайт соцсети спрашивает, кто вошёл в AsoPlay в этом браузере (CORS с cookie)
  GET /auth/as/to-social         → сайт соцсети сразу под тем же аккаунтом

Кто уже вошёл в AsoPlay по почте, тем же входом связывает с соцсетью свою сессию; если в соцсети открыт другой
аккаунт, вход отклоняется (?as=other), а не подменяет человека. Включается AV_AS_LOGIN=1 при заданном AV_TOKEN_KEY.
Адрес возврата должен быть разрешён в движке соцсети: по умолчанию {схема}://{хост}/auth/as/callback текущего
запроса, AV_AS_REDIRECT_URI его заменяет.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
import secrets
from typing import Any
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

from core.classes import as_api, as_sessions
from core.config import animesocial, config
from core.functions import rate_limit, sessions, site_settings, social_login

log = logging.getLogger("asoplay.as_login")

FLOW_COOKIE = "aviev_as_flow"

router = APIRouter(prefix="/auth/as")


def _safe_next(value: str) -> str:
    """Только путь этого сайта: «//хост», «/\\хост» и адреса со схемой увели бы на чужой сайт."""
    if not value or not value.startswith("/") or value.startswith(("//", "/\\")) or any(c in value for c in "\r\n"):
        return "/"
    return value[:500]


def redirect_uri(request: Request) -> str:
    """Адрес возврата со страницы соцсети: AV_AS_REDIRECT_URI или {схема}://{хост}/auth/as/callback этого
    запроса; схема — из X-Forwarded-Proto, если запрос пришёл через локальный nginx."""
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
    """Страница соцсети «Войти в AsoPlay как …?»."""
    return as_api.site_url() + "/auth/app"


def _finish(location: str) -> RedirectResponse:
    """Переход в конце входа: cookie незавершённого входа снимается, ответ не кешируется."""
    resp = RedirectResponse(location, status_code=302)
    resp.delete_cookie(FLOW_COOKIE, path="/auth/as")
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _with_as(path: str, outcome: str) -> str:
    """next с отметкой ?as=… — чем кончился вход; страница покажет сообщение."""
    return f"{path}{'&' if '?' in path else '?'}as={outcome}"


@router.get("/start")
def as_start(request: Request, next: str = "/") -> RedirectResponse:
    """Начало входа: state и code_verifier (PKCE) — в память процесса, state — ещё и в cookie браузера;
    переход на страницу соцсети. Вход выключен в панели — обратно с ?as=disabled."""
    if not as_sessions.enabled():
        raise HTTPException(404, "not found")
    if not site_settings.feature("as_login"):
        # Вход через Anime Social выключили в панели: обратно, с объяснением на странице
        return RedirectResponse(_with_as(_safe_next(next), "disabled"), status_code=302)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    social_login.flows.put(state, {"verifier": verifier, "next": _safe_next(next)})
    query = urlencode({"client_id": as_api.client_id(), "redirect_uri": redirect_uri(request),
                       "code_challenge": challenge, "code_challenge_method": "S256", "state": state})
    resp = RedirectResponse(f"{_authorize_url()}?{query}", status_code=302)
    resp.set_cookie(FLOW_COOKIE, state, max_age=social_login.FLOW_TTL, httponly=True, secure=config.COOKIE_SECURE,
                    samesite="lax", path="/auth/as")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@router.get("/callback")
def as_callback(request: Request, code: str = "", state: str = "", error: str = "",
                flow_state: str | None = Cookie(default=None, alias=FLOW_COOKIE)) -> RedirectResponse:
    """Возврат со страницы соцсети: код меняется на токены, открывается сессия AsoPlay (или связывается уже
    открытая), переход на next; неудача — ?as=… с причиной."""
    if not as_sessions.enabled():
        raise HTTPException(404, "not found")
    flow = social_login.flows.take(state) if state and flow_state and hmac.compare_digest(state, flow_state) else None
    if flow is None:
        return _finish("/login?as=expired")
    # Уже вошедший в AsoPlay (по почте) связывает с соцсетью ту же сессию — например, со страницы
    # «Друзья»; неудача возвращает его туда же, а не на страницу входа
    current = request.cookies.get(sessions.COOKIE_NAME) or ""
    signed_in = social_login.session_user(current)

    def fail(outcome: str) -> RedirectResponse:
        """Неудача: вошедший возвращается туда же с ?as=…, остальные — на страницу входа."""
        return _finish(_with_as(flow["next"], outcome) if signed_in else f"/login?as={outcome}")

    if error or not code:
        return fail("denied" if error == "access_denied" else "error")
    user_ip, user_agent = rate_limit.client_ip(request), request.headers.get("user-agent") or ""
    try:
        result = as_api.exchange_code(code, flow["verifier"], user_ip, user_agent)
    except as_api.ASApiError as exc:
        log.warning("Anime Social code exchange failed: %s %s", exc.status, exc.code)
        return fail("error")
    parsed = social_login.auth_result(result)
    if parsed is None:
        return fail("error")
    tokens, uid, user = parsed
    if user.get("banned"):
        as_sessions.logout_quietly(str(tokens["access_token"]), user_ip, user_agent)
        return fail("banned")
    if signed_in:
        if int(signed_in["id"]) != uid:
            # В соцсети открыт другой аккаунт: молча менять того, кто вошёл в AsoPlay, нельзя
            as_sessions.logout_quietly(str(tokens["access_token"]), user_ip, user_agent)
            return fail("other")
        try:
            as_sessions.link(current, uid, tokens, user_ip, user_agent)
        except Exception:  # noqa: BLE001 — как и при входе: связи нет, хвосты убираем
            log.exception("Could not link the session to Anime Social")
            as_sessions.logout_quietly(str(tokens["access_token"]), user_ip, user_agent)
            return fail("error")
        return _finish(flow["next"])
    session = social_login.open_session(uid, tokens, request, user_ip, user_agent)
    if session is None:
        return _finish("/login?as=error")
    resp = _finish(flow["next"])
    sessions.set_session_cookie(resp, session)
    return resp


# ---------- «Имя Фамилия, это вы?» на сайте Anime Social ----------
def _origin(url: str) -> str:
    """Схема и хост адреса или пустая строка."""
    parts = urlsplit(url.strip())
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme in ("http", "https") and parts.netloc else ""


def _social_origins() -> set[str]:
    """Адреса сайта соцсети, которым можно узнать, кто вошёл в AsoPlay в этом браузере."""
    origins = {_origin(as_api.site_url())}
    origins.update(_origin(o) for o in config.as_origins())
    return {o for o in origins if o}


@router.get("/whoami")
def as_whoami(request: Request, user: dict[str, Any] | None = Depends(sessions.current_user_optional)) -> JSONResponse:
    """Кто вошёл в AsoPlay в этом браузере. Читать ответ из другого сайта (CORS с cookie) можно
    только сайту Anime Social; в ответе имя и аватар, без почты и логина."""
    if not as_sessions.enabled():
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
                 session: str | None = Cookie(default=None, alias=sessions.COOKIE_NAME)) -> RedirectResponse:
    """«Продолжить под этим аккаунтом» на сайте Anime Social: вход на сайт тем аккаунтом, которым
    браузер вошёл в AsoPlay (POST /me/web-login токеном его сессии). Нет сессии или связи с
    соцсетью — просто на ту же страницу сайта."""
    if not as_sessions.enabled():
        raise HTTPException(404, "not found")
    path = _safe_next(next)
    target = as_api.site_url() + path
    user_ip, user_agent = rate_limit.client_ip(request), request.headers.get("user-agent") or ""
    user = sessions.touch_session(session) if session else None
    token = as_sessions.access_token(session, user_ip, user_agent) if user else None
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
