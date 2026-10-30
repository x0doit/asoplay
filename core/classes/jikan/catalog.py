"""Embedded Jikan parser. No public Jikan API, alternate catalog or PHP web server."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shutil
import subprocess
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

from core.config import config
from core.functions import site_settings

log = logging.getLogger("asoplay.catalog")
_BRIDGE = Path(__file__).with_name("php") / "bridge.php"
_VERSION = "embedded-jikan-5-v1"
_php: str | None = None
_status: dict = {"engine": "jikan", "embedded": True, "ready": False}
_pending: dict[str, asyncio.Task] = {}
_workers = asyncio.Semaphore(4)
_network = asyncio.Semaphore(2)
_retry_after = 0.0


def status() -> dict:
    return dict(_status)


def _php_binary() -> str:
    global _php
    if _php:
        return _php
    explicit = config.jikan_php()
    candidates = [explicit] if explicit else []
    if not explicit and os.name == "nt":
        root = config.ospanel_dir()
        candidates += [str(root / "modules/PHP-8.4/php.exe"), str(root / "modules/PHP-8.5/php.exe")]
    if not explicit:
        candidates += [shutil.which("php") or ""]
    for candidate in candidates:
        resolved = shutil.which(candidate) if candidate else None
        if resolved:
            _php = resolved
            return _php
    raise HTTPException(503, "Embedded catalog requires PHP >= 8.4.4 (AV_JIKAN_PHP)")


async def _bridge(payload: dict) -> dict:
    if not (_BRIDGE.parent / "vendor/autoload.php").is_file():
        raise HTTPException(503, "Install embedded catalog dependencies in core/classes/jikan/php with Composer")
    async with _workers:
        try:
            process = await asyncio.create_subprocess_exec(
                _php_binary(), "-d", "display_errors=stderr", "-d", "memory_limit=128M", str(_BRIDGE),
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
            )
        except OSError as exc:
            raise HTTPException(503, "Embedded PHP runtime unavailable") from exc
        try:
            out, err = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), 8)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            raise
        if process.returncode:
            # stderr is diagnostic only, never included in a public response.
            log.error("Jikan parser failed: %s", err.decode("utf-8", "replace")[:1000])
            raise HTTPException(502, "Embedded catalog parser failed")
        try:
            return json.loads(out)
        except ValueError as exc:
            raise HTTPException(502, "Invalid embedded parser response") from exc


async def startup() -> None:
    try:
        _status.update(await _bridge({"mode": "health"}), ready=True)
        log.info("Embedded Jikan %s ready (PHP %s)", _status["jikan"], _status["php"])
    except (HTTPException, OSError, asyncio.TimeoutError) as exc:
        _status.update(ready=False)
        log.error("Embedded Jikan unavailable: %s", getattr(exc, "detail", str(exc)))


async def shutdown() -> None:
    tasks = list(_pending.values())
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _pending.clear()


async def _fetch_native(path: str, params: dict) -> dict:
    from core.classes import http
    global _retry_after
    description = await _bridge({"mode": "describe", "path": path, "params": params})
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
        result = await _bridge({"mode": "parse", "path": path, "params": params,
                                "html": response.text, "status": response.status_code})
        result["page_size"] = description["page_size"]
        return result


async def _native(path: str, params: dict) -> dict:
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
            _pending.pop(key, None)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
    return await asyncio.shield(task)


def _validate(path: str, params: dict) -> dict:
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
    rating = item.get("rating") or item.get("rated") or ""
    return not (rating.startswith(("Rx", "R+")) or item.get("r18") or item.get("explicit_genres"))


def _page(items: list, page: int, limit: int, has_next: bool) -> dict:
    return {"data": items, "pagination": {"current_page": page, "has_next_page": has_next,
            "last_visible_page": page + int(has_next), "items": {"count": len(items), "per_page": limit}}}


async def catalog(path: str, params: dict | None = None) -> dict:
    p = _validate(path, params or {})
    page, limit = p.pop("page"), p.pop("limit")
    if path == "random/anime":
        # Random MAL entry from a ranked pool; never assume sequential IDs exist.
        pool = await _native("top/anime", {"page": random.randint(1, 20), "filter": "bypopularity"})
        candidate = random.choice(site_settings.visible(pool["data"]) or pool["data"])
        if site_settings.hidden(candidate.get("mal_id")):
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
                and not site_settings.hidden(x.get("mal_id"))]
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
                    and not (titles and site_settings.hidden(x.get("mal_id"))))
        more = result["has_next_page"]
        if len(rows) > offset + limit or not more:
            return _page(rows[offset:offset + limit], page, limit, len(rows) > offset + limit or more)
        native_page += 1


async def fetch(path: str, params: dict | None = None, *, timeout: float = 25) -> tuple[bytes, str, int]:
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
