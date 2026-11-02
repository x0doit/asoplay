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

По этому же соединению идут события просмотра (раздел 14): кадры events шлёт core/functions/as_events_sender.py
через send(), а ответы events_ok / events_error и смену соединения (ready, closed) получает его on_events.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import functools
import json
import logging
import ssl
import time
from typing import Any, Awaitable, Callable

import certifi
import websockets

from core.classes import as_api, as_sessions
from core.config import config

log = logging.getLogger("asoplay.as_realtime")

OPEN_TIMEOUT = 10
ATTEMPT_TIMEOUT = 20      # адрес, подключение и вход — до ready, не дольше
# 1, 2, 4… до 30 секунд, как просит соцсеть; ключи не приняты — раз в 5 минут
FIRST_RETRY = 1
BACKOFF_MAX = 30
BAD_KEYS_WAIT = 300
MAX_QUEUE = 10_000
URL_TTL = 86_400          # адрес realtime перечитывается раз в сутки…
URL_STALE_AFTER = 3       # …или после стольких неудач подряд
DOWN_ERROR_AFTER = 60     # ready нет дольше — ERROR в журнал, не чаще раза в минуту


def _tls(url: str) -> ssl.SSLContext | None:
    """Сертификат соцсети — как в HTTP-запросах (as_api): certifi или AV_AS_CA."""
    if not url.startswith("wss://"):
        return None
    return _tls_context(config.as_ca())


@functools.lru_cache(maxsize=4)
def _tls_context(ca: str) -> ssl.SSLContext:
    """Контекст TLS для ca — один на процесс: чтение списка сертификатов держит цикл событий около 0,2 с, а
    переподключение не должно его держать на каждой попытке."""
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
        # Соцсеть приняла вход (ready) на соединении номер generation; on_events — кому отдавать ответы на кадры
        # events и смену соединения
        self.ready = False
        self.generation = 0
        self.on_events: Callable[[dict[str, Any]], None] | None = None
        # Адрес realtime запоминается; запрос /system/config — в своём потоке: общий пул могут занять другие,
        # и переподключение ждало бы молча
        self._url_at = 0.0
        self._url_pool: concurrent.futures.ThreadPoolExecutor | None = None
        self._deadline: asyncio.Timeout | None = None
        self._down_since = time.monotonic()
        self._down_logged = 0.0

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

    def send(self, frame: dict[str, Any]) -> bool:
        """Кадр соцсети (события просмотра) по живому соединению. False — соединения нет или очередь полна: кадр не
        ушёл, отправитель повторит после переподключения."""
        if not self.ready:
            return False
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            return False
        return True

    def _notify(self, frame: dict[str, Any]) -> None:
        """Ответ на кадр events или смена соединения — отправителю событий."""
        if self.on_events is not None:
            try:
                self.on_events(frame)
            except Exception:  # noqa: BLE001 — отправитель не должен рвать соединение
                log.exception("Could not pass an Anime Social events answer")

    # ----- соединение -----
    async def _realtime_url(self, failures: int = 0) -> str:
        """Адрес realtime соцсети из её /system/config (realtime.url). Запомненный — пока не прошло URL_TTL и не было
        URL_STALE_AFTER неудач подряд. Нет адреса или не ws(s) — ASApiError bad_response."""
        if self.url and failures < URL_STALE_AFTER and time.monotonic() - self._url_at < URL_TTL:
            return self.url
        if self._url_pool is None:
            self._url_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="as-realtime-url")
        loop = asyncio.get_running_loop()
        config = await loop.run_in_executor(self._url_pool, as_api.call, "GET", "/system/config")
        url = str(((config or {}).get("realtime") or {}).get("url") or "") if isinstance(config, dict) else ""
        if not url.startswith(("wss://", "ws://")):
            raise as_api.ASApiError(502, "bad_response", "no realtime.url in /system/config")
        self._url_at = time.monotonic()
        return url

    async def run_once(self, failures: int = 0) -> str:
        """Одно соединение от входа до обрыва. Возвращает, чем кончилось: closed | invalid_client. До ready —
        не дольше ATTEMPT_TIMEOUT (TimeoutError), после ready срока нет."""
        async with asyncio.timeout(ATTEMPT_TIMEOUT) as deadline:
            self._deadline = deadline
            self.url = await self._realtime_url(failures)
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
                    if self.ready:
                        self.ready = False
                        self._down_since = time.monotonic()
                        self._notify({"type": "closed", "generation": self.generation})
        return "closed"

    async def _send_loop(self, ws) -> None:
        """Кадры из очереди — соцсети, пока соединение живо."""
        while True:
            frame = await self._queue.get()
            await ws.send(json.dumps(frame, ensure_ascii=False))
            await asyncio.sleep(0)  # длинная очередь не держит цикл событий: get и send могут не ждать вовсе

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
            if self._deadline is not None:
                self._deadline.reschedule(None)  # вошли — у живого соединения срока нет
            self.generation += 1
            self.ready = True
            self._notify({"type": "ready", "generation": self.generation})
        elif kind in ("events_ok", "events_error"):
            self._notify(frame)
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

    def _log_if_down(self) -> None:
        """ready нет дольше DOWN_ERROR_AFTER при включённом входе — ERROR в журнал, не чаще раза в минуту: залипшее
        соединение должно быть видно (события просмотра и «в сети» ждут его)."""
        now = time.monotonic()
        if self.ready or now - self._down_since < DOWN_ERROR_AFTER or now - self._down_logged < 60:
            return
        self._down_logged = now
        log.error("Anime Social realtime has been down for %d s: watch events and online status wait for it",
                  now - self._down_since)

    async def _loop(self) -> None:
        """Соединение с повторами: вход через соцсеть выключен — ждать; обрыв или попытка без ready за ATTEMPT_TIMEOUT —
        снова через FIRST_RETRY, 2, 4… до BACKOFF_MAX секунд, что бы ни случилось; ключи не приняты — через
        BAD_KEYS_WAIT."""
        failures = 0
        while True:
            if not as_sessions.enabled():
                self._down_since = time.monotonic()  # вход выключен — это не простой соединения
                await asyncio.sleep(BACKOFF_MAX)
                continue
            self._log_if_down()
            outcome = "closed"
            try:
                outcome = await self.run_once(failures)
                failures = 0
            except asyncio.CancelledError:
                raise
            except TimeoutError:
                failures += 1
                log.warning("Anime Social realtime: no ready within %s s; trying again", ATTEMPT_TIMEOUT)
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
                await asyncio.sleep(FIRST_RETRY if failures == 0 else min(BACKOFF_MAX, 2 ** (failures - 1)))

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
        if self._url_pool is not None:
            self._url_pool.shutdown(wait=False)  # поток адреса не остаётся после остановки
            self._url_pool = None
