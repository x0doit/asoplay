"""
Пределы выборок: у посетителя и у хоста своя доля мест, и остальным места остаются; хост, который подряд не отвечает,
отдыхает без запросов наверх; посетители с IPv6 считаются по /64, а адрес посетителя берётся только из X-Real-IP
от nginx на 127.0.0.1.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/image_proxy: вход (core/tests/test_image_proxy.py) собирает части по порядку.
"""
import asyncio
import unittest
from unittest.mock import patch

import httpx
import uvicorn
from starlette.requests import Request

from core.api.sections import catalog
from core.classes import image_limits, image_proxy
from core.functions import rate_limit
from core.tests.image_proxy.lib import MAL, PNG, ProxyCase, image


class SlotShares(unittest.IsolatedAsyncioTestCase):
    async def test_a_visitor_and_a_host_get_their_share_and_the_rest_stays_for_others(self):
        slots = image_limits.Slots(total=4, per_visitor=2, per_host=3)
        await slots.acquire("v1", "a", 0.1)
        await slots.acquire("v1", "b", 0.1)
        with self.assertRaises(TimeoutError):
            await slots.acquire("v1", "c", 0.1)  # доля посетителя
        await slots.acquire("v2", "a", 0.1)
        await slots.acquire("v3", "a", 0.1)
        with self.assertRaises(TimeoutError):
            await slots.acquire("v4", "a", 0.1)  # доля хоста
        with self.assertRaises(TimeoutError):
            await slots.acquire("v4", "d", 0.1)  # всего
        waiting = asyncio.create_task(slots.acquire("v4", "d", 2.0))
        await asyncio.sleep(0.05)
        slots.release("v1", "a")
        await waiting  # место отдали — ждущий его получил
        for visitor, host in (("v1", "b"), ("v2", "a"), ("v3", "a"), ("v4", "d")):
            slots.release(visitor, host)
        self.assertEqual((slots._busy, slots._held), (0, {}))


class Fairness(ProxyCase):
    async def test_one_visitor_or_one_dead_host_cannot_take_every_place(self):
        gate = asyncio.Event()

        async def dead(req):
            self.direct.seen.append(str(req.url))
            if "anilist" in req.url.host:
                return image()
            await gate.wait()
            return image()

        await self.clients["direct"].aclose()
        self.clients["direct"] = httpx.AsyncClient(transport=httpx.MockTransport(dead))
        with patch.object(image_proxy, "slots", image_limits.Slots(total=4, per_visitor=2, per_host=3)), \
                patch.object(image_proxy, "_SLOT_WAIT", 0.3):
            held = [asyncio.create_task(self.get(MAL + f"?{n}")) for n in (1, 2)]
            await asyncio.sleep(0.05)
            third = await self.get(MAL + "?3")  # третья выборка того же посетителя ждёт своей доли
            self.assertEqual((third.status_code, third.headers["Retry-After"]), (503, "5"))
            other = await self.get("https://s4.anilist.co/file/a.png", ip="198.51.100.9")
            self.assertEqual(other.body, PNG)  # другой посетитель и хост — место есть
            gate.set()
            self.assertEqual([r.body for r in await asyncio.gather(*held)], [PNG, PNG])

    async def test_a_host_that_keeps_failing_rests_without_requests(self):
        self.direct.answers[MAL + "?1"] = self.direct.answers[MAL + "?2"] = httpx.ConnectError("down")
        self.direct.answers[MAL + "?3"] = self.direct.answers[MAL + "?4"] = httpx.ConnectError("down")
        with patch.object(image_proxy, "breaker", image_limits.Breaker(after=3, pause=60)), \
                patch.object(image_proxy, "_RETRY_PAUSE", 0.01):
            for n in (1, 2, 3):
                self.assertEqual((await self.get(MAL + f"?{n}")).status_code, 502)
            asked = len(self.direct.seen)
            self.assertEqual((await self.get(MAL + "?4")).status_code, 502)
            self.assertEqual(len(self.direct.seen), asked)  # хост отдыхает: наверх не ходили
            image_proxy.breaker.answered("cdn.myanimelist.net")
            self.direct.answers[MAL + "?4"] = image
            self.assertEqual((await self.get(MAL + "?4")).body, PNG)


class Visitors(ProxyCase):
    def test_ipv6_visitors_count_by_their_64_and_the_address_comes_only_from_nginx(self):
        self.assertEqual(rate_limit.visitor_key("2001:db8:1:2:3:4:5:6"), "2001:db8:1:2::/64")
        self.assertEqual(rate_limit.visitor_key("::ffff:198.51.100.4"), "198.51.100.4")
        self.assertEqual(rate_limit.visitor_key("203.0.113.7"), "203.0.113.7")

        def asked(client, headers):
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": client,
                            "query_string": b""}, receive)

        nginx = ("127.0.0.1", 50000)
        self.assertEqual(rate_limit.client_ip(asked(nginx, [(b"x-real-ip", b"203.0.113.7"),
                                                            (b"x-forwarded-for", b"6.6.6.6, 203.0.113.7")])),
                         "203.0.113.7")
        self.assertEqual(rate_limit.client_ip(asked(("198.51.100.9", 1), [(b"x-real-ip", b"6.6.6.6")])),
                         "198.51.100.9")  # не от nginx — заголовку не верим

    async def test_through_uvicorn_as_it_is_started_the_key_is_x_real_ip_whatever_x_forwarded_for_says(self):
        from core import __main__ as entry
        with patch.object(entry.uvicorn, "run") as run:
            entry.main()
        started = {**run.call_args.kwargs, "log_config": None}
        seen = []

        async def app(scope, receive, send):
            seen.append(rate_limit.client_ip(Request(scope, receive)))
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            return None

        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
                 "path": "/", "raw_path": b"/", "query_string": b"", "root_path": "", "server": ("127.0.0.1", 8787),
                 "client": ("127.0.0.1", 50000),
                 "headers": [(b"x-real-ip", b"203.0.113.7"), (b"x-forwarded-for", b"6.6.6.6")]}
        for proxy_headers in (started["proxy_headers"], True):
            server = uvicorn.Config(app, **{**started, "proxy_headers": proxy_headers})
            server.load()
            await server.loaded_app(dict(scope), receive, send)
        self.assertEqual(seen, ["203.0.113.7", "6.6.6.6"])  # как запущено — X-Real-IP; с proxy_headers — XFF

    async def test_the_miss_limit_is_shared_by_a_whole_ipv6_64(self):
        with patch.object(catalog, "_IMG_MISSES", rate_limit.SlidingWindow(limit=2, window=60)):
            for n, ip in enumerate(("2001:db8:1:2::1", "2001:db8:1:2::2", "2001:db8:1:2::3")):
                self.direct.answers[MAL + f"?{n}"] = image
                status = (await self.get(MAL + f"?{n}", ip=ip)).status_code
                self.assertEqual(status, 200 if n < 2 else 429, ip)
            self.direct.answers[MAL + "?9"] = image
            self.assertEqual((await self.get(MAL + "?9", ip="2001:db8:1:3::1")).status_code, 200)
