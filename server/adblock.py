# -*- coding: utf-8 -*-
"""
Lightweight ad-block layer for player URLs.

This is intentionally not a full browser-extension-grade AdGuard engine. A web
page cannot inspect or rewrite the network requests made inside a cross-origin
iframe. What we can safely do in this app:

* load official AdGuard-generated filter lists at runtime and cache them;
* reject ad/tracker iframe or video URLs before sending them to the frontend;
* keep a small built-in fallback while the lists are loading or unavailable.

The filter files are not vendored into the proprietary repository. They are
downloaded at runtime from AdGuard's public distribution URLs and cached under
.cache/adblock, which is already ignored by git.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

log = logging.getLogger("animeviev.adblock")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_CACHE_DIR = _PROJECT_ROOT / ".cache" / "adblock"

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

_FALLBACK_BLOCK_HOSTS = frozenset({
    "2mdn.net",
    "adfox.ru",
    "adnxs.com",
    "adskeeper.co.uk",
    "adskeeper.com",
    "adservice.google.com",
    "adtrafficquality.google",
    "adtrue.com",
    "advmaker.ru",
    "betweendigital.com",
    "bdbdqbdkdb.com",
    "bs.serving-sys.ru",
    "doubleclick.net",
    "exoclick.com",
    "googleadservices.com",
    "googlesyndication.com",
    "googletagmanager.com",
    "googletagservices.com",
    "mgid.com",
    "popads.net",
    "propellerads.com",
    "revcontent.com",
    "serving-sys.ru",
    "taboola.com",
    "trafficbass.com",
    "trafficjunky.net",
    "umwiba.com",
    "yandexadexchange.net",
})

_FALLBACK_SNIPPETS = (
    "/adfox/",
    "/adserver/",
    "/ads/",
    "/advert/",
    "/banner/",
    "/banners/",
    "/popunder",
    "://ads.",
    "://adserver.",
    "ad_type=",
    "adunit",
    "doubleclick",
    "googlesyndication",
    "google-ima",
    "ima3",
    "pre-roll",
    "preroll",
    "rollad",
    "/vast/",
    "vast.xml",
    "vast?",
    "/vpaid/",
    "vpaid",
)

_TYPE_MODIFIERS = {
    "document",
    "subdocument",
    "popup",
    "script",
    "image",
    "stylesheet",
    "font",
    "media",
    "object",
    "xmlhttprequest",
    "websocket",
    "ping",
    "other",
}
_DOCUMENT_TYPES = {"document", "subdocument", "popup"}
_UNSUPPORTED_MODIFIERS = {
    "badfilter",
    "csp",
    "cookie",
    "denyallow",
    "extension",
    "hls",
    "inline-font",
    "inline-script",
    "jsonprune",
    "method",
    "permissions",
    "redirect",
    "redirect-rule",
    "removeheader",
    "removeparam",
    "replace",
    "stealth",
    "to",
    "urltransform",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _split_csv_env(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def _host_suffixes(host: str) -> list[str]:
    host = host.lower().strip(".")
    if not host:
        return []
    parts = host.split(".")
    return [".".join(parts[i:]) for i in range(len(parts))]


def _domain_from_anchor(pattern: str) -> str | None:
    if not pattern.startswith("||"):
        return None
    rest = pattern[2:]
    m = re.match(r"^([a-z0-9][a-z0-9.-]*[a-z0-9])(?:[\^/:?*]|$)", rest, re.I)
    if not m:
        return None
    domain = m.group(1).lower().strip(".")
    if "." not in domain or "*" in domain:
        return None
    return domain


def _pattern_to_regex(pattern: str) -> re.Pattern[str] | None:
    if not pattern or len(pattern) < 4:
        return None
    if pattern.startswith("/") and pattern.endswith("/") and len(pattern) > 2:
        return None
    anchored_start = pattern.startswith("|") and not pattern.startswith("||")
    anchored_end = pattern.endswith("|")
    if anchored_start:
        pattern = pattern[1:]
    if anchored_end:
        pattern = pattern[:-1]
    if pattern.startswith("||"):
        domain = _domain_from_anchor(pattern)
        if not domain:
            return None
        rest = pattern[2 + len(domain):]
        prefix = r"^[a-z][a-z0-9+.-]*://([^/?#]+\.)?"
        body = re.escape(domain) + _wildcard_to_regex(rest)
        regex = prefix + body
    else:
        regex = _wildcard_to_regex(pattern)
        if anchored_start:
            regex = "^" + regex
    if anchored_end:
        regex += "$"
    try:
        return re.compile(regex, re.I)
    except re.error:
        return None


def _wildcard_to_regex(pattern: str) -> str:
    out: list[str] = []
    for ch in pattern:
        if ch == "*":
            out.append(".*")
        elif ch == "^":
            out.append(r"(?:[^A-Za-z0-9_.%-]|$)")
        else:
            out.append(re.escape(ch))
    return "".join(out)


def _rule_applies_to_player_url(modifiers: str) -> bool:
    if not modifiers:
        return True
    mods = [m.strip().lower() for m in modifiers.split(",") if m.strip()]
    names = {m[1:] if m.startswith("~") else m for m in mods}
    if names & _UNSUPPORTED_MODIFIERS:
        return False
    if "first-party" in names:
        return False
    for m in mods:
        if m in {"~document", "~subdocument", "~popup"}:
            return False
    type_mods = names & _TYPE_MODIFIERS
    if type_mods and not (type_mods & _DOCUMENT_TYPES):
        return False
    # Site-specific domain rules usually depend on the source page. The player
    # checker sees only the candidate iframe URL, so skipping them avoids broad
    # false positives.
    if any(m.startswith("domain=") for m in mods):
        return False
    return True


@dataclass
class _CompiledRules:
    block_hosts: set[str] = field(default_factory=lambda: set(_FALLBACK_BLOCK_HOSTS))
    allow_hosts: set[str] = field(default_factory=set)
    block_snippets: set[str] = field(default_factory=lambda: set(_FALLBACK_SNIPPETS))
    allow_snippets: set[str] = field(default_factory=set)
    block_regexes: list[re.Pattern[str]] = field(default_factory=list)
    allow_regexes: list[re.Pattern[str]] = field(default_factory=list)
    loaded_filters: int = 0
    parsed_rules: int = 0
    updated_at: float = 0.0


class AdblockEngine:
    def __init__(self) -> None:
        self.enabled = _env_bool("AV_ADBLOCK_ENABLED", True)
        urls = _split_csv_env("AV_ADBLOCK_FILTER_URLS")
        self.filter_urls = urls or _DEFAULT_FILTER_URLS
        self.cache_ttl = max(3600, int(os.environ.get("AV_ADBLOCK_CACHE_TTL", "86400")))
        self.allow_hosts = set(h.lower().strip(".") for h in _split_csv_env("AV_ADBLOCK_ALLOW_HOSTS"))
        self.rules = _CompiledRules()
        self._refresh_task: asyncio.Task[None] | None = None

    async def startup(self) -> None:
        if not self.enabled:
            log.info("adblock disabled")
            return
        await self._load_cached()
        self._refresh_task = asyncio.create_task(self._refresh())

    async def shutdown(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

    async def wait_ready(self, timeout: float = 1.5) -> None:
        task = self._refresh_task
        if not task or task.done() or self.rules.loaded_filters:
            return
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return

    async def filter_videos(self, videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        if host in self.allow_hosts or any(s in self.allow_hosts for s in _host_suffixes(host)):
            return False, "allow-host"
        for suffix in _host_suffixes(host):
            if suffix in self.rules.allow_hosts:
                return False, "allow-filter-host"
        if any(s in normalized for s in self.rules.allow_snippets):
            return False, "allow-filter-snippet"
        if any(rx.search(normalized) for rx in self.rules.allow_regexes):
            return False, "allow-filter-regex"
        for suffix in _host_suffixes(host):
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
            self.rules = self._compile(texts)
            log.info(
                "adblock ready: filters=%s rules=%s hosts=%s regex=%s",
                self.rules.loaded_filters,
                self.rules.parsed_rules,
                len(self.rules.block_hosts),
                len(self.rules.block_regexes),
            )

    async def _get_filter_text(self, client: httpx.AsyncClient, url: str) -> str | None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = _CACHE_DIR / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()[:16]}.txt"
        now = time.time()
        if cache_file.exists() and now - cache_file.stat().st_mtime < self.cache_ttl:
            return cache_file.read_text(encoding="utf-8", errors="ignore")
        response = await client.get(url)
        response.raise_for_status()
        text = response.text
        cache_file.write_text(text, encoding="utf-8")
        return text

    async def _load_cached(self) -> None:
        if not _CACHE_DIR.exists():
            return
        texts: list[str] = []
        for path in _CACHE_DIR.glob("*.txt"):
            try:
                texts.append(path.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
        if texts:
            self.rules = self._compile(texts)

    def _compile(self, filter_texts: list[str]) -> _CompiledRules:
        rules = _CompiledRules(loaded_filters=len(filter_texts), updated_at=time.time())
        regex_limit = int(os.environ.get("AV_ADBLOCK_REGEX_LIMIT", "25000"))
        snippet_limit = int(os.environ.get("AV_ADBLOCK_SNIPPET_LIMIT", "25000"))
        for text in filter_texts:
            for raw in text.splitlines():
                self._parse_line(raw, rules, regex_limit=regex_limit, snippet_limit=snippet_limit)
        return rules

    def _parse_line(
        self,
        raw: str,
        rules: _CompiledRules,
        *,
        regex_limit: int,
        snippet_limit: int,
    ) -> None:
        line = raw.strip()
        if not line or line.startswith("!") or line.startswith("["):
            return
        if any(marker in line for marker in ("##", "#@#", "#?#", "#$#", "#%#", "$$")):
            return
        allow = line.startswith("@@")
        if allow:
            line = line[2:]
        if "$" in line:
            pattern, modifiers = line.rsplit("$", 1)
            if not _rule_applies_to_player_url(modifiers):
                return
        else:
            pattern = line
        pattern = pattern.strip()
        if not pattern or pattern == "*":
            return
        domain = _domain_from_anchor(pattern)
        target_hosts = rules.allow_hosts if allow else rules.block_hosts
        target_snippets = rules.allow_snippets if allow else rules.block_snippets
        target_regexes = rules.allow_regexes if allow else rules.block_regexes
        if domain and pattern in {f"||{domain}", f"||{domain}^"}:
            target_hosts.add(domain)
            rules.parsed_rules += 1
            return
        simple = pattern.strip("|").replace("^", "")
        if (
            "*" not in simple
            and simple
            and len(simple) >= 5
            and "://" not in simple
            and len(target_snippets) < snippet_limit
        ):
            target_snippets.add(simple.lower())
            rules.parsed_rules += 1
            return
        if len(target_regexes) < regex_limit:
            rx = _pattern_to_regex(pattern)
            if rx is not None:
                target_regexes.append(rx)
                rules.parsed_rules += 1


def _redact_url(url: str) -> str:
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path[:80]}"
    except Exception:
        return url[:120]


engine = AdblockEngine()


async def startup() -> None:
    await engine.startup()


async def shutdown() -> None:
    await engine.shutdown()


async def filter_videos(videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return await engine.filter_videos(videos)


def should_block(url: str) -> tuple[bool, str | None]:
    return engine.should_block(url)


def status() -> dict[str, Any]:
    return engine.status()
