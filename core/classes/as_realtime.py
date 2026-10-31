"""
Соединение сервер–сервер с realtime Anime Social (socialnetwork.local/docs/api/clients.md, раздел 9).

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Сервер AsoPlay держит один WebSocket к realtime.url соцсети: входит ключами клиента, при каждом
подключении присылает снимок — кто сейчас на AsoPlay и что смотрит, — дальше presence в момент изменения
(core/functions/realtime.py, Hub.publish). Обратно приходят события людям AsoPlay (`to`): друг зашёл в соцсеть или
вышел, изменилась дружба, страницу заблокировали — сервер раздаёт их по вкладкам этих людей.

Пока соединения нет, соцсеть узнаёт о присутствии из запасного пакета POST /clients/presence раз в минуту
(presence_batch.report_once); после переподключения снимок заменяет всё, что она знала.
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
from typing import Any, Awaitable, Callable

import certifi
import websockets

from core.classes import as_api, as_sessions
from core.config import config

log = logging.getLogger("asoplay.as_realtime")

OPEN_TIMEOUT = 10
# 1, 2, 4… до 30 секунд, как просит соцсеть; ключи не приняты — раз в 5 минут
BACKOFF_MAX = 30
BAD_KEYS_WAIT = 300
MAX_QUEUE = 10_000


def _tls(url: str) -> ssl.SSLContext | None:
    """Сертификат соцсети — как в HTTP-запросах (as_api): certifi или AV_AS_CA."""
    if not url.startswith("wss://"):
        return None
    ca = config.as_ca()
    if ca.lower() == "system":
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=ca or certifi.where())


class SocialLink:
    """Одно соединение AsoPlay с realtime соцсети. snapshot() — кто сейчас на AsoPlay (тело пакета presence);
    on_event(name, data, to) — событие людям AsoPlay."""

    def __init__(self, snapshot: Callable[[], Awaitable[dict[str, Any]]],
                 on_event: Callable[[str, dict[str, Any], list[int]], Awaitable[None]],
                 connect: Callable[..., Any] = websockets.connect):
        """snapshot — кто сейчас на AsoPlay (соцсети при каждом подключении), on_event — куда отдавать её события;
        connect — подключение (в проверках — своё)."""
        self._snapshot = snapshot
        self._on_event = on_event
        self._connect = connect
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(MAX_QUEUE)
        self._task: asyncio.Task | None = None
        self.connected = False
        self.url = ""

    # ----- что сообщать соцсети -----
    def presence(self, user_id: int, online: bool, watching: dict[str, str] | None) -> None:
        """Изменилось, что соцсеть должна знать о человеке. Без соединения кадр не нужен: при
        переподключении уйдёт снимок."""
        if not self.connected:
            return
        frame: dict[str, Any] = {"type": "presence", "user_id": user_id, "online": online}
        if online:
            frame["watching"] = watching
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            log.warning("Anime Social realtime queue is full; the next snapshot will catch up")

    # ----- соединение -----
    async def _realtime_url(self) -> str:
        """Адрес realtime соцсети из её /system/config (realtime.url); нет или не ws(s) — ASApiError bad_response."""
        config = await asyncio.to_thread(as_api.call, "GET", "/system/config")
        url = str(((config or {}).get("realtime") or {}).get("url") or "") if isinstance(config, dict) else ""
        if not url.startswith(("wss://", "ws://")):
            raise as_api.ASApiError(502, "bad_response", "no realtime.url in /system/config")
        return url

    async def run_once(self) -> str:
        """Одно соединение от входа до обрыва. Возвращает, чем кончилось: closed | invalid_client."""
        self.url = await self._realtime_url()
        client_id, secret = config.as_client_keys()
        async with self._connect(self.url, ssl=_tls(self.url), open_timeout=OPEN_TIMEOUT,
                                 max_size=8 * 1024 * 1024) as ws:
            await ws.send(json.dumps({"type": "auth", "client_id": client_id, "client_secret": secret}))
            # Снимок можно слать сразу за входом: соцсеть обработает его после ready
            await ws.send(json.dumps({"type": "snapshot", **await self._snapshot()}, ensure_ascii=False))
            while not self._queue.empty():
                self._queue.get_nowait()
            self.connected = True
            sender = asyncio.create_task(self._send_loop(ws))
            try:
                async for message in ws:
                    outcome = await self._handle(message)
                    if outcome:
                        return outcome
            finally:
                self.connected = False
                sender.cancel()
        return "closed"

    async def _send_loop(self, ws) -> None:
        """Кадры из очереди — соцсети, пока соединение живо."""
        while True:
            frame = await self._queue.get()
            await ws.send(json.dumps(frame, ensure_ascii=False))

    async def _handle(self, message: Any) -> str | None:
        """Кадр соцсети: error — конец соединения (invalid_client или closed), ready и snapshot_ok — в журнал,
        event — людям AsoPlay. None — соединение продолжается."""
        try:
            frame = json.loads(message)
        except (TypeError, ValueError):
            return None
        if not isinstance(frame, dict):
            return None
        kind = frame.get("type")
        if kind == "error":
            log.error("Anime Social realtime refused AsoPlay: %s", frame.get("code"))
            return "invalid_client" if frame.get("code") == "invalid_client" else "closed"
        if kind == "ready":
            log.info("Connected to Anime Social realtime at %s", self.url)
        elif kind == "snapshot_ok":
            log.info("Anime Social realtime snapshot: %s accepted, %s ignored", frame.get("accepted"), frame.get("ignored"))
        elif kind == "warning":
            log.debug("Anime Social realtime warning: %s for %s", frame.get("code"), frame.get("user_id"))
        elif kind == "event":
            to = [int(u) for u in frame.get("to") or [] if isinstance(u, int) and not isinstance(u, bool)]
            data = frame.get("data") if isinstance(frame.get("data"), dict) else {}
            try:
                await self._on_event(str(frame.get("event") or ""), data, to)
            except Exception:  # noqa: BLE001 — одно сломанное событие не рвёт соединение
                log.exception("Could not deliver an Anime Social realtime event")
        return None

    async def _loop(self) -> None:
        """Соединение с повторами: вход через соцсеть выключен — ждать; обрыв — снова через 1, 2, 4… до BACKOFF_MAX
        секунд; ключи не приняты — через BAD_KEYS_WAIT."""
        failures = 0
        while True:
            if not as_sessions.enabled():
                await asyncio.sleep(BACKOFF_MAX)
                continue
            outcome = "closed"
            try:
                outcome = await self.run_once()
                failures = 0
            except asyncio.CancelledError:
                raise
            except websockets.ConnectionClosed as exc:
                # Неверные ключи или адрес: соцсеть закрывает с 4001 раньше, чем мы дочитали её кадр ошибки
                failures += 1
                if exc.rcvd is not None and exc.rcvd.code == 4001:
                    outcome = "invalid_client"
                    log.error("Anime Social realtime refused AsoPlay: %s", exc.rcvd.reason or "invalid_client")
                else:
                    log.warning("Anime Social realtime connection closed: %s", exc)
            except Exception as exc:  # noqa: BLE001 — сеть, сертификат, перезапуск соцсети: просто ещё раз
                failures += 1
                log.warning("Anime Social realtime connection failed: %s", exc)
            if outcome == "invalid_client":
                await asyncio.sleep(BAD_KEYS_WAIT)
            else:
                await asyncio.sleep(1 if failures == 0 else min(BACKOFF_MAX, 2 ** (failures - 1)))

    def start(self) -> None:
        """Запустить соединение с realtime соцсети, если оно ещё не запущено."""
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Остановить соединение."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.connected = False
