# -*- coding: utf-8 -*-
"""
AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Присутствие на AsoPlay и друзья из Anime Social (socialnetwork.local/docs/api/clients.md, разделы 9–10).

  GET  /account/friends             → друзья из соцсети: кто на AsoPlay и что смотрит, кто в сети
                                      в соцсети, кто когда был; сколько входящих и исходящих заявок
  GET  /account/friends/title/{id}  → друзья на этом тайтле: кто смотрит его сейчас, у кого он в списках
  GET  /account/friends/requests    → заявки: входящие или исходящие (?type=)
  GET  /account/friends/suggestions → «Возможные друзья»
  POST /account/friends/{id}[/accept|/decline], DELETE /account/friends/{id}
                                    → добавить, принять, отклонить, отменить заявку, удалить из друзей
  POST /account/messages/{id}       → «Написать»: сообщение в переписку соцсети

Кто на сайте, сообщают вкладки по живому соединению (server/realtime.py): человек на AsoPlay, пока
хоть одна его вкладка открыта и на экране, и ещё GRACE секунд после — перезагрузка страницы не мигает
«вышел». Настройки — в aviev_privacy: hide_presence никому не показывает, что человек здесь,
show_watching (по умолчанию выключено) показывает друзьям, что он смотрит.

Соцсети сервер сообщает присутствие по соединению сервер–сервер (server/as_realtime.py) в момент
изменения; пока соединения нет — запасным пакетом POST /clients/presence раз в минуту. Соцсеть принимает
только тех, у кого есть сессия AsoPlay в ней (вход кнопкой или формой почты).

Всё это — в памяти процесса: бэкенд работает одним процессом, а после перезапуска вкладки
переподключаются сами. Список друзей соцсеть отдаёт по токену сессии соцсети этого человека.
"""
from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable
from urllib.parse import quote

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from server import animesocial, animesocial_config, as_api, as_login, rate_limit
from server.animesocial import connect, current_user_required
from server.title_pages import SITE_URL

log = logging.getLogger("asoplay.presence")

router = APIRouter(prefix="/account")

# Сколько секунд человек ещё на AsoPlay после того, как закрыл или свернул последнюю вкладку
GRACE = 30
REPORT_EVERY = 60
FRIENDS_TTL = 120
# GET /users принимает до 1000 id, но адрес в 8 КБ режет nginx — просим по 500
_USERS_CHUNK = 500
_SQL_CHUNK = 1000
_TITLE_MAX = 100
_TITLE_FRIENDS_MAX = 30
MAL_MAX = 10_000_000
# Управляющие и невидимые символы: в названии они ломали бы строку «смотрит «…»» у друзей
_CONTROL = re.compile(r"[\x00-\x1f\x7f\U0000200b-\U0000200f\U00002028\U00002029\U0000feff]+")
_ALIAS = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_SOCIAL_WHERE = ("site", "mobile")
_STATUS_ORDER = {"watching": 0, "completed": 1, "postponed": 2, "planned": 3, "dropped": 4}


def clean_title(value: Any) -> str:
    return " ".join(_CONTROL.sub(" ", value if isinstance(value, str) else "").split())[:_TITLE_MAX]


# ---------- кто сейчас на сайте ----------
@dataclass(frozen=True)
class Mark:
    at: float
    mal_id: int = 0
    title: str = ""


@dataclass(frozen=True)
class Tab:
    visible: bool
    mal_id: int
    title: str
    at: float


class Presence:
    """Вкладки людей. Вкладка на экране держит человека на AsoPlay; когда последняя закрылась или ушла
    в фон, он остаётся ещё grace секунд с тем, что смотрел. «Смотрит» — у той видимой вкладки, где
    плеер заиграл последним."""

    def __init__(self, grace: float = GRACE, clock: Callable[[], float] = time.monotonic):
        self.grace = grace
        self._clock = clock
        self._tabs: dict[int, dict[int, Tab]] = {}
        self._left: dict[int, Mark] = {}
        self._lock = threading.Lock()

    def tab(self, user_id: int, tab_id: int, visible: bool, mal_id: int = 0, title: str = "") -> None:
        with self._lock:
            was = self._here_locked(user_id)
            self._tabs.setdefault(user_id, {})[tab_id] = Tab(visible, mal_id, title, self._clock())
            self._settle_locked(user_id, was)

    def close(self, user_id: int, tab_id: int) -> None:
        with self._lock:
            was = self._here_locked(user_id)
            tabs = self._tabs.get(user_id, {})
            tabs.pop(tab_id, None)
            if not tabs:
                self._tabs.pop(user_id, None)
            self._settle_locked(user_id, was)

    def _here_locked(self, user_id: int) -> Mark | None:
        shown = [t for t in self._tabs.get(user_id, {}).values() if t.visible]
        if not shown:
            return None
        playing = max((t for t in shown if t.mal_id), key=lambda t: t.at, default=None)
        return Mark(self._clock(), playing.mal_id, playing.title) if playing else Mark(self._clock())

    def _settle_locked(self, user_id: int, was: Mark | None) -> None:
        if self._here_locked(user_id):
            self._left.pop(user_id, None)
        elif was:
            self._left[user_id] = Mark(self._clock(), was.mal_id, was.title)

    def current(self) -> dict[int, Mark]:
        now = self._clock()
        with self._lock:
            for uid in [uid for uid, m in self._left.items() if now - m.at > self.grace]:
                del self._left[uid]
            out = dict(self._left)
            for uid in self._tabs:
                mark = self._here_locked(uid)
                if mark:
                    out[uid] = mark
            return out


presence = Presence()

# ---------- база ----------
@dataclass(frozen=True)
class Privacy:
    hide_presence: bool = False
    show_watching: bool = False
    hide_lists: bool = False


_OPEN = Privacy()


def _chunks(ids: Iterable[int], size: int = _SQL_CHUNK) -> Iterable[list[int]]:
    ordered = sorted(set(ids))
    for i in range(0, len(ordered), size):
        yield ordered[i:i + size]


def _privacy(user_ids: Iterable[int]) -> dict[int, Privacy]:
    """Настройки людей; у кого строки нет — всё по умолчанию (_OPEN)."""
    out: dict[int, Privacy] = {}
    chunks = list(_chunks(user_ids))
    if not chunks:
        return out
    with connect() as conn:
        cur = conn.cursor()
        for chunk in chunks:
            cur.execute(
                f"""SELECT user_id, hide_presence, show_watching, hide_lists FROM aviev_privacy
                    WHERE user_id IN ({', '.join(['%s'] * len(chunk))})""",
                chunk,
            )
            for uid, hide_presence, show_watching, hide_lists in cur.fetchall():
                out[int(uid)] = Privacy(bool(hide_presence), bool(show_watching), bool(hide_lists))
    return out


def _titles(mal_ids: Iterable[int]) -> dict[int, tuple[str, str]]:
    """Название (русское, иначе английское) и постер из кеша страниц тайтлов. Без строки — название из
    браузера и без постера."""
    out: dict[int, tuple[str, str]] = {}
    chunks = list(_chunks(m for m in mal_ids if m))
    if not chunks:
        return out
    with connect() as conn:
        cur = conn.cursor()
        for chunk in chunks:
            cur.execute(
                f"""SELECT mal_id, title_ru, title_en, poster_url FROM aviev_title_pages
                    WHERE mal_id IN ({', '.join(['%s'] * len(chunk))})""",
                chunk,
            )
            for mal_id, title_ru, title_en, poster_url in cur.fetchall():
                title = clean_title(title_ru or title_en or "")
                poster = str(poster_url or "") if str(poster_url or "").startswith(("https://", "http://")) else ""
                if title or poster:
                    out[int(mal_id)] = (title, poster)
    return out


def _list_statuses(mal_id: int, user_ids: Iterable[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    chunks = list(_chunks(user_ids))
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


# ---------- отметки для соцсети ----------
def _watching(mark: Mark | None, privacy: Privacy, titles: dict[int, tuple[str, str]]) -> dict[str, Any] | None:
    if mark is None or not mark.mal_id or privacy.hide_presence or not privacy.show_watching:
        return None
    title, poster = titles.get(mark.mal_id, ("", ""))
    title = title or mark.title
    return {"mal_id": mark.mal_id, "title": title, "poster": poster, "path": f"/anime/{mark.mal_id}"} if title else None


def build_batch() -> dict[str, Any]:
    """Тело POST /clients/presence: кто сейчас на AsoPlay и, с согласия, что смотрит."""
    marks = presence.current()
    privacy = _privacy(marks)
    shown = {uid: m for uid, m in marks.items() if not privacy.get(uid, _OPEN).hide_presence}
    titles = _titles(m.mal_id for uid, m in shown.items() if privacy.get(uid, _OPEN).show_watching)
    watching = []
    for uid, mark in sorted(shown.items()):
        item = _watching(mark, privacy.get(uid, _OPEN), titles)
        if item:
            watching.append({"user_id": uid, "title": item["title"], "url": SITE_URL + item["path"]})
    return {"users": sorted(shown)[:10_000], "watching": watching}


def report_once(reported: bool) -> bool:
    """Отправить пакет; True — в нём кто-то был. Пустой пакет («сейчас никого») уходит один раз —
    после пакета, в котором кто-то был, — дальше молчим."""
    batch = build_batch()
    if batch["users"] or reported:
        as_api.call("POST", "/clients/presence", body=batch)
    return bool(batch["users"])


_task: asyncio.Task | None = None


# Нужен ли сейчас пакет: server/realtime.py отвечает «нет», пока держит соединение сервер–сервер с соцсетью
batch_needed: Callable[[], bool] = lambda: True


async def _report_loop() -> None:
    reported = False
    while True:
        await asyncio.sleep(REPORT_EVERY)
        if not as_login.enabled():
            continue
        if not batch_needed():
            # Снимок при переподключении заменит всё, что соцсеть знала, — пустой пакет после него не нужен
            reported = False
            continue
        try:
            reported = await asyncio.to_thread(report_once, reported)
        except as_api.ASApiError as exc:
            log.warning("Anime Social presence batch failed: %s %s", exc.status, exc.code)
        except Exception:  # noqa: BLE001 — сбой базы не останавливает следующие пакеты
            log.exception("Could not report presence to Anime Social")


async def startup() -> None:
    global _task
    if _task is None:
        _task = asyncio.create_task(_report_loop())


async def shutdown() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None


# ---------- друзья ----------
_friend_cache: dict[int, tuple[float, list[int]]] = {}
_friend_lock = threading.Lock()


def _ids(values: Any) -> list[int]:
    out = []
    for value in values if isinstance(values, list) else []:
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.append(n)
    return out


# Кому ещё нужны свежие id друзей (server/realtime.py: кому раздавать события присутствия)
friend_listeners: list[Callable[[int, list[int]], None]] = []


def _friend_ids(user_id: int, token: str, user_ip: str, user_agent: str) -> list[int]:
    now = time.monotonic()
    with _friend_lock:
        hit = _friend_cache.get(user_id)
    if hit and hit[0] > now:
        return hit[1]
    data = as_api.call("GET", f"/users/{user_id}/friends/ids", token=token, user_ip=user_ip, user_agent=user_agent)
    ids = _ids((data or {}).get("ids") if isinstance(data, dict) else None)
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


def _people(ids: list[int], token: str, user_ip: str, user_agent: str) -> list[dict[str, Any]]:
    """Пользователи соцсети по id (порядок — как в запросе), без удалённых и заблокированных."""
    out: list[dict[str, Any]] = []
    for i in range(0, len(ids), _USERS_CHUNK):
        chunk = ids[i:i + _USERS_CHUNK]
        data = as_api.call("GET", "/users?ids=" + ",".join(map(str, chunk)), token=token,
                           user_ip=user_ip, user_agent=user_agent)
        items = (data or {}).get("items") if isinstance(data, dict) else None
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and _ids([item.get("id")]) and not item.get("deactivated"):
                out.append(item)
    return out


def _person(user: dict[str, Any]) -> dict[str, Any]:
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
        "social_url": animesocial_config.profile_url(uid),
        "verified": bool(user.get("verified")),
        "elite": bool(user.get("elite")),
    }


def _social_session(request: Request, session: str | None) -> tuple[str | None, str, str]:
    """Токен сессии соцсети этого браузера, адрес и браузер человека; без связи с соцсетью — None."""
    user_ip, user_agent = rate_limit.client_ip(request), request.headers.get("user-agent") or ""
    token = as_login.access_token(session, user_ip, user_agent) if session and as_login.enabled() else None
    return token, user_ip, user_agent


def _social_failure(session: str | None, exc: as_api.ASApiError, unlinked: dict[str, Any]) -> dict[str, Any]:
    if session and as_login.forget_if_dead(session, exc):
        return unlinked
    log.warning("Anime Social friends request failed: %s %s", exc.status, exc.code)
    raise HTTPException(503, "AnimeSocial не отвечает") from exc


def _rank(item: dict[str, Any]) -> tuple:
    group = 0 if item["watching"] else 1 if item["on_asoplay"] else 2 if item["social"] else 3
    return (group, -(item["last_seen"] or 0) if group == 3 else 0, item["name"].casefold())


@router.get("/friends")
def friends(request: Request, user: dict[str, Any] = Depends(current_user_required),
            session: str | None = Cookie(default=None, alias=animesocial.COOKIE_NAME)) -> dict[str, Any]:
    unlinked = {"connected": False, "items": [], "total": 0, "online": 0}
    token, user_ip, user_agent = _social_session(request, session)
    if not token:
        return unlinked
    try:
        people = _people(_friend_ids(int(user["id"]), token, user_ip, user_agent), token, user_ip, user_agent)
    except as_api.ASApiError as exc:
        return _social_failure(session, exc, unlinked)
    marks = presence.current()
    here = [int(p["id"]) for p in people if int(p["id"]) in marks]
    privacy = _privacy(here)
    titles = _titles(marks[uid].mal_id for uid in here if privacy.get(uid, _OPEN).show_watching)
    items = []
    for person in people:
        uid = int(person["id"])
        mark, rules = marks.get(uid), privacy.get(uid, _OPEN)
        where = person.get("online_where")
        last_seen = person.get("last_seen")
        items.append({
            **_person(person),
            "on_asoplay": mark is not None and not rules.hide_presence,
            "watching": _watching(mark, rules, titles),
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
                     session: str | None = Cookie(default=None, alias=animesocial.COOKIE_NAME)) -> dict[str, Any]:
    """Друзья на странице тайтла: кто смотрит его прямо сейчас (если разрешил) и у кого он в списках
    (если списки не скрыты)."""
    if not 0 < mal_id < MAL_MAX:
        raise HTTPException(400, "bad mal_id")
    unlinked = {"connected": False, "items": [], "total": 0}
    token, user_ip, user_agent = _social_session(request, session)
    if not token:
        return unlinked
    try:
        friend_ids = _friend_ids(int(user["id"]), token, user_ip, user_agent)
    except as_api.ASApiError as exc:
        return _social_failure(session, exc, unlinked)
    marks = presence.current()
    live = {uid for uid in friend_ids if uid in marks and marks[uid].mal_id == mal_id}
    statuses = _list_statuses(mal_id, friend_ids)
    privacy = _privacy(live | set(statuses))
    picked: dict[int, tuple[bool, str | None]] = {}
    for uid in live | set(statuses):
        rules = privacy.get(uid, _OPEN)
        now = uid in live and not rules.hide_presence and rules.show_watching
        status = None if rules.hide_lists else statuses.get(uid)
        if now or status:
            picked[uid] = (now, status)
    if not picked:
        return {"connected": True, "items": [], "total": 0}
    try:
        people = _people(sorted(picked), token, user_ip, user_agent)
    except as_api.ASApiError as exc:
        return _social_failure(session, exc, unlinked)
    items = [{**_person(p), "now": picked[int(p["id"])][0], "status": picked[int(p["id"])][1]}
             for p in people if int(p["id"]) in picked]
    items.sort(key=lambda i: (not i["now"], _STATUS_ORDER.get(i["status"] or "", 9), i["name"].casefold()))
    return {"connected": True, "items": items[:_TITLE_FRIENDS_MAX], "total": len(items)}


# ---------- действия от имени человека: дружба, «Написать» ----------
# Отказы соцсети, которые человеку объясняются словами (detail — код соцсети)
_REFUSALS = {"privacy", "friends", "blacklist", "blacklisted", "blocked", "banned", "peer_banned", "self", "not_found",
             "already_friends", "already_sent", "incoming_request", "validation_failed"}


def _social_call(request: Request, session: str | None, method: str, path: str,
                 body: dict[str, Any] | None = None) -> Any:
    """Запрос к соцсети токеном её сессии этого человека. Связи с соцсетью нет или её сессия больше не
    работает — 409; отказ соцсети — его код в detail (403/404/409); лимит — 429 с её Retry-After."""
    token, user_ip, user_agent = _social_session(request, session)
    if not token:
        raise HTTPException(409, "not linked to Anime Social")
    try:
        return as_api.call(method, path, token=token, body=body, user_ip=user_ip, user_agent=user_agent)
    except as_api.ASApiError as exc:
        if session and as_login.forget_if_dead(session, exc):
            raise HTTPException(409, "not linked to Anime Social") from exc
        if exc.code == "rate_limited":
            raise HTTPException(429, "rate_limited", headers={"Retry-After": str(exc.retry_after or 60)}) from exc
        if exc.code in _REFUSALS:
            raise HTTPException(exc.status if exc.status in (400, 403, 404, 409) else 403, exc.code) from exc
        log.warning("Anime Social request %s %s failed: %s %s", method, path.split("?")[0], exc.status, exc.code)
        raise HTTPException(503, "Anime Social is unavailable") from exc


def _listed(user: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """Человек из заявок и «Возможных друзей»: как друг в списке плюс город и что ещё прислала соцсеть."""
    location = user.get("location")
    return {**_person(user), "location": location if isinstance(location, str) and location else None, **extra}


@router.get("/friends/requests")
def friend_requests(request: Request, type: str = "incoming", cursor: str = "",
                    user: dict[str, Any] = Depends(current_user_required),
                    session: str | None = Cookie(default=None, alias=animesocial.COOKIE_NAME)) -> dict[str, Any]:
    """Вкладки «Заявки» и «Исходящие» — новые сверху, по 30."""
    if type not in ("incoming", "outgoing"):
        raise HTTPException(400, "type must be incoming or outgoing")
    data = _social_call(request, session, "GET", f"/friends/requests?type={type}&limit=30"
                        + (f"&cursor={quote(cursor[:200])}" if cursor else ""))
    data = data if isinstance(data, dict) else {}
    items = [_listed(u, requested_at=u.get("requested_at") if isinstance(u.get("requested_at"), int) else None)
             for u in data.get("items") or [] if isinstance(u, dict) and _ids([u.get("id")]) and not u.get("deactivated")]
    return {"items": items, "next_cursor": data.get("next_cursor") or None,
            "total": data.get("total") if isinstance(data.get("total"), int) else len(items)}


@router.get("/friends/suggestions")
def friend_suggestions(request: Request, user: dict[str, Any] = Depends(current_user_required),
                       session: str | None = Cookie(default=None, alias=animesocial.COOKIE_NAME)) -> dict[str, Any]:
    """«Возможные друзья», как на странице друзей соцсети: пять человек."""
    data = _social_call(request, session, "GET", "/friends/suggestions?limit=5")
    items = (data or {}).get("items") if isinstance(data, dict) else None
    return {"items": [_listed(u, mutual=int(u.get("mutual") or 0)) for u in items or []
                      if isinstance(u, dict) and _ids([u.get("id")]) and not u.get("deactivated")]}


def _friend_action(request: Request, session: str | None, user: dict[str, Any], method: str, peer_id: int,
                   suffix: str = "") -> dict[str, Any]:
    if not 0 < peer_id < 2 ** 31 or peer_id == int(user["id"]):
        raise HTTPException(400, "bad user")
    result = _social_call(request, session, method, f"/friends/{peer_id}{suffix}")
    forget_friends(int(user["id"]))
    result = result if isinstance(result, dict) else {}
    return {k: result.get(k) for k in ("status", "friends_count", "friend_requests")}


@router.post("/friends/{peer_id}")
def friend_add(peer_id: int, request: Request, user: dict[str, Any] = Depends(current_user_required),
               session: str | None = Cookie(default=None, alias=animesocial.COOKIE_NAME)) -> dict[str, Any]:
    """«Добавить в друзья»: заявка; лимит 50 в сутки и чёрный список — как на сайте соцсети."""
    return _friend_action(request, session, user, "POST", peer_id)


@router.post("/friends/{peer_id}/accept")
def friend_accept(peer_id: int, request: Request, user: dict[str, Any] = Depends(current_user_required),
                  session: str | None = Cookie(default=None, alias=animesocial.COOKIE_NAME)) -> dict[str, Any]:
    return _friend_action(request, session, user, "POST", peer_id, "/accept")


@router.post("/friends/{peer_id}/decline")
def friend_decline(peer_id: int, request: Request, user: dict[str, Any] = Depends(current_user_required),
                   session: str | None = Cookie(default=None, alias=animesocial.COOKIE_NAME)) -> dict[str, Any]:
    return _friend_action(request, session, user, "POST", peer_id, "/decline")


@router.delete("/friends/{peer_id}")
def friend_remove(peer_id: int, request: Request, user: dict[str, Any] = Depends(current_user_required),
                  session: str | None = Cookie(default=None, alias=animesocial.COOKIE_NAME)) -> dict[str, Any]:
    """«Отменить заявку» и «Удалить из друзей»."""
    return _friend_action(request, session, user, "DELETE", peer_id)


class MessageIn(BaseModel):
    text: str = Field(..., max_length=8000)


@router.post("/messages/{peer_id}")
def send_message(peer_id: int, payload: MessageIn, request: Request,
                 user: dict[str, Any] = Depends(current_user_required),
                 session: str | None = Cookie(default=None, alias=animesocial.COOKIE_NAME)) -> dict[str, Any]:
    """«Написать» у друга: сообщение уходит в переписку Anime Social, как из окна «Написать» на сайте
    соцсети. Кто может писать и антифлуд (40 сообщений в минуту) — по правилам соцсети."""
    text = payload.text.strip()
    if not 0 < peer_id < 2 ** 31 or not text or len(text) > 4000:
        raise HTTPException(400, "bad message")
    _social_call(request, session, "POST", f"/dialogs/{peer_id}/messages", {"text": text})
    # Переписка с этим человеком на сайте соцсети: /im?uid= ведёт в /dialogs?uid=
    return {"ok": True, "dialog_url": f"{animesocial_config.path_for('messages')}?uid={peer_id}"}
