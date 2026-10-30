"""Shared player selection policy for source discovery and proxy entry points."""
import re
from urllib.parse import parse_qs, unquote, urlsplit

_VK_DOMAINS = ("vk.com", "vk.ru", "vkvideo.ru", "vkvideo.com")
_VK_WRAPPER_NAMES = {"iframevk.html", "iframevk"}
_VK_OWNER_RE = re.compile(r"-?[1-9]\d{0,19}")
_VK_VIDEO_RE = re.compile(r"[1-9]\d{0,19}")
# Score below every other player, including unknown hosts (0).
VK_SCORE = -10


def _split(url: str):
    parsed = urlsplit((url or "").strip())
    host = (parsed.hostname or "").lower().rstrip(".")
    return parsed, host, unquote(parsed.path).lower().rstrip("/")


def _is_vk_host(host: str) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in _VK_DOMAINS)


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
