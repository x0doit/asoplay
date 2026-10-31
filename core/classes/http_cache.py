"""
Кеш апстримов на диске и запросы через него: повторы, паузы апстрима, один запрос на все одинаковые, устаревший кеш.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Ключ — sha1 от метода, адреса, параметров и тела; запись — тело и .meta (время, тип, код) в core/config/cache.
Апстримы каталога (AniList, API Shikimori, поиск old.yummyani) после двух сбоев соединения отдыхают 10 секунд,
и пока есть устаревший кеш, отдаётся он. Сами запросы идут через core/classes/http.py (request, через модуль).
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
from fastapi import HTTPException

from core.classes import http
from core.config import config

log = logging.getLogger("asoplay.proxies")

config.CACHE_DIR.mkdir(exist_ok=True)

SHIKI_ORIGIN = "https://shikimori.io"
SHIKI_HOST = urlparse(SHIKI_ORIGIN).hostname
SHIKI_HEADERS = {"User-Agent": "AsoPlay/1.1 (+https://asoplay.example.com)"}
_RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
_pending_fetches: dict[str, asyncio.Task] = {}
_upstream_retry_after: dict[str, float] = {}
_upstream_connect_failures: dict[str, int] = {}


# ---------- disk cache ----------
def _cache_path(key: str) -> Path:
    """Файл записи кеша: core/config/cache/<первые два знака ключа>/<ключ>."""
    return config.CACHE_DIR / key[:2] / key


def cache_get(key: str, ttl: float) -> tuple[bytes, str, int] | None:
    """Запись кеша моложе ttl секунд: (тело, тип, код); нет или испорчена — None."""
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
    """Запись кеша любой давности — для отдачи устаревших данных при сбое апстрима."""
    return cache_get(key, float("inf"))


def cache_put(key: str, body: bytes, content_type: str, status: int = 200) -> None:
    """Записать ответ в кеш: тело и .meta со временем, типом и кодом; ошибка записи — только в журнал."""
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


def cache_key(method: str, url: str, params: dict | None = None, body: Any = None) -> str:
    """Ключ кеша: sha1 от метода, адреса, параметров и тела запроса."""
    norm = (
        f"{method.upper()}|{url}|"
        f"{json.dumps(params, sort_keys=True) if params else ''}|"
        f"{json.dumps(body, sort_keys=True) if body is not None else ''}"
    )
    return sha1(norm.encode("utf-8")).hexdigest()


def cache_drop(key: str) -> bool:
    """Удалить запись дискового кеша. True — она была."""
    path = _cache_path(key)
    found = False
    for item in (path, path.with_suffix(".meta")):
        try:
            item.unlink()
            found = True
        except FileNotFoundError:
            pass
        except OSError as exc:
            log.warning("cache drop failed: %s", exc)
    return found


def forget_shiki_title(mal_id: int) -> bool:
    """Карточка тайтла с Shikimori (кнопка «Сбросить кеш» в панели Anime Social)."""
    return cache_drop(cache_key("GET", f"{SHIKI_ORIGIN}/api/animes/{mal_id}"))


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
    """Ответ апстрима через кеш: свежий — сразу, иначе один запрос на все одинаковые; при сбое — устаревший кеш.
    Апстрим каталога на паузе после сбоев — устаревший кеш или 503."""
    key = cache_key(method, url, params, json_body)
    cached = cache_get(key, ttl)
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
            """Запрос без кеша; при ошибке — устаревший кеш, если он есть."""
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
            """Убрать запрос из ожидающих и забрать исключение, чтобы asyncio не писал его в журнал."""
            _pending_fetches.pop(key, None)
            if not task.cancelled():
                task.exception()  # A disconnected caller must not leave an unhandled task.
        pending.add_done_callback(finished)
    return await asyncio.shield(pending)


async def _fetch_uncached(method, url, *, params, json_body, timeout, headers, key, stale,
                          catalog_api=False):
    """Запрос к апстриму с повторами в пределах бюджета времени; 200 — в кеш (у AniList — без ошибок GraphQL).
    Два сбоя соединения с апстримом каталога — пауза 10 секунд; попытки кончились — 502, время вышло — 504."""
    attempts = 1 if stale else (2 if catalog_api else 4)
    # Bound upstream I/O independently from the embedded catalog worker queue.
    remaining = min(timeout, 10.0) if catalog_api else timeout * attempts
    if stale:
        remaining = min(remaining, 2.5)
    host = urlparse(url).hostname or ""
    last_exc: Exception | None = None
    for attempt in range(attempts):
        if catalog_api and _upstream_retry_after.get(host, 0) > time.monotonic():
            raise HTTPException(503, "catalog upstream connection unavailable")
        budget = min(timeout, remaining)
        started = time.monotonic()
        try:
            # request() falls back to the VPN bridge within this budget.
            resp = await asyncio.wait_for(
                http.request(
                    method, url, params=params, json=json_body, headers=headers,
                    timeout=httpx.Timeout(budget, connect=min(budget, 4.0)),
                ),
                timeout=budget,
            )
        except http.NETWORK_ERRORS as exc:
            remaining -= time.monotonic() - started
            last_exc = exc
            if catalog_api and isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
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
                cache_put(key, resp.content, ct, resp.status_code)
            return resp.content, ct, resp.status_code
        if resp.status_code in _RETRY_STATUS_CODES:
            last_exc = HTTPException(resp.status_code, f"upstream status {resp.status_code}")
            if attempt < attempts - 1 and remaining > 0:
                await asyncio.sleep(0.7 + attempt * 1.2)
                continue
            break
        return resp.content, ct, resp.status_code

    raise HTTPException(502, f"proxy upstream: {last_exc or 'retry exhausted'}")


async def shutdown() -> None:
    """Отменить запросы к апстримам, которые ещё идут, и забыть паузы апстримов."""
    pending = list(_pending_fetches.values())
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    _pending_fetches.clear()
    _upstream_retry_after.clear()
    _upstream_connect_failures.clear()
