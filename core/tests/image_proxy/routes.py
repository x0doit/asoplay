"""
Пути выборки: мост — когда напрямую не вышло (сеть или заглушка своего DNS), via "vpn" — мост первым, без моста —
напрямую; непубличный адрес на всех путях или по DoH — 403, сбой DoH — сетевой сбой; ok в /summary — «хост ответил»,
и чужой поддомен строку не меняет;
клиенты путей — со своими сетевыми слоями, без перенаправлений и без кук.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/image_proxy: вход (core/tests/test_image_proxy.py) собирает части по порядку.
"""
import time
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from core.classes import http_bridge, http_public, image_fetch, image_proxy
from core.tests.image_proxy.lib import MAL, PNG, ProxyCase, image
from core.tests.player_guard.stubs import Recorder, addresses

STUB = http_public.Refused("cdn.myanimelist.net resolves to non-public 127.0.0.1")


class Routes(ProxyCase):
    async def test_the_bridge_is_used_only_when_direct_fails_and_the_route_is_remembered(self):
        self.bridge_on = True
        self.direct.answers[MAL] = httpx.ConnectError("blocked")
        self.vpn.answers[MAL] = image
        self.assertEqual((await self.get()).body, PNG)
        self.assertEqual((self.direct.seen, self.vpn.seen), ([MAL], [MAL]))
        self.assertEqual(self.remembered, [("cdn.myanimelist.net", "vpn")])
        last = image_proxy.routes["cdn.myanimelist.net"]
        self.assertEqual((last["route"], last["ok"], last["status"]), ("vpn", True, 200))
        self.assertLessEqual(abs(last["at"] - time.time()), 5)

    async def test_a_stub_from_the_own_dns_goes_through_the_bridge_instead_of_403(self):
        self.bridge_on = True
        self.direct.answers[MAL] = STUB
        self.vpn.answers[MAL] = image
        self.assertEqual((await self.get()).body, PNG)
        self.assertEqual((self.direct.seen, self.vpn.seen), ([MAL], [MAL]))

    async def test_via_vpn_goes_to_the_bridge_first_and_without_the_bridge_goes_direct(self):
        self.panel(("myanimelist.net", "vpn"))
        self.direct.answers[MAL] = self.vpn.answers[MAL + "?2"] = image
        self.assertEqual((await self.get()).body, PNG)
        self.assertEqual(image_proxy.routes["cdn.myanimelist.net"]["route"], "direct")  # моста нет
        self.bridge_on = True
        self.direct.seen.clear()
        self.assertEqual((await self.get(MAL + "?2")).body, PNG)
        self.assertEqual((self.direct.seen, self.vpn.seen, self.remembered), ([], [MAL + "?2"], []))

    async def test_a_private_address_on_every_path_is_403_but_a_doh_failure_is_a_network_failure(self):
        self.bridge_on = True
        self.direct.answers[MAL] = STUB
        self.vpn.answers[MAL] = http_public.Refused("cdn.myanimelist.net resolves to non-public 10.0.0.5")
        self.assertEqual((await self.get()).status_code, 403)
        self.assertEqual(image_proxy.routes["cdn.myanimelist.net"], {**image_proxy.routes["cdn.myanimelist.net"],
                                                                     "ok": False, "status": 403, "route": "vpn"})
        self.vpn.answers[MAL] = httpx.ConnectError("DoH failed for cdn.myanimelist.net")
        self.assertEqual((await self.get()).status_code, 502)

    async def test_ok_in_the_summary_means_that_the_host_answered(self):
        for answer, ok, status in ((lambda: httpx.Response(404), True, 404), (image, True, 200),
                                   (lambda: httpx.Response(503), False, 503), (lambda: httpx.Response(429), False, 429),
                                   (httpx.ConnectError("down"), False, None)):
            self.direct.answers[MAL + f"?{status}"] = answer
            await self.get(MAL + f"?{status}")
            last = image_proxy.routes["cdn.myanimelist.net"]
            self.assertEqual((last["ok"], last["status"]), (ok, status), status)

    async def test_a_subdomain_or_a_redirect_loop_does_not_make_the_row_a_failure(self):
        self.direct.answers[MAL] = image
        await self.get()
        good = dict(image_proxy.routes["cdn.myanimelist.net"])
        self.direct.answers["https://nx1.cdn.myanimelist.net/a.jpg"] = httpx.ConnectError("NXDOMAIN")
        self.assertEqual((await self.get("https://nx1.cdn.myanimelist.net/a.jpg")).status_code, 502)
        self.assertEqual(image_proxy.routes["cdn.myanimelist.net"], good)  # чужой поддомен строку не трогает
        self.direct.answers[MAL + "?loop"] = lambda: httpx.Response(302, headers={"location": MAL + "?loop"})
        self.assertEqual((await self.get(MAL + "?loop")).status_code, 502)
        last = image_proxy.routes["cdn.myanimelist.net"]
        self.assertEqual((last["ok"], last["status"]), (True, 302))  # хост ответил — перенаправлением

    async def test_a_private_address_by_doh_wins_over_a_network_failure_of_the_other_path(self):
        self.bridge_on = True
        self.direct.answers[MAL] = httpx.ConnectError("down")
        self.vpn.answers[MAL] = http_public.Refused("cdn.myanimelist.net resolves to non-public 10.0.0.5")
        self.assertEqual((await self.get()).status_code, 403)

    async def test_direct_connections_go_only_to_public_addresses(self):
        transport = http_public.transport()
        transport._pool._network_backend._inner = inner = Recorder([b""])
        client = httpx.AsyncClient(transport=transport)
        self.addAsyncCleanup(client.aclose)
        self.clients["direct"] = client
        with patch.object(http_public.anyio, "getaddrinfo", AsyncMock(return_value=addresses("127.0.0.1"))):
            self.assertEqual((await self.get()).status_code, 403)
        self.assertEqual(inner.hosts, [])


class RealClients(unittest.IsolatedAsyncioTestCase):
    async def test_the_path_clients_have_their_layers_and_keep_no_redirects_or_cookies(self):
        await image_fetch.shutdown()
        try:
            direct, bridge = image_fetch.client("direct"), image_fetch.client("vpn")
            self.assertIsInstance(direct._transport._pool._network_backend, http_public.PublicOnlyBackend)
            self.assertIsInstance(bridge._transport._pool._network_backend, http_bridge.BridgeBackend)
            for client in (direct, bridge):
                self.assertFalse(client.follow_redirects)
                set_cookie = httpx.Response(200, headers={"set-cookie": "a=1; Path=/"},
                                            request=httpx.Request("GET", "https://cdn.myanimelist.net/a.png"))
                client.cookies.extract_cookies(set_cookie)
                self.assertEqual(len(client.cookies.jar), 0)
        finally:
            await image_fetch.shutdown()
