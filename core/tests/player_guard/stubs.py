"""
Подставки проверок прокси плеера: запрос браузера, подставной интернет с записью запросов, тело ответа, которое
помнит закрытие, ответы getaddrinfo и сетевой слой с записью подключений; адреса для проверок.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/player_guard: вход (core/tests/test_player_guard.py) собирает части по порядку.
"""
import socket
from unittest.mock import AsyncMock, patch

import httpcore
import httpx
from starlette.requests import Request

from core.modules.player import routes as player

KODIK = "https://kodikplayer.com/serial/1/abc/720p"
AKSOR = "https://player.aksor.tv/video/1"
SANDBOX = "sandbox allow-scripts allow-presentation"
# Внутренние адреса и IP-литералы: по имени их нет в списке, а адреса — не публичные
INTERNAL = ("http://127.0.0.1:8787/health", "http://10.0.0.1/", "http://169.254.169.254/latest/meta-data/",
            "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://8.8.8.8/", "http://2130706433/", "http://127.1/")
# Чужие хосты и обходы сравнения: похожее имя, логин в адресе, сайт и API Yummy на yani.tv
FOREIGN = ("https://example.com/", "https://kodikplayer.com.evil.test/", "https://evilkodikplayer.com/",
           "https://kodikpl" + chr(0x430) + "yer.com/", "https://yani.tv/", "https://api.yani.tv/v1/anime",
           "https://kodikplayer.com@evil.test/", "https://user:pw@kodikplayer.com/", "https://evil.test\\@kodikplayer.com/")


def request(method="GET", path="/player/proxy", raw_path=None, query=b""):
    """Запрос браузера к прокси с пустым телом."""
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    return Request({"type": "http", "method": method, "path": path, "raw_path": raw_path or path.encode(),
                    "query_string": query, "headers": []}, receive)


class Body(httpx.AsyncByteStream):
    """Тело ответа, которое помнит, закрыли ли его."""

    def __init__(self, data: bytes):
        self.data, self.closed = data, False

    async def __aiter__(self):
        yield self.data

    async def aclose(self):
        self.closed = True


class Upstream:
    """Подставной интернет для клиента плеера: ответ — по адресу запроса, все запросы записываются."""

    def __init__(self, answers=None):
        self.answers, self.seen = answers or {}, []

    def __call__(self, req):
        self.seen.append(req)
        return self.answers[str(req.url)]()

    def patched(self):
        """Клиент плеера и adblock подменены: сеть не трогается, реклама не мешает."""
        client = httpx.AsyncClient(transport=httpx.MockTransport(self))
        return Patched(client)


class Patched:
    """Контекст с подменёнными клиентом плеера и adblock."""

    def __init__(self, client):
        self.patches = (patch.object(player, "_get_client", AsyncMock(return_value=client)),
                        patch.object(player.adblock, "should_block", return_value=(False, "")))

    def __enter__(self):
        for item in self.patches:
            item.start()

    def __exit__(self, *exc):
        for item in self.patches:
            item.stop()


def addresses(*ips):
    """Ответ getaddrinfo с этими адресами, IPv4 и IPv6."""
    return [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", (ip, 443, 0, 0)) if ":" in ip
            else (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ips]


class Recorder(httpcore.AsyncMockBackend):
    """Подставной сетевой слой под проверкой: записывает, к чему подключались, и отвечает буфером."""

    def __init__(self, buffer):
        super().__init__(buffer)
        self.hosts = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.hosts.append(host)
        return await super().connect_tcp(host, port, timeout, local_address, socket_options)
