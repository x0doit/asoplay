# -*- coding: utf-8 -*-
"""
AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Живое соединение браузера с AsoPlay — WebSocket /ws — вместо опроса сервера по таймеру.

  браузер → сервер  {"type": "visible", "value": true}                  вкладка на экране / в фоне
                    {"type": "watching", "mal_id": 52991, "title": "…"}  плеер заиграл; mal_id null — перестал
                    {"type": "ping"}                                     → {"type": "pong"}
  сервер → браузер  {"type": "hello", "user_id": 17 | null, "status": {...}}   сразу после подключения
                    {"type": "status", "status": {...}}                 изменились источники или VPN
                    {"type": "presence", "user": {...}, "on_asoplay": true, "watching": {...} | null}
                    {"type": "social_presence", "user_id": 17, "online_where": "site", "last_seen": …}  друг в соцсети
                    {"type": "friendship", "user_id": 17, "status": "friends"}   изменилась дружба

Присутствие — по соединению (core/functions/presence.py, Presence): человек на AsoPlay, пока хоть одна его
вкладка открыта и на экране. Событие о нём сразу уходит его друзьям из Anime Social, которые сейчас на
сайте, и самой соцсети (core/classes/as_realtime.py) — с учётом его настроек (hide_presence, show_watching).
Обратно соцсеть присылает события людям AsoPlay: друг зашёл в соцсеть или вышел, изменилась дружба,
страницу заблокировали. Состояние сервера (`status` — то, что раньше вкладки спрашивали у /health раз в
30 секунд) приходит при подключении и при изменении.

Подключаться можно только со страниц самого AsoPlay (заголовок Origin): иначе чужой сайт по cookie
человека слушал бы, кто из его друзей где. Обрыв соединения и «вышел» сервер замечает по ping-кадрам
WebSocket (uvicorn, раз в 25 секунд) — других регулярных запросов нет.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.classes import as_realtime, db
from core.config import animesocial
from core.functions import presence, rate_limit, social_login
from core.config.config import SITE_URL

log = logging.getLogger("asoplay.realtime")

router = APIRouter()

MAX_FRAME = 4096
# Не больше 30 кадров за 10 секунд от вкладки; лишние не обрабатываются
FRAME_LIMIT, FRAME_WINDOW = 30, 10.0
MAX_TABS_PER_USER = 20
MAX_GUESTS_PER_IP = 30
SWEEP_EVERY = 5
# Раз в 5 минут — сверка сессий: выход в другой вкладке или с другого устройства закрывает соединение
SESSION_CHECK_EVERY = 300
PRIVACY_TTL = 60
SEND_TIMEOUT = 5

# Задаёт core/core.py: состояние сервера для вкладок и адреса страниц, которым можно подключаться
status_provider: Callable[[], dict[str, Any]] = lambda: {"ok": True, "sources": [], "vpn": False}
extra_origins: set[str] = set()


@dataclass(eq=False)
class Conn:
    ws: WebSocket
    id: int
    user_id: int
    session: str
    ip: str
    brief: dict[str, Any] | None
    visible: bool = False
    mal_id: int = 0
    title: str = ""
    frames: list[float] = field(default_factory=list)

    def allow_frame(self, now: float) -> bool:
        self.frames = [t for t in self.frames if now - t < FRAME_WINDOW]
        if len(self.frames) >= FRAME_LIMIT:
            return False
        self.frames.append(now)
        return True


def _origin_ok(ws: WebSocket) -> bool:
    """Страница того же адреса, что и сервер (заголовок Host проходит nginx как есть), или адрес из
    AV_ALLOWED_ORIGINS — фронтенд для разработки на другом порту."""
    origin = (ws.headers.get("origin") or "").strip()
    if not origin:
        return False
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    host = (ws.headers.get("host") or "").strip().lower()
    return parts.netloc.lower() == host or origin.rstrip("/") in extra_origins


def _session_user(session: str) -> dict[str, Any] | None:
    if not session:
        return None
    try:
        return db.touch_session(session)
    except Exception:  # noqa: BLE001 — база недоступна: подключаем как гостя
        return None


def _brief(user: dict[str, Any]) -> dict[str, Any]:
    """Кто это — для событий друзьям: те же поля, что у друга в /account/friends."""
    uid = int(user["id"])
    avatar = user.get("avatar") or None
    if avatar == animesocial.avatar_url(uid, None):
        avatar = None
    return {"id": uid, "name": user.get("name") or f"id{uid}", "avatar": avatar,
            "profile_path": user.get("profile_path") or f"/@profile{uid}"}


class Hub:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._ids = itertools.count(1)
        self.conns: dict[int, Conn] = {}
        self.by_user: dict[int, set[int]] = {}
        self.guests_by_ip: dict[str, int] = {}
        # Друзья тех, кто на сайте, и обратный указатель: о ком кому сообщать
        self.friends: dict[int, frozenset[int]] = {}
        self.followers: dict[int, set[int]] = {}
        # Что друзьям уже сообщено: (на AsoPlay, что смотрит); и кто это — пока не сообщено «вышел»
        self.published: dict[int, tuple[bool, tuple | None]] = {}
        self.briefs: dict[int, dict[str, Any]] = {}
        self._privacy: dict[int, tuple[float, presence.Privacy]] = {}
        self._titles: dict[int, tuple[str, str]] = {}
        self._status: dict[str, Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        # Ссылки на фоновые задачи: без них asyncio может собрать задачу на полпути
        self._background: set[asyncio.Task] = set()
        # Соединение сервер–сервер с realtime соцсети
        self.social = as_realtime.SocialLink(self.social_snapshot, self.on_social_event)

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ----- подключение -----
    def has_room(self, user: dict[str, Any] | None, ip: str) -> bool:
        if user:
            return len(self.by_user.get(int(user["id"]), ())) < MAX_TABS_PER_USER
        return self.guests_by_ip.get(ip, 0) < MAX_GUESTS_PER_IP

    def join(self, ws: WebSocket, user: dict[str, Any] | None, session: str, ip: str) -> Conn:
        conn = Conn(ws, next(self._ids), int(user["id"]) if user else 0, session if user else "", ip,
                    _brief(user) if user else None)
        self.conns[conn.id] = conn
        if conn.user_id:
            self.by_user.setdefault(conn.user_id, set()).add(conn.id)
            self.briefs[conn.user_id] = conn.brief
        else:
            self.guests_by_ip[ip] = self.guests_by_ip.get(ip, 0) + 1
        return conn

    async def leave(self, conn: Conn) -> None:
        if self.conns.pop(conn.id, None) is None:
            return
        if not conn.user_id:
            left = self.guests_by_ip.get(conn.ip, 1) - 1
            if left > 0:
                self.guests_by_ip[conn.ip] = left
            else:
                self.guests_by_ip.pop(conn.ip, None)
            return
        tabs = self.by_user.get(conn.user_id, set())
        tabs.discard(conn.id)
        if not tabs:
            self.by_user.pop(conn.user_id, None)
            # Некому больше раздавать события о его друзьях
            self.set_friends(conn.user_id, frozenset())
        presence.presence.close(conn.user_id, conn.id)
        await self.publish(conn.user_id)

    async def load_friends(self, conn: Conn) -> None:
        """Друзья из соцсети — по токену сессии соцсети этого человека; без неё друзей нет."""
        ua = conn.ws.headers.get("user-agent") or ""
        try:
            token = await asyncio.to_thread(social_login.access_token, conn.session, conn.ip, ua) if social_login.enabled() else None
            if token:
                # presence._friend_ids сам сообщит id через friend_listeners
                await asyncio.to_thread(presence._friend_ids, conn.user_id, token, conn.ip, ua)
        except Exception as exc:  # noqa: BLE001 — без друзей соединение всё равно работает
            log.info("Could not load friends for realtime: %s", exc)

    def set_friends(self, user_id: int, ids: frozenset[int]) -> None:
        if ids and user_id not in self.by_user:
            return
        old = self.friends.get(user_id, frozenset())
        for fid in old - ids:
            watchers = self.followers.get(fid)
            if watchers:
                watchers.discard(user_id)
                if not watchers:
                    self.followers.pop(fid, None)
        for fid in ids - old:
            self.followers.setdefault(fid, set()).add(user_id)
        if ids:
            self.friends[user_id] = ids
        else:
            self.friends.pop(user_id, None)

    def friends_from_thread(self, user_id: int, ids: list[int]) -> None:
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.set_friends, user_id, frozenset(ids))

    # ----- кадры браузера -----
    async def handle(self, conn: Conn, text: str) -> None:
        if len(text) > MAX_FRAME or not conn.allow_frame(self._clock()):
            return
        try:
            frame = json.loads(text)
        except ValueError:
            return
        kind = frame.get("type") if isinstance(frame, dict) else None
        if kind == "ping":
            await self.send(conn, {"type": "pong"})
            return
        if not conn.user_id:
            return
        if kind == "visible":
            conn.visible = frame.get("value") is True
        elif kind == "watching":
            mal_id = frame.get("mal_id")
            ok = isinstance(mal_id, int) and not isinstance(mal_id, bool) and 0 < mal_id < presence.MAL_MAX
            conn.mal_id = mal_id if ok else 0
            conn.title = presence.clean_title(frame.get("title")) if ok else ""
        else:
            return
        presence.presence.tab(conn.user_id, conn.id, conn.visible, conn.mal_id, conn.title)
        await self.publish(conn.user_id)

    # ----- события друзьям -----
    async def _rules(self, user_id: int) -> presence.Privacy:
        hit = self._privacy.get(user_id)
        if hit and hit[0] > self._clock():
            return hit[1]
        try:
            rules = (await asyncio.to_thread(presence._privacy, [user_id])).get(user_id, presence.Privacy())
        except Exception:  # noqa: BLE001 — база недоступна: никому не показываем
            return presence.Privacy(hide_presence=True)
        self._privacy[user_id] = (self._clock() + PRIVACY_TTL, rules)
        return rules

    async def _title(self, mal_id: int, fallback: str) -> tuple[str, str]:
        """Название и постер тайтла из кеша страниц тайтлов; нет строки — название из вкладки."""
        if mal_id not in self._titles:
            try:
                found = (await asyncio.to_thread(presence._titles, [mal_id])).get(mal_id)
            except Exception:  # noqa: BLE001
                found = None
            if len(self._titles) > 5000:
                self._titles.clear()
            if found:
                self._titles[mal_id] = found
            title, poster = found or ("", "")
            return title or fallback, poster
        title, poster = self._titles[mal_id]
        return title or fallback, poster

    async def publish(self, user_id: int) -> None:
        """Сообщить друзьям, если для них что-то изменилось: зашёл, вышел, начал или перестал смотреть."""
        mark = presence.presence.current().get(user_id)
        rules = await self._rules(user_id) if mark else presence.Privacy()
        here = mark is not None and not rules.hide_presence
        watching = None
        if here and rules.show_watching and mark.mal_id:
            title, poster = await self._title(mark.mal_id, mark.title)
            if title:
                watching = {"mal_id": mark.mal_id, "title": title, "poster": poster, "path": f"/anime/{mark.mal_id}"}
        state = (here, (watching["mal_id"], watching["title"]) if watching else None)
        if self.published.get(user_id, (False, None)) == state:
            return
        self.social.presence(user_id, here, {"title": watching["title"], "url": SITE_URL + watching["path"]} if watching else None)
        brief = self.briefs.get(user_id)
        if here:
            self.published[user_id] = state
        else:
            self.published.pop(user_id, None)
            if user_id not in self.by_user:
                self.briefs.pop(user_id, None)
        if not brief:
            return
        event = {"type": "presence", "user": brief, "on_asoplay": here, "watching": watching}
        for viewer in list(self.followers.get(user_id, ())):
            for conn_id in list(self.by_user.get(viewer, ())):
                conn = self.conns.get(conn_id)
                if conn:
                    await self.send(conn, event)

    async def privacy_changed(self, user_id: int) -> None:
        self._privacy.pop(user_id, None)
        await self.publish(user_id)

    # ----- соцсеть -----
    async def social_snapshot(self) -> dict[str, Any]:
        """Снимок для соцсети при каждом подключении — то же, что запасной пакет: кто на AsoPlay, что смотрит."""
        return await asyncio.to_thread(presence.build_batch)

    async def _to_people(self, user_ids: list[int], frame: dict[str, Any]) -> None:
        for uid in user_ids:
            for conn_id in list(self.by_user.get(uid, ())):
                conn = self.conns.get(conn_id)
                if conn:
                    await self.send(conn, frame)

    async def on_social_event(self, name: str, data: dict[str, Any], to: list[int]) -> None:
        """Событие соцсети людям AsoPlay (clients.md, 9.2). Кому — решила соцсеть (`to`)."""
        if name == "presence":
            uid = data.get("user_id")
            if not isinstance(uid, int):
                return
            await self._to_people(to, {"type": "social_presence", "user_id": uid,
                                       "online_where": data.get("online_where"), "last_seen": data.get("last_seen")})
        elif name == "profile_friend":
            other, status = data.get("user_id"), data.get("status")
            if not isinstance(other, int) or status not in ("friends", "incoming", "outgoing", "none"):
                return
            for viewer in to:
                if viewer == other:
                    continue
                presence.forget_friends(viewer)
                if viewer not in self.by_user:
                    continue
                ids = set(self.friends.get(viewer, frozenset()))
                if status == "friends":
                    ids.add(other)
                else:
                    ids.discard(other)
                self.set_friends(viewer, frozenset(ids))
                await self._to_people([viewer], {"type": "friendship", "user_id": other, "status": status})
        elif name == "account_banned":
            uid = data.get("user_id")
            if not isinstance(uid, int):
                return
            try:
                await asyncio.to_thread(db.revoke_user_sessions, uid)
            except Exception:  # noqa: BLE001 — вкладки закроем всё равно; сессии закроет сверка
                log.exception("Could not revoke the sessions of a banned account")
            for conn_id in list(self.by_user.get(uid, ())):
                conn = self.conns.get(conn_id)
                if conn:
                    await self.close(conn, 4001)
        # notify и account_state — для значка соцсети в шапке, client_comment — для веток комментариев:
        # их у AsoPlay пока нет

    def privacy_changed_from_thread(self, user_id: int) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.privacy_changed(user_id), self._loop)

    async def lists_changed(self, user_id: int, items: list[dict[str, Any]]) -> None:
        """Правило 30 дней перенесло тайтлы человека: кадр «lists» во все его вкладки."""
        await self._to_people([user_id], {"type": "lists", "items": items})

    def lists_changed_from_thread(self, user_id: int, items: list[dict[str, Any]]) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.lists_changed(user_id, items), self._loop)

    # ----- отправка и закрытие -----
    async def send(self, conn: Conn, data: dict[str, Any]) -> None:
        try:
            await asyncio.wait_for(conn.ws.send_json(data), SEND_TIMEOUT)
        except Exception:  # noqa: BLE001 — вкладка не принимает: закрываем, она переподключится
            await self.close(conn, 1011)

    async def close(self, conn: Conn, code: int) -> None:
        try:
            await conn.ws.close(code=code)
        except Exception:  # noqa: BLE001
            pass

    async def drop_session(self, session: str) -> None:
        """Выход из AsoPlay: вкладки этой сессии переподключаются гостями."""
        for conn in [c for c in self.conns.values() if c.session and c.session == session]:
            await self.close(conn, 4001)

    def drop_session_from_thread(self, session: str) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.drop_session(session), self._loop)

    def status(self) -> dict[str, Any]:
        try:
            return status_provider()
        except Exception:  # noqa: BLE001
            log.exception("Could not read the server status for realtime")
            return {"ok": True, "sources": [], "vpn": False}

    # ----- фон: «вышел» по истечении паузы, состояние сервера, сверка сессий -----
    async def sweep(self) -> None:
        current = presence.presence.current()
        for user_id in [uid for uid in self.published if uid not in current]:
            await self.publish(user_id)
        await self.broadcast_status()

    async def broadcast_status(self) -> None:
        """Состояние сервера и решения панели Anime Social — всем вкладкам, если что-то поменялось."""
        status = self.status()
        if self._status is not None and status != self._status:
            for conn in list(self.conns.values()):
                await self.send(conn, {"type": "status", "status": status})
        self._status = status

    def status_changed_from_thread(self) -> None:
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.broadcast_status(), self._loop)

    async def check_sessions(self) -> None:
        for session in {c.session for c in self.conns.values() if c.session}:
            if await asyncio.to_thread(_session_user, session) is None:
                await self.drop_session(session)

    async def _loop_forever(self) -> None:
        last_check = self._clock()
        while True:
            await asyncio.sleep(SWEEP_EVERY)
            try:
                await self.sweep()
                if self._clock() - last_check >= SESSION_CHECK_EVERY:
                    last_check = self._clock()
                    await self.check_sessions()
            except Exception:  # noqa: BLE001 — следующий проход всё равно будет
                log.exception("Realtime sweep failed")

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._status = self.status()
        if self._task is None:
            self._task = asyncio.create_task(self._loop_forever())
        self.social.start()

    async def stop(self) -> None:
        await self.social.stop()
        if self._task is not None:
            self._task.cancel()
            self._task = None
        # 1012 — сервер перезапускается: вкладки переподключатся сами
        for conn in list(self.conns.values()):
            await self.close(conn, 1012)
        self._loop = None


hub = Hub()
presence.friend_listeners.append(hub.friends_from_thread)
presence.batch_needed = lambda: not hub.social.connected


async def startup() -> None:
    await hub.start()


async def shutdown() -> None:
    await hub.stop()


def privacy_changed(user_id: int) -> None:
    """Из обычного обработчика (поток): человек поменял настройки присутствия."""
    hub.privacy_changed_from_thread(user_id)


def status_changed() -> None:
    """Из обычного обработчика (поток): в панели Anime Social поменяли то, что видят все вкладки."""
    hub.status_changed_from_thread()


def lists_changed(user_id: int, items: list[dict[str, Any]]) -> None:
    """Из потока: правило 30 дней перенесло тайтлы человека — его вкладки перекрашивают списки."""
    hub.lists_changed_from_thread(user_id, items)


def drop_session(session: str) -> None:
    """Из обычного обработчика (поток): выход из AsoPlay."""
    hub.drop_session_from_thread(session)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    if not _origin_ok(ws):
        await ws.close(code=1008)
        return
    ip = rate_limit.client_ip(ws)
    session = ws.cookies.get(db.COOKIE_NAME) or ""
    user = await asyncio.to_thread(_session_user, session) if session else None
    if not hub.has_room(user, ip):
        await ws.close(code=1013)
        return
    await ws.accept()
    conn = hub.join(ws, user, session, ip)
    try:
        await hub.send(conn, {"type": "hello", "user_id": conn.user_id or None, "status": hub.status()})
        if conn.user_id:
            hub.spawn(hub.load_friends(conn))
        while True:
            await hub.handle(conn, await ws.receive_text())
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — сломанный кадр или обрыв: соединение закрывается, вкладка переподключится
        log.debug("Realtime connection %s ended with an error", conn.id, exc_info=True)
    finally:
        await hub.leave(conn)
