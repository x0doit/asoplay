# -*- coding: utf-8 -*-
"""
AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Upstream proxy layer — AniList, Shikimori, Google Translate, images.
Shared disk cache, single keep-alive HTTP client, MAL request rate limiter
and a small fallback to stale data when upstreams are flaky.

Register routes with:

    from fastapi import APIRouter
    from server import proxies

    app.include_router(proxies.router)
    app.on_event("startup")(proxies.startup)
    app.on_event("shutdown")(proxies.shutdown)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from hashlib import sha1
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from server import vpn_bridge

log = logging.getLogger("animeviev.proxies")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / ".cache"
_CACHE_DIR.mkdir(exist_ok=True)

SHIKI_ORIGIN = "https://shikimori.io"
SHIKI_HOST = urlparse(SHIKI_ORIGIN).hostname
SHIKI_HEADERS = {"User-Agent": "AnimeViev/1.1 (+https://animeviev.example.com)"}

_IMG_HOSTS = (
    "cdn.myanimelist.net",
    "myanimelist.net",
    "s4.anilist.co",
    "img.anili.st",
    SHIKI_HOST,
    # Preserve compatibility with image URLs stored in older cache entries.
    "shikimori.one",
    "nyaa.shikimori.one",
    "cache.libria.fun",
    "static-libria.weekstorm.one",
    "static.yani.tv",
    "moe.shikimori.one",
)
_IMG_TTL = 86400 * 7
_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}

_httpx_limits = httpx.Limits(
    max_connections=50, max_keepalive_connections=20, keepalive_expiry=60
)
_http_client: httpx.AsyncClient | None = None
_http_client_proxy_env: tuple[str, str, str, str] | None = None
_direct_client: httpx.AsyncClient | None = None
_pending_fetches: dict[str, asyncio.Task] = {}
_upstream_retry_after: dict[str, float] = {}
_upstream_connect_failures: dict[str, int] = {}

_NETWORK_ERRORS: tuple[type[BaseException], ...] = (httpx.HTTPError, OSError, TimeoutError, asyncio.TimeoutError)
try:
    import anyio

    _NETWORK_ERRORS += (anyio.ClosedResourceError,)
except Exception:
    pass


def _proxy_env_fingerprint() -> tuple[str, str, str, str]:
    return (
        os.environ.get("HTTP_PROXY", ""),
        os.environ.get("HTTPS_PROXY", ""),
        os.environ.get("ALL_PROXY", ""),
        os.environ.get("NO_PROXY", ""),
    )


async def get_client() -> httpx.AsyncClient:
    global _http_client, _http_client_proxy_env
    vpn_bridge.ensure_active(blocking=False)
    proxy_env = _proxy_env_fingerprint()
    if (
        _http_client is not None
        and not _http_client.is_closed
        and _http_client_proxy_env != proxy_env
    ):
        await _http_client.aclose()
        _http_client = None
    if _http_client is None or _http_client.is_closed:
        trust_env = any(proxy_env[:3])
        _http_client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            limits=_httpx_limits,
            trust_env=trust_env,
        )
        _http_client_proxy_env = proxy_env
    return _http_client


async def get_direct_client() -> httpx.AsyncClient:
    global _direct_client
    if _direct_client is None or _direct_client.is_closed:
        _direct_client = httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            limits=_httpx_limits,
            trust_env=False,
        )
    return _direct_client


# ---------- disk cache ----------
def _cache_path(key: str) -> Path:
    return _CACHE_DIR / key[:2] / key


def _cache_get(key: str, ttl: float) -> tuple[bytes, str, int] | None:
    p = _cache_path(key)
    meta = p.with_suffix(".meta")
    if not p.exists() or not meta.exists():
        return None
    try:
        record = json.loads(meta.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if time.time() - record.get("ts", 0) > ttl:
        return None
    try:
        return p.read_bytes(), record.get("ct", "application/octet-stream"), int(record.get("status", 200))
    except OSError:
        return None


def _cache_get_any(key: str) -> tuple[bytes, str, int] | None:
    return _cache_get(key, float("inf"))


def _cache_put(key: str, body: bytes, content_type: str, status: int = 200) -> None:
    p = _cache_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_bytes(body)
        p.with_suffix(".meta").write_text(
            json.dumps({"ts": time.time(), "ct": content_type, "status": status}),
            encoding="utf-8",
        )
    except OSError as exc:
        log.debug("cache write failed: %s", exc)


def _cache_key(method: str, url: str, params: dict | None = None, body: Any = None) -> str:
    norm = (
        f"{method.upper()}|{url}|"
        f"{json.dumps(params, sort_keys=True) if params else ''}|"
        f"{json.dumps(body, sort_keys=True) if body is not None else ''}"
    )
    return sha1(norm.encode("utf-8")).hexdigest()


# ---------- rate limiter ----------
class _RateLimiter:
    """One request per self.min_gap seconds, serialized via asyncio.Lock."""

    def __init__(self, rps: float):
        self.min_gap = 1.0 / rps
        self.last = 0.0
        self.lock: asyncio.Lock | None = None
        self.loop: asyncio.AbstractEventLoop | None = None

    def _lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self.lock is None or self.loop is not loop:
            self.lock = asyncio.Lock()
            self.loop = loop
        return self.lock

    async def wait(self) -> None:
        async with self._lock():
            now = time.monotonic()
            wait = self.last + self.min_gap - now
            if wait > 0:
                await asyncio.sleep(wait)
                self.last = time.monotonic()
            else:
                self.last = now


# The embedded parser shares this per-process limit for uncached MAL pages.
_MAL_LIMIT = _RateLimiter(rps=1.0)


async def cached_fetch(
    method: str,
    url: str,
    *,
    params: dict | None = None,
    json_body: Any = None,
    ttl: float,
    timeout: float = 15.0,
    headers: dict | None = None,
) -> tuple[bytes, str, int]:
    key = _cache_key(method, url, params, json_body)
    cached = _cache_get(key, ttl)
    if cached:
        return cached

    parsed = urlparse(url)
    host = parsed.hostname or ""
    catalog_api = host == "graphql.anilist.co" or (
        host == SHIKI_HOST and parsed.path.startswith("/api/")
    ) or (
        host == "old.yummyani.me" and parsed.path == "/api/search"
    )
    stale = _cache_get_any(key)
    if catalog_api and _upstream_retry_after.get(host, 0) > time.monotonic():
        if stale:
            return stale
        raise HTTPException(503, "catalog upstream temporarily unavailable")

    pending = _pending_fetches.get(key)
    if pending is None:
        async def fetch_once():
            try:
                return await _fetch_uncached(
                    method, url, params=params, json_body=json_body,
                    timeout=timeout, headers=headers, key=key, stale=stale,
                    catalog_api=catalog_api,
                )
            except HTTPException:
                if stale:
                    return stale
                raise

        pending = asyncio.create_task(fetch_once())
        _pending_fetches[key] = pending
        def finished(task):
            _pending_fetches.pop(key, None)
            if not task.cancelled():
                task.exception()  # A disconnected caller must not leave an unhandled task.
        pending.add_done_callback(finished)
    return await asyncio.shield(pending)


async def _fetch_uncached(method, url, *, params, json_body, timeout, headers, key, stale,
                          catalog_api=False):
    attempts = 1 if stale else (2 if catalog_api else 4)
    # Bound upstream I/O independently from the embedded catalog worker queue.
    remaining = min(timeout, 10.0) if catalog_api else timeout * attempts
    if stale:
        remaining = min(remaining, 2.5)
    host = urlparse(url).hostname or ""
    last_exc: Exception | None = None
    force_direct = False
    for attempt in range(attempts):
        if catalog_api and _upstream_retry_after.get(host, 0) > time.monotonic():
            raise HTTPException(503, "catalog upstream connection unavailable")
        client = await get_direct_client() if force_direct else await get_client()
        budget = min(timeout, remaining)
        started = time.monotonic()
        try:
            resp = await asyncio.wait_for(
                client.request(
                    method, url, params=params, json=json_body, headers=headers,
                    timeout=httpx.Timeout(budget, connect=min(budget, 4.0)),
                ),
                timeout=budget,
            )
        except _NETWORK_ERRORS as exc:
            remaining -= time.monotonic() - started
            last_exc = exc
            if not force_direct and vpn_bridge.is_active():
                log.warning("proxy path failed for %s, retrying direct outbound: %s", url, exc)
                force_direct = True
            elif catalog_api and isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
                failures = _upstream_connect_failures.get(host, 0) + 1
                _upstream_connect_failures[host] = failures
                if failures >= 2:
                    _upstream_retry_after[host] = time.monotonic() + 10
                    log.warning("catalog connection failed (%s); retrying after 10s", host)
                    break
            if remaining <= 0:
                raise HTTPException(504, "catalog upstream timed out") from exc
            if attempt < attempts - 1:
                await asyncio.sleep(0.4 + attempt * 0.6)
                continue
            break
        remaining -= time.monotonic() - started
        # An HTTP response proves connectivity, even if this particular query failed.
        _upstream_connect_failures.pop(host, None)
        _upstream_retry_after.pop(host, None)
        ct = resp.headers.get("content-type", "application/octet-stream")
        if resp.status_code == 200:
            graphql_errors = False
            if host == "graphql.anilist.co":
                try:
                    graphql_errors = bool(resp.json().get("errors"))
                except (ValueError, AttributeError):
                    graphql_errors = True
            if not graphql_errors:
                _cache_put(key, resp.content, ct, resp.status_code)
            return resp.content, ct, resp.status_code
        if resp.status_code in _RETRY_STATUS_CODES:
            last_exc = HTTPException(resp.status_code, f"upstream status {resp.status_code}")
            if attempt < attempts - 1 and remaining > 0:
                await asyncio.sleep(0.7 + attempt * 1.2)
                continue
            break
        return resp.content, ct, resp.status_code

    raise HTTPException(502, f"proxy upstream: {last_exc or 'retry exhausted'}")


# ---------- routes ----------
router = APIRouter()

shiki_cache: dict[str, Any] = {}


@router.get("/shiki/anime/{mal_id}")
async def shiki_anime(mal_id: int):
    url = f"{SHIKI_ORIGIN}/api/animes/{mal_id}"
    content, ct, status = await cached_fetch(
        "GET", url, ttl=86400, timeout=10, headers=SHIKI_HEADERS
    )
    if status != 200:
        raise HTTPException(status, "shiki upstream")
    return Response(content=content, media_type=ct, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/shiki/batch")
async def shiki_batch(ids: str = Query(..., description="comma-separated MAL ids")):
    id_list = [i.strip() for i in ids.split(",") if i.strip().isdigit()][:50]
    if not id_list:
        return {}
    sorted_ids = ",".join(sorted(id_list))
    try:
        content, ct, status = await cached_fetch(
            "GET",
            f"{SHIKI_ORIGIN}/api/animes",
            params={"ids": sorted_ids, "limit": 50},
            ttl=86400,
            timeout=12,
            headers=SHIKI_HEADERS,
        )
    except HTTPException:
        return {}
    if status != 200:
        return {}
    try:
        items = json.loads(content.decode("utf-8")) or []
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return {
        str(x["id"]): {
            "russian": x.get("russian"),
            "name": x.get("name"),
            "image": x.get("image", {}),
        }
        for x in items
        if "id" in x
    }


@router.get("/shiki/search")
async def shiki_search(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=24, ge=1, le=50),
    page: int = Query(default=1, ge=1, le=50),
):
    """Прокси к Shikimori /api/animes с поддержкой постраничной выборки.
    Русскоязычные запросы фронтенд гонит сюда (Jikan на MAL их не находит)."""
    try:
        content, ct, status = await cached_fetch(
            "GET", f"{SHIKI_ORIGIN}/api/animes",
            params={"search": q, "limit": limit, "page": page},
            ttl=3600, timeout=3.5, headers=SHIKI_HEADERS,
        )
    except HTTPException:
        status = 503
    if status != 200:
        return await _yummy_search(q, limit, page)
    return Response(content=content, media_type=ct)


async def _yummy_search(q: str, limit: int, page: int):
    """Preserve the existing Shikimori-shaped search contract and MAL identity."""
    content, _, status = await cached_fetch(
        "GET", "https://old.yummyani.me/api/search",
        params={"q": q, "limit": limit, "offset": (page - 1) * limit},
        ttl=3600, timeout=3.5, headers={"User-Agent": "Mozilla/5.0"},
    )
    try:
        items = json.loads(content).get("response")
    except (ValueError, AttributeError) as exc:
        raise HTTPException(502, "search reserve invalid response") from exc
    if status != 200 or not isinstance(items, list):
        raise HTTPException(502, "search providers unavailable")
    result = []
    for item in items:
        mal_id = (item.get("remote_ids") or {}).get("myanimelist_id")
        if not str(mal_id or "").isdigit() or int(mal_id) <= 0:
            continue
        poster = item.get("poster") or {}
        image = poster.get("big") or poster.get("medium") or ""
        if image.startswith("//"):
            image = "https:" + image
        status_alias = (item.get("anime_status") or {}).get("alias")
        result.append({
            "id": int(mal_id), "russian": item.get("title") or "",
            "name": next(iter(item.get("other_titles") or []), item.get("title") or ""),
            "image": {"original": image}, "kind": (item.get("type") or {}).get("alias") or "",
            "status": {"released": "released", "ongoing": "ongoing", "announced": "anons"}.get(status_alias, ""),
            "score": (item.get("rating") or {}).get("myanimelist_rating"),
            "aired_on": str(item["year"]) if item.get("year") else None,
            "episodes": (item.get("episodes") or {}).get("count"),
            "duration": round(item["duration"] / 60) if item.get("duration") else None,
        })
    return {"data": result, "pagination": {
        "has_next_page": len(items) >= limit,
        "last_visible_page": page + 1 if len(items) >= limit else page,
    }}


@router.get("/proxy/jikan/{path:path}")
async def proxy_jikan(path: str, request: Request):
    from server import jikan_catalog
    content, ct, status = await jikan_catalog.fetch(path, dict(request.query_params))
    return Response(content=content, status_code=status, media_type=ct,
                    headers={"Cache-Control": "no-store" if path == "random/anime" or status != 200 else "public, max-age=300"})


@router.post("/proxy/anilist")
async def proxy_anilist(request: Request):
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "anilist: bad json body")
    content, ct, status = await cached_fetch(
        "POST", "https://graphql.anilist.co", json_body=body, ttl=600
    )
    return Response(content=content, status_code=status, media_type=ct)


@router.get("/proxy/translate")
async def proxy_translate(q: str = Query(...), sl: str = "auto", tl: str = "ru"):
    url = "https://translate.googleapis.com/translate_a/single"
    params = {"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": q}
    content, ct, status = await cached_fetch(
        "GET", url, params=params, ttl=86400 * 7, timeout=8
    )
    return Response(
        content=content,
        status_code=status,
        media_type=ct,
        headers={"Cache-Control": "public, max-age=604800"},
    )


@router.get("/proxy/img")
async def proxy_img(url: str = Query(...)):
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        raise HTTPException(400, "bad url")
    if not any(host == h or host.endswith("." + h) for h in _IMG_HOSTS):
        raise HTTPException(403, f"host not allowed: {host}")
    content, ct, status = await cached_fetch(
        "GET", url, ttl=_IMG_TTL, timeout=15.0,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    if status != 200:
        raise HTTPException(status, "img upstream status")
    return Response(
        content=content,
        media_type=ct,
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


# ---------- lifecycle ----------
async def startup() -> None:
    from server import jikan_catalog
    await jikan_catalog.startup()


async def shutdown() -> None:
    global _http_client, _http_client_proxy_env, _direct_client
    from server import jikan_catalog
    await jikan_catalog.shutdown()
    pending = list(_pending_fetches.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    _pending_fetches.clear()
    _upstream_retry_after.clear()
    _upstream_connect_failures.clear()
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None
    _http_client_proxy_env = None
    if _direct_client and not _direct_client.is_closed:
        await _direct_client.aclose()
    _direct_client = None
