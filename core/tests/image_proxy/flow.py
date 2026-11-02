"""
Ход выборки: старая копия — сразу, обновление — в фоне одной выборкой, даже если хост отвечает дольше 5 секунд;
одна выборка на одинаковые адреса, и первый ожидающий, отвалившись, её не останавливает; предел промахов на
посетителя — 429 (а старая копия и попадания — как обычно). Доли мест — в limits.py.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/image_proxy: вход (core/tests/test_image_proxy.py) собирает части по порядку.
"""
import asyncio
import time
from unittest.mock import patch

import httpx

from core.api.sections import catalog
from core.classes import image_cache, image_proxy
from core.functions import rate_limit
from core.tests.image_proxy.lib import MAL, PNG, ProxyCase, expire, image


class Gate:
    """Хост, который отвечает только по команде; запросы записываются."""

    def __init__(self, body=PNG):
        self.open, self.seen, self.body = asyncio.Event(), [], body

    async def __call__(self, req):
        self.seen.append(str(req.url))
        await self.open.wait()
        return image(self.body)


class Flow(ProxyCase):
    async def slow_host(self, body=PNG) -> Gate:
        """Прямой путь — хост, который отвечает по команде."""
        gate = Gate(body)
        await self.clients["direct"].aclose()
        self.clients["direct"] = httpx.AsyncClient(transport=httpx.MockTransport(gate))
        return gate

    async def test_an_old_copy_is_served_at_once_and_refreshed_in_the_background_by_one_fetch(self):
        image_cache.store(image_proxy.canonical(MAL), b"old", "image/jpeg")
        expire(next(self.cache.iterdir()))
        gate = await self.slow_host(b"new")
        started = time.monotonic()
        self.assertEqual([(await self.get()).body for _ in range(3)], [b"old"] * 3)
        self.assertLess(time.monotonic() - started, 1)  # не ждём хост, который отвечает дольше 5 секунд
        await asyncio.sleep(0.05)
        self.assertEqual(gate.seen, [MAL])
        gate.open.set()
        await asyncio.gather(*image_proxy._pending.values())
        self.assertEqual((await self.get()).body, b"new")
        self.assertEqual(gate.seen, [MAL])

    async def test_one_fetch_for_everyone_and_the_first_waiter_dropping_out_does_not_stop_it(self):
        gate = await self.slow_host()
        waiting = [asyncio.create_task(self.get()) for _ in range(5)]
        await asyncio.sleep(0.05)
        waiting[0].cancel()
        gate.open.set()
        self.assertEqual([r.body for r in await asyncio.gather(*waiting[1:])], [PNG] * 4)
        self.assertTrue(waiting[0].cancelled())
        self.assertEqual((gate.seen, image_proxy._pending), ([MAL], {}))
        self.assertEqual(len(list(self.cache.iterdir())), 1)

    async def test_a_visitor_out_of_misses_gets_429_but_hits_and_old_copies_still_come(self):
        with patch.object(catalog, "_IMG_MISSES", rate_limit.SlidingWindow(limit=2, window=60)):
            for n in (1, 2):
                self.direct.answers[MAL + f"?{n}"] = image
                self.assertEqual((await self.get(MAL + f"?{n}")).status_code, 200)
            self.direct.answers[MAL + "?3"] = image
            busy = await self.get(MAL + "?3")
            self.assertEqual(busy.status_code, 429)
            self.assertTrue(0 < int(busy.headers["Retry-After"]) <= 60)
            self.assertEqual((await self.get(MAL + "?1")).status_code, 200)  # попадание — не промах
            self.assertEqual((await self.get(MAL + "?3", ip="198.51.100.9")).status_code, 200)  # другой посетитель
            expire(image_cache.path(image_proxy.canonical(MAL + "?1")))
            seen = len(self.direct.seen)
            self.assertEqual((await self.get(MAL + "?1")).body, PNG)  # старая копия — без новой выборки
            self.assertEqual(len(self.direct.seen), seen)
