"""
Вход и выход AsoPlay: /auth/login, /auth/logout, /auth/me, /auth/register (переход на регистрацию соцсети)
и /auth/config (адреса и настройки соцсети для каждой страницы).

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Пароль проверяет соцсеть, если вход через неё включён, иначе — AsoPlay по хешу в базе (core/classes/passwords.py).
Перебор паролей ограничен, как на сайте соцсети. Блокировку называем только тому, кто знает пароль. Регистрации
здесь нет: /auth/register ведёт на сайт соцсети.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from core.classes import accounts, as_api, passwords
from core.config import animesocial, config
from core.functions import rate_limit, realtime, sessions, site_settings, social_login

def _register_url() -> str:
    """Back-compat: AV_REGISTER_URL env wins over animesocial.json for legacy
    deployments. Otherwise pull from the single-source-of-truth config."""
    override = config.register_url_override()
    return override or animesocial.register_url()


router = APIRouter(prefix="/auth")


class LoginIn(BaseModel):
    """Форма входа: логин или почта и пароль."""
    login: str = Field(..., max_length=200)
    password: str = Field(..., max_length=500)


# Перебор паролей Anime Social через эту форму: лимиты как у входа на сайте — не больше
# 20 неудачных попыток с одного адреса и 8 на один логин за 15 минут
LOGIN_WINDOW = 15 * 60
_login_by_ip = rate_limit.SlidingWindow(20, LOGIN_WINDOW)
_login_by_identity = rate_limit.SlidingWindow(8, LOGIN_WINDOW)


@router.post("/login")
def auth_login(payload: LoginIn, request: Request, response: Response) -> dict[str, Any]:
    """Вход по логину или почте и паролю: лимиты попыток, проверка пароля (соцсетью или по хешу в базе),
    бан — только тому, кто знает пароль; удачный вход ставит cookie сессии."""
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
        sessions.set_session_cookie(response, token)
        return {"ok": True, "user": user}
    row = accounts.lookup_user(identity)
    # Блокировку называем только тому, кто знает пароль: иначе форма выдаёт, что аккаунт есть
    if not row or not passwords.verify_password(payload.password, row["hash_col"], row.get("salt_col")):
        _login_by_ip.hit(ip)
        _login_by_identity.hit(identity_key)
        raise HTTPException(401, "invalid credentials")
    _login_by_identity.reset(identity_key)
    schema = accounts.introspect_schema()
    if accounts.is_banned(schema.get("status_col"), row.get("status_col")):
        raise HTTPException(403, "account is blocked")
    user = accounts.serialize_user(row)
    token = sessions.create_session(user["id"], request)
    sessions.set_session_cookie(response, token)
    return {"ok": True, "user": user}


@router.post("/logout")
def auth_logout(
    request: Request,
    response: Response,
    token: str | None = Cookie(default=None, alias=sessions.COOKIE_NAME),
) -> dict[str, Any]:
    """Выход: сессия соцсети отзывается, вкладки переподключаются гостями, сессия закрывается, cookie снимается."""
    if token:
        social_login.end_session(token, rate_limit.client_ip(request), request.headers.get("user-agent") or "")
        # Вкладки этой сессии переподключаются гостями
        realtime.drop_session(token)
        try:
            sessions.revoke_session(token)
        except HTTPException:
            pass
    sessions.clear_session_cookie(response)
    return {"ok": True}


@router.get("/me")
def auth_me(user: dict[str, Any] | None = Depends(sessions.current_user_optional)) -> dict[str, Any]:
    """Кто вошёл: {authenticated, user}."""
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
