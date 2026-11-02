"""
Пределы выборок картинок наверх: места — всего, на посетителя и на хост; пауза хоста, который подряд не отвечает.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Выборка держит место, пока идёт (до срока выборки), а мёртвый хост держит его долго. Чтобы один посетитель или один
мёртвый хост не заняли все места, у каждого — своя доля (Slots): мест всего TOTAL, на посетителя PER_VISITOR, на хост
PER_HOST. Место ждут не дольше срока ожидания; не дождались — Busy у вызывающего. Хост, который BREAK_AFTER раз подряд
не ответил по сети, отдыхает BREAK_FOR секунд (Breaker): за это время к нему не ходим — сразу старая копия или 502.

Ключ хоста — полное имя из адреса, а не строка списка хостов: по строке три сбоя мусорных поддоменов усыпили бы
для всех весь myanimelist.net. Занять поддоменами все места не даёт доля посетителя: на это нужны
TOTAL / PER_VISITOR разных посетителей — адресов IPv4 или сетей IPv6 /64 (core/functions/rate_limit.py visitor_key).
Ключи посетителя и хоста даёт вызывающий; словари держат только тех, у кого сейчас что-то есть.
"""
from __future__ import annotations

import asyncio
import time

TOTAL = 16
PER_VISITOR = 4
PER_HOST = 6
BREAK_AFTER = 3
BREAK_FOR = 30.0
_BREAKER_MAX = 1000


class Slots:
    """Места выборок: всего, на посетителя и на хост. Свои у каждого цикла событий (проверки запускают новый)."""

    def __init__(self, total: int = TOTAL, per_visitor: int = PER_VISITOR, per_host: int = PER_HOST):
        """Сколько мест всего, на одного посетителя и на один хост."""
        self.total, self.per_visitor, self.per_host = total, per_visitor, per_host
        self._loop: asyncio.AbstractEventLoop | None = None
        self._busy = 0
        self._held: dict[str, int] = {}
        self._waiters: list[asyncio.Future] = []

    def _here(self) -> asyncio.AbstractEventLoop:
        """Цикл событий; в новом цикле прежние места забываются."""
        loop = asyncio.get_running_loop()
        if self._loop is not loop:
            self._loop, self._busy, self._held, self._waiters = loop, 0, {}, []
        return loop

    def _free(self, visitor: str, host: str) -> bool:
        """Есть ли место для этой пары: всего, у посетителя и у хоста."""
        return (self._busy < self.total and self._held.get("v:" + visitor, 0) < self.per_visitor
                and self._held.get("h:" + host, 0) < self.per_host)

    def _count(self, visitor: str, host: str, step: int) -> None:
        """Занять (+1) или отдать (-1) место; ключи без мест убираются."""
        self._busy += step
        for key in ("v:" + visitor, "h:" + host):
            left = self._held.get(key, 0) + step
            if left > 0:
                self._held[key] = left
            else:
                self._held.pop(key, None)

    async def acquire(self, visitor: str, host: str, wait: float) -> None:
        """Занять место; не освободилось за wait секунд — TimeoutError."""
        loop = self._here()
        deadline = loop.time() + wait
        while not self._free(visitor, host):
            left = deadline - loop.time()
            if left <= 0:
                raise TimeoutError("no free fetch slot")
            woken = loop.create_future()
            self._waiters.append(woken)
            try:
                await asyncio.wait_for(woken, left)
            except asyncio.TimeoutError:
                raise TimeoutError("no free fetch slot") from None
            finally:
                if woken in self._waiters:
                    self._waiters.remove(woken)
        self._count(visitor, host, +1)

    def release(self, visitor: str, host: str) -> None:
        """Отдать место и разбудить ждущих; без await — годится и при отмене."""
        self._count(visitor, host, -1)
        waiters, self._waiters = self._waiters, []
        for woken in waiters:
            if not woken.done():
                woken.set_result(None)


class Breaker:
    """Пауза хоста, который подряд не отвечает по сети."""

    def __init__(self, after: int = BREAK_AFTER, pause: float = BREAK_FOR):
        """after сетевых сбоев подряд — pause секунд без попыток."""
        self.after, self.pause = after, pause
        self._hosts: dict[str, list[float]] = {}  # хост → [сбоев подряд, до какого времени пауза]

    def resting(self, host: str) -> float:
        """Сколько секунд хост ещё отдыхает; 0 — к нему можно идти."""
        state = self._hosts.get(host)
        return max(0.0, state[1] - time.monotonic()) if state else 0.0

    def failed(self, host: str) -> None:
        """Ещё один сетевой сбой; набралось after — пауза."""
        if host not in self._hosts and len(self._hosts) >= _BREAKER_MAX:
            self._hosts.clear()
        state = self._hosts.setdefault(host, [0, 0.0])
        state[0] += 1
        if state[0] >= self.after:
            state[0], state[1] = 0, time.monotonic() + self.pause

    def answered(self, host: str) -> None:
        """Хост ответил — счёт сбоев с начала."""
        self._hosts.pop(host, None)
