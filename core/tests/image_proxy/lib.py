"""
Подставки проверок прокси картинок: подставной интернет напрямую и через мост с записью запросов, временная папка
кеша, пустые записи маршрутов, свой предел промахов, запрос к /proxy/img от посетителя с ответом или его ошибкой,
запись кеша старше срока, маленький список общих суффиксов.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/tests/image_proxy: вход (core/tests/test_image_proxy.py) собирает части по порядку.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from starlette.requests import Request

from core.api.sections import catalog
from core.classes import http, image_cache, image_fetch, image_limits, image_proxy
from core.classes.vpn import bridge as vpn_bridge
from core.config import config
from core.functions import public_suffix, rate_limit, site_settings

MAL = "https://cdn.myanimelist.net/images/anime/1/1.jpg"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
# Часть настоящего списка: обычные зоны, частные суффиксы, правило со звёздочкой и исключение
SUFFIXES = "// ===BEGIN ICANN DOMAINS===\ncom\nuk\nco.uk\nio\nnet\ndev\nck\n*.ck\n!www.ck\nрф\n" \
           "// ===BEGIN PRIVATE DOMAINS===\ngithub.io\ns3.amazonaws.com\nr2.dev\nduckdns.org\n"


def image(body: bytes = PNG, content_type: str = "image/png", **headers) -> httpx.Response:
    """Ответ хоста с картинкой."""
    return httpx.Response(200, headers={"content-type": content_type, **headers}, content=body)


def request(ip: str = "203.0.113.7") -> Request:
    """Запрос браузера к /proxy/img через nginx перед сайтом: адрес посетителя — в X-Real-IP."""
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    return Request({"type": "http", "method": "GET", "path": "/proxy/img", "raw_path": b"/proxy/img",
                    "query_string": b"", "headers": [(b"x-real-ip", ip.encode())], "client": ("127.0.0.1", 50000)},
                   receive)


class Net:
    """Подставной интернет одного пути: ответ — по адресу запроса (функция или исключение), запросы записываются."""

    def __init__(self, answers=None):
        self.answers, self.seen = dict(answers or {}), []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.seen.append(str(req.url))
        answer = self.answers.get(str(req.url), lambda: httpx.Response(404))
        if isinstance(answer, Exception):
            raise answer
        return answer()

    def client(self) -> httpx.AsyncClient:
        """Клиент этого пути."""
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


class ProxyCase(unittest.IsolatedAsyncioTestCase):
    """Кеш — во временной папке, панель — пустая, маршруты, выборки и промахи — свои у каждой проверки."""

    def setUp(self):
        self.cache = Path(tempfile.mkdtemp(prefix="asoplay-img-"))
        self.addCleanup(shutil.rmtree, self.cache, ignore_errors=True)
        self.direct, self.vpn = Net(), Net()
        self.bridge_on = False
        self.remembered = []
        suffixes = self.cache.parent / f"{self.cache.name}.dat"
        suffixes.write_text(SUFFIXES, encoding="utf-8")
        self.addCleanup(suffixes.unlink)
        for item in (patch.object(config, "IMG_CACHE_DIR", self.cache),
                     patch.object(config, "PUBLIC_SUFFIX_FILE", suffixes),
                     patch.object(site_settings, "state", site_settings._State()),
                     patch.object(image_proxy, "routes", {}), patch.object(image_proxy, "_pending", {}),
                     patch.object(image_proxy, "slots", image_limits.Slots()),
                     patch.object(image_proxy, "breaker", image_limits.Breaker()),
                     patch.object(catalog, "_IMG_MISSES", rate_limit.SlidingWindow(limit=240, window=60)),
                     patch.object(image_fetch, "client", lambda route: self.clients[route]),
                     patch.object(vpn_bridge, "proxy_url", lambda: "http://127.0.0.1:9" if self.bridge_on else None),
                     patch.object(http, "route_order", lambda host: ["direct", "vpn"] if self.bridge_on else ["direct"]),
                     patch.object(http, "remember_route", lambda host, route: self.remembered.append((host, route)))):
            item.start()
            self.addCleanup(item.stop)
        public_suffix._rules.cache_clear()
        self.addCleanup(public_suffix._rules.cache_clear)
        image_cache.forget_size()
        self.addCleanup(image_cache.forget_size)

    async def asyncSetUp(self):
        self.clients = {"direct": self.direct.client(), "vpn": self.vpn.client()}

    async def asyncTearDown(self):
        for client in self.clients.values():
            await client.aclose()

    def panel(self, *rows):
        """Список хостов панели: строки (host, via)."""
        site_settings.state.settings = site_settings.Settings(
            proxy_hosts=tuple({"host": host, "via": via} for host, via in rows))

    async def get(self, url=MAL, ip="203.0.113.7"):
        """Ответ /proxy/img посетителю или его HTTPException."""
        try:
            return await catalog.proxy_img(request(ip), url)
        except HTTPException as exc:
            return exc


def expire(path):
    """Сделать запись кеша старше срока: время выборки в её строке — на TTL и минуту раньше."""
    head, _, body = path.read_bytes().partition(b"\n")
    info = json.loads(head)
    info["at"] -= image_cache.TTL + 60
    path.write_bytes(json.dumps(info).encode() + b"\n" + body)
