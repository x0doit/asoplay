"""
Источники видео: реестр, ключи найденного и вызов источника — напрямую, а если так не вышло, через мост VPN.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Реестр — скрейперы anicli-api (SOURCES) и свои клиенты (NATIVE_SOURCES, core/classes/sources); они грузятся при
импорте этого модуля. Найденное — тайтл, серия, озвучка — хранится в памяти под ключом: раздел /src/…
(core/api/sections/sources) отдаёт ключ фронтенду и по нему берёт объект на следующем шаге. source_call зовёт
источник сначала напрямую, а если он так недоступен — тем же шагом через мост VPN; сбой засчитывается источнику
(core/functions/source_health.py), только если не помог ни один маршрут. Плееры остаются прямыми: их ссылки на поток
привязаны к IP, который их открыл.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

import httpx
from fastapi import HTTPException

from core.classes import http
from core.classes.sources.animevost import Animevost
from core.classes.sources.oldyummy import OldYummy
from core.classes.vpn import bridge as vpn_bridge
from core.functions import site_settings, source_health

log = logging.getLogger("asoplay")  # тот же журнал, что у core.py до деления


# ---------- source registry ----------
SOURCES: dict[str, Any] = {}
NATIVE_SOURCES: dict[str, Any] = {}


def _register(name: str, import_paths: list[str]) -> None:
    """Первый модуль из import_paths, где есть Extractor, — в реестр под именем name; ни одного — предупреждение."""
    for path in import_paths:
        try:
            module = __import__(path, fromlist=["Extractor"])
            cls = getattr(module, "Extractor", None)
            if cls is None:
                continue
            SOURCES[name] = cls()
            log.info("source loaded: %s (%s)", name, path)
            return
        except Exception as exc:
            log.debug("skip %s (%s): %s", name, path, exc)
    log.warning("source not available: %s", name)


for _name in (
    "yummy_anime",
    "animego",
    "yummy_anime_org",
    "dreamcast",
    "anilibria",
    "anilibme",
    "sameband",
):
    _register(_name, [f"anicli_api.source.{_name}"])

try:
    NATIVE_SOURCES["oldyummy"] = OldYummy()
    log.info("source loaded: oldyummy (native)")
except Exception as exc:
    log.warning("native oldyummy failed to load: %s", exc)
try:
    NATIVE_SOURCES["animevost"] = Animevost()
    log.info("source loaded: animevost (native)")
except Exception as exc:
    log.warning("native animevost failed to load: %s", exc)

if not SOURCES:
    log.error("ни один источник anicli-api не загрузился. pip install anicli-api")


# ---------- in-memory key cache for source objects ----------
_CACHE: dict[str, Any] = {}


def put(obj: Any) -> str:
    """Запомнить найденное под новым ключом; больше 4000 — старейшая тысяча забывается."""
    k = uuid.uuid4().hex
    _CACHE[k] = obj
    if len(_CACHE) > 4000:
        for old in list(_CACHE.keys())[:1000]:
            _CACHE.pop(old, None)
    return k


def get(key: str) -> Any:
    """Найденное по ключу; забыто — 410 «повторите поиск»."""
    obj = _CACHE.get(key)
    if obj is None:
        raise HTTPException(410, "объект устарел, повторите поиск")
    return obj


def get_extractor(source: str):
    """Скрейпер anicli-api по имени; нет такого — 400 со списком доступных."""
    if source not in SOURCES:
        raise HTTPException(400, f"source '{source}' недоступен. Доступные: {list(SOURCES)}")
    return SOURCES[source]


async def call(fn, *args, **kwargs):
    """Вызвать fn: корутину — дождаться, обычную функцию — в отдельном потоке."""
    if asyncio.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return await asyncio.to_thread(fn, *args, **kwargs)


def all_sources() -> list[str]:
    """Все загруженные источники: сначала anicli-api, затем свои."""
    return list(SOURCES) + list(NATIVE_SOURCES)


def _source_unavailable(source: str) -> HTTPException:
    """503: источник остывает после череды сбоев — повторить через столько-то секунд."""
    wait = source_health.cooldown_remaining(source)
    return HTTPException(
        503,
        f"source '{source}' is cooling down after repeated failures; retry in {wait}s",
    )


async def guard_source(source: str, op: str, awaitable, timeout: float):
    """Дождаться шага источника за timeout и засчитать ему успех или сбой (source_health). Остывающий источник —
    сразу 503, а сам шаг отменяется."""
    if not source_health.is_available(source):
        cancel = getattr(awaitable, "cancel", None)
        close = getattr(awaitable, "close", None)
        if callable(cancel):
            cancel()
        elif callable(close):
            close()
        raise _source_unavailable(source)
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(awaitable, timeout=timeout)
    except HTTPException:
        raise
    except asyncio.TimeoutError as exc:
        source_health.mark_failure(source, f"{op} timeout after {timeout}s")
        raise HTTPException(504, f"{op} timeout") from exc
    except Exception as exc:
        source_health.mark_failure(source, exc)
        raise
    else:
        elapsed = int((time.perf_counter() - started) * 1000)
        source_health.mark_success(source, elapsed)
        return result


# ---------- direct-first source routing ----------
# Sources are reached directly. When a source fails that way, the same step is
# repeated through the VPN bridge, and the source keeps using the bridge for a
# while (http.route_order). Players stay direct: their stream links are bound
# to the IP that opened them.
_ROUTE_ERRORS = (httpx.TransportError, OSError, asyncio.TimeoutError, TimeoutError)
_vpn_http: tuple[str, Any, Any] | None = None
_vpn_extractors: dict[str, Any] = {}
_vpn_natives: dict[str, tuple[str, Any]] = {}


def _bridge_proxy() -> str:
    """Адрес моста VPN; моста нет — ConnectError, как у недоступной сети."""
    proxy = vpn_bridge.proxy_url()
    if proxy is None:
        raise httpx.ConnectError("VPN bridge is not available")
    return proxy


def _anicli_http(source: str, route: str) -> tuple[Any, Any]:
    """Клиенты anicli-api для маршрута: прямые — свои у источника, через VPN — общие, новые при смене моста."""
    global _vpn_http
    if route == "direct":
        return SOURCES[source].http, SOURCES[source].http_async
    proxy = _bridge_proxy()
    if _vpn_http is None or _vpn_http[0] != proxy:
        from anicli_api._http import HTTPAsync, HTTPSync
        _vpn_http = (proxy, HTTPSync(proxy=proxy, trust_env=False), HTTPAsync(proxy=proxy, trust_env=False))
    return _vpn_http[1], _vpn_http[2]


def extractor(source: str, route: str):
    """Скрейпер источника для маршрута: прямой — из реестра, через VPN — его копия на клиентах моста."""
    if route == "direct":
        return SOURCES[source]
    sync_client, async_client = _anicli_http(source, route)
    ext = _vpn_extractors.get(source)
    if ext is None or ext.http is not sync_client:
        ext = type(SOURCES[source])(http_client=sync_client, http_async_client=async_client)
        _vpn_extractors[source] = ext
    return ext


def on_route(source: str, obj, route: str):
    """Point a cached anicli result at a route's clients; its children inherit them."""
    if hasattr(obj, "http") and hasattr(obj, "http_async"):
        obj.http, obj.http_async = _anicli_http(source, route)
    return obj


def native(source: str, route: str):
    """Свой клиент источника для маршрута: прямой — из реестра, через VPN — его копия с прокси моста."""
    if route == "direct":
        return NATIVE_SOURCES[source]
    proxy = _bridge_proxy()
    cached = _vpn_natives.get(source)
    if cached is None or cached[0] != proxy:
        cached = (proxy, type(NATIVE_SOURCES[source])(proxy=proxy))
        _vpn_natives[source] = cached
    return cached[1]


async def source_call(source: str, op: str, run, timeout: float):
    """Await run(route) directly and, when the source is unreachable that way,
    through the VPN bridge. A failure counts against the source only when every
    route failed, so a direct outage that the bridge covers never cools it down."""
    if not source_health.is_available(source):
        raise _source_unavailable(source)
    route_key = f"source:{source}"
    routes = http.route_order(route_key)
    deadline = time.monotonic() + timeout
    started = time.perf_counter()
    last_exc: BaseException | None = None
    for n, route in enumerate(routes):
        remaining = deadline - time.monotonic()
        if remaining <= 0.2:
            break
        # The first route leaves at least half of the budget to the second one.
        budget = remaining / 2 if n + 1 < len(routes) else remaining
        try:
            result = await asyncio.wait_for(run(route), timeout=budget)
        except HTTPException:
            raise
        except _ROUTE_ERRORS as exc:
            last_exc = exc
            log.info("%s %s failed via %s: %s", source, op, route, str(exc) or type(exc).__name__)
            continue
        except Exception as exc:
            source_health.mark_failure(source, exc)
            raise
        http.remember_route(route_key, route)
        source_health.mark_success(source, int((time.perf_counter() - started) * 1000))
        return result
    source_health.mark_failure(source, last_exc or f"{op} timeout after {timeout}s")
    if last_exc is None or isinstance(last_exc, (asyncio.TimeoutError, TimeoutError)):
        raise HTTPException(504, f"{op} timeout")
    raise HTTPException(502, f"{op} failed: {last_exc}")


# ---------- источники для плеера ----------
def player_sources() -> list[str]:
    """Источники для плеера: включённые в панели Anime Social, в её порядке, без тех, что сейчас остывают."""
    ready = set(source_health.available_sources(all_sources()))
    return [s for s in site_settings.source_order() if s in ready]


# ---------- остановка ----------
async def shutdown() -> None:
    """Закрыть клиенты источников, открытые через мост VPN (core.py — при остановке сервера)."""
    for _proxy, client in list(_vpn_natives.values()):
        await client.close()
    if _vpn_http is not None:
        await _vpn_http[2].aclose()
        _vpn_http[1].close()
