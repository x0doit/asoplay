"""
Мост без обходов: рабочий конфиг xray не подменяет назначение именем из TLS (routeOnly) и не разрешает имена для
маршрутов сам (AsIs), откуда бы конфиг ни пришёл; чужой уже работающий xray — не для картинок; поток к мосту
закрывается при сроке и при отмене; у DoH свой срок, A и AAAA — разом, ответ ложится в кеш и после отмены запроса;
TLS идёт по имени, туннель — к адресу; настоящий клиент DoH — через мост, по IP, с проверкой сертификата.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/image_proxy: вход (core/tests/test_image_proxy.py) собирает части по порядку.
"""
import asyncio
import ipaddress
import json
import ssl
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

import httpcore
import httpx

from core.classes import http_bridge
from core.classes.vpn import bridge as vpn_bridge, xray
from core.config import config
from core.tests.image_proxy.bridge import OPEN, PICTURE, Bridge, BridgeCase, Tunnel, records

LEGACY = {"inbounds": [{"protocol": "http", "port": 1, "tag": "http",
                        "sniffing": {"enabled": True, "destOverride": ["http", "tls"]}},
                       {"protocol": "socks", "port": 2, "tag": "socks", "sniffing": {"enabled": False}}],
          "outbounds": [{"protocol": "freedom", "tag": "direct"}], "routing": {"domainStrategy": "IPIfNonMatch"}}


class XrayConfig(unittest.TestCase):
    def test_the_template_pins_destinations_and_resolves_no_names_for_routing(self):
        cfg = json.loads(config.XRAY_TEMPLATE.read_text(encoding="utf-8"))
        sniffed = [i for i in cfg["inbounds"] if (i.get("sniffing") or {}).get("enabled")]
        self.assertTrue(sniffed and all(i["sniffing"].get("routeOnly") is True for i in sniffed))
        self.assertEqual(cfg["routing"]["domainStrategy"], "AsIs")

    def test_the_runtime_config_pins_destinations_whatever_its_source(self):
        runtime = Path(tempfile.mkdtemp(prefix="asoplay-xray-")) / "xray-runtime.json"
        with patch.object(xray, "build_config", lambda: json.loads(json.dumps(LEGACY))), \
                patch.object(config, "XRAY_RUNTIME", runtime):
            xray.prepare_runtime()
        written = json.loads(runtime.read_text(encoding="utf-8"))
        self.assertTrue(written["inbounds"][0]["sniffing"]["routeOnly"])
        self.assertNotIn("routeOnly", written["inbounds"][1]["sniffing"])  # без подмены — и без флага

    def test_only_an_xray_started_here_is_trusted_for_images(self):
        proc = types.SimpleNamespace(poll=lambda: None, terminate=lambda: None, wait=lambda timeout=None: 0,
                                     kill=lambda: None)
        alive = {"port": True}
        with patch.object(vpn_bridge, "is_enabled", lambda: True), patch.object(vpn_bridge.atexit, "register"), \
                patch.object(vpn_bridge, "_xray_proc", None), patch.object(vpn_bridge, "_bridge_desired", False), \
                patch.object(vpn_bridge, "_pinned", False), \
                patch.object(xray, "port_alive", lambda *a, **k: alive["port"]), \
                patch.object(xray, "prepare_runtime", lambda: Path("xray-runtime.json")), \
                patch.object(xray, "launch", lambda path: proc):
            self.assertTrue(vpn_bridge.activate())  # порт уже занят чужим процессом
            self.assertFalse(vpn_bridge.destinations_pinned())
            alive["port"] = False
            self.assertTrue(vpn_bridge.activate())  # запущен здесь, с рабочим конфигом
            alive["port"] = True
            self.assertTrue(vpn_bridge.destinations_pinned())
            vpn_bridge.shutdown()
            self.assertFalse(vpn_bridge.destinations_pinned())


class Hanging(Tunnel):
    """Поток к мосту, который CONNECT принял и молчит; помнит, закрыли ли его."""

    def __init__(self, written):
        super().__init__([], written)
        self.closed = False

    async def read(self, max_bytes, timeout=None):
        await asyncio.sleep(3600)

    async def aclose(self):
        self.closed = True


class Tls(Tunnel):
    """Поток через мост, который запоминает имя, с которым начат TLS."""

    names: list = []

    async def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        Tls.names.append(server_hostname)
        return self


class Safety(BridgeCase):
    async def test_a_hanging_tunnel_is_closed_on_its_deadline_and_on_cancel(self):
        self.answers = {("cdn.myanimelist.net", "A"): records((1, "93.184.216.34", 60))}
        streams = []

        class Stalled(httpcore.AsyncMockBackend):
            async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
                streams.append(Hanging([]))
                return streams[-1]

        backend = http_bridge.BridgeBackend(Stalled([]))
        with self.assertRaises(httpcore.ConnectTimeout):
            await backend.connect_tcp("cdn.myanimelist.net", 443, timeout=0.2)
        task = asyncio.create_task(backend.connect_tcp("cdn.myanimelist.net", 443, timeout=30))
        await asyncio.sleep(0.1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual([s.closed for s in streams], [True, True])

    async def test_doh_has_its_own_time_asks_both_records_at_once_and_keeps_the_answer_after_a_cancel(self):
        async def slow(req):
            await asyncio.sleep(0.3)
            kind = req.url.params["type"]
            return httpx.Response(200, json=records((1, "93.184.216.34", 60)) if kind == "A" else records())

        await self.doh.aclose()
        self.doh = httpx.AsyncClient(transport=httpx.MockTransport(slow))
        started = time.monotonic()
        stream = await http_bridge.BridgeBackend(Bridge([OPEN])).connect_tcp("cdn.myanimelist.net", 443, timeout=0.05)
        await stream.aclose()
        self.assertLess(time.monotonic() - started, 0.55)  # A и AAAA разом, а не друг за другом
        task = asyncio.create_task(http_bridge.resolve("s4.anilist.co"))
        await asyncio.sleep(0.05)
        task.cancel()
        await asyncio.sleep(0.4)
        self.assertEqual(http_bridge._names["s4.anilist.co"][1], ["93.184.216.34"])

    async def test_tls_goes_by_the_name_while_the_tunnel_goes_to_the_checked_address(self):
        self.answers = {("cdn.myanimelist.net", "A"): records((1, "93.184.216.34", 60))}
        written, Tls.names = [], []

        class Named(httpcore.AsyncMockBackend):
            async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
                return Tls([OPEN, PICTURE], written)

        transport = http_bridge.transport()
        transport._pool._network_backend._inner = Named([])
        async with httpx.AsyncClient(transport=transport) as client:
            self.assertEqual((await client.get("https://cdn.myanimelist.net/a.png")).content, b"PNG!")
        self.assertEqual((Tls.names, written[0][:31]), (["cdn.myanimelist.net"], b"CONNECT 93.184.216.34:443 HTTP/"))


class RealDoh(unittest.IsolatedAsyncioTestCase):
    async def test_the_doh_client_goes_through_the_bridge_layer_to_ips_and_checks_certificates(self):
        await http_bridge.shutdown()
        try:
            client = http_bridge._doh_client()
            pool = client._transport._pool
            self.assertIsInstance(pool._network_backend, http_bridge.BridgeBackend)
            self.assertEqual((pool._ssl_context.verify_mode, pool._ssl_context.check_hostname), (ssl.CERT_REQUIRED, True))
            for server in http_bridge.DOH_SERVERS:
                self.assertEqual(urlsplit(server).scheme, "https")
                ipaddress.ip_address(urlsplit(server).hostname)
        finally:
            await http_bridge.shutdown()
