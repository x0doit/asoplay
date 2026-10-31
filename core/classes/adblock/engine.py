"""
Блокировщик рекламы для адресов плеера: списки AdGuard из сети с кешем и проверка адреса до отдачи во фронтенд.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Это не полноценный движок AdGuard из расширения браузера: страница не видит и не переписывает запросы внутри чужого
iframe. Что можно сделать в приложении:
    - загружать официальные списки AdGuard при работе и держать их в кеше;
    - отбрасывать адреса iframe и видео рекламы и трекеров, прежде чем отдать их фронтенду;
    - пока списки грузятся или недоступны — держаться встроенного запасного набора.
Списки не лежат в репозитории: они скачиваются с адресов AdGuard и кешируются в core/config/cache/adblock (вне git).
Разбор правил — core/classes/adblock/rules.py, через модуль.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import Any
from urllib.parse import urlparse

import httpx

from core.classes.adblock import rules
from core.config import config

log = logging.getLogger("asoplay.adblock")


_DEFAULT_FILTER_URLS = (
    # AdGuard Russian filter.
    "https://filters.adtidy.org/extension/chromium/filters/1.txt",
    # AdGuard Base filter.
    "https://filters.adtidy.org/extension/chromium/filters/2.txt",
    # AdGuard Tracking Protection filter.
    "https://filters.adtidy.org/extension/chromium/filters/3.txt",
    # AdGuard Popups filter.
    "https://filters.adtidy.org/extension/chromium/filters/19.txt",
)


class AdblockEngine:
    """Блокировщик адресов плеера: списки AdGuard из сети с кешем на диске и проверка адреса по правилам."""
    def __init__(self) -> None:
        """Настройки из окружения и запасной набор правил до загрузки списков."""
        self.enabled = config.adblock_enabled()
        urls = config.adblock_filter_urls()
        self.filter_urls = urls or _DEFAULT_FILTER_URLS
        self.cache_ttl = config.adblock_cache_ttl()
        self.allow_hosts = set(h.lower().strip(".") for h in config.adblock_allow_hosts())
        self.rules = rules.CompiledRules()
        self._refresh_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        """Правила из кеша — сразу, свежие списки — в фоне."""
        if not self.enabled:
            log.info("adblock disabled")
            return
        await self._load_cached()
        self._refresh_task = asyncio.create_task(self._refresh())

    async def shutdown(self) -> None:
        """Остановить фоновую загрузку списков."""
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

    async def wait_ready(self, timeout: float = 1.5) -> None:
        """Подождать загрузку списков (до timeout секунд), если правил из сети ещё нет."""
        task = self._refresh_task
        if not task or task.done() or self.rules.loaded_filters:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return

    async def filter_videos(self, videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Убрать из найденных видео адреса, которые блокируются; остальные — как были."""
        if not self.enabled:
            return videos
        await self.wait_ready()
        out: list[dict[str, Any]] = []
        for item in videos:
            url = str(item.get("url") or "")
            blocked, reason = self.should_block(url)
            if blocked:
                log.info("adblock dropped player URL (%s): %s", reason, _redact_url(url))
                continue
            out.append(item)
        return out

    def should_block(self, url: str) -> tuple[bool, str | None]:
        """Блокировать ли адрес: (да или нет, причина); свои и разрешающие правила — раньше блокирующих."""
        if not self.enabled or not url:
            return False, None
        try:
            parsed = urlparse(url)
        except Exception:
            return False, None
        host = (parsed.hostname or "").lower().strip(".")
        normalized = url.lower()
        if not host:
            return False, None
        if host in self.allow_hosts or any(s in self.allow_hosts for s in rules.host_suffixes(host)):
            return False, "allow-host"
        for suffix in rules.host_suffixes(host):
            if suffix in self.rules.allow_hosts:
                return False, "allow-filter-host"
        if any(s in normalized for s in self.rules.allow_snippets):
            return False, "allow-filter-snippet"
        if any(rx.search(normalized) for rx in self.rules.allow_regexes):
            return False, "allow-filter-regex"
        for suffix in rules.host_suffixes(host):
            if suffix in self.rules.block_hosts:
                return True, f"host:{suffix}"
        for snippet in self.rules.block_snippets:
            if snippet in normalized:
                return True, f"snippet:{snippet[:32]}"
        for rx in self.rules.block_regexes:
            if rx.search(normalized):
                return True, "regex"
        return False, None

    def status(self) -> dict[str, Any]:
        """Состояние блокировщика для /adblock/status и /health."""
        task = self._refresh_task
        return {
            "enabled": self.enabled,
            "ready": bool(self.rules.loaded_filters),
            "loaded_filters": self.rules.loaded_filters,
            "parsed_rules": self.rules.parsed_rules,
            "block_hosts": len(self.rules.block_hosts),
            "block_snippets": len(self.rules.block_snippets),
            "block_regexes": len(self.rules.block_regexes),
            "updated_at": int(self.rules.updated_at or 0),
            "refreshing": bool(task and not task.done()),
        }

    async def _refresh(self) -> None:
        """Взять списки (из кеша, пока он свежий, или из сети) и собрать правила заново."""
        if not self.enabled:
            return
        texts: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                for url in self.filter_urls:
                    text = await self._get_filter_text(client, url)
                    if text:
                        texts.append(text)
        except Exception as exc:
            log.warning("adblock refresh failed: %s", exc)
        if texts:
            self.rules = rules.compile_rules(texts)
            log.info(
                "adblock ready: filters=%s rules=%s hosts=%s regex=%s",
                self.rules.loaded_filters,
                self.rules.parsed_rules,
                len(self.rules.block_hosts),
                len(self.rules.block_regexes),
            )

    async def _get_filter_text(self, client: httpx.AsyncClient, url: str) -> str | None:
        """Текст списка: из кеша на диске, пока он моложе cache_ttl, иначе из сети с записью в кеш."""
        config.ADBLOCK_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = config.ADBLOCK_CACHE_DIR / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}.txt"
        now = time.time()
        if cache_file.exists() and now - cache_file.stat().st_mtime < self.cache_ttl:
            return cache_file.read_text(encoding="utf-8", errors="ignore")
        response = await client.get(url)
        response.raise_for_status()
        text = response.text
        cache_file.write_text(text, encoding="utf-8")
        return text

    async def _load_cached(self) -> None:
        """Правила из всех списков в кеше на диске — при старте, до загрузки из сети."""
        if not config.ADBLOCK_CACHE_DIR.exists():
            return
        texts: list[str] = []
        for path in config.ADBLOCK_CACHE_DIR.glob("*.txt"):
            try:
                texts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
        if texts:
            self.rules = rules.compile_rules(texts)


def _redact_url(url: str) -> str:
    """Адрес для журнала: без строки запроса, путь обрезан."""
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path[:80]}"
    except Exception:
        return url[:120]


engine = AdblockEngine()


async def startup() -> None:
    """Запустить общий блокировщик (обработчик старта FastAPI)."""
    await engine.startup()


async def shutdown() -> None:
    """Остановить общий блокировщик."""
    await engine.shutdown()


async def filter_videos(videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Отфильтровать видео общим блокировщиком."""
    return await engine.filter_videos(videos)


def should_block(url: str) -> tuple[bool, str | None]:
    """Проверить адрес общим блокировщиком."""
    return engine.should_block(url)


def status() -> dict[str, Any]:
    """Состояние общего блокировщика."""
    return engine.status()
