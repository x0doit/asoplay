"""
Отправка событий просмотра соцсети: очередь aviev_as_events → кадр events по соединению сервер–сервер.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Пакет — до as_events.BATCH старейших событий с id «b-<id первой строки>-<мс>». В полёте один пакет: следующий — после
ответа на предыдущий и не раньше чем через PACE секунд (у соцсети — 120 пакетов в минуту). Ответы
(socialnetwork.local/docs/api/clients.md, раздел 14):
- events_ok с тем же id — события удаляются из очереди, как бы соцсеть их ни посчитала (accepted, duplicates, ignored);
- busy — прежний пакет ещё не подтверждён: ждём ответа на него, потом снова;
- server_error, rate_limited — тот же набор с новым id через retry_after;
- client_forbidden — до переподключения не шлём, очередь цела;
- validation_failed — сломан сам кадр, это ошибка AsoPlay: громко в журнал, строки остаются, отправка встаёт до
  перезапуска сервера.
Нет ответа ANSWER_WAIT секунд при живом соединении — тот же набор с новым id по тому же сокету: соединение не рвём, на
нём «в сети» всех людей. Обрыв — тот же набор после переподключения; повтор соцсеть дважды не засчитает.

Новое событие будит отправителя сразу (as_events.wake из обработчика после commit); без пробуждения очередь
проверяется раз в IDLE секунд — так доходят и события, записанные другим процессом (досылка с 30.10).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from core.functions import as_events

log = logging.getLogger("asoplay.as_events")

PACE = 0.5
ANSWER_WAIT = 30.0
IDLE = 60.0
RETRY = {"busy": 1, "server_error": 5, "rate_limited": 60}  # если соцсеть не прислала retry_after


class Sender:
    """Отправитель очереди по соединению link (core/classes/as_realtime.SocialLink). take и drop — чтение очереди
    и удаление подтверждённого (в проверках — свои)."""

    def __init__(self, link: Any, take: Callable[[], list[as_events.Row]] = as_events.take,
                 drop: Callable[[list[as_events.Row]], int] = as_events.drop_sent):
        """link — соединение сервер–сервер; take и drop — очередь (по умолчанию aviev_as_events)."""
        self.link = link
        self._take, self._drop = take, drop
        self._loop: asyncio.AbstractEventLoop | None = None
        self._woken: asyncio.Event | None = None
        self._answers: asyncio.Queue[dict[str, Any]] | None = None
        self._waiting: dict[str, list[as_events.Row]] = {}  # отправленные пакеты без ответа: id → строки
        self._forbidden_in: int | None = None               # соединение, на котором пришёл client_forbidden
        self._last_sent = 0.0
        self._task: asyncio.Task | None = None
        self.stopped = False

    # ----- связь с соединением и обработчиками -----
    def on_answer(self, frame: dict[str, Any]) -> None:
        """От соединения (тот же цикл событий): ответ на events, ready или closed."""
        if self._answers is not None:
            self._answers.put_nowait(frame)
        if frame.get("type") == "ready" and self._woken is not None:
            self._woken.set()

    def wake_from_thread(self) -> None:
        """Из обработчика запроса (поток): в очереди новое событие."""
        loop, woken = self._loop, self._woken
        if loop is not None and woken is not None and not loop.is_closed():
            loop.call_soon_threadsafe(woken.set)

    # ----- цикл -----
    async def run(self) -> None:
        """Пока не остановлен — шаг за шагом; сбой базы или соединения не останавливает отправку."""
        while not self.stopped:
            try:
                await self._step()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — события остаются в очереди, следующий шаг их отправит
                log.exception("Anime Social events sender step failed")
                await asyncio.sleep(5)
            # Шаг, который ни разу не ждал по-настоящему, не должен держать цикл событий: без этого сервер
            # перестаёт отвечать и не переподключается (02.11 на проде так дошло до OOM)
            await asyncio.sleep(0)

    async def _step(self) -> None:
        """Соединение готово — пакет из очереди; нет событий или соединения — ждать."""
        if not self.link.ready or self._forbidden_in == self.link.generation:
            # Пробуждение без соединения ничего не даст, а взведённый флаг вернул бы ожидание сразу — цикл
            # крутился бы вхолостую. Гасим: ready от соединения разбудит снова
            self._woken.clear()
            await self._rest()
            return
        self._woken.clear()  # до чтения очереди: пробуждение после него не потеряется
        rows = await asyncio.to_thread(self._take)
        if not rows:
            await self._rest()
            return
        await self._send(rows)

    async def _rest(self) -> None:
        """Ждать пробуждения или ready, не дольше IDLE."""
        try:
            await asyncio.wait_for(self._woken.wait(), IDLE)
        except asyncio.TimeoutError:
            pass

    async def _send(self, rows: list[as_events.Row]) -> None:
        """Один пакет: отправить, дождаться ответа и поступить по нему."""
        await self._drain()
        delay = PACE - (time.monotonic() - self._last_sent)
        if delay > 0:
            await asyncio.sleep(delay)
        batch = f"b-{rows[0].id}-{int(time.time() * 1000)}"
        generation = self.link.generation
        if not self.link.send({"type": "events", "id": batch, "events": [as_events.frame(row) for row in rows]}):
            # Соединения уже нет или его очередь полна — тот же набор позже. Пауза PACE: без неё шаг сразу читал бы
            # очередь из базы снова, вхолостую, пока соединение не примет кадр
            await asyncio.sleep(PACE)
            return
        self._last_sent = time.monotonic()
        self._waiting[batch] = rows
        answer = await self._answer(batch, generation, ANSWER_WAIT)
        if answer is None:
            log.warning("No answer from Anime Social to events batch %s in %.0f s; sending it again",
                        batch, ANSWER_WAIT)
        elif answer.get("type") == "events_error":
            await self._failed(batch, generation, answer)

    async def _answer(self, batch: str, generation: int, wait: float) -> dict[str, Any] | None:
        """Ответ на пакет batch. Ответы на прежние пакеты разбираются по дороге; обрыв — ответ closed; None — не
        дождались за wait секунд."""
        deadline = time.monotonic() + wait
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                return None
            try:
                frame = await asyncio.wait_for(self._answers.get(), left)
            except asyncio.TimeoutError:
                return None
            if frame.get("type") == "closed" and frame.get("generation") == generation:
                self._waiting.clear()
                return frame
            if frame.get("type") == "events_error" and frame.get("code") == "validation_failed" \
                    and frame.get("id") is None:
                return frame  # id кадра не прочитан — значит, это наш кадр
            if frame.get("id") == batch:
                await self._settle(frame)
                return frame
            await self._settle(frame)

    async def _settle(self, frame: dict[str, Any]) -> None:
        """Ответ на один из отправленных пакетов: events_ok — удалить его события из очереди."""
        rows = self._waiting.pop(str(frame.get("id")), None)
        if rows is None or frame.get("type") != "events_ok":
            return
        dropped = await asyncio.to_thread(self._drop, rows)
        log.info("Anime Social took events batch %s: %s accepted, %s duplicates, %s ignored (%s left the queue)",
                 frame.get("id"), frame.get("accepted"), frame.get("duplicates"), frame.get("ignored"), dropped)

    async def _failed(self, batch: str, generation: int, answer: dict[str, Any]) -> None:
        """events_error на пакет: что делать — по коду ответа."""
        code = str(answer.get("code") or "")
        retry = answer.get("retry_after")
        wait = float(retry) if isinstance(retry, (int, float)) and retry > 0 else RETRY.get(code, 5)
        if code == "validation_failed":
            self.stopped = True
            log.critical("Anime Social rejected events batch %s as malformed (validation_failed): sending stops until "
                         "AsoPlay restarts; the events stay in aviev_as_events", batch)
        elif code == "client_forbidden":
            self._forbidden_in = generation
            log.error("Anime Social does not take events from AsoPlay (client_forbidden): waiting for a new connection")
        elif code == "busy" and self._waiting:
            await self._previous(generation, ANSWER_WAIT)
        else:
            log.warning("Anime Social events batch %s: %s; again in %s s", batch, code or "error", wait)
            await asyncio.sleep(wait)

    async def _previous(self, generation: int, wait: float) -> None:
        """busy: ждать ответа на прежние пакеты (они в _waiting), не дольше wait секунд; обрыв — сразу дальше."""
        deadline = time.monotonic() + wait
        while self._waiting:
            left = deadline - time.monotonic()
            if left <= 0:
                return
            try:
                frame = await asyncio.wait_for(self._answers.get(), left)
            except asyncio.TimeoutError:
                return
            if frame.get("type") == "closed" and frame.get("generation") == generation:
                self._waiting.clear()
                return
            await self._settle(frame)

    async def _drain(self) -> None:
        """Ответы, пришедшие без ожидания (на прежние пакеты), — разобрать до нового пакета."""
        while not self._answers.empty():
            frame = self._answers.get_nowait()
            if frame.get("type") == "closed":
                self._waiting.clear()
            elif frame.get("type") in ("events_ok", "events_error"):
                await self._settle(frame)

    # ----- запуск -----
    def start(self) -> None:
        """Запустить в текущем цикле событий: подписаться на соединение и на новые события очереди."""
        self._loop = asyncio.get_running_loop()
        self._woken = asyncio.Event()
        self._answers = asyncio.Queue()
        self.link.on_events = self.on_answer
        as_events.on_new(self.wake_from_thread)
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        """Остановить отправку (события остаются в очереди)."""
        self.stopped = True
        as_events.off_new(self.wake_from_thread)
        if self.link.on_events == self.on_answer:
            self.link.on_events = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001 — остановка не должна падать
                pass
            self._task = None


_sender: Sender | None = None


async def startup(link: Any) -> None:
    """Запустить отправителя событий на соединении link (обработчик старта FastAPI, после realtime)."""
    global _sender
    if _sender is None:
        _sender = Sender(link)
        _sender.start()


async def shutdown() -> None:
    """Остановить отправителя событий."""
    global _sender
    if _sender is not None:
        await _sender.stop()
        _sender = None
