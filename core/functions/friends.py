"""
Друзья из Anime Social и вызовы соцсети от имени человека: id друзей с кешем, люди по id, статусы в списках.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Список друзей соцсеть отдаёт по токену сессии соцсети этого человека (core/classes/as_sessions.py), кеш — на
FRIENDS_TTL секунд. social_call — запрос от имени человека с переводом отказов соцсети в ответы AsoPlay; его
зовут разделы друзей и «Написать» (core/api/sections/friends.py, messages.py).
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

from fastapi import HTTPException, Request

from core.classes import as_api, as_sessions, db
from core.classes.db import connect
from core.config import animesocial
from core.functions import rate_limit

log = logging.getLogger("asoplay.presence")

FRIENDS_TTL = 120
# GET /users принимает до 1000 id, но адрес в 8 КБ режет nginx — просим по 500
_USERS_CHUNK = 500
_ALIAS = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


_friend_cache: dict[int, tuple[float, list[int]]] = {}
_friend_lock = threading.Lock()


def positive_ids(values: Any) -> list[int]:
    """Из списка — положительные целые id; всё остальное отбрасывается."""
    out = []
    for value in values if isinstance(values, list) else []:
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.append(n)
    return out


# Кому ещё нужны свежие id друзей (core/functions/realtime.py: кому раздавать события присутствия)
friend_listeners: list[Callable[[int, list[int]], None]] = []


def friend_ids(user_id: int, token: str, user_ip: str, user_agent: str) -> list[int]:
    """Id друзей человека в соцсети: из кеша на FRIENDS_TTL секунд или запросом;
    свежие id — подписчикам friend_listeners."""
    now = time.monotonic()
    with _friend_lock:
        hit = _friend_cache.get(user_id)
    if hit and hit[0] > now:
        return hit[1]
    data = as_api.call("GET", f"/users/{user_id}/friends/ids", token=token, user_ip=user_ip, user_agent=user_agent)
    ids = positive_ids((data or {}).get("ids") if isinstance(data, dict) else None)
    with _friend_lock:
        if len(_friend_cache) > 5000:
            for uid in [u for u, (until, _) in _friend_cache.items() if until <= now]:
                del _friend_cache[uid]
        _friend_cache[user_id] = (now + FRIENDS_TTL, ids)
    for listener in friend_listeners:
        listener(user_id, ids)
    return ids


def forget_friends(user_id: int) -> None:
    """Дружба изменилась (действие отсюда или событие соцсети): следующий список — из соцсети, не из кеша."""
    with _friend_lock:
        _friend_cache.pop(user_id, None)


def people(ids: list[int], token: str, user_ip: str, user_agent: str) -> list[dict[str, Any]]:
    """Пользователи соцсети по id (порядок — как в запросе), без удалённых и заблокированных."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), _USERS_CHUNK):
        chunk = ids[i:i + _USERS_CHUNK]
        data = as_api.call("GET", "/users?ids=" + ",".join(map(str, chunk)), token=token,
                           user_ip=user_ip, user_agent=user_agent)
        items = (data or {}).get("items") if isinstance(data, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and positive_ids([item.get("id")]) and not item.get("deactivated"):
                out.append(item)
    return out


def person(user: dict[str, Any]) -> dict[str, Any]:
    """Человек соцсети для ответа: имя, аватар, пол, путь профиля на AsoPlay и ссылка в соцсеть."""
    uid = int(user["id"])
    alias = str(user.get("alias") or "")
    avatar = user.get("avatar") if isinstance(user.get("avatar"), dict) else {}
    name = " ".join(str(user.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
    return {
        "id": uid,
        "name": name or f"id{uid}",
        "avatar": avatar.get("medium") or avatar.get("small") or None,
        "sex": user.get("sex") if user.get("sex") in (0, 1, 2) else 0,
        "profile_path": "/@" + (alias if _ALIAS.match(alias) else f"profile{uid}"),
        "social_url": animesocial.profile_url(uid),
        "verified": bool(user.get("verified")),
        "elite": bool(user.get("elite")),
    }


def list_statuses(mal_id: int, user_ids: Iterable[int]) -> dict[int, str]:
    """Статусы тайтла в списках этих людей: {user_id: status}."""
    out: dict[int, str] = {}
    chunks = list(db.chunks(user_ids))
    if not chunks:
        return out
    with connect() as conn:
        cur = conn.cursor()
        for chunk in chunks:
            cur.execute(
                f"""SELECT user_id, status FROM aviev_user_lists
                    WHERE mal_id = %s AND status IS NOT NULL AND user_id IN ({', '.join(['%s'] * len(chunk))})""",
                (mal_id, *chunk),
            )
            out.update({int(uid): str(status) for uid, status in cur.fetchall()})
    return out


def social_session(request: Request, session: str | None) -> tuple[str | None, str, str]:
    """Токен сессии соцсети этого браузера, адрес и браузер человека; без связи с соцсетью — None."""
    user_ip, user_agent = rate_limit.client_ip(request), request.headers.get("user-agent") or ""
    token = as_sessions.access_token(session, user_ip, user_agent) if session and as_sessions.enabled() else None
    return token, user_ip, user_agent


# Отказы соцсети, которые человеку объясняются словами (detail — код соцсети)
_REFUSALS = {"privacy", "friends", "blacklist", "blacklisted", "blocked", "banned", "peer_banned", "self", "not_found",
             "already_friends", "already_sent", "incoming_request", "validation_failed"}


def social_call(request: Request, session: str | None, method: str, path: str,
                 body: dict[str, Any] | None = None) -> Any:
    """Запрос к соцсети токеном её сессии этого человека. Связи с соцсетью нет или её сессия больше не
    работает — 409; отказ соцсети — его код в detail (403/404/409); лимит — 429 с её Retry-After."""
    token, user_ip, user_agent = social_session(request, session)
    if not token:
        raise HTTPException(409, "not linked to Anime Social")
    try:
        return as_api.call(method, path, token=token, body=body, user_ip=user_ip, user_agent=user_agent)
    except as_api.ASApiError as exc:
        if session and as_sessions.forget_if_dead(session, exc):
            raise HTTPException(409, "not linked to Anime Social") from exc
        if exc.code == "rate_limited":
            raise HTTPException(429, "rate_limited", headers={"Retry-After": str(exc.retry_after or 60)}) from exc
        if exc.code in _REFUSALS:
            raise HTTPException(exc.status if exc.status in (400, 403, 404, 409) else 403, exc.code) from exc
        log.warning("Anime Social request %s %s failed: %s %s", method, path.split("?")[0], exc.status, exc.code)
        raise HTTPException(503, "Anime Social is unavailable") from exc
