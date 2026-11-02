"""
Public Suffix List: общие суффиксы, под которыми живут сайты разных владельцев (co.uk, github.io, s3.amazonaws.com).

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Строка списка хостов панели покрывает хост и все его поддомены, поэтому общий суффикс в ней открыл бы прокси
картинок для чужих сайтов — как и хост, под которым такие суффиксы есть (amazonaws.com: под ним s3.amazonaws.com
и *.compute.amazonaws.com). Список — официальный файл publicsuffix.org (config.PUBLIC_SUFFIX_FILE, MPL-2.0, версия
и дата — в его шапке); обновляется заменой файла. Правила читаются при первом обращении и переводятся в punycode,
как хосты панели.
"""
from __future__ import annotations

from functools import lru_cache

import idna

from core.config import config


@lru_cache(maxsize=1)
def _rules() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Правила списка: точные, со звёздочкой (без «*.») и исключения (без «!»)."""
    exact: set[str] = set()
    wild: set[str] = set()
    exceptions: set[str] = set()
    try:
        text = config.PUBLIC_SUFFIX_FILE.read_text(encoding="utf-8")
    except FileNotFoundError as exc:  # без списка панель не сохраняет хосты: проверить их нечем
        raise RuntimeError(f"Public Suffix List is missing: {config.PUBLIC_SUFFIX_FILE}") from exc
    for line in text.splitlines():
        rule = line.strip().split(maxsplit=1)[0] if line.strip() else ""
        if not rule or rule.startswith("//"):
            continue
        target = exceptions if rule.startswith("!") else wild if rule.startswith("*.") else exact
        rule = rule.removeprefix("!").removeprefix("*.")
        try:
            rule = rule.lower() if rule.isascii() else idna.encode(rule, uts46=True).decode("ascii")
        except idna.IDNAError:
            continue
        target.add(rule)
    return frozenset(exact), frozenset(wild), frozenset(exceptions)


@lru_cache(maxsize=1)
def _parents(rules: tuple[frozenset[str], frozenset[str], frozenset[str]]) -> frozenset[str]:
    """Хосты, под которыми есть общие суффиксы: предки точных правил, основы правил «*.» и их предки. Ключ кеша —
    сами правила: сменился список — посчитается заново."""
    exact, wild, _ = rules
    out: set[str] = set(wild)
    for rule in (*exact, *wild):
        labels = rule.split(".")
        out.update(".".join(labels[n:]) for n in range(1, len(labels)))
    return frozenset(out)


def has_suffix_below(host: str) -> bool:
    """Под хостом есть общий суффикс: строка с ним покрыла бы сайты разных владельцев."""
    return host in _parents(_rules())


def is_public_suffix(host: str) -> bool:
    """Хост (punycode, нижний регистр) — сам общий суффикс: правило списка или подходит под правило «*.»."""
    exact, wild, exceptions = _rules()
    if host in exceptions:
        return False
    parent = host.partition(".")[2]
    return host in exact or (bool(parent) and parent in wild)
