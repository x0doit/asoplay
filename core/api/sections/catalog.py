"""
Маршруты каталога и прокси: Shikimori (карточка, пачка, поиск с запасным поиском old.yummyani), встроенный Jikan,
AniList, перевод Google и картинки с разрешённых хостов.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Ответы апстримов идут через кеш на диске (core/classes/http_cache.py, cached_fetch — через модуль). Тайтлы, скрытые
в панели Anime Social по жалобе правообладателя, не находятся и поиском (site_settings).
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from core.classes import http_cache
from core.classes.http_cache import SHIKI_HEADERS, SHIKI_HOST, SHIKI_ORIGIN
from core.classes.jikan import catalog as jikan_catalog
from core.functions import site_settings

router = APIRouter()

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


@router.get("/shiki/anime/{mal_id}")
async def shiki_anime(mal_id: int):
    """Карточка тайтла с Shikimori по id MAL, кеш на сутки; ошибка апстрима — с его кодом."""
    url = f"{SHIKI_ORIGIN}/api/animes/{mal_id}"
    content, ct, status = await http_cache.cached_fetch(
        "GET", url, ttl=86400, timeout=10, headers=SHIKI_HEADERS
    )
    if status != 200:
        raise HTTPException(status, "shiki upstream")
    return Response(content=content, media_type=ct, headers={"Cache-Control": "public, max-age=86400"})


@router.get("/shiki/batch")
async def shiki_batch(ids: str = Query(..., description="comma-separated MAL ids")):
    """Русские названия и картинки с Shikimori для пачки id MAL (до 50); при сбое — пустой ответ."""
    id_list = [i.strip() for i in ids.split(",") if i.strip().isdigit()][:50]
    if not id_list:
        return {}
    sorted_ids = ",".join(sorted(id_list))
    try:
        content, ct, status = await http_cache.cached_fetch(
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
        content, ct, status = await http_cache.cached_fetch(
            "GET", f"{SHIKI_ORIGIN}/api/animes",
            params={"search": q, "limit": limit, "page": page},
            ttl=3600, timeout=3.5, headers=SHIKI_HEADERS,
        )
    except HTTPException:
        status = 503
    if status != 200:
        return await _yummy_search(q, limit, page)
    if site_settings.state.flags.hidden:
        # Скрытые из панели Anime Social тайтлы (жалоба правообладателя) не находятся и поиском
        try:
            items = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            items = None
        if isinstance(items, list):
            content = json.dumps(site_settings.visible(items, "id"), ensure_ascii=False).encode("utf-8")
    return Response(content=content, media_type=ct)


async def _yummy_search(q: str, limit: int, page: int):
    """Preserve the existing Shikimori-shaped search contract and MAL identity."""
    content, _, status = await http_cache.cached_fetch(
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
        if not str(mal_id or "").isdigit() or int(mal_id) <= 0 or site_settings.hidden(int(mal_id)):
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
    """Встроенный каталог Jikan как есть; случайный тайтл и ошибки браузер не кеширует."""
    content, ct, status = await jikan_catalog.fetch(path, dict(request.query_params))
    return Response(content=content, status_code=status, media_type=ct,
                    headers={"Cache-Control": "no-store" if path == "random/anime" or status != 200 else "public, max-age=300"})


@router.post("/proxy/anilist")
async def proxy_anilist(request: Request):
    """Запрос GraphQL к AniList через кеш на 10 минут; тело не JSON — 400."""
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "anilist: bad json body")
    content, ct, status = await http_cache.cached_fetch(
        "POST", "https://graphql.anilist.co", json_body=body, ttl=600
    )
    return Response(content=content, status_code=status, media_type=ct)


@router.get("/proxy/translate")
async def proxy_translate(q: str = Query(...), sl: str = "auto", tl: str = "ru"):
    """Перевод через Google Translate (gtx) с кешем на неделю."""
    url = "https://translate.googleapis.com/translate_a/single"
    params = {"client": "gtx", "sl": sl, "tl": tl, "dt": "t", "q": q}
    content, ct, status = await http_cache.cached_fetch(
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
    """Картинка с разрешённого хоста через кеш на неделю; чужой хост — 403."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        raise HTTPException(400, "bad url")
    if not any(host == h or host.endswith("." + h) for h in _IMG_HOSTS):
        raise HTTPException(403, f"host not allowed: {host}")
    content, ct, status = await http_cache.cached_fetch(
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

