# -*- coding: utf-8 -*-
"""
AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

FastAPI wiring. The heavy lifting lives in the core package (started with python -m core):

    classes/vpn/bridge.py        — xray launcher (started from FastAPI lifecycle, not imports)
    classes/http.py              — Jikan/AniList/Shiki/translate/img with shared disk cache
    classes/db.py                — DB bridge + auth (/auth/*)
    api/sections/account.py      — authenticated favorites/history/progress/ratings (/account/*)
    modules/title_pages.py       — canonical /anime/{id}-{slug}/ SSR (HTML: title_render, cache: title_cache)
    modules/seo.py               — sitemap + robots
    classes/sources/animevost.py — native client to api.animevost.org
    classes/sources/oldyummy.py  — native client to old.yummyani.me/api

This file owns: env loading, CORS, startup/shutdown hooks, source registry
(anicli-api + native sources), and the search/episodes/dubs/videos routes that
every source plugin shares.
"""
from __future__ import annotations

import asyncio
import logging
import random as _random
import re as _re
import time
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger("asoplay")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from core.config import env

env.load()  # до остальных импортов: настройки в core/config/config.py читают окружение при импорте

# VPN must not start during import — that was the old bug. We arm it from the
# FastAPI startup event below. Importing the module is cheap and side-effect
# free.
from core.admin import auth as admin_auth, router as admin_router, summary as admin_summary
from core.api.sections import account, activity, auth, catalog, friends, lists, messages, social_login
from core.api.sections import realtime as realtime_ws
from core.classes import db, http, http_cache
from core.classes.adblock import engine as adblock
from core.classes.jikan import catalog as jikan_catalog
from core.classes.sources.animevost import Animevost
from core.classes.sources.oldyummy import OldYummy
from core.classes.vpn import bridge as vpn_bridge
from core.config import config
from core.functions import (
    lists_sweep, presence_batch, realtime, realtime_social, site_settings, source_health, video_reports,
)
from core.functions.player_hosts import (
    VK_SCORE, iframe_preference_score, is_disabled_player_url, vk_video_ref,
)
from core.modules import profile_pages, seo, title_pages
from core.modules.player import routes as player_proxy


import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exception_handlers import http_exception_handler, request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException


# ---------- source registry ----------
SOURCES: dict[str, Any] = {}
NATIVE_SOURCES: dict[str, Any] = {}


def _register(name: str, import_paths: list[str]) -> None:
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


# ---------- app ----------
log.info("CORS allowed origins: %s", config.ALLOWED_ORIGINS)

app = FastAPI(title="AsoPlay Backend", version="2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


@app.middleware("http")
async def _player_proxy_null_origin_cors(request: Request, call_next):
    # Proxied players run inside a sandbox without allow-same-origin. Browser
    # fetch/XHR requests from that frame therefore use Origin: null. Keep this
    # exception scoped to /player/proxy so account/auth APIs are not exposed to
    # arbitrary opaque-origin documents.
    if (
        (request.url.path.startswith("/player/proxy") or request.url.path.startswith("/player/cvh-api"))
        and request.headers.get("origin") == "null"
    ):
        if request.method == "OPTIONS":
            return Response(headers={
                "Access-Control-Allow-Origin": "null",
                "Access-Control-Allow-Methods": "GET, POST, HEAD, OPTIONS",
                "Access-Control-Allow-Headers": request.headers.get("access-control-request-headers", "*"),
                "Access-Control-Allow-Credentials": "true",
                "Access-Control-Max-Age": "600",
            })
        response = await call_next(request)
        response.headers["Access-Control-Allow-Origin"] = "null"
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
        return response
    return await call_next(request)


# ---------- lifecycle ----------
@app.on_event("startup")
async def _startup() -> None:
    # Start xray here, not at import time — tests and tooling can now import
    # the server module without spawning processes.
    if vpn_bridge.activate():
        log.info("VPN bridge active via %s", vpn_bridge.VPN_PROXY_URL)
    else:
        log.info("VPN bridge not active — falling back to direct outbound")
    vpn_bridge.start_watchdog()
    admin_summary.STARTED_AT = time.time()
    await asyncio.to_thread(site_settings.state.load)
    await jikan_catalog.startup()
    await adblock.startup()
    await player_proxy.startup()
    await presence_batch.startup()
    await realtime.startup()
    await lists_sweep.startup()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await lists_sweep.shutdown()
    await realtime.shutdown()
    await presence_batch.shutdown()
    await player_proxy.shutdown()
    await adblock.shutdown()
    await jikan_catalog.shutdown()
    await http_cache.shutdown()
    await http.shutdown()
    for _proxy, client in list(_vpn_natives.values()):
        await client.close()
    if _vpn_http is not None:
        await _vpn_http[2].aclose()
        _vpn_http[1].close()
    vpn_bridge.shutdown()


# ---------- in-memory key cache for source objects ----------
_CACHE: dict[str, Any] = {}


def _put(obj: Any) -> str:
    k = uuid.uuid4().hex
    _CACHE[k] = obj
    if len(_CACHE) > 4000:
        for old in list(_CACHE.keys())[:1000]:
            _CACHE.pop(old, None)
    return k


def _get(key: str) -> Any:
    obj = _CACHE.get(key)
    if obj is None:
        raise HTTPException(410, "объект устарел, повторите поиск")
    return obj


def _get_extractor(source: str):
    if source not in SOURCES:
        raise HTTPException(400, f"source '{source}' недоступен. Доступные: {list(SOURCES)}")
    return SOURCES[source]


async def _run(fn, *args, **kwargs):
    if asyncio.iscoroutinefunction(fn):
        return await fn(*args, **kwargs)
    return await asyncio.to_thread(fn, *args, **kwargs)


def _all_sources() -> list[str]:
    return list(SOURCES) + list(NATIVE_SOURCES)


def _source_unavailable(source: str) -> HTTPException:
    wait = source_health.cooldown_remaining(source)
    return HTTPException(
        503,
        f"source '{source}' is cooling down after repeated failures; retry in {wait}s",
    )


async def _guard_source(source: str, op: str, awaitable, timeout: float):
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
# while (proxies.route_order). Players stay direct: their stream links are bound
# to the IP that opened them.
_ROUTE_ERRORS = (httpx.TransportError, OSError, asyncio.TimeoutError, TimeoutError)
_vpn_http: tuple[str, Any, Any] | None = None
_vpn_extractors: dict[str, Any] = {}
_vpn_natives: dict[str, tuple[str, Any]] = {}


def _bridge_proxy() -> str:
    proxy = vpn_bridge.proxy_url()
    if proxy is None:
        raise httpx.ConnectError("VPN bridge is not available")
    return proxy


def _anicli_http(source: str, route: str) -> tuple[Any, Any]:
    global _vpn_http
    if route == "direct":
        return SOURCES[source].http, SOURCES[source].http_async
    proxy = _bridge_proxy()
    if _vpn_http is None or _vpn_http[0] != proxy:
        from anicli_api._http import HTTPAsync, HTTPSync
        _vpn_http = (proxy, HTTPSync(proxy=proxy, trust_env=False), HTTPAsync(proxy=proxy, trust_env=False))
    return _vpn_http[1], _vpn_http[2]


def _extractor(source: str, route: str):
    if route == "direct":
        return SOURCES[source]
    sync_client, async_client = _anicli_http(source, route)
    ext = _vpn_extractors.get(source)
    if ext is None or ext.http is not sync_client:
        ext = type(SOURCES[source])(http_client=sync_client, http_async_client=async_client)
        _vpn_extractors[source] = ext
    return ext


def _on_route(source: str, obj, route: str):
    """Point a cached anicli result at a route's clients; its children inherit them."""
    if hasattr(obj, "http") and hasattr(obj, "http_async"):
        obj.http, obj.http_async = _anicli_http(source, route)
    return obj


def _native(source: str, route: str):
    if route == "direct":
        return NATIVE_SOURCES[source]
    proxy = _bridge_proxy()
    cached = _vpn_natives.get(source)
    if cached is None or cached[0] != proxy:
        cached = (proxy, type(NATIVE_SOURCES[source])(proxy=proxy))
        _vpn_natives[source] = cached
    return cached[1]


async def _source_call(source: str, op: str, run, timeout: float):
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


# ---------- status endpoints ----------
def _player_sources() -> list[str]:
    """Источники для плеера: включённые в панели Anime Social, в её порядке, без тех, что сейчас остывают."""
    ready = set(source_health.available_sources(_all_sources()))
    return [s for s in site_settings.source_order() if s in ready]


# Вкладкам по живому соединению (/ws): то же, что им нужно из /health, без запроса в базу, и то, что
# решили в панели Anime Social (плашка, скрытые и 18+ тайтлы, способы входа) — правка видна сразу
realtime_social.status_provider = lambda: {
    "ok": True,
    "sources": _player_sources(),
    "vpn": vpn_bridge.is_active(),
    "site": site_settings.public_state(),
}
realtime_ws.extra_origins = {o.rstrip("/") for o in config.ALLOWED_ORIGINS if o != "*"}
site_settings.listeners.append(lambda _what: realtime.status_changed())
admin_summary.loaded_sources = _all_sources
# Каталог (core/classes/jikan) ниже site_settings и не импортирует его: скрытые по жалобе правообладателя
# тайтлы он берёт из функций, которые core.py регистрирует здесь, до первого запроса.
jikan_catalog.register_title_filter(hidden=site_settings.hidden, visible=site_settings.visible)


@app.get("/health")
def health():
    active_sources = _player_sources()
    return {
        "ok": True,
        "sources": active_sources,
        "vpn": vpn_bridge.is_active(),
        "catalog": jikan_catalog.status(),
        "db": db.health(),
        "adblock": adblock.status(),
        "source_health": source_health.snapshot(_all_sources()),
        "site": site_settings.public_state(),
    }


@app.get("/sources")
def sources():
    return source_health.available_sources(_all_sources())


@app.get("/source-health")
def source_health_status():
    return source_health.snapshot(_all_sources())


@app.get("/adblock/status")
def adblock_status():
    return adblock.status()


@app.get("/adblock/check")
def adblock_check(url: str = Query(..., min_length=4)):
    blocked, reason = adblock.should_block(url)
    return {"blocked": blocked, "reason": reason}


# ---------- source-based search / episodes / dubs / videos ----------
def _filter_by_year(results: list[dict], year: int | None) -> list[dict]:
    if not year:
        return results

    def _y(x):
        try:
            return int(str(x.get("year") or 0))
        except (TypeError, ValueError):
            return 0

    filtered = [r for r in results if _y(r) == 0 or abs(_y(r) - year) <= 1]
    return filtered or results


@app.get("/src/search")
async def search(
    q: str = Query(..., min_length=1),
    source: str = "anilibria",
    year: int | None = None,
    mal_id: int | None = None,
):
    if mal_id and site_settings.hidden(mal_id):
        # Скрыт из панели Anime Social по жалобе правообладателя — видео не ищем
        raise HTTPException(451, "unavailable")
    if not site_settings.source_enabled(source):
        return []
    if source in NATIVE_SOURCES:
        try:
            results = await _source_call(
                source,
                "search",
                lambda route: _native_search(source, q, mal_id=mal_id, route=route),
                timeout=10.0,
            )
        except Exception as exc:
            log.info("search failed on %s: %s (returning empty)", source, exc)
            return []
        return _filter_by_year(results, year)

    _get_extractor(source)

    def run_search(route):
        ext = _extractor(source, route)
        # The async API can be cancelled when a route runs out of time; a thread cannot.
        return _run(getattr(ext, "a_search", None) or ext.search, q)

    try:
        results = await _source_call(source, "search", run_search, timeout=10.0)
    except Exception as exc:
        # Source-скрейперы anicli-api (animego, dreamcast, yummy_anime_org…)
        # регулярно падают: у них сайт-цель или блокирует запрос, или сменил
        # вёрстку, или rate-limit'ит. Плеер перебирает источники в цепочку и
        # уже обрабатывает «пусто» как «иду дальше» — ему не нужен 502.
        # Возвращаем [] вместо throw, чтобы browser-консоль не пестрила
        # красным. Причину по-прежнему видно в server-логе.
        log.info("search failed on %s: %s (returning empty)", source, exc)
        return []

    out = []
    for r in results[:20]:
        title = getattr(r, "title", None) or getattr(r, "name", None) or str(r)
        thumb = getattr(r, "thumbnail", None) or getattr(r, "poster", None) or getattr(r, "image", None)
        url = getattr(r, "url", None)

        item_year = None
        mal_id_hit = None
        raw_data = getattr(r, "data", None)
        if isinstance(raw_data, dict):
            for k in ("year", "release_year", "releasedOn"):
                v = raw_data.get(k)
                if v:
                    try:
                        item_year = int(str(v)[:4])
                        break
                    except (TypeError, ValueError):
                        pass
            ri = raw_data.get("remote_ids")
            if isinstance(ri, dict):
                mal_id_hit = ri.get("myanimelist_id") or ri.get("mal_id")
                try:
                    mal_id_hit = int(mal_id_hit) if mal_id_hit else None
                except (TypeError, ValueError):
                    mal_id_hit = None
        if item_year is None:
            for attr in ("year", "release_year"):
                v = getattr(r, attr, None)
                if v:
                    try:
                        item_year = int(str(v)[:4])
                        break
                    except (TypeError, ValueError):
                        pass

        out.append({
            "title": title,
            "title_en": getattr(r, "title_english", None) or getattr(r, "alt_title", None),
            "thumbnail": thumb,
            "url": url,
            "year": item_year,
            "key": _put(r),
            "source": source,
            "mal_id": mal_id_hit,
        })
    return _filter_by_year(out, year)


@app.get("/src/episodes")
async def episodes(key: str, source: str = "anilibria"):
    if source in NATIVE_SOURCES:
        return await _source_call(
            source,
            "episodes",
            lambda route: _native_episodes(source, key, route=route),
            timeout=12.0,
        )

    search_result = _get(key)

    async def run_episodes(route):
        found = _on_route(source, search_result, route)
        get_anime = getattr(found, "a_get_anime", None) or found.get_anime
        anime = await _run(get_anime)
        get_eps = getattr(anime, "a_get_episodes", None) or anime.get_episodes
        return await _run(get_eps)

    try:
        episodes_list = await _source_call(source, "episodes", run_episodes, timeout=24.0)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("episodes failed")
        raise HTTPException(502, f"episodes failed: {exc}")

    out = []
    for idx, e in enumerate(episodes_list):
        num = getattr(e, "num", None) or getattr(e, "number", None) or idx + 1
        title = getattr(e, "title", None) or getattr(e, "name", None) or f"Эпизод {num}"
        out.append({"num": num, "title": title, "key": _put(e)})
    return out


def _norm_dub_name(name: str) -> str:
    s = (name or "").lower().strip()
    for prefix in ("озвучка ", "субтитры ", "оригинал ", "original ", "voice ", "sub "):
        if s.startswith(prefix):
            s = s[len(prefix):]
    s = _re.sub(r"\((ru|en|jp|ja|рус|русс?кий|англ\w*|яп\w*)\)", "", s)
    s = _re.sub(r"[^\w\s]+", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s


def _unplayable_player_url(url: str) -> bool:
    # VK plays only through the clean frame, which needs the owner/video ids.
    return is_disabled_player_url(url) and not vk_video_ref(url)


def _vk_last(identities: list, scores: dict) -> list:
    # VK is the last resort: its dubs follow every other player, order kept.
    return sorted(identities, key=lambda identity: scores[identity] <= VK_SCORE)


async def _filter_source_videos(videos: list[dict]) -> list[dict]:
    return await adblock.filter_videos([
        video for video in videos if not _unplayable_player_url(video.get("url", ""))
    ])


@app.get("/src/dubs")
async def dubs_for_episode(key: str, source: str = "anilibria"):
    if source in NATIVE_SOURCES:
        return await _guard_source(
            source,
            "dubs",
            _native_dubs(source, key),
            timeout=12.0,
        )

    episode = _get(key)

    def run_dubs(route):
        found = _on_route(source, episode, route)
        return _run(getattr(found, "a_get_sources", None) or found.get_sources)

    try:
        sources_list = await _source_call(source, "dubs", run_dubs, timeout=12.0)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("sources failed")
        raise HTTPException(502, f"sources failed: {exc}")

    seen: dict[tuple[str, str], dict] = {}
    scores: dict[tuple[str, str], int] = {}
    for s in sources_list:
        url = getattr(s, "url", None) or ""
        if _unplayable_player_url(url):
            continue
        name = getattr(s, "title", None) or getattr(s, "name", None) or "—"
        norm = _norm_dub_name(name)
        if not norm:
            continue
        kind = "sub" if "субтитры" in name.lower() or "sub" in name.lower() else "voice"
        identity = (norm, kind)
        score = iframe_preference_score(url)
        if identity in seen and score <= scores[identity]:
            continue
        scores[identity] = score
        seen[identity] = {"name": name.strip(), "norm": norm, "kind": kind, "key": _put(s)}
    return [seen[identity] for identity in _vk_last(list(seen), scores)]


@app.get("/src/videos")
async def videos(key: str, source: str = "anilibria"):
    if source in NATIVE_SOURCES:
        vids = await _guard_source(
            source,
            "videos",
            _native_videos(source, key),
            timeout=15.0,
        )
        return await _filter_source_videos(vids)

    obj = _get(key)
    if hasattr(obj, "get_videos") or hasattr(obj, "a_get_videos"):
        name = getattr(obj, "title", None) or getattr(obj, "name", None)
        iframe_url = getattr(obj, "url", None) or ""
        if _unplayable_player_url(iframe_url):
            return []

        if iframe_url.startswith("http"):
            return await _filter_source_videos([{
                "url": iframe_url, "quality": None, "type": "iframe",
                "headers": {}, "source_name": name,
            }])

        out: list[dict] = []

        def run_videos(route):
            found = _on_route(source, obj, route)
            return _run(getattr(found, "a_get_videos", None) or found.get_videos)

        try:
            vids = await _source_call(source, "videos", run_videos, timeout=15.0)
            for v in vids:
                url = getattr(v, "url", None) or str(v)
                if not url:
                    continue
                out.append({
                    "url": url, "quality": getattr(v, "quality", None),
                    "type": getattr(v, "type", None) or ("m3u8" if ".m3u8" in url else "mp4"),
                    "headers": getattr(v, "headers", None) or {},
                    "source_name": name,
                })
        except Exception as exc:
            log.debug("videos extract failed: %s", exc)
        return await _filter_source_videos(out)

    # Legacy branch — extractor gave us an Episode, we'd enumerate sources × videos.
    episode = obj

    def run_video_sources(route):
        found = _on_route(source, episode, route)
        return _run(getattr(found, "a_get_sources", None) or found.get_sources)

    try:
        sources_list = await _source_call(source, "video sources", run_video_sources, timeout=12.0)

        sem = asyncio.Semaphore(8)

        async def fetch_one(s):
            if is_disabled_player_url(getattr(s, "url", None) or ""):
                return []
            async with sem:
                try:
                    get_videos = getattr(s, "a_get_videos", None) or s.get_videos
                    vids = await asyncio.wait_for(_run(get_videos), timeout=8.0)
                except Exception as exc:
                    log.debug("source videos skipped: %s", exc)
                    return []
                out = []
                name = getattr(s, "title", None) or getattr(s, "name", None)
                for v in vids:
                    url = getattr(v, "url", None) or str(v)
                    if not url:
                        continue
                    quality = getattr(v, "quality", None)
                    vtype = getattr(v, "type", None) or ("m3u8" if ".m3u8" in url else "mp4")
                    headers = getattr(v, "headers", None) or {}
                    out.append({"url": url, "quality": quality, "type": vtype,
                                "headers": headers, "source_name": name})
                return out

        grouped = await _guard_source(
            source,
            "videos",
            asyncio.gather(*[fetch_one(s) for s in sources_list], return_exceptions=True),
            timeout=15.0,
        )
        all_videos: list[dict] = []
        for g in grouped:
            if isinstance(g, list):
                all_videos.extend(g)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("videos failed")
        raise HTTPException(502, f"videos failed: {exc}")

    def score(v):
        q = v.get("quality") or 0
        try:
            q = int(str(q).replace("p", ""))
        except ValueError:
            q = 0
        return (1 if v["type"] == "m3u8" else 0, q)

    all_videos.sort(key=score, reverse=True)
    return await _filter_source_videos(all_videos)


# ---------- native source glue ----------
async def _native_search(source: str, q: str, mal_id: int | None = None, route: str = "direct"):
    if source == "animevost":
        vost: Animevost = _native("animevost", route)
        results = await vost.search(q, limit=20)
        out = []
        for item in results:
            k = _put({
                "type": "anime", "source": "animevost",
                "vost_id": item["id"], "title": item["title_ru"] or item["title"],
            })
            out.append({
                "title": item["title_ru"] or item["title"],
                "title_en": item.get("title_en"),
                "thumbnail": item.get("image"),
                "url": f"https://animevost.org/tip/tv/{item['id']}-x.html",
                "key": k, "source": "animevost",
                "year": item.get("year"), "note": item.get("note"),
            })
        return out

    if source == "oldyummy":
        oy: OldYummy = _native("oldyummy", route)
        results = await oy.search(q, limit=20)
        if mal_id:
            exact = [r for r in results if r.get("mal_id") == mal_id]
            if exact:
                results = exact + [r for r in results if r not in exact]
        out = []
        for item in results:
            k = _put({
                "type": "anime", "source": "oldyummy",
                "anime_id": item["id"], "title": item["title"],
                "mal_id": item.get("mal_id"),
            })
            out.append({
                "title": item["title"],
                "title_en": item.get("title_en"),
                "thumbnail": item.get("thumbnail"),
                "url": f"https://old.yummyani.me/catalog/item/{item.get('alias','')}",
                "key": k, "source": "oldyummy",
                "year": item.get("year"),
                "mal_id": item.get("mal_id"),
            })
        return out

    raise HTTPException(400, f"unknown native source {source}")


async def _native_episodes(source: str, key: str, route: str = "direct"):
    obj = _get(key)

    if source == "animevost" and obj.get("source") == "animevost":
        vost: Animevost = _native("animevost", route)
        eps = await vost.episodes(obj["vost_id"])
        out = []
        for e in eps:
            ek = _put({"type": "episode", "source": "animevost", "qualities": e["qualities"]})
            out.append({"num": e["num"], "title": e["name"], "key": ek, "preview": e.get("preview")})
        return out

    if source == "oldyummy" and obj.get("source") == "oldyummy":
        oy: OldYummy = _native("oldyummy", route)
        raw = await oy.videos(obj["anime_id"])
        by_num: dict[int, list[dict]] = {}
        for v in raw:
            try:
                num = int(str(v.get("number") or "0").strip() or 0)
            except (TypeError, ValueError):
                num = 0
            if not num:
                continue
            by_num.setdefault(num, []).append(v)
        out = []
        for num in sorted(by_num.keys()):
            dubs = by_num[num]
            ek = _put({"type": "episode", "source": "oldyummy", "dubs": dubs})
            out.append({"num": num, "title": None, "key": ek, "preview": None})
        return out

    raise HTTPException(400, "bad episode source")


async def _native_dubs(source: str, key: str):
    obj = _get(key)
    if source == "oldyummy" and obj.get("source") == "oldyummy":
        seen: dict[tuple[str, str], dict] = {}
        for v in obj.get("dubs", []):
            d = v.get("data") or {}
            name = (d.get("dubbing") or d.get("player") or "—").strip()
            norm = _norm_dub_name(name)
            if not norm:
                continue
            iframe_url = v.get("iframe_url") or ""
            if _unplayable_player_url(iframe_url):
                continue
            kind = "sub" if "субтитры" in name.lower() else "voice"
            identity = (norm, kind)
            score = iframe_preference_score(iframe_url)
            if identity in seen and score <= seen[identity].get("_score", -1):
                continue
            dk = _put({"type": "dub", "source": "oldyummy",
                       "iframe_url": iframe_url, "name": name})
            seen[identity] = {"name": name, "norm": norm, "kind": kind, "key": dk, "_score": score}
        scores = {identity: item["_score"] for identity, item in seen.items()}
        return [
            {item_key: item_value for item_key, item_value in seen[identity].items() if item_key != "_score"}
            for identity in _vk_last(list(seen), scores)
        ]
    return []


async def _native_videos(source: str, key: str):
    obj = _get(key)

    if source == "animevost" and obj.get("source") == "animevost":
        return [
            {"url": q["url"], "quality": q.get("quality"), "type": q.get("type", "mp4"),
             "headers": {}, "source_name": "Animevost"}
            for q in obj.get("qualities", [])
        ]

    if source == "oldyummy" and obj.get("source") == "oldyummy":
        iframe = obj.get("iframe_url") or ""
        if iframe.startswith("//"):
            iframe = "https:" + iframe
        return [{
            "url": iframe, "quality": None, "type": "iframe",
            "headers": {}, "source_name": obj.get("name", "OldYummy"),
        }]

    raise HTTPException(400, "bad video source")


# ---------- random watchable ----------
_OLDYUMMY_CATALOG_MAX = 10000


@app.get("/random/watchable")
async def random_watchable():
    try:
        return await asyncio.wait_for(_random_watchable(), timeout=8)
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "random pool timed out") from exc


async def _random_watchable():
    client = await http.get_client()
    tried: set[int] = set()
    for _ in range(15):
        offset = _random.randint(0, _OLDYUMMY_CATALOG_MAX - 1)
        if offset in tried:
            continue
        tried.add(offset)
        try:
            r = await client.get(
                f"https://old.yummyani.me/api/anime?limit=1&offset={offset}",
                timeout=8, headers={"User-Agent": "Mozilla/5.0"},
            )
            if r.status_code != 200:
                continue
            data = r.json()
            resp = data.get("response")
            items = resp if isinstance(resp, list) else (resp or {}).get("data", [])
            if not items:
                continue
            a = items[0]
            mal_id = (a.get("remote_ids") or {}).get("myanimelist_id")
            if not str(mal_id or "").isdigit() or site_settings.hidden(int(mal_id)):
                continue
            type_alias = (a.get("type") or {}).get("alias", "")
            if type_alias in ("music", "cm", "pv"):
                continue
            dur = a.get("duration", 0) or 0
            if type_alias == "movie" and dur and dur < 40 * 60:
                continue
            if type_alias != "movie" and dur and dur < 8 * 60:
                continue
            rating = (a.get("rating") or {}).get("average", 0) or 0
            if rating and rating < 6:
                continue
            try:
                _, _, status = await jikan_catalog.fetch(f"anime/{mal_id}", timeout=4)
                if status == 404:
                    continue
            except HTTPException:
                pass  # A Jikan outage doesn't invalidate the source's MAL mapping.
            return {
                "mal_id": int(mal_id),
                "anime_id": a.get("anime_id"),
                "title": a.get("title"),
                "year": a.get("year"),
                "type": type_alias,
            }
        except httpx.HTTPError as exc:
            log.debug("random watchable attempt failed: %s", exc)
            continue
    raise HTTPException(502, "random pool unreachable")


# ---------- register sibling routers ----------
app.include_router(catalog.router)
app.include_router(auth.router)
app.include_router(social_login.router)
app.include_router(account.router)
app.include_router(friends.router)
app.include_router(messages.router)
app.include_router(realtime_ws.router)
app.include_router(lists.router)
app.include_router(activity.router)
app.include_router(profile_pages.router)
app.include_router(player_proxy.router)
app.include_router(player_proxy.kodik_router)
app.include_router(title_pages.router)
app.include_router(seo.router)
app.include_router(video_reports.router)
app.include_router(admin_router.router)
app.add_exception_handler(admin_auth.AdminError, admin_auth.error_response)


# Панели Anime Social ошибки — в обёртке API v2 и по-русски, остальным — как обычно у FastAPI
@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/api/admin/"):
        field = ".".join(str(part) for part in (exc.errors() or [{}])[0].get("loc", ()) if part != "body")
        message = f"Неверное поле: {field}" if field else "Неверное тело запроса"
        return admin_auth.error_response(request, admin_auth.AdminError(422, "validation_failed", message))
    return await request_validation_exception_handler(request, exc)


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/api/admin/"):
        code, message = {404: ("not_found", "Нет такого метода"),
                         405: ("method_not_allowed", "Метод не поддерживается")}.get(exc.status_code, ("error", "Ошибка AsoPlay"))
        return admin_auth.error_response(request, admin_auth.AdminError(exc.status_code, code, message))
    return await http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def _server_error(request: Request, exc: Exception):
    # Сам сбой всё равно попадёт в журнал сервера: обработчик только отвечает
    if request.url.path.startswith("/api/admin/"):
        return admin_auth.error_response(request, admin_auth.AdminError(500, "internal_error", "Внутренняя ошибка AsoPlay"))
    return PlainTextResponse("Internal Server Error", status_code=500)


# ---------- static assets + SPA shell ----------
# app.js, /js/*.js, styles.css are served from the project root as-is so the
# dev workflow (open index.html via Live Server) and the production workflow
# (uvicorn serving everything) stay on the same file tree.
class _StaticAssets(StaticFiles):
    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            # Long cache for static assets; index.html (served via title_pages
            # / shell routes) has its own short TTL.
            if path.endswith((".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico")):
                resp.headers["Cache-Control"] = "public, max-age=86400"
        return resp


_ASSET_DIRS = [
    ("/styles.css", env.ROOT / "styles.css"),
    ("/app.js", env.ROOT / "app.js"),
]


def _file_route(app: FastAPI, route: str, path: Path, media: str | None = None) -> None:
    @app.get(route, include_in_schema=False)
    def _serve():
        if not path.is_file():
            raise HTTPException(404)
        data = path.read_bytes()
        ct = media or (
            "text/css" if path.suffix == ".css"
            else "application/javascript" if path.suffix == ".js"
            else "application/octet-stream"
        )
        return Response(
            data, media_type=ct,
            headers={"Cache-Control": "public, max-age=60"},
        )


for _route, _path in _ASSET_DIRS:
    _file_route(app, _route, _path)

# Serve the whole /js/ directory if present (split frontend modules live here).
_JS_DIR = env.ROOT / "js"
if _JS_DIR.is_dir():
    app.mount("/js", _StaticAssets(directory=str(_JS_DIR)), name="js")

# Brand assets: logos, icons. Directory is created on startup so /assets always
# resolves (empty 404 until files are added).
_ASSETS_DIR = env.ROOT / "assets"
_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/assets", _StaticAssets(directory=str(_ASSETS_DIR)), name="assets")

# Shell routes (home, catalog, trending, account...) go LAST so they don't
# eat API routes like /search or /episodes.
title_pages.register_shell_routes(app)

