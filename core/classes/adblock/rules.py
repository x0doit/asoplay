"""
Правила блокировщика плеера: разбор списков фильтров AdGuard в набор правил — хосты, фрагменты адреса, регулярки.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Берётся только то, что можно проверить по одному адресу плеера (iframe или видео): правила документа, без косметики
и без правил, которые зависят от страницы-источника. До загрузки списков действует встроенный запасной набор. Списки
скачивает и держит в кеше core/classes/adblock/engine.py, он же проверяет адреса.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from core.config import config


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


def host_suffixes(host: str) -> list[str]:
    """Хост и все его родительские домены: a.b.c → a.b.c, b.c, c."""
    host = host.lower().strip(".")
    if not host:
        return []
    parts = host.split(".")
    return [".".join(parts[i:]) for i in range(len(parts))]


def _domain_from_anchor(pattern: str) -> str | None:
    """Домен из правила вида ||домен^ или None (без точки или со звёздочкой — None)."""
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
    """Шаблон правила AdGuard — в регулярное выражение; регулярки /…/ и неразборное — None."""
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
    """Шаблон с * и ^ — в текст регулярного выражения, остальные знаки экранируются."""
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
    """Подходит ли правило с модификаторами ($…) к адресу плеера: без неподдерживаемых, без first-party и domain=, тип —
    документ или не указан."""
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
class CompiledRules:
    """Набор правил: хосты, фрагменты адреса и регулярки — блокирующие и разрешающие; до загрузки списков — встроенный
    запасной набор."""
    block_hosts: set[str] = field(default_factory=lambda: set(_FALLBACK_BLOCK_HOSTS))
    allow_hosts: set[str] = field(default_factory=set)
    block_snippets: set[str] = field(default_factory=lambda: set(_FALLBACK_SNIPPETS))
    allow_snippets: set[str] = field(default_factory=set)
    block_regexes: list[re.Pattern[str]] = field(default_factory=list)
    allow_regexes: list[re.Pattern[str]] = field(default_factory=list)
    loaded_filters: int = 0
    parsed_rules: int = 0
    updated_at: float = 0.0


def compile_rules(filter_texts: list[str]) -> CompiledRules:
    """Списки фильтров — в набор правил; число регулярок и фрагментов ограничено настройками."""
    rules = CompiledRules(loaded_filters=len(filter_texts), updated_at=time.time())
    regex_limit = config.adblock_regex_limit()
    snippet_limit = config.adblock_snippet_limit()
    for text in filter_texts:
        for raw in text.splitlines():
            parse_line(raw, rules, regex_limit=regex_limit, snippet_limit=snippet_limit)
    return rules


def parse_line(
    raw: str,
    rules: CompiledRules,
    *,
    regex_limit: int,
    snippet_limit: int,
) -> None:
    """Строка фильтра — в набор правил: хост, фрагмент адреса или регулярка; комментарии, косметика и неподходящие
    правила пропускаются."""
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
