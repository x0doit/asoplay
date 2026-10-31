"""
Сетевой слой клиента плеера (core/classes/http_public.py): публичные адреса по таблице, отказ имени с любым
непубличным адресом до подключения, подключение к проверенному адресу, а не к имени; слой стоит в клиенте плеера
и проверяет адрес каждого шага.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/player_guard: вход (core/tests/test_player_guard.py) собирает части по порядку.
"""
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException

from core.classes import http_public
from core.modules.player import routes as player
from core.tests.player_guard.stubs import Patched, Recorder, addresses, request


class NetworkLayer(unittest.IsolatedAsyncioTestCase):
    def test_public_addresses(self):
        for ip in ("93.184.216.34", "2606:2800:220:1::1", "64:ff9b::808:808"):
            self.assertTrue(http_public.is_public(ip), ip)
        for ip in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1", "0.0.0.0",
                   "224.0.0.1", "240.0.0.1", "255.255.255.255", "::1", "::", "fe80::1", "fc00::1", "ff02::1",
                   "::ffff:10.0.0.1", "64:ff9b::a00:1", "2002:7f00:1::1"):
            self.assertFalse(http_public.is_public(ip), ip)

    async def test_name_with_any_non_public_address_is_refused_before_connecting(self):
        for ips in (("10.0.0.5",), ("127.0.0.1",), ("::ffff:127.0.0.1",), ("169.254.169.254",), ("::1",),
                    ("100.64.0.1",), ("93.184.216.34", "10.0.0.5"), ("2606:2800:220:1::1", "fe80::1")):
            inner = Recorder([b""])
            layer = http_public.PublicOnlyBackend(inner)
            with patch.object(http_public.anyio, "getaddrinfo", AsyncMock(return_value=addresses(*ips))):
                with self.assertRaises(http_public.Refused, msg=ips):
                    await layer.connect_tcp("kodikplayer.com", 443)
            self.assertEqual(inner.hosts, [], ips)

    async def test_public_name_connects_to_its_checked_address_not_the_name(self):
        inner = Recorder([b""])
        layer = http_public.PublicOnlyBackend(inner)
        with patch.object(http_public.anyio, "getaddrinfo",
                          AsyncMock(return_value=addresses("93.184.216.34", "2606:2800:220:1::1"))):
            await layer.connect_tcp("kodikplayer.com", 443)
        self.assertEqual(inner.hosts, ["2606:2800:220:1::1"])  # первым — IPv6, как у anyio

    async def test_player_client_checks_the_address_of_every_hop(self):
        # Шаг на имя из списка, у которого адрес внутренний: отказ сетевого слоя, к нему не подключаются
        transport = http_public.transport()
        layer = transport._pool._network_backend
        self.assertIsInstance(layer, http_public.PublicOnlyBackend)
        layer._inner = inner = Recorder(
            [b"HTTP/1.1 302 Found\r\nLocation: https://cdn.kodikplayer.com/x\r\nContent-Length: 0\r\n\r\n"])
        dns = {"kodikplayer.com": addresses("93.184.216.34"), "cdn.kodikplayer.com": addresses("10.0.0.5")}

        async def resolve(host, port, **kwargs):
            return dns[host]

        with Patched(httpx.AsyncClient(transport=transport)), \
             patch.object(http_public.anyio, "getaddrinfo", resolve):
            with self.assertRaises(HTTPException) as caught:
                await player._fetch(request(), "https://kodikplayer.com/x", "https://kodikplayer.com/x")
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(inner.hosts, ["93.184.216.34"])

    async def test_running_player_client_has_the_layer(self):
        await player.shutdown()
        try:
            client = await player._get_client()
            self.assertIsInstance(client._transport._pool._network_backend, http_public.PublicOnlyBackend)
            self.assertFalse(client.follow_redirects)
        finally:
            await player.shutdown()
