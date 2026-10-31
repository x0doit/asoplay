"""
Живое соединение вкладок с AsoPlay: хаб соединений — подключение, кадры вкладок, отправка, фон.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Hub собран из трёх частей: соединения и кадры — здесь, присутствие друзьям — HubPresence
(core/functions/realtime_presence.py), соцсеть и состояние сервера — HubSocial (core/functions/realtime_social.py).
Маршрут /ws и протокол кадров — core/api/sections/realtime.py. Из обычных обработчиков (потоков) хаб зовут
функции модуля: privacy_changed, status_changed, lists_changed, drop_session.
"""
from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import WebSocket

from core.classes import as_realtime
from core.config import animesocial
from core.functions import friends as friend_data, presence, presence_batch, privacy as privacy_rules, sessions
from core.functions.realtime_presence import HubPresence
from core.functions.realtime_social import HubSocial

log = logging.getLogger("asoplay.realtime")

MAX_FRAME = 4096
# Не больше 30 кадров за 10 секунд от вкладки; лишние не обрабатываются
FRAME_LIMIT, FRAME_WINDOW = 30, 10.0
MAX_TABS_PER_USER = 20
MAX_GUESTS_PER_IP = 30
SWEEP_EVERY = 5
# Раз в 5 минут — сверка сессий: выход в другой вкладке или с другого устройства закрывает соединение
SESSION_CHECK_EVERY = 300
SEND_TIMEOUT = 5


@dataclass(eq=False)
class Conn:
    """Одно соединение вкладки: кто, с какого адреса, на экране ли, что смотрит, и её последние кадры."""
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
        """Не больше FRAME_LIMIT кадров за FRAME_WINDOW секунд; лишний кадр — False."""
        self.frames = [t for t in self.frames if now - t < FRAME_WINDOW]
        if len(self.frames) >= FRAME_LIMIT:
            return False
        self.frames.append(now)
        return True


def session_user(session: str) -> dict[str, Any] | None:
    """Человек по сессии AsoPlay или None: сессии нет, она закрыта или база недоступна — тогда гость."""
    if not session:
        return None
    try:
        return sessions.touch_session(session)
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


class Hub(HubPresence, HubSocial):
    """Живые соединения вкладок: кто подключён, их кадры, отправка и фон. Присутствие друзьям — HubPresence
    (realtime_presence.py), соцсеть и состояние сервера — HubSocial (realtime_social.py)."""
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        """Пустой хаб: соединения, друзья тех, кто на сайте, что им уже сообщено, кеши и связь с realtime соцсети."""
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
        self._privacy: dict[int, tuple[float, privacy_rules.Privacy]] = {}
        self._titles: dict[int, tuple[str, str]] = {}
        self._status: dict[str, Any] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        # Ссылки на фоновые задачи: без них asyncio может собрать задачу на полпути
        self._background: set[asyncio.Task] = set()
        # Соединение сервер–сервер с realtime соцсети
        self.social = as_realtime.SocialLink(self.social_snapshot, self.on_social_event)

    def spawn(self, coro) -> None:
        """Фоновая задача с сохранённой ссылкой, чтобы asyncio не собрал её на полпути."""
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    # ----- подключение -----
    def has_room(self, user: dict[str, Any] | None, ip: str) -> bool:
        """Есть ли место ещё для одной вкладки: у человека — до MAX_TABS_PER_USER, у гостей с адреса — до
        MAX_GUESTS_PER_IP."""
        if user:
            return len(self.by_user.get(int(user["id"]), ())) < MAX_TABS_PER_USER
        return self.guests_by_ip.get(ip, 0) < MAX_GUESTS_PER_IP

    def join(self, ws: WebSocket, user: dict[str, Any] | None, session: str, ip: str) -> Conn:
        """Принять соединение: у человека — запомнить вкладку и кто он, гостя — посчитать по адресу."""
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
        """Соединение закрылось: вкладку — из присутствия, друзьям — что изменилось; ушла последняя вкладка
        человека — забыть его друзей."""
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

    # ----- кадры браузера -----
    async def handle(self, conn: Conn, text: str) -> None:
        """Кадр вкладки: ping — pong; visible и watching — в присутствие и друзьям.
        Длинный, лишний или испорченный кадр — мимо."""
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

    # ----- отправка и закрытие -----
    async def send(self, conn: Conn, data: dict[str, Any]) -> None:
        """Отправить кадр вкладке; не принимает за SEND_TIMEOUT — закрыть, она переподключится."""
        try:
            await asyncio.wait_for(conn.ws.send_json(data), SEND_TIMEOUT)
        except Exception:  # noqa: BLE001 — вкладка не принимает: закрываем, она переподключится
            await self.close(conn, 1011)

    async def close(self, conn: Conn, code: int) -> None:
        """Закрыть соединение с кодом; ошибка закрытия не важна."""
        try:
            await conn.ws.close(code=code)
        except Exception:  # noqa: BLE001
            pass

    async def drop_session(self, session: str) -> None:
        """Выход из AsoPlay: вкладки этой сессии переподключаются гостями."""
        for conn in [c for c in self.conns.values() if c.session and c.session == session]:
            await self.close(conn, 4001)

    def drop_session_from_thread(self, session: str) -> None:
        """drop_session из обычного обработчика (поток)."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.drop_session(session), self._loop)

    # ----- фон: «вышел» по истечении паузы, состояние сервера, сверка сессий -----
    async def sweep(self) -> None:
        """«Вышел» тем, у кого истекла пауза, и состояние сервера, если оно поменялось."""
        current = presence.presence.current()
        for user_id in [uid for uid in self.published if uid not in current]:
            await self.publish(user_id)
        await self.broadcast_status()

    async def check_sessions(self) -> None:
        """Сверка сессий: закрытая в другой вкладке или на другом устройстве — её вкладки переподключаются гостями."""
        for session in {c.session for c in self.conns.values() if c.session}:
            if await asyncio.to_thread(session_user, session) is None:
                await self.drop_session(session)

    async def _loop_forever(self) -> None:
        """Фон хаба: sweep раз в SWEEP_EVERY секунд, сверка сессий раз в SESSION_CHECK_EVERY; сбой — в журнал."""
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
        """Запуск при старте сервера: цикл событий, состояние сервера, фон и связь с соцсетью."""
        self._loop = asyncio.get_running_loop()
        self._status = self.status()
        if self._task is None:
            self._task = asyncio.create_task(self._loop_forever())
        self.social.start()

    async def stop(self) -> None:
        """Остановка: связь с соцсетью, фон и все вкладки — с кодом 1012, они переподключатся сами."""
        await self.social.stop()
        if self._task is not None:
            self._task.cancel()
            self._task = None
        # 1012 — сервер перезапускается: вкладки переподключатся сами
        for conn in list(self.conns.values()):
            await self.close(conn, 1012)
        self._loop = None


hub = Hub()
friend_data.friend_listeners.append(hub.friends_from_thread)
presence_batch.batch_needed = lambda: not hub.social.connected


async def startup() -> None:
    """Запустить хаб (обработчик старта FastAPI)."""
    await hub.start()


async def shutdown() -> None:
    """Остановить хаб."""
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
