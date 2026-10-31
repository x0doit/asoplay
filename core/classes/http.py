"""
Как ходить наружу: прямой клиент и клиент через VPN-мост, маршрут для хоста, темп запросов к MAL.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Каждый апстрим сначала пробуется напрямую; хост, который ответил только через VPN-мост, ходит через него первым
VPN_STICKY_SECONDS. Ограничитель MAL_LIMIT стоит здесь, рядом с request(): он задаёт темп исходящих запросов.
Кеш на диске и запросы через него — core/classes/http_cache.py, маршруты каталога — core/api/sections/catalog.py.
"""
from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

import httpx

from core.classes.vpn import bridge as vpn_bridge

log = logging.getLogger("asoplay.proxies")

_httpx_limits = httpx.Limits(
    max_connections=50, max_keepalive_connections=20, keepalive_expiry=60
)
_http_client: httpx.AsyncClient | None = None
_vpn_client: httpx.AsyncClient | None = None
_vpn_client_proxy: str | None = None
# Hosts that failed directly but answered through the VPN bridge use it first for
# a while, so later requests do not wait for the direct timeout again.
VPN_STICKY_SECONDS = 600.0
_vpn_preferred_until: dict[str, float] = {}

NETWORK_ERRORS: tuple[type[BaseException], ...] = (httpx.HTTPError, OSError, TimeoutError, asyncio.TimeoutError)
try:
    import anyio

    NETWORK_ERRORS += (anyio.ClosedResourceError,)
except Exception:
    pass


async def get_client() -> httpx.AsyncClient:
    """The direct client: every upstream is tried without the VPN first."""
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            limits=_httpx_limits,
            trust_env=False,
        )
    return _http_client


async def get_vpn_client() -> httpx.AsyncClient | None:
    """A client through the VPN bridge, or None while the bridge is off or down."""
    global _vpn_client, _vpn_client_proxy
    proxy = vpn_bridge.proxy_url()
    if proxy is None:
        return None
    if _vpn_client is None or _vpn_client.is_closed or _vpn_client_proxy != proxy:
        if _vpn_client is not None and not _vpn_client.is_closed:
            await _vpn_client.aclose()
        _vpn_client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            limits=_httpx_limits,
            trust_env=False,
            proxy=proxy,
        )
        _vpn_client_proxy = proxy
    return _vpn_client


def route_order(host: str) -> list[str]:
    """Routes to try for a host: direct first, unless it recently needed the VPN."""
    if vpn_bridge.proxy_url() is None:
        return ["direct"]
    if _vpn_preferred_until.get(host, 0) > time.monotonic():
        return ["vpn", "direct"]
    return ["direct", "vpn"]


def remember_route(host: str, route: str) -> None:
    """Запомнить маршрут, который ответил: VPN — на VPN_STICKY_SECONDS вперёд, прямой — забыть VPN для хоста."""
    if route == "vpn":
        _vpn_preferred_until[host] = time.monotonic() + VPN_STICKY_SECONDS
    else:
        _vpn_preferred_until.pop(host, None)


def _first_route_timeout(timeout):
    # A dead first route must fail fast enough to leave the second one some budget.
    """Таймаут первого из двух маршрутов: соединение не дольше 2,5 секунды, чтобы второму осталось время."""
    if isinstance(timeout, httpx.Timeout):
        return httpx.Timeout(connect=min(timeout.connect or 2.5, 2.5), read=timeout.read,
                             write=timeout.write, pool=timeout.pool)
    if isinstance(timeout, (int, float)):
        return httpx.Timeout(timeout, connect=min(timeout, 2.5))
    return timeout


async def request(method: str, url: str, **kwargs) -> httpx.Response:
    """Send one request directly, or through the VPN bridge when the host is
    unreachable directly. The route that answered is remembered for the host."""
    host = urlparse(url).hostname or ""
    routes = route_order(host)
    last_exc: BaseException | None = None
    for n, route in enumerate(routes):
        client = await (get_vpn_client() if route == "vpn" else get_client())
        if client is None:
            continue
        options = kwargs
        if n == 0 and len(routes) > 1:
            options = dict(kwargs, timeout=_first_route_timeout(kwargs.get("timeout", 15.0)))
        try:
            response = await client.request(method, url, **options)
        except NETWORK_ERRORS as exc:
            last_exc = exc
            log.info("%s route to %s failed: %s", route, host, str(exc) or type(exc).__name__)
            continue
        remember_route(host, route)
        return response
    if last_exc is not None:
        raise last_exc
    raise httpx.ConnectError(f"no route to {host}")


# ---------- rate limiter ----------
class _RateLimiter:
    """One request per self.min_gap seconds, serialized via asyncio.Lock."""

    def __init__(self, rps: float):
        """Не чаще rps запросов в секунду."""
        self.min_gap = 1.0 / rps
        self.last = 0.0
        self.lock: asyncio.Lock | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def _lock(self) -> asyncio.Lock:
        """Замок текущего цикла событий: в новом цикле (например, в проверках) — новый."""
        loop = asyncio.get_running_loop()
        if self.lock is None or self.loop is not loop:
            self.lock = asyncio.Lock()
            self.loop = loop
        return self.lock

    async def wait(self) -> None:
        """Дождаться своей очереди: между запросами не меньше min_gap секунд."""
        async with self._lock():
            now = time.monotonic()
            wait = self.last + self.min_gap - now
            if wait > 0:
                await asyncio.sleep(wait)
                self.last = time.monotonic()
            else:
                self.last = now


# The embedded parser shares this per-process limit for uncached MAL pages.
MAL_LIMIT = _RateLimiter(rps=1.0)


async def shutdown() -> None:
    """Закрыть оба клиента и забыть, каким хостам нужен VPN."""
    global _http_client, _vpn_client, _vpn_client_proxy
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None
    if _vpn_client and not _vpn_client.is_closed:
        await _vpn_client.aclose()
    _vpn_client = None
    _vpn_client_proxy = None
    _vpn_preferred_until.clear()
