"""
Встроенный каталог MAL: парсер Jikan у себя — без публичного API Jikan, запасного каталога и PHP-веб-сервера.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Запрос проверяется до PHP и сети. Страница MAL качается под общим лимитом MAL (core/classes/http.py) и разбирается
PHP-парсером (core/classes/jikan/php_bridge.py, через модуль). Ответы кешируются на диске, одинаковые запросы ждут
одну задачу, при сбое MAL отдаётся устаревший кеш. Скрытые по жалобе правообладателя тайтлы уходят из списков
через фильтр, который регистрирует core.py (register_title_filter): site_settings выше по слоям.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Callable
from datetime import date
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from core.classes.jikan import php_bridge

log = logging.getLogger("asoplay.catalog")
_VERSION = "embedded-jikan-5-v1"
_status: dict = {"engine": "jikan", "embedded": True, "ready": False}
_pending: dict[str, asyncio.Task] = {}
_network = asyncio.Semaphore(2)
_retry_after = 0.0


def _unregistered(*_args, **_kwargs):
    """Заглушка до регистрации фильтра скрытых тайтлов: без него списки тайтлов не отдаются."""
    raise RuntimeError("Hidden-title filter is not registered: core.py calls "
                       "jikan_catalog.register_title_filter() before serving requests")


# Скрытые по жалобе правообладателя тайтлы решает site_settings: он выше по слоям, и каталог его не импортирует.
# core.py регистрирует его hidden и visible до первого запроса; без регистрации списки тайтлов падают с ясной ошибкой.
_hidden = _unregistered
_visible = _unregistered


def register_title_filter(*, hidden: Callable[[Any], bool], visible: Callable[[list], list]) -> None:
    """Фильтр скрытых тайтлов для списков каталога: hidden(mal_id) — скрыт ли тайтл, visible(items) — список
    без скрытых. Регистрирует core.py до первого запроса."""
    global _hidden, _visible
    _hidden, _visible = hidden, visible


def status() -> dict:
    """Состояние встроенного каталога для /health: готов ли парсер, версии Jikan и PHP."""
    return dict(_status)


async def startup() -> None:
    """Проверить PHP-парсер при старте: готов — версии в состояние, нет — ошибка в журнал, сервер работает дальше."""
    try:
        _status.update(await php_bridge.call({"mode": "health"}), ready=True)
        log.info("Embedded Jikan %s ready (PHP %s)", _status["jikan"], _status["php"])
    except (HTTPException, OSError, asyncio.TimeoutError) as exc:
        _status.update(ready=False)
        log.error("Embedded Jikan unavailable: %s", getattr(exc, "detail", str(exc)))


async def shutdown() -> None:
    """Отменить запросы к MAL, которые ещё идут, и дождаться их."""
    tasks = list(_pending.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _pending.clear()


async def _fetch_native(path: str, params: dict) -> dict:
    """Страница MAL через парсер: bridge.php описывает запрос (только https://myanimelist.net), страница качается
    под общим лимитом MAL, bridge.php разбирает HTML. После 403 или 429 — 30 секунд без новых запросов."""
    from core.classes import http
    global _retry_after
    description = await php_bridge.call({"mode": "describe", "path": path, "params": params})
    url = description["url"]
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "myanimelist.net":
        raise HTTPException(502, "Invalid parser request URL")
    async with _network:
        await http._MAL_LIMIT.wait()
        if time.monotonic() < _retry_after:
            raise HTTPException(503, "MyAnimeList rate limit; retry shortly")
        try:
            response = await http.request("GET", url, timeout=httpx.Timeout(12, connect=4),
                                             headers={"User-Agent": "AsoPlay/1.0 (Jikan/5.0)"})
        except httpx.HTTPError as exc:
            raise HTTPException(502, "MyAnimeList connection failed") from exc
        empty_page = response.status_code == 404 and (path == "anime" or path.endswith("/episodes"))
        if response.status_code == 404 and not empty_page:
            raise HTTPException(404, "Anime not found")
        if response.status_code in (403, 429):
            _retry_after = time.monotonic() + 30
        if response.status_code != 200 and not empty_page:
            raise HTTPException(503, "MyAnimeList temporarily unavailable")
        if len(response.content) > 10_000_000:
            raise HTTPException(502, "Catalog response too large")
        result = await php_bridge.call({"mode": "parse", "path": path, "params": params,
                                        "html": response.text, "status": response.status_code})
        result["page_size"] = description["page_size"]
        return result


async def _native(path: str, params: dict) -> dict:
    """Разобранная страница MAL из кеша на диске или новым запросом. Кеш: карточка — час, жанры — сутки, остальное —
    15 минут. Одинаковые запросы ждут одну задачу (не больше 64 сразу); при сбое 5xx — устаревший кеш до 7 дней."""
    from core.classes import http
    key = http._cache_key("PARSE", f"{_VERSION}/{path}", params)
    ttl = 3600 if re.fullmatch(r"anime/\d+(?:/full)?", path) else 900
    if path == "genres/anime":
        ttl = 86400
    cached = http._cache_get(key, ttl)
    if cached:
        return json.loads(cached[0])
    task = _pending.get(key)
    if task is None:
        if len(_pending) >= 64:
            raise HTTPException(503, "Catalog request queue is busy")
        async def load():
            """Запрос с таймаутом 30 секунд и запись в кеш; при сбое 5xx — устаревший кеш, если он есть."""
            try:
                try:
                    result = await asyncio.wait_for(_fetch_native(path, params), 30)
                except asyncio.TimeoutError as exc:
                    raise HTTPException(504, "Catalog worker timed out") from exc
                http._cache_put(key, json.dumps(result).encode(), "application/json")
                return result
            except HTTPException as exc:
                stale = http._cache_get(key, 86400 * 7)
                if stale and exc.status_code >= 500:
                    return json.loads(stale[0])
                raise
        task = asyncio.create_task(load())
        _pending[key] = task
        def finished(done):
            """Задача закончилась: убрать из ожидающих и забрать исключение, чтобы asyncio не писал его в журнал."""
            _pending.pop(key, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
    return await asyncio.shield(task)


def _validate(path: str, params: dict) -> dict:
    """Путь и параметры запроса: только известные пути и фильтры, числа и даты в границах. Иначе 404 или 400 —
    до PHP и сети."""
    if not re.fullmatch(r"(?:anime(?:/[1-9]\d{0,7}(?:/(?:full|characters|recommendations|episodes))?)?|top/anime|genres/anime|random/anime|seasons/\d{4}/(?:winter|spring|summer|fall))", path):
        raise HTTPException(404, "Unknown catalog endpoint")
    allowed = {"page", "limit", "q", "genres", "type", "filter", "status", "order_by", "sort", "min_score", "start_date", "end_date", "sfw", "_"}
    if params.keys() - allowed:
        raise HTTPException(400, "Unknown catalog parameter")
    p = {k: str(v) for k, v in params.items() if k != "_" and v is not None and str(v) != ""}
    try:
        for key, default, upper in (("page", 1, 1000), ("limit", 100 if path.endswith("/episodes") else 24, 100)):
            n = int(p.get(key, default))
            if not 1 <= n <= upper:
                raise ValueError(key)
            p[key] = n
        for key, values in {
            "type": {"tv", "movie", "ova", "ona", "special", "music"},
            "filter": {"tv", "movie", "ova", "ona", "special", "music", "bypopularity", "airing", "upcoming", "favorite"},
            "status": {"airing", "complete", "upcoming"},
            "order_by": {"popularity", "score", "title", "start_date", "episodes"},
            "sort": {"asc", "desc"}, "sfw": {"true", "false"},
        }.items():
            if key in p and p[key] not in values:
                raise ValueError(key)
        if "q" in p and not 3 <= len(p["q"]) <= 200:
            raise ValueError("q")
        if "genres" in p and not re.fullmatch(r"[1-9]\d{0,3}(?:,[1-9]\d{0,3}){0,19}", p["genres"]):
            raise ValueError("genres")
        if "min_score" in p and not 0 <= float(p["min_score"]) <= 10:
            raise ValueError("min_score")
        for key in ("start_date", "end_date"):
            if key in p:
                date.fromisoformat(p[key])
        if p.get("start_date", "") > p.get("end_date", "9999-12-31"):
            raise ValueError("dates")
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid catalog filter")
    return p


def _safe(item: dict) -> bool:
    """Тайтл без 18+: рейтинг не Rx и не R+, нет пометки r18 и откровенных жанров."""
    rating = item.get("rating") or item.get("rated") or ""
    return not (rating.startswith(("Rx", "R+")) or item.get("r18") or item.get("explicit_genres"))


def _page(items: list, page: int, limit: int, has_next: bool) -> dict:
    """Страница ответа в формате Jikan: data и pagination."""
    return {"data": items, "pagination": {"current_page": page, "has_next_page": has_next,
            "last_visible_page": page + int(has_next), "items": {"count": len(items), "per_page": limit}}}


async def catalog(path: str, params: dict | None = None) -> dict:
    """Ответ в формате Jikan по пути и фильтрам: карточка, сезон, случайный тайтл или список. Списки фильтруются
    до нарезки на страницы: 18+ (sfw) и скрытые тайтлы."""
    p = _validate(path, params or {})
    page, limit = p.pop("page"), p.pop("limit")
    if path == "random/anime":
        # Random MAL entry from a ranked pool; never assume sequential IDs exist.
        pool = await _native("top/anime", {"page": random.randint(1, 20), "filter": "bypopularity"})
        candidate = random.choice(_visible(pool["data"]) or pool["data"])
        if _hidden(candidate.get("mal_id")):
            raise HTTPException(503, "No suitable random title; retry")
        result = await catalog(f"anime/{candidate['mal_id']}/full")
        if p.get("sfw") == "true" and not _safe(result["data"]):
            raise HTTPException(503, "No suitable random title; retry")
        return result
    if path == "genres/anime" or re.fullmatch(r"anime/\d+(?:/(?:full|characters|recommendations))?", path):
        if re.fullmatch(r"anime/\d+", path):
            path += "/full"
        return {"data": (await _native(path, {}))["data"]}
    if path.startswith("seasons/"):
        rows = (await _native(path, {}))["data"]
        kind = p.get("filter") or p.get("type")
        rows = [x for x in rows if not x.get("continuing")
                and (not kind or (x.get("type") or "").lower() == kind)
                and (p.get("sfw") != "true" or _safe(x))
                and not _hidden(x.get("mal_id"))]
        rows.sort(key=lambda x: (-(x.get("members") or 0), x["mal_id"]))
        offset = (page - 1) * limit
        result = _page(rows[offset:offset + limit], page, limit, len(rows) > offset + limit)
        result["pagination"]["last_visible_page"] = max(1, (len(rows) + limit - 1) // limit)
        return result
    # MAL pages contain 50 rows (100 for episodes). Filter BEFORE slicing so adjacent
    # AsoPlay pages neither lose nor repeat titles when SFW removes a MAL row.
    # Titles hidden from the AnimeSocial panel (copyright) leave title lists the same way;
    # episode rows also carry mal_id (the episode number), so they are not filtered.
    native_params = {k: v for k, v in p.items() if k != "sfw"}
    titles = path in ("anime", "top/anime")
    offset, rows, native_page = (page - 1) * limit, [], 1
    while True:
        result = await _native(path, dict(native_params, page=native_page))
        rows.extend(x for x in result["data"] if (p.get("sfw") != "true" or _safe(x))
                    and not (titles and _hidden(x.get("mal_id"))))
        more = result["has_next_page"]
        if len(rows) > offset + limit or not more:
            return _page(rows[offset:offset + limit], page, limit, len(rows) > offset + limit or more)
        native_page += 1


async def fetch(path: str, params: dict | None = None, *, timeout: float = 25) -> tuple[bytes, str, int]:
    """Ответ каталога: JSON байтами, тип и код. 404 — {"data":null}, дольше timeout — 504, другие ошибки — как были."""
    try:
        data = await asyncio.wait_for(catalog(path, params), timeout)
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "Embedded catalog request timed out") from exc
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return b'{"data":null}', "application/json", 404
    return json.dumps(data, ensure_ascii=False).encode(), "application/json", 200


async def search_titles(q: str, limit: int = 10) -> list[dict]:
    """Поиск по каталогу без фильтров сайта — для панели Anime Social: там ищут и скрытые тайтлы."""
    p = _validate("anime", {"q": q})
    return (await _native("anime", {"q": p["q"], "page": 1}))["data"][:limit]


def forget_title(mal_id: int) -> int:
    """Удалить с диска кеш карточки тайтла, его персонажей, рекомендаций и списка серий (кнопка «Сбросить кеш»
    в панели Anime Social). Сколько записей было."""
    from core.classes import http
    keys = [http._cache_key("PARSE", f"{_VERSION}/anime/{mal_id}/{part}", {})
            for part in ("full", "characters", "recommendations")]
    keys += [http._cache_key("PARSE", f"{_VERSION}/anime/{mal_id}/episodes", {"page": n}) for n in range(1, 21)]
    return sum(http.cache_drop(key) for key in keys)
