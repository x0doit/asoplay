"""
Картинка с хоста для /proxy/img одним путём: клиенты напрямую и через мост, шаги перенаправлений, проверка ответа.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Напрямую — клиент на core/classes/http_public.py: свой DNS, подключение только к публичным адресам. Через мост —
клиент на core/classes/http_bridge.py: DoH по мосту и CONNECT ровно на проверенный адрес. Оба без кук (клиенты свои,
не общие с каталогом) и без сжатия: просят Accept-Encoding: identity, а ответ со сжатием отклоняют — распаковка
раздула бы его в памяти до проверки размера.

Перенаправления — вручную, не больше MAX_HOPS; каждый шаг проходит allowed, с https на http — отказ. Ответ — только
200 с типом из TYPES (наружу и в кеш — приведённый тип), не больше MAX_BYTES. Ошибки: Refused — адрес не разрешён
(403); AddressRefused — адрес этого пути не публичный (свой DNS ответил заглушкой — можно попробовать мост);
Failed — не достали: network — сетевой сбой пути, upstream — код ответа хоста.
"""
from __future__ import annotations

from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import Callable

import httpx

from core.classes import http, http_bridge, http_public

TYPES = {"image/jpeg": "image/jpeg", "image/jpg": "image/jpeg", "image/pjpeg": "image/jpeg", "image/png": "image/png",
         "image/gif": "image/gif", "image/webp": "image/webp", "image/avif": "image/avif"}
MAX_BYTES = 8 * 1024 * 1024
MAX_HOPS = 3
TIMEOUT = httpx.Timeout(15.0, connect=8.0)
# Первый из двух путей: мёртвое соединение должно быстро уступить второму, как в http.request()
FIRST_OF_TWO = httpx.Timeout(15.0, connect=2.5)
_REDIRECTS = {301, 302, 303, 307, 308}
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Encoding": "identity"}

_clients: dict[str, httpx.AsyncClient] = {}


class Refused(Exception):
    """Картинку не берём: адрес шага не прошёл allowed или перенаправление с https на http (ответ 403). upstream —
    код ответа хоста, который так перенаправил (хост ответил); у первого шага — None."""

    def __init__(self, reason: str, *, upstream: int | None = None):
        """reason — что не так, upstream — код перенаправления, если отказ пришёл на шаге после него."""
        super().__init__(reason)
        self.upstream = upstream


class AddressRefused(Refused):
    """Адрес хоста на этом пути не публичный — например, свой DNS ответил заглушкой; другой путь может подойти."""


class Failed(Exception):
    """Картинку не достали. status — код ответа сайта; network — сетевой сбой пути; upstream — код ответа хоста."""

    def __init__(self, status: int, reason: str, *, network: bool = False, upstream: int | None = None):
        """status — код ответа сайта, reason — что случилось, network и upstream — для повтора и для /summary."""
        super().__init__(reason)
        self.status, self.network, self.upstream = status, network, upstream


def client(route: str) -> httpx.AsyncClient:
    """Клиент пути: direct — напрямую к публичным адресам, vpn — через мост к проверенным. Без кук."""
    found = _clients.get(route)
    if found is None or found.is_closed:
        layer = http_public if route == "direct" else http_bridge
        no_cookies = CookieJar(DefaultCookiePolicy(allowed_domains=[]))
        found = _clients[route] = httpx.AsyncClient(
            transport=layer.transport(trust_env=False), trust_env=False, follow_redirects=False, timeout=TIMEOUT,
            cookies=no_cookies)
    return found


async def shutdown() -> None:
    """Закрыть клиенты путей и клиент DoH моста."""
    for found in list(_clients.values()):
        await found.aclose()
    _clients.clear()
    await http_bridge.shutdown()


async def _open(route: str, url: str, allowed: Callable[[httpx.URL], bool], timeout: httpx.Timeout) -> httpx.Response:
    """Поток ответа на картинку; перенаправления — вручную, каждый шаг проходит allowed. Поток закрывает
    вызывающий."""
    target: httpx.URL | str = url
    scheme = ""
    hop: int | None = None  # код последнего перенаправления: отказ после него — «хост ответил»
    for _ in range(MAX_HOPS + 1):
        try:
            request = client(route).build_request("GET", target, headers=_HEADERS, timeout=timeout)
        except (httpx.InvalidURL, ValueError) as exc:
            raise Failed(502, "bad redirect", upstream=hop) from exc
        if not allowed(request.url):
            raise Refused(f"not allowed: {request.url.host}", upstream=hop)
        if scheme == "https" and request.url.scheme != "https":
            raise Refused(f"https to http: {request.url.host}", upstream=hop)
        scheme = request.url.scheme
        response = await client(route).send(request, stream=True, follow_redirects=False)
        location = response.headers.get("location")
        if response.status_code not in _REDIRECTS or not location:
            return response
        hop = response.status_code
        await response.aclose()
        try:
            target = request.url.join(location)
        except (httpx.InvalidURL, ValueError) as exc:
            raise Failed(502, "bad redirect", upstream=hop) from exc
    raise Failed(502, "too many redirects", upstream=hop)


async def _read(response: httpx.Response) -> tuple[bytes, str]:
    """Тело картинки: только 200, тип из TYPES, без сжатия, не больше MAX_BYTES; поток закрывается всегда."""
    try:
        status = response.status_code
        if status != 200:
            raise Failed(status if 400 <= status < 600 else 502, f"upstream {status}", upstream=status)
        raw_type = response.headers.get("content-type", "")
        media = raw_type.split(";", 1)[0].strip().lower()
        if "," in raw_type or media not in TYPES:
            raise Failed(502, "not an image", upstream=status)
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            raise Failed(502, "compressed answer", upstream=status)
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > MAX_BYTES:
            raise Failed(502, "too large", upstream=status)
        body = bytearray()
        async for chunk in _raw(response):
            body += chunk
            if len(body) > MAX_BYTES:
                raise Failed(502, "too large", upstream=status)
        return bytes(body), TYPES[media]
    finally:
        await response.aclose()


async def _raw(response: httpx.Response):
    """Тело ответа как пришло, по кускам; уже прочитанное в память (ответ подставного транспорта) — целиком."""
    if response.is_stream_consumed:
        yield response.content
        return
    async for chunk in response.aiter_raw():
        yield chunk


async def get(route: str, url: str, allowed: Callable[[httpx.URL], bool],
              timeout: httpx.Timeout = TIMEOUT) -> tuple[bytes, str]:
    """Картинка одним путём: (тело, приведённый тип). Refused, AddressRefused или Failed."""
    try:
        return await _read(await _open(route, url, allowed, timeout))
    except http_public.Refused as exc:
        raise AddressRefused(str(exc)) from exc
    except http.NETWORK_ERRORS as exc:
        raise Failed(502, f"{route}: {type(exc).__name__}", network=True) from exc
