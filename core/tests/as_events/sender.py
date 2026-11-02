"""
Отправитель на поддельном соединении: пакет до 500 событий с id «b-…», удаление только после events_ok с тем же
id, busy — ждать ответа на прежний, server_error — тот же набор с новым id, нет ответа — повтор по тому же сокету,
обрыв — повтор после переподключения, client_forbidden — до нового соединения, validation_failed — стоп без удаления,
пробуждение из потока обработчика, темп пакетов.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
import asyncio
import re
import threading
import time
import unittest
from unittest.mock import patch

from core.functions import as_events, as_events_sender
from core.tests.as_events.lib import FakeLink, row


class SenderCase(unittest.IsolatedAsyncioTestCase):
    ROWS = 3    # сколько серий в очереди до старта отправителя
    PACE = 0.0

    async def asyncSetUp(self):
        for name, value in (("PACE", self.PACE), ("ANSWER_WAIT", 0.3), ("IDLE", 0.2)):
            p = patch.object(as_events_sender, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.queue = [row(n, episode=n) for n in range(1, self.ROWS + 1)]
        self.link = FakeLink()
        self.sender = as_events_sender.Sender(self.link, take=self.take, drop=self.drop)
        self.sender.start()

    async def asyncTearDown(self):
        await self.sender.stop()

    def take(self):
        return list(self.queue[:as_events.BATCH])

    def drop(self, rows):
        ids = {r.id for r in rows}
        before = len(self.queue)
        self.queue = [r for r in self.queue if r.id not in ids]
        return before - len(self.queue)

    async def frames(self, n, wait=2.0):
        """Ждать, пока уйдёт n кадров."""
        deadline = time.monotonic() + wait
        while len(self.link.sent) < n and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self.assertGreaterEqual(len(self.link.sent), n, self.link.sent)
        return self.link.sent[n - 1]

    def ok(self, frame):
        self.link.answer({"type": "events_ok", "id": frame["id"], "accepted": len(frame["events"]),
                          "duplicates": 0, "ignored": 0})

    def error(self, frame, code, retry_after=None):
        answer = {"type": "events_error", "id": frame["id"] if frame else None, "code": code}
        if retry_after is not None:
            answer["retry_after"] = retry_after
        self.link.answer(answer)


class Batches(SenderCase):
    async def test_events_ok_removes_exactly_that_batch(self):
        frame = await self.frames(1)
        self.assertEqual(frame["type"], "events")
        self.assertRegex(frame["id"], r"^b-1-\d+$")
        self.assertTrue(re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", frame["id"]))
        self.assertEqual(frame["events"], [as_events.frame(r) for r in self.take()])
        self.ok(frame)
        await asyncio.sleep(0.1)
        self.assertEqual(self.queue, [])
        self.assertEqual(len(self.link.sent), 1)

class BigQueue(SenderCase):
    ROWS = 501
    PACE = 0.3

    async def test_a_batch_has_at_most_500_events_and_the_next_goes_after_the_answer_at_its_pace(self):
        first = await self.frames(1)
        self.assertEqual(len(first["events"]), 500)
        await asyncio.sleep(0.1)
        self.assertEqual(len(self.link.sent), 1)  # в полёте один пакет
        answered = time.monotonic()
        self.ok(first)
        second = await self.frames(2)
        self.assertEqual([e["episode"] for e in second["events"]], [501])
        self.assertGreaterEqual(time.monotonic() - answered, 0.15)  # PACE от отправки первого, 0.1 с уже прошло


class FullLink(SenderCase):
    PACE = 0.1

    async def test_a_link_that_takes_no_frame_does_not_make_the_queue_spin(self):
        takes = []
        self.sender._take = lambda: takes.append(time.monotonic()) or self.take()
        self.link.send = lambda frame: False  # соединение готово, но его очередь полна
        await asyncio.sleep(0.55)
        gaps = [round(b - a, 3) for a, b in zip(takes, takes[1:])]
        self.assertLessEqual(len(takes), 7, f"{len(takes)} reads in 0.55 s")  # не чаще раза в PACE (0.1 с)
        self.assertTrue(all(gap >= 0.09 for gap in gaps), gaps[:10])
        self.assertEqual(len(self.queue), 3)  # ничего не удалено


class Answers(SenderCase):
    async def test_server_error_sends_the_same_events_again_with_a_new_id_after_retry_after(self):
        first = await self.frames(1)
        self.error(first, "server_error", retry_after=0.2)
        second = await self.frames(2)
        self.assertNotEqual(second["id"], first["id"])
        self.assertEqual(second["events"], first["events"])
        self.assertEqual(len(self.queue), 3)  # без events_ok ничего не удалено

    async def test_busy_waits_for_the_answer_to_the_previous_batch(self):
        first = await self.frames(1)                  # ответа нет — повтор с новым id
        second = await self.frames(2)
        self.error(second, "busy", retry_after=1)
        await asyncio.sleep(0.1)
        self.assertEqual(len(self.link.sent), 2)      # ждёт ответа на первый, не шлёт
        self.ok(first)
        await asyncio.sleep(0.2)
        self.assertEqual(self.queue, [])
        self.assertEqual(len(self.link.sent), 2)

    async def test_no_answer_on_a_live_connection_sends_again_on_the_same_socket(self):
        first = await self.frames(1)
        second = await self.frames(2)
        self.assertEqual((self.link.ready, self.link.generation), (True, 1))  # соединение не рвали
        self.assertEqual(second["events"], first["events"])
        self.ok(first)                                 # поздний ответ на первый тоже засчитывается
        await asyncio.sleep(0.1)
        self.assertEqual(self.queue, [])

    async def test_a_break_sends_the_same_events_after_reconnecting(self):
        first = await self.frames(1)
        self.link.ready = False
        self.link.answer({"type": "closed", "generation": 1})
        await asyncio.sleep(0.4)
        self.assertEqual(len(self.link.sent), 1)      # без соединения не шлёт
        self.link.generation, self.link.ready = 2, True
        self.link.answer({"type": "ready", "generation": 2})
        second = await self.frames(2)
        self.assertEqual(second["events"], first["events"])

    async def test_client_forbidden_waits_for_a_new_connection(self):
        first = await self.frames(1)
        self.error(first, "client_forbidden")
        await asyncio.sleep(0.4)
        self.assertEqual(len(self.link.sent), 1)
        self.link.reconnect()
        await self.frames(2)
        self.assertEqual(len(self.queue), 3)

    async def test_validation_failed_keeps_the_events_and_stops_sending(self):
        await self.frames(1)
        with self.assertLogs("asoplay.as_events", "CRITICAL"):
            self.error(None, "validation_failed")
            await asyncio.sleep(0.1)
        self.assertTrue(self.sender.stopped)
        self.link.reconnect()
        await asyncio.sleep(0.3)
        self.assertEqual((len(self.link.sent), len(self.queue)), (1, 3))


class Waking(SenderCase):
    async def test_a_new_event_from_a_request_thread_goes_at_once(self):
        first = await self.frames(1)
        self.ok(first)
        await asyncio.sleep(0.05)
        with patch.object(as_events_sender, "IDLE", 30.0):
            await asyncio.sleep(0.3)                  # отправитель ждёт IDLE — уже 30 секунд
            self.queue.append(row(9, "rating", score=8))
            threading.Thread(target=as_events.wake).start()
            frame = await self.frames(2, wait=1.0)
        self.assertEqual(frame["events"][0]["score"], 8)


class NoLink(SenderCase):
    async def test_waking_without_a_link_does_not_spin_and_lets_the_loop_run(self):
        # Обрыв 02.11 на проде: сохранение прогресса будило отправителя, пока соединения не было, — ожидание
        # возвращалось сразу, цикл событий вставал (ни /health, ни переподключения), память росла до OOM
        takes, beats = [], []
        self.sender._take = lambda: takes.append(1) or self.take()
        self.link.ready = False

        async def heartbeat():
            while True:
                beats.append(time.monotonic())
                await asyncio.sleep(0.01)
        task = asyncio.create_task(heartbeat())
        try:
            for _ in range(5):
                threading.Thread(target=as_events.wake).start()
                await asyncio.sleep(0.05)
        finally:
            task.cancel()
        self.assertGreater(len(beats), 15)  # цикл событий жив: соседняя задача шла всё время
        self.assertEqual(takes, [])         # без соединения очередь не читается
