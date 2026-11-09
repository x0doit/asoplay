"""
Слой моста (core/classes/http_bridge.py): имя — через DoH по мосту, A и AAAA, кеш по TTL в границах, запасной сервер;
свой DNS не спрашивается вовсе; непубличный адрес — отказ, нет имени или сбой DoH — сетевой сбой; мост получает
CONNECT ровно на проверенный адрес, TLS и Host — по имени; каждый шаг перенаправления проверяет своё имя.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/image_proxy: вход (core/tests/test_image_proxy.py) собирает части по порядку.
"""
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpcore
import httpx

from core.classes import http_bridge, http_public, image_fetch
from core.classes.vpn import bridge as vpn_bridge

OPEN = b"HTTP/1.1 200 Connection established\r\n\r\n"
PICTURE = b"HTTP/1.1 200 OK\r\nContent-Type: image/png\r\nContent-Length: 4\r\n\r\nPNG!"
HOP = b"HTTP/1.1 302 Found\r\nLocation: https://myanimelist.net/x.png\r\nContent-Length: 0\r\n\r\n"


def records(*items, status=0):
    """Ответ DoH в JSON: записи (тип, адрес, TTL)."""
    return {"Status": status, "Answer": [{"name": "x.", "type": t, "TTL": ttl, "data": data} for t, data, ttl in items]}


class Tunnel(httpcore.AsyncMockStream):
    """Поток через подставной мост: всё написанное записывается."""

    def __init__(self, buffer, written):
        super().__init__(buffer)
        self.written = written

    async def write(self, buffer, timeout=None):
        self.written.append(bytes(buffer))


class Bridge(httpcore.AsyncMockBackend):
    """Подставной мост: записывает, куда подключались и что ему написали; отвечает буфером."""

    def __init__(self, buffer):
        super().__init__(buffer)
        self.hosts, self.written = [], []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.hosts.append((host, port))
        return Tunnel(list(self._buffer), self.written)


class BridgeCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.asked, self.answers, self.failing = [], {}, set()
        own_dns = AsyncMock(side_effect=AssertionError("own DNS must not be asked"))
        for item in (patch.object(vpn_bridge, "proxy_url", lambda: "http://127.0.0.1:9"),
                     patch.object(vpn_bridge, "destinations_pinned", lambda: True),
                     patch.object(http_bridge, "_names", {}), patch.object(http_bridge, "_resolving", {}),
                     patch.object(http_public.anyio, "getaddrinfo", own_dns),
                     patch.object(http_bridge, "_doh_client", lambda: self.doh)):
            item.start()
            self.addCleanup(item.stop)

    async def asyncSetUp(self):
        self.doh = httpx.AsyncClient(transport=httpx.MockTransport(self._doh))

    async def asyncTearDown(self):
        await self.doh.aclose()

    def _doh(self, req):
        name, kind = req.url.params["name"], req.url.params["type"]
        self.asked.append((req.url.host, name, kind))
        if req.url.host in self.failing:
            return httpx.Response(500)
        return httpx.Response(200, json=self.answers.get((name, kind), records()))

    async def tunnel(self, host, buffer=(OPEN,)):
        """Подключение слоя к host через подставной мост; ответ — сам мост для проверок."""
        bridge = Bridge(list(buffer))
        stream = await http_bridge.BridgeBackend(bridge).connect_tcp(host, 443, timeout=5)
        await stream.aclose()
        return bridge


class Resolve(BridgeCase):
    async def test_both_records_are_asked_ipv4_first_and_kept_for_a_bounded_ttl(self):
        self.answers = {("cdn.myanimelist.net", "A"): records((1, "93.184.216.34", 5)),
                        ("cdn.myanimelist.net", "AAAA"): records((28, "2606:2800:220:1::1", 100000)),
                        ("s4.anilist.co", "A"): records((1, "104.16.0.1", 100000))}
        self.assertEqual(await http_bridge.resolve("cdn.myanimelist.net"), ["93.184.216.34", "2606:2800:220:1::1"])
        self.assertEqual(sorted(self.asked), [("1.1.1.1", "cdn.myanimelist.net", "A"),
                                              ("1.1.1.1", "cdn.myanimelist.net", "AAAA")])
        await http_bridge.resolve("cdn.myanimelist.net")
        self.assertEqual(len(self.asked), 2)  # из кеша
        await http_bridge.resolve("s4.anilist.co")
        left = {name: until - time.monotonic() for name, (until, _) in http_bridge._names.items()}
        self.assertAlmostEqual(left["cdn.myanimelist.net"], http_bridge.TTL_MIN, delta=2)
        self.assertAlmostEqual(left["s4.anilist.co"], http_bridge.TTL_MAX, delta=2)

    async def test_any_non_public_address_in_the_answer_is_refused(self):
        self.answers = {("a.example.com", "A"): records((1, "10.0.0.5", 60)),
                        ("b.example.com", "A"): records((1, "93.184.216.34", 60)),
                        ("b.example.com", "AAAA"): records((28, "::1", 60))}
        for name in ("a.example.com", "b.example.com"):
            with self.assertRaises(http_public.Refused, msg=name):
                await http_bridge.resolve(name)

    async def test_no_such_name_or_failing_servers_are_a_network_failure_and_the_spare_server_helps(self):
        self.answers = {("gone.example.com", "A"): records(status=3), ("gone.example.com", "AAAA"): records(status=3),
                        ("ok.example.com", "A"): records((1, "93.184.216.34", 60))}
        with self.assertRaises(httpcore.ConnectError):
            await http_bridge.resolve("gone.example.com")
        self.failing = {"1.1.1.1"}
        self.assertEqual(await http_bridge.resolve("ok.example.com"), ["93.184.216.34"])
        self.assertEqual(self.asked[-1][0], "8.8.8.8")
        self.failing = {"1.1.1.1", "8.8.8.8"}
        with self.assertRaises(httpcore.ConnectError) as caught:
            await http_bridge.resolve("other.example.com")
        self.assertNotIsInstance(caught.exception, http_public.Refused)


class Tunnels(BridgeCase):
    async def test_the_bridge_gets_connect_to_the_checked_address_never_the_name(self):
        self.answers = {("cdn.myanimelist.net", "A"): records((1, "93.184.216.34", 60)),
                        ("v6.example.com", "AAAA"): records((28, "2606:2800:220:1::1", 60))}
        bridge = await self.tunnel("cdn.myanimelist.net")
        self.assertEqual(bridge.hosts, [("127.0.0.1", 9)])
        self.assertTrue(bridge.written[0].startswith(b"CONNECT 93.184.216.34:443 HTTP/1.1\r\n"))
        bridge = await self.tunnel("v6.example.com")
        self.assertTrue(bridge.written[0].startswith(b"CONNECT [2606:2800:220:1::1]:443 "))
        asked = len(self.asked)
        bridge = await self.tunnel("1.1.1.1")  # сам сервер DoH: адрес не разрешается, только проверяется
        self.assertEqual((len(self.asked), bridge.written[0][:30]), (asked, b"CONNECT 1.1.1.1:443 HTTP/1.1\r\n"))
        with self.assertRaises(http_public.Refused):
            await self.tunnel("10.0.0.5")

    async def test_a_refused_connect_tries_the_next_address_and_then_fails_as_a_network_failure(self):
        self.answers = {("cdn.myanimelist.net", "A"): records((1, "93.184.216.34", 60), (1, "93.184.216.35", 60))}
        backend = http_bridge.BridgeBackend(bridge := Bridge([b"HTTP/1.1 403 Forbidden\r\n\r\n"]))
        with self.assertRaises(httpcore.ConnectError):
            await backend.connect_tcp("cdn.myanimelist.net", 443, timeout=5)
        self.assertEqual([w[:24] for w in bridge.written], [b"CONNECT 93.184.216.34:44", b"CONNECT 93.184.216.35:44"])
        for down in (patch.object(vpn_bridge, "proxy_url", lambda: None),
                     patch.object(vpn_bridge, "destinations_pinned", lambda: False)):  # чужой xray без routeOnly
            with down, self.assertRaises(httpcore.ConnectError):
                await self.tunnel("cdn.myanimelist.net")

    async def test_a_request_goes_by_name_to_the_pinned_address_and_every_hop_checks_its_own_name(self):
        self.answers = {("cdn.myanimelist.net", "A"): records((1, "93.184.216.34", 60)),
                        ("myanimelist.net", "A"): records((1, "192.168.1.10", 60))}
        transport = http_bridge.transport()
        transport._pool._network_backend._inner = bridge = Bridge([OPEN, PICTURE])
        async with httpx.AsyncClient(transport=transport) as client:
            resp = await client.get("https://cdn.myanimelist.net/a.png")
        self.assertEqual((resp.status_code, resp.content), (200, b"PNG!"))
        self.assertTrue(bridge.written[0].startswith(b"CONNECT 93.184.216.34:443"))
        self.assertIn(b"Host: cdn.myanimelist.net", b"".join(bridge.written[1:]))
        transport = http_bridge.transport()
        transport._pool._network_backend._inner = Bridge([OPEN, HOP])
        client = httpx.AsyncClient(transport=transport, follow_redirects=False)
        self.addAsyncCleanup(client.aclose)
        with patch.object(image_fetch, "client", lambda route: client), self.assertRaises(image_fetch.AddressRefused):
            await image_fetch.get("vpn", "https://cdn.myanimelist.net/a.png", lambda url: True)
