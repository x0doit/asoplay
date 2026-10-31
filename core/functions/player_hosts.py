"""
Политика плееров для поиска у источников и для прокси плеера: белый список хостов прокси, VK, неиграющие плееры,
предпочтения хостов.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Прокси плеера ходит только к хостам из PROXY_HOSTS (самим и их поддоменам), не к IP-адресам и без логина в адресе;
остальное — 403. Адрес после разрешения имени проверяет сетевой слой клиента (core/classes/http_public.py). Если
плеер перестал играть после смены CDN, его хост виден в журнале «player proxy refused»: его добавляют сюда, когда
убедились, что это хост самого плеера.

Плееры VK и обёртка Yummy над VK никогда не грузятся как проксируемые документы: их видео идут только через
чистый фрейм AsoPlay, без плеера VK и рекламных скриптов обёртки. Остальные хосты ранжируются: CVH, затем Kodik,
затем Alloha, затем неизвестные; VK — ниже всех (VK_SCORE).
"""
import ipaddress
import re
from urllib.parse import parse_qs, unquote, urlsplit

import httpx

_VK_DOMAINS = ("vk.com", "vk.ru", "vkvideo.ru", "vkvideo.com")
_VK_WRAPPER_NAMES = {"iframevk.html", "iframevk"}
_VK_OWNER_RE = re.compile(r"-?[1-9]\d{0,19}")
_VK_VIDEO_RE = re.compile(r"[1-9]\d{0,19}")
# Score below every other player, including unknown hosts (0).
VK_SCORE = -10
# Куда ходит прокси плеера (/player/*, зеркала Kodik): сами хосты и их поддомены. Только плееры и их CDN — yani.tv
# целиком не входит, там сайт Yummy и его API.
PROXY_HOSTS = (
    "kodikplayer.com", "kodikres.com", "solodcdn.com",  # Kodik: плеер, хранилище, видео, превью и проба CDN
    "fonts.googleapis.com", "fonts.gstatic.com", "www.gstatic.com",  # шрифты и Cast в плеере Kodik
    "alloha.yani.tv", "vkvideo.cloud",  # Alloha: плеер и потоки HLS
    "aksor.yani.tv", "aksor.tv",  # Aksor
    "cdnvideohub.com",  # CVH: API и плеер
    "okcdn.ru", "mycdn.me", "vkuser.net", "ok.ru",  # видео CVH и чистого кадра VK
    *_VK_DOMAINS,  # встраивание VK читает сам сервер; документы VK — 403 (is_disabled_player_url)
    "sibnet.ru",  # Sibnet: страница плеера и видео с её CDN
)


def _split(url: str):
    """Адрес → (разобранный адрес, хост строчными без точки в конце, путь без %-кодов строчными без / в конце)."""
    parsed = urlsplit((url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed, host, unquote(parsed.path).lower().rstrip("/")


def _is_vk_host(host: str) -> bool:
    """Хост VK или его поддомен."""
    return any(host == domain or host.endswith("." + domain) for domain in _VK_DOMAINS)


def _is_ip(host: str) -> bool:
    """IP-адрес ли это, а не имя (IPv6 — без скобок, как его отдаёт httpx)."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def proxy_url_refusal(url: str) -> str | None:
    """Почему прокси плеера не пойдёт по адресу; None — пойдёт.

    Только http и https, без логина и пароля, хост — имя из PROXY_HOSTS или его поддомен, не IP-адрес. Адрес
    разбирается так же, как его разберёт httpx при запросе, поэтому проверенный хост и хост подключения совпадают.
    """
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError, ValueError):
        return "bad url"
    if parsed.scheme not in {"http", "https"}:
        return "scheme"
    if parsed.userinfo:
        return "credentials"
    host = parsed.raw_host.decode("ascii", "replace").lower().rstrip(".")
    if _is_ip(host):
        return "ip address"
    if not any(host == domain or host.endswith("." + domain) for domain in PROXY_HOSTS):
        return "host not allowed"
    return None


def vk_video_ref(url: str) -> dict[str, str] | None:
    """Owner and video ids of a VK video behind Yummy's wrapper or a VK embed."""
    try:
        parsed, host, path = _split(url)
        query = parse_qs(parsed.query)
    except ValueError:
        return None
    first = lambda key: (query.get(key) or [""])[0].strip()
    if path.rsplit("/", 1)[-1] in _VK_WRAPPER_NAMES:
        value = first("id")
        owner, _, video = value.removeprefix("video").partition("_")
    elif _is_vk_host(host) and path.endswith("/video_ext.php"):
        owner, video = first("oid"), first("id")
    else:
        return None
    if not _VK_OWNER_RE.fullmatch(owner) or not _VK_VIDEO_RE.fullmatch(video):
        return None
    embed_hash = first("hash")
    return {"oid": owner, "id": video,
            "hash": embed_hash if re.fullmatch(r"[0-9a-f]{1,64}", embed_hash) else ""}


def is_disabled_player_url(url: str) -> bool:
    """VK players and Yummy's VK wrapper never load as proxied documents.

    Their videos play only through AsoPlay's clean frame, without the VK player
    or the wrapper's ad scripts.
    """
    try:
        _, host, path = _split(url)
    except ValueError:
        return False
    # Yummy wraps VK behind its own domain, so host checks alone are insufficient.
    return _is_vk_host(host) or path.rsplit("/", 1)[-1] in _VK_WRAPPER_NAMES


def iframe_preference_score(url: str) -> int:
    """Насколько хорош плеер по адресу: CVH — 30, Kodik — 20, Alloha — 10, остальные — 0, VK — VK_SCORE."""
    try:
        _, host, path = _split(url)
    except ValueError:
        return 0
    if is_disabled_player_url(url):
        return VK_SCORE
    if path.endswith("/iframecvh.html"):
        return 30
    if host == "kodikplayer.com" or host.endswith(".kodikplayer.com"):
        return 20
    if host == "alloha.yani.tv" or host.endswith(".alloha.yani.tv"):
        return 10
    return 0
