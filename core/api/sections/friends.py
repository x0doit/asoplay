"""
Друзья из Anime Social (socialnetwork.local/docs/api/clients.md, разделы 9–10).

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

  GET  /account/friends             → друзья из соцсети: кто на AsoPlay и что смотрит, кто в сети
                                      в соцсети, кто когда был; сколько входящих и исходящих заявок
  GET  /account/friends/title/{id}  → друзья на этом тайтле: кто смотрит его сейчас, у кого он в списках
  GET  /account/friends/requests    → заявки: входящие или исходящие (?type=)
  GET  /account/friends/suggestions → «Возможные друзья»
  POST /account/friends/{id}[/accept|/decline], DELETE /account/friends/{id}
                                    → добавить, принять, отклонить, отменить заявку, удалить из друзей

Модуль друзей импортирован как friend_data: маршрут friends() иначе перепривязал бы имя модуля.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request

from core.classes import as_api, as_sessions
from core.functions import friends as friend_data, presence, privacy as privacy_rules, sessions
from core.functions.sessions import current_user_required

log = logging.getLogger("asoplay.presence")

router = APIRouter(prefix="/account")

_TITLE_FRIENDS_MAX = 30
_SOCIAL_WHERE = ("site", "mobile")
_STATUS_ORDER = {"watching": 0, "completed": 1, "postponed": 2, "planned": 3, "dropped": 4}


def _social_failure(session: str | None, exc: as_api.ASApiError, unlinked: dict[str, Any]) -> dict[str, Any]:
    """Сбой соцсети на списке друзей: её сессия умерла — ответ «нет связи»; иначе — 503."""
    if session and as_sessions.forget_if_dead(session, exc):
        return unlinked
    log.warning("Anime Social friends request failed: %s %s", exc.status, exc.code)
    raise HTTPException(503, "AnimeSocial не отвечает") from exc


def _rank(item: dict[str, Any]) -> tuple:
    """Порядок в списке друзей: смотрит, на AsoPlay, в сети в соцсети, остальные — кто был недавно."""
    group = 0 if item["watching"] else 1 if item["on_asoplay"] else 2 if item["social"] else 3
    return (group, -(item["last_seen"] or 0) if group == 3 else 0, item["name"].casefold())


@router.get("/friends")
def friends(request: Request, user: dict[str, Any] = Depends(current_user_required),
            session: str | None = Cookie(default=None, alias=sessions.COOKIE_NAME)) -> dict[str, Any]:
    """Друзья из соцсети: кто на AsoPlay и что смотрит, кто в сети в соцсети, кто когда был; счётчики заявок."""
    unlinked = {"connected": False, "items": [], "total": 0, "online": 0}
    token, user_ip, user_agent = friend_data.social_session(request, session)
    if not token:
        return unlinked
    try:
        people = friend_data.people(friend_data.friend_ids(int(user["id"]), token, user_ip, user_agent),
                                    token, user_ip, user_agent)
    except as_api.ASApiError as exc:
        return _social_failure(session, exc, unlinked)
    marks = presence.presence.current()
    here = [int(p["id"]) for p in people if int(p["id"]) in marks]
    privacy = privacy_rules.for_users(here)
    titles = presence.title_cards(marks[uid].mal_id for uid in here if privacy.get(uid, privacy_rules.OPEN).show_watching)
    items = []
    for person in people:
        uid = int(person["id"])
        mark, rules = marks.get(uid), privacy.get(uid, privacy_rules.OPEN)
        where = person.get("online_where")
        last_seen = person.get("last_seen")
        items.append({
            **friend_data.person(person),
            "on_asoplay": mark is not None and not rules.hide_presence,
            "watching": presence.watching(mark, rules, titles),
            "social": where if where in _SOCIAL_WHERE else None,
            "last_seen": last_seen if isinstance(last_seen, int) and not isinstance(last_seen, bool) else None,
        })
    items.sort(key=_rank)
    requests = {}
    for kind in ("incoming", "outgoing"):
        try:
            page = as_api.call("GET", f"/friends/requests?type={kind}&limit=1", token=token,
                               user_ip=user_ip, user_agent=user_agent)
            requests[kind] = int((page or {}).get("total") or 0) if isinstance(page, dict) else 0
        except as_api.ASApiError as exc:
            log.info("Anime Social friend requests count failed: %s %s", exc.status, exc.code)
            requests[kind] = 0
    return {"connected": True, "items": items, "total": len(items),
            "online": sum(1 for i in items if i["on_asoplay"] or i["social"]), "requests": requests}


@router.get("/friends/title/{mal_id}")
def friends_on_title(mal_id: int, request: Request, user: dict[str, Any] = Depends(current_user_required),
                     session: str | None = Cookie(default=None, alias=sessions.COOKIE_NAME)) -> dict[str, Any]:
    """Друзья на странице тайтла: кто смотрит его прямо сейчас (если разрешил) и у кого он в списках
    (если списки не скрыты)."""
    if not 0 < mal_id < presence.MAL_MAX:
        raise HTTPException(400, "bad mal_id")
    unlinked = {"connected": False, "items": [], "total": 0}
    token, user_ip, user_agent = friend_data.social_session(request, session)
    if not token:
        return unlinked
    try:
        friend_ids = friend_data.friend_ids(int(user["id"]), token, user_ip, user_agent)
    except as_api.ASApiError as exc:
        return _social_failure(session, exc, unlinked)
    marks = presence.presence.current()
    live = {uid for uid in friend_ids if uid in marks and marks[uid].mal_id == mal_id}
    statuses = friend_data.list_statuses(mal_id, friend_ids)
    privacy = privacy_rules.for_users(live | set(statuses))
    picked: dict[int, tuple[bool, str | None]] = {}
    for uid in live | set(statuses):
        rules = privacy.get(uid, privacy_rules.OPEN)
        now = uid in live and not rules.hide_presence and rules.show_watching
        status = None if rules.hide_lists else statuses.get(uid)
        if now or status:
            picked[uid] = (now, status)
    if not picked:
        return {"connected": True, "items": [], "total": 0}
    try:
        people = friend_data.people(sorted(picked), token, user_ip, user_agent)
    except as_api.ASApiError as exc:
        return _social_failure(session, exc, unlinked)
    items = [{**friend_data.person(p), "now": picked[int(p["id"])][0], "status": picked[int(p["id"])][1]}
             for p in people if int(p["id"]) in picked]
    items.sort(key=lambda i: (not i["now"], _STATUS_ORDER.get(i["status"] or "", 9), i["name"].casefold()))
    return {"connected": True, "items": items[:_TITLE_FRIENDS_MAX], "total": len(items)}


def _listed(user: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Человек из заявок и «Возможных друзей»: как друг в списке плюс город и что ещё прислала соцсеть."""
    location = user.get("location")
    return {**friend_data.person(user), "location": location if isinstance(location, str) and location else None,
            **extra}


@router.get("/friends/requests")
def friend_requests(request: Request, type: str = "incoming", cursor: str = "",
                    user: dict[str, Any] = Depends(current_user_required),
                    session: str | None = Cookie(default=None, alias=sessions.COOKIE_NAME)) -> dict[str, Any]:
    """Вкладки «Заявки» и «Исходящие» — новые сверху, по 30."""
    if type not in ("incoming", "outgoing"):
        raise HTTPException(400, "type must be incoming or outgoing")
    data = friend_data.social_call(request, session, "GET", f"/friends/requests?type={type}&limit=30"
                        + (f"&cursor={quote(cursor[:200])}" if cursor else ""))
    data = data if isinstance(data, dict) else {}
    items = [_listed(u, requested_at=u.get("requested_at") if isinstance(u.get("requested_at"), int) else None)
             for u in data.get("items") or []
             if isinstance(u, dict) and friend_data.positive_ids([u.get("id")]) and not u.get("deactivated")]
    return {"items": items, "next_cursor": data.get("next_cursor") or None,
            "total": data.get("total") if isinstance(data.get("total"), int) else len(items)}


@router.get("/friends/suggestions")
def friend_suggestions(request: Request, user: dict[str, Any] = Depends(current_user_required),
                       session: str | None = Cookie(default=None, alias=sessions.COOKIE_NAME)) -> dict[str, Any]:
    """«Возможные друзья», как на странице друзей соцсети: пять человек."""
    data = friend_data.social_call(request, session, "GET", "/friends/suggestions?limit=5")
    items = (data or {}).get("items") if isinstance(data, dict) else None
    return {"items": [_listed(u, mutual=int(u.get("mutual") or 0)) for u in items or []
                      if isinstance(u, dict) and friend_data.positive_ids([u.get("id")]) and not u.get("deactivated")]}


def _friend_action(request: Request, session: str | None, user: dict[str, Any], method: str, peer_id: int,
                   suffix: str = "") -> dict[str, Any]:
    """Действие с дружбой от имени человека; кеш друзей сбрасывается."""
    if not 0 < peer_id < 2 ** 31 or peer_id == int(user["id"]):
        raise HTTPException(400, "bad user")
    result = friend_data.social_call(request, session, method, f"/friends/{peer_id}{suffix}")
    friend_data.forget_friends(int(user["id"]))
    result = result if isinstance(result, dict) else {}
    return {k: result.get(k) for k in ("status", "friends_count", "friend_requests")}


@router.post("/friends/{peer_id}")
def friend_add(peer_id: int, request: Request, user: dict[str, Any] = Depends(current_user_required),
               session: str | None = Cookie(default=None, alias=sessions.COOKIE_NAME)) -> dict[str, Any]:
    """«Добавить в друзья»: заявка; лимит 50 в сутки и чёрный список — как на сайте соцсети."""
    return _friend_action(request, session, user, "POST", peer_id)


@router.post("/friends/{peer_id}/accept")
def friend_accept(peer_id: int, request: Request, user: dict[str, Any] = Depends(current_user_required),
                  session: str | None = Cookie(default=None, alias=sessions.COOKIE_NAME)) -> dict[str, Any]:
    """«Принять заявку»."""
    return _friend_action(request, session, user, "POST", peer_id, "/accept")


@router.post("/friends/{peer_id}/decline")
def friend_decline(peer_id: int, request: Request, user: dict[str, Any] = Depends(current_user_required),
                   session: str | None = Cookie(default=None, alias=sessions.COOKIE_NAME)) -> dict[str, Any]:
    """«Отклонить заявку»."""
    return _friend_action(request, session, user, "POST", peer_id, "/decline")


@router.delete("/friends/{peer_id}")
def friend_remove(peer_id: int, request: Request, user: dict[str, Any] = Depends(current_user_required),
                  session: str | None = Cookie(default=None, alias=sessions.COOKIE_NAME)) -> dict[str, Any]:
    """«Отменить заявку» и «Удалить из друзей»."""
    return _friend_action(request, session, user, "DELETE", peer_id)
