# -*- coding: utf-8 -*-
"""
AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Клиент API v2 Anime Social (документация — socialnetwork.local/docs/api).

Адрес — сайт соцсети из animesocial.json плюс /api/v2, AV_AS_API_URL его заменяет.
Ответ приходит обёрткой {"ok": true, "data": …} или {"ok": false, "error": {code,
message}}; ошибка поднимается как ASApiError с постоянным машинным кодом.

AsoPlay — доверенный сайт-клиент (socialnetwork.local/docs/api/clients.md): каждый
запрос идёт с ключами X-Client-Id / X-Client-Secret (AV_AS_CLIENT_ID, AV_AS_CLIENT_SECRET),
а запрос по действию человека — ещё с его адресом и браузером: по ним соцсеть
применяет баны и лимиты и называет сессию «AsoPlay — Chrome, Windows».
"""
from __future__ import annotations

import functools
import logging
import os
import ssl
from typing import Any

import httpx

from server import animesocial_config

log = logging.getLogger("asoplay.as_api")

_TIMEOUT = httpx.Timeout(10.0, connect=5.0)


class ASApiError(Exception):
    def __init__(self, status: int, code: str, message: str = "", retry_after: int = 0):
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        # Через сколько секунд повторять — Retry-After соцсети при rate_limited и maintenance
        self.retry_after = retry_after


def base_url() -> str:
    override = os.environ.get("AV_AS_API_URL", "").strip().rstrip("/")
    return override or animesocial_config.site_url() + "/api/v2"


def site_url() -> str:
    """Сайт той же соцсети, что и API: страницы входа и ссылки «войти на сайт» — на нём."""
    return base_url().removesuffix("/api/v2")


def _client_keys() -> tuple[str, str]:
    return (os.environ.get("AV_AS_CLIENT_ID", "").strip(), os.environ.get("AV_AS_CLIENT_SECRET", "").strip())


def configured() -> bool:
    client_id, secret = _client_keys()
    return bool(client_id and secret)


def client_id() -> str:
    return _client_keys()[0]


@functools.lru_cache(maxsize=4)
def _tls(ca: str) -> ssl.SSLContext | bool:
    """Сертификат соцсети сверяется с набором certifi; AV_AS_CA — свой файл CA или system — хранилище
    системы (ему доверяет самоподписанный сертификат локального движка в OSPanel)."""
    if not ca:
        return True
    if ca.lower() == "system":
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=ca)


def call(method: str, path: str, *, token: str | None = None, body: dict[str, Any] | None = None,
         user_ip: str = "", user_agent: str = "") -> Any:
    client, secret = _client_keys()
    headers = {"Accept": "application/json", "User-Agent": "AsoPlay", "X-Client-Id": client, "X-Client-Secret": secret}
    if user_ip:
        headers["X-Client-User-IP"] = user_ip
    if user_agent:
        headers["X-Client-User-Agent"] = user_agent[:500]
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with httpx.Client(timeout=_TIMEOUT, verify=_tls(os.environ.get("AV_AS_CA", "").strip())) as client:
            resp = client.request(method, base_url() + path, json=body, headers=headers)
    except (httpx.HTTPError, OSError) as exc:
        raise ASApiError(503, "unavailable", str(exc)) from exc
    try:
        payload = resp.json()
    except ValueError:
        raise ASApiError(resp.status_code or 502, "bad_response", resp.text[:200]) from None
    if isinstance(payload, dict) and payload.get("ok") is True:
        return payload.get("data")
    error = payload.get("error") if isinstance(payload, dict) else None
    error = error if isinstance(error, dict) else {}
    try:
        retry_after = max(0, int(resp.headers.get("retry-after") or 0))
    except ValueError:
        retry_after = 0
    raise ASApiError(resp.status_code, str(error.get("code") or "error"), str(error.get("message") or ""), retry_after)


def password_login(email: str, password: str, user_ip: str = "", user_agent: str = "") -> dict[str, Any]:
    """Почта и пароль → {tokens, user}; сессия выдаётся AsoPlay, как после входа через сайт соцсети."""
    return call("POST", "/auth/login", body={"login": email, "password": password}, user_ip=user_ip, user_agent=user_agent)


def exchange_code(code: str, verifier: str, user_ip: str = "", user_agent: str = "") -> dict[str, Any]:
    """Код со страницы входа соцсети → {tokens, user}. Сессию соцсеть называет по браузеру человека."""
    return call("POST", "/auth/web/exchange", body={"code": code, "code_verifier": verifier},
                user_ip=user_ip, user_agent=user_agent)


def refresh(refresh_token: str, user_ip: str = "", user_agent: str = "") -> dict[str, Any]:
    """Новая пара токенов; прежний токен обновления после этого не действует."""
    return call("POST", "/auth/refresh", body={"refresh_token": refresh_token}, user_ip=user_ip, user_agent=user_agent)


def logout(access_token: str, user_ip: str = "", user_agent: str = "") -> None:
    """Завершить сессию устройства AsoPlay в Anime Social (вход на самом сайте остаётся)."""
    call("POST", "/auth/logout", token=access_token, user_ip=user_ip, user_agent=user_agent)


def web_login(access_token: str, path: str, nonce: str = "", user_ip: str = "", user_agent: str = "") -> dict[str, Any]:
    """Одноразовая ссылка на сайт соцсети сразу со входом: {url, expires_in}."""
    body = {"path": path, **({"nonce": nonce} if nonce else {})}
    return call("POST", "/me/web-login", token=access_token, body=body, user_ip=user_ip, user_agent=user_agent)
