"""
VPN-мост (VLESS или Shadowsocks) и маршрут: сначала напрямую, мост — только для недоступного хоста;
сбой, который покрыл мост, источнику не засчитывается.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/proxy_resilience: вход (core/tests/test_proxy_resilience.py) собирает части по порядку.
"""
import asyncio
import json
import os
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import HTTPException
from core import core
from core.classes import http
from core.classes.sources import oldyummy
from core.classes.vpn import xray as vpn_xray
from core.functions import source_health


class VpnBridgeConfigTests(unittest.TestCase):
    LINK = ("vless://0e9cd2c4-8b2f-4c6d-9d8b-2f2b4c1d7a11@203.0.113.7:443?type=tcp&security=reality"
            "&encryption=none&flow=xtls-rprx-vision&sni=www.example.com&fp=chrome"
            "&pbk=Zm9vYmFyLWtleS1fdGVzdA&sid=6ba85179e30d4fc2&spx=%2F#AsoPlay")
    SS = {"SS_ADDRESS": "198.51.100.9", "SS_PASSWORD": "secret", "SS_PORT": "8388"}

    def build(self, env):
        with patch.dict(os.environ, env, clear=True), patch.object(vpn_xray, "_LEGACY_PATHS", []):
            return vpn_xray.build_config()

    def test_vless_link_replaces_shadowsocks_as_first_outbound(self):
        cfg = self.build({"AV_VPN_URL": self.LINK, **self.SS})
        proxy = cfg["outbounds"][0]
        server = proxy["settings"]["vnext"][0]
        self.assertEqual((proxy["tag"], proxy["protocol"]), ("proxy", "vless"))
        self.assertEqual((server["address"], server["port"]), ("203.0.113.7", 443))
        self.assertEqual(server["users"], [{"id": "0e9cd2c4-8b2f-4c6d-9d8b-2f2b4c1d7a11",
                                            "encryption": "none", "flow": "xtls-rprx-vision"}])
        self.assertEqual(proxy["streamSettings"], {"network": "tcp", "security": "reality", "realitySettings": {
            "serverName": "www.example.com", "fingerprint": "chrome",
            "publicKey": "Zm9vYmFyLWtleS1fdGVzdA", "shortId": "6ba85179e30d4fc2", "spiderX": "/"}})
        self.assertEqual([o["tag"] for o in cfg["outbounds"]], ["proxy", "direct", "block"])
        self.assertNotIn("shadowsocks", json.dumps(cfg))

    def test_broken_link_falls_back_to_shadowsocks(self):
        cfg = self.build({"AV_VPN_URL": "vless://no-host-or-port", **self.SS})
        self.assertEqual(cfg["outbounds"][0]["protocol"], "shadowsocks")
        self.assertEqual(cfg["outbounds"][0]["settings"]["servers"][0]["port"], 8388)

    def test_unsupported_transport_or_missing_key_is_not_half_configured(self):
        self.assertIsNone(self.build({"AV_VPN_URL": self.LINK.replace("type=tcp", "type=ws")}))
        self.assertIsNone(self.build({"AV_VPN_URL": self.LINK.replace("pbk=", "nopbk=")}))

    def test_without_settings_the_bridge_stays_direct(self):
        self.assertIsNone(self.build({}))


class RouteFallbackTests(unittest.IsolatedAsyncioTestCase):
    """Direct first; the VPN bridge only for a host that is unreachable directly."""

    async def asyncSetUp(self):
        http._vpn_preferred_until.clear()
        self.direct = Mock(request=AsyncMock(side_effect=httpx.ConnectError("direct down")))
        self.vpn = Mock(request=AsyncMock(return_value=httpx.Response(200, json={"ok": True})))
        self.patches = [patch.object(http, "get_client", new=AsyncMock(return_value=self.direct)),
                        patch.object(http, "get_vpn_client", new=AsyncMock(return_value=self.vpn)),
                        patch.object(http.vpn_bridge, "proxy_url", return_value="http://127.0.0.1:20809")]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        http._vpn_preferred_until.clear()

    async def test_unreachable_host_goes_through_the_vpn_and_is_remembered(self):
        resp = await http.request("GET", "https://api.example.test/a", timeout=httpx.Timeout(10, connect=4))
        self.assertEqual(resp.status_code, 200)
        self.assertLessEqual(self.direct.request.call_args.kwargs["timeout"].connect, 2.5)
        self.assertEqual(http.route_order("api.example.test"), ["vpn", "direct"])
        self.direct.request.reset_mock()
        await http.request("GET", "https://api.example.test/b")
        self.direct.request.assert_not_awaited()

    async def test_reachable_host_never_touches_the_vpn(self):
        self.direct.request.side_effect = None
        self.direct.request.return_value = httpx.Response(200)
        await http.request("GET", "https://api.example.test/a")
        self.vpn.request.assert_not_awaited()
        self.assertEqual(http.route_order("api.example.test"), ["direct", "vpn"])

    async def test_without_the_bridge_only_the_direct_route_exists(self):
        with patch.object(http.vpn_bridge, "proxy_url", return_value=None):
            with self.assertRaises(httpx.ConnectError):
                await http.request("GET", "https://api.example.test/a")
        self.vpn.request.assert_not_awaited()


class SourceRouteTests(unittest.IsolatedAsyncioTestCase):
    SOURCE = "oldyummy"

    async def asyncSetUp(self):
        http._vpn_preferred_until.clear()
        source_health._STATES.pop(self.SOURCE, None)
        self.bridge = patch.object(core.vpn_bridge, "proxy_url", return_value="http://127.0.0.1:20809")
        self.bridge.start()

    async def asyncTearDown(self):
        self.bridge.stop()
        http._vpn_preferred_until.clear()
        source_health._STATES.pop(self.SOURCE, None)

    async def test_direct_outage_covered_by_the_vpn_does_not_count_as_a_failure(self):
        calls = []

        async def run(route):
            calls.append(route)
            if route == "direct":
                raise httpx.ConnectTimeout("handshake timed out")
            return ["hit"]

        for _ in range(3):
            self.assertEqual(await core._source_call(self.SOURCE, "search", run, timeout=2), ["hit"])
        self.assertEqual(calls, ["direct", "vpn", "vpn", "vpn"])
        self.assertTrue(source_health.is_available(self.SOURCE))
        self.assertEqual(source_health._STATES[self.SOURCE].consecutive_failures, 0)

    async def test_hung_direct_route_leaves_time_for_the_vpn(self):
        async def run(route):
            if route == "direct":
                await asyncio.sleep(5)
            return ["hit"]

        start = time.monotonic()
        self.assertEqual(await core._source_call(self.SOURCE, "search", run, timeout=0.8), ["hit"])
        self.assertLess(time.monotonic() - start, 0.8)

    async def test_both_routes_failing_is_a_502_and_counts_once(self):
        async def run(route):
            raise httpx.ConnectError(f"{route} down")

        with self.assertRaises(HTTPException) as error:
            await core._source_call(self.SOURCE, "episodes", run, timeout=2)
        self.assertEqual(error.exception.status_code, 502)
        self.assertEqual(source_health._STATES[self.SOURCE].consecutive_failures, 1)

    async def test_native_source_reports_an_unreachable_host_instead_of_an_empty_result(self):
        client = oldyummy.OldYummy()
        try:
            with patch.object(client._client, "get", new=AsyncMock(side_effect=httpx.ConnectError("down"))):
                with self.assertRaises(httpx.ConnectError):
                    await client.search("frieren")
            with patch.object(client._client, "get", new=AsyncMock(return_value=httpx.Response(500))):
                self.assertEqual(await client.search("frieren"), [])
        finally:
            await client.close()
