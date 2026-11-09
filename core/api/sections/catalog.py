"""
Маршруты каталога и прокси: Shikimori (карточка, пачка, поиск с запасным поиском old.yummyani), встроенный Jikan,
AniList и картинки с разрешённых хостов. Присланный текст сервер не переводит: прокси перевода здесь нет.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Ответы апстримов идут через кеш на диске (core/classes/http_cache.py, cached_fetch — через модуль). Тайтлы, скрытые
в панели Anime Social по жалобе правообладателя, не находятся и поиском (site_settings). Картинки — со своим кешем
и своими проверками (core/classes/image_proxy.py), хосты — каталога и из панели (core/functions/image_hosts.py).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from core.classes import http_cache, image_proxy
from core.classes.http_cache import SHIKI_HEADERS, SHIKI_ORIGIN
from core.classes.jikan import catalog as jikan_catalog
from core.functions import image_hosts, rate_limit, site_settings

router = APIRouter()

# Картинка не исполняется как страница, даже если хост отдал SVG со скриптом, и браузер не угадывает её тип
# Промахи кеша картинок на посетителя: каждый — выборка наверх, поэтому их число ограничено
_IMG_MISSES = rate_limit.SlidingWindow(limit=240, window=60)
_IMG_HEADERS = {
    "Cache-Control": "public, max-age=86400, immutable",
    "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
    "X-Content-Type-Options": "nosniff",
}


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


def _miss_budget(ip: str):
    """Можно ли посетителю начать ещё одну выборку наверх: 0 — да (и засчитать), иначе — через сколько секунд."""
    def take() -> int:
        """Засчитать промах, если предел не исчерпан."""
        wait = _IMG_MISSES.retry_after(ip)
        if not wait:
            _IMG_MISSES.hit(ip)
        return wait
    return take


@router.get("/proxy/img")
async def proxy_img(request: Request, url: str = Query(...)):
    """Картинка с хоста каталога или из панели через свой кеш на неделю; хост из панели с via "vpn" — через мост.
    Неразборчивый адрес — 400, чужой хост, порт, IP или перенаправление на них — 403; промахи посетителя
    кончились — 429, все места выборок заняты — 503."""
    hosts = site_settings.img_hosts()
    refusal = image_hosts.url_refusal(url, hosts)
    if refusal:
        raise HTTPException(400 if refusal == "bad url" else 403, refusal)
    entry = image_hosts.entry_for(image_hosts.url_host(url), hosts) or ""
    visitor = rate_limit.visitor_key(rate_limit.client_ip(request))
    try:
        content, ct = await image_proxy.fetch(
            url, allowed=lambda target: image_hosts.url_refusal(target, hosts) is None,
            via=site_settings.proxy_via(entry), key=entry, take=_miss_budget(visitor), visitor=visitor,
        )
    except image_proxy.Busy as exc:
        raise HTTPException(exc.status, "busy", headers={"Retry-After": str(exc.retry_after)}) from exc
    except image_proxy.Refused as exc:
        raise HTTPException(403, "host not allowed") from exc
    except image_proxy.Failed as exc:
        raise HTTPException(exc.status, "img upstream status") from exc
    return Response(content=content, media_type=ct, headers=_IMG_HEADERS)

