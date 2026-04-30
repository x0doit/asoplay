# -*- coding: utf-8 -*-
"""
AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Upstream proxy layer — Jikan, AniList, Shikimori, Google Translate, images.
Shared disk cache, single keep-alive HTTP client, Jikan-specific rate limiter
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
import time
from hashlib import sha1
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

log = logging.getLogger("animeviev.proxies")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / ".cache"
_CACHE_DIR.mkdir(exist_ok=True)

SHIKI_HEADERS = {"User-Agent": "AnimeViev/1.1 (+https://animeviev.example.com)"}

_IMG_HOSTS = (
    "cdn.myanimelist.net",
    "myanimelist.net",
    "s4.anilist.co",
    "img.anili.st",
    "shikimori.one",
    "nyaa.shikimori.one",
    "cache.libria.fun",
    "static-libria.weekstorm.one",
    "moe.shikimori.one",
)
_IMG_TTL = 86400 * 7

_httpx_limits = httpx.Limits(
    max_connections=50, max_keepalive_connections=20, keepalive_expiry=60
)
_http_client: httpx.AsyncClient | None = None


async def get_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=15.0, follow_redirects=True, limits=_httpx_limits
        )
    return _http_client


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
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            now = time.monotonic()
            wait = self.last + self.min_gap - now
            if wait > 0:
                await asyncio.sleep(wait)
                self.last = time.monotonic()
            else:
                self.last = now


# Jikan docs say 3 RPS but the free tier trips 429 often; 2 RPS is calm.
_JIKAN_LIMIT = _RateLimiter(rps=2.0)


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

    global _http_client
    is_jikan = "api.jikan.moe" in url
    last_exc: Exception | None = None
    for attempt in range(4):
        if is_jikan:
            await _JIKAN_LIMIT.wait()
        client = await get_client()
        try:
            resp = await client.request(
                method, url, params=params, json=json_body, headers=headers, timeout=timeout
            )
        except httpx.HTTPError as exc:
            last_exc = exc
            if _http_client is not None:
                try:
                    await _http_client.aclose()
                except httpx.HTTPError:
                    pass
            _http_client = None
            if attempt < 3:
                await asyncio.sleep(0.4 + attempt * 0.6)
                continue
            break
        if resp.status_code == 429 and attempt < 3:
            await asyncio.sleep(0.7 + attempt * 1.2)
            continue
        ct = resp.headers.get("content-type", "application/octet-stream")
        if resp.status_code == 200:
            _cache_put(key, resp.content, ct, resp.status_code)
        return resp.content, ct, resp.status_code

    stale = _cache_get(key, ttl * 3)
    if stale:
        return stale
    raise HTTPException(502, f"proxy upstream: {last_exc or 'retry exhausted'}")


# ---------- routes ----------
router = APIRouter()

shiki_cache: dict[str, Any] = {}


@router.get("/shiki/anime/{mal_id}")
async def shiki_anime(mal_id: int):
    url = f"https://shikimori.one/api/animes/{mal_id}"
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
            "https://shikimori.one/api/animes",
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
    content, ct, status = await cached_fetch(
        "GET",
        "https://shikimori.one/api/animes",
        params={"search": q, "limit": limit, "page": page},
        ttl=3600,
        timeout=10,
        headers=SHIKI_HEADERS,
    )
    if status != 200:
        return []
    return Response(content=content, media_type=ct)


@router.get("/proxy/jikan/{path:path}")
async def proxy_jikan(path: str, request: Request):
    url = f"https://api.jikan.moe/v4/{path}"
    if path.startswith("random/"):
        client = await get_client()
        try:
            r = await client.get(url, params=dict(request.query_params))
        except httpx.HTTPError as exc:
            raise HTTPException(502, f"proxy upstream: {exc}")
        return Response(
            content=r.content,
            status_code=r.status_code,
            media_type=r.headers.get("content-type", "application/json"),
            headers={"Cache-Control": "no-store"},
        )
    ttl = 3600 if path.startswith("anime/") and "/" not in path[6:] else 900
    content, ct, status = await cached_fetch(
        "GET", url, params=dict(request.query_params), ttl=ttl
    )
    return Response(
        content=content,
        status_code=status,
        media_type=ct,
        headers={"Cache-Control": f"public, max-age={ttl}"},
    )


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
    """Prime the in-process cache with endpoints the home page needs."""
    urls: list[tuple[str, str, dict | None, float]] = [
        ("GET", "https://api.jikan.moe/v4/top/anime", {"limit": 25, "filter": "bypopularity"}, 900),
        ("GET", "https://api.jikan.moe/v4/seasons/now", {"limit": 25, "sfw": "true"}, 900),
        ("GET", "https://api.jikan.moe/v4/genres/anime", None, 86400),
    ]

    async def _warm(method, url, params, ttl):
        try:
            await cached_fetch(method, url, params=params, ttl=ttl, timeout=20)
        except HTTPException as exc:
            log.debug("warmup %s failed: %s", url, exc)

    await asyncio.gather(*[_warm(*t) for t in urls])
    log.info("warmup complete (%d endpoints)", len(urls))


async def shutdown() -> None:
    global _http_client
    if _http_client and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None
