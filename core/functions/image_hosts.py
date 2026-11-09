"""
Хосты картинок: какие принимает /proxy/img и как проверяется хост из списка панели.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

IMG_HOSTS — хосты картинок каталога (MAL, AniList, Shikimori, Anilibria, Yummy): их /proxy/img берёт всегда.
Список панели proxy_hosts (хранит core/functions/site_settings.py) добавляет свои хосты: их картинки браузер сразу
берёт через /proxy/img, а via говорит серверу, как идти к хосту — как обычно (site) или через VPN-мост (vpn).
Запись покрывает сам хост и его поддомены. Хост из панели — имя без схемы, пути и порта. В punycode он переводится
по UTS 46 без переходной обработки, как его переводят браузеры и httpx: straße.de — xn--strae-oqa.de, а не
strasse.de. Зона — с буквы, поэтому IP-адрес и числовые записи вроде 0x7f.1 или 2130706433 — не имя. Общий суффикс
из Public Suffix List (co.uk, github.io) — не хост одного сайта (core/functions/public_suffix.py).
"""
import ipaddress
import re
from typing import Any, Iterable

import httpx
import idna

from core.classes.http_cache import SHIKI_HOST
from core.functions import public_suffix

IMG_HOSTS = (
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
PROXY_HOSTS_MAX = 100
VIA = ("site", "vpn")
# Сервисы «любой IP в имени» (1.2.3.4.nip.io → 1.2.3.4): строка с ними — IP-адрес в обёртке имени. В Public
# Suffix List их нет, поэтому они отклоняются отдельно
WILDCARD_DNS = ("nip.io", "sslip.io", "xip.io", "localtest.me", "lvh.me", "traefik.me")
_LABEL = re.compile(r"(?!-)[a-z0-9-]{1,63}(?<!-)")
_HOST = re.compile(r"[a-z0-9.-]+")


def normalize_host(raw: Any) -> str:
    """Хост из панели в виде для сравнения: punycode по UTS 46, нижний регистр, без точки в конце. Не имя хоста —
    ValueError(raw)."""
    host = str(raw or "").strip().rstrip(".")
    if not host or any(c in host for c in "/:@?#[]\\ \t%"):
        raise ValueError(raw)
    try:
        host = idna.encode(host, uts46=True, transitional=False).decode("ascii")
    except (idna.IDNAError, UnicodeError) as exc:
        raise ValueError(raw) from exc
    labels = host.split(".")
    if (len(host) > 253 or len(labels) < 2 or not labels[-1][:1].isalpha()  # длина — уже в punycode
            or not all(_LABEL.fullmatch(label) for label in labels)):
        raise ValueError(raw)
    return host


def parse_proxy_hosts(items: Iterable[Any], *, strict: bool) -> list[dict[str, str]]:
    """Список панели в сохраняемом виде [{host, via}]: хосты приведены, повторы схлопнуты (первая запись хоста
    остаётся), не больше PROXY_HOSTS_MAX.

    strict — запрос панели: неверный хост или via — ValueError с ним, общий суффикс — ValueError(хост, почему) (этот
    текст панель показывает человеку), лишние записи — ValueError. Иначе — чтение из базы: неверное пропускается,
    лишнее отрезается, а общие суффиксы проверены при сохранении — чтение от файла списка не зависит.
    """
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        raw_host = item.get("host") if isinstance(item, dict) else None
        via = item.get("via", "site") if isinstance(item, dict) else None
        try:
            host = normalize_host(raw_host)
            if via not in VIA:
                raise ValueError(raw_host)
            if strict and public_suffix.is_public_suffix(host):
                raise ValueError(raw_host, "это общий суффикс: под ним сайты разных владельцев, укажите сам сайт")
            if strict and public_suffix.has_suffix_below(host):
                raise ValueError(raw_host, "под ним есть общие суффиксы (как s3.amazonaws.com): строка покрыла бы "
                                           "сайты разных владельцев, укажите сам сайт")
            if strict and matches(host, WILDCARD_DNS):
                raise ValueError(raw_host, "это сервис «любой IP в имени»: такой хост — тот же IP-адрес")
        except ValueError:
            if strict:
                raise
            continue
        if host not in seen:
            seen.add(host)
            out.append({"host": host, "via": via})
    if len(out) > PROXY_HOSTS_MAX:
        if strict:
            raise ValueError(f"больше {PROXY_HOSTS_MAX} хостов")
        out = out[:PROXY_HOSTS_MAX]
    return out


def entry_for(host: str, hosts: Iterable[str]) -> str | None:
    """Запись списка, которой подходит хост: он сам или его поддомен; из нескольких — самая точная (длинная)."""
    host = (host or "").lower().rstrip(".")
    found = [h for h in hosts if host and (host == h or host.endswith("." + h))]
    return max(found, key=len) if found else None


def matches(host: str, hosts: Iterable[str]) -> bool:
    """Хост — один из hosts или поддомен одного из них."""
    return entry_for(host, hosts) is not None


def url_host(url: httpx.URL | str) -> str:
    """Хост адреса так, как к нему подключится httpx: punycode, нижний регистр, без точки в конце."""
    parsed = url if isinstance(url, httpx.URL) else httpx.URL(url)
    return parsed.raw_host.decode("ascii", "replace").lower().rstrip(".")


def url_refusal(url: httpx.URL | str, hosts: Iterable[str]) -> str | None:
    """Почему /proxy/img не пойдёт по адресу; None — пойдёт.

    Только http и https, без логина и пароля, без своего порта (80 и 443 — можно), хост — имя из hosts или его
    поддомен из одних a-z, 0-9, точки и дефиса, не IP-адрес. Адрес разбирается так же, как его разберёт httpx при
    запросе, поэтому проверенный хост и хост подключения совпадают.
    """
    try:
        parsed = url if isinstance(url, httpx.URL) else httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError):
        return "bad url"
    if parsed.scheme not in ("http", "https"):
        return "scheme"
    if parsed.userinfo:
        return "credentials"
    if parsed.port not in (None, 80, 443):
        return "port"
    host = url_host(parsed)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOST.fullmatch(host):
            return "bad host"
        return None if matches(host, hosts) else "host not allowed"
    return "ip address"
