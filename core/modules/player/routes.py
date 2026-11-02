# -*- coding: utf-8 -*-
"""
Same-origin player proxy with request-level ad filtering.

Browser JavaScript cannot block network requests inside a cross-origin iframe.
This proxy makes the embedded player load through our origin, rewrites resource
URLs to /player/proxy, applies AdGuard-backed checks on every proxied request,
and injects a small bridge that blocks popups plus rewrites dynamically created
script/img/media/fetch/XHR URLs before they escape to third-party origins.
"""
from __future__ import annotations

import html
import json
import logging
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote, urljoin, urlparse, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from core.classes import http_public
from core.classes.adblock import engine as adblock
from core.config import config
from core.functions import site_settings
from core.functions.player_hosts import is_disabled_player_url, proxy_url_refusal, vk_video_ref

log = logging.getLogger("asoplay.player_proxy")

router = APIRouter(prefix="/player")
kodik_router = APIRouter()

_client: httpx.AsyncClient | None = None
_PROXY_VERSION = "20261029-player39"
_KODIK_SKIN_CSS = config.PLAYER_DIR / "kodik-skin.css"
_SLOT = re.compile(r"@@(\w+)@@")
_AUTHOR = "Автор: Чепела Даниил Максимович."


def _text(name: str) -> str:
    """Текст файла из design/EDDAS/player без шапки-комментария с автором: она для читателя, в ответ не идёт.

    Шапка должна быть ровно одна и в начале файла. Нет её или их две — ошибка при запуске, а не лишний текст в ответе.
    """
    raw = (config.PLAYER_DIR / name).read_text(encoding="utf-8")
    end = "*/\n" if raw.startswith("/*") else "-->\n" if raw.startswith("<!--") else ""
    if not end or end not in raw:
        raise RuntimeError(f"design/EDDAS/player/{name}: в начале нет шапки-комментария")
    cut = raw.index(end) + len(end)
    if _AUTHOR not in raw[:cut] or _AUTHOR in raw[cut:]:
        raise RuntimeError(f"design/EDDAS/player/{name}: шапка с автором должна быть ровно одна, в начале")
    return raw[cut:]


def _template(name: str, slots: set[str], **parts: tuple[str, ...]) -> str:
    """Шаблон кадра: файл, в чьи места @@часть@@ вставлены склеенные части; в итоге — ровно места slots."""
    out = _text(name)
    for slot, files in parts.items():
        out = out.replace(f"@@{slot}@@", "".join(_text(f) for f in files), 1)
    found = set(_SLOT.findall(out))
    if found != slots:
        raise RuntimeError(f"design/EDDAS/player/{name}: места {sorted(found)}, ждали {sorted(slots)}")
    return out


def _fill(template: str, **values: str) -> str:
    """Подстановка в один проход, как у f-string: вставленные значения повторно не просматриваются."""
    return _SLOT.sub(lambda m: values[m.group(1)], template)


_FRAME_PAGE = _template("frame.html", {"safe_id", "proxied_src", "poster_attr", "sources_json", "hls_only"},
                        frame_js=("frame/01-player.js", "frame/02-controls.js"))
_BRIDGE_PAGE = _template("bridge.html", {"kodik_skin", "base_js", "is_kodik_js", "is_alloha_js", "proxy_version"},
                         bridge_js=("bridge/01-shield.js", "bridge/02-urls.js", "bridge/03-network.js",
                                    "bridge/04-dom.js"))
_ERROR_PAGE = _template("error.html", {"message"})
_SHIELD_LOGGER_JS = _text("shield.js")
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_YUMMY_SITE = "https://ru.yummyani.me/"
# The player iframe's flags on the page (app.js createIframe), never allow-same-origin: a proxied document
# opened on its own gets an opaque origin, without the site's cookies and storage.
_FRAME_SANDBOX = "sandbox allow-scripts allow-presentation"
# Answers a browser can open as a page on their own. Outside the player's documents they get a sandbox with no
# flags: no scripts, no site origin. An answer without a type counts too, since the browser sniffs it.
_PAGE_TYPES = {"", "text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml"}
# Redirects are followed by hand in _send, each hop checked; as many as httpx follows by default.
_MAX_REDIRECTS = 20

_TEXT_TYPES = (
    "text/",
    "application/javascript",
    "application/x-javascript",
    "application/json",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/xml",
    "application/xhtml+xml",
    "image/svg+xml",
)
_BLOCKED_HEADERS = {
    "connection",
    "content-encoding",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "transfer-encoding",
    "upgrade",
}
_PASSTHROUGH_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "borth",  # Alloha signs its playlist request with it and answers 404 without it.
    "content-type",
    "range",
    "user-agent",
    "x-requested-with",
}
_VIDEO_HOST_SUFFIXES = (
    "okcdn.ru",
    "vkuser.net",
    "mycdn.me",
    "ok.ru",
    "video.sibnet.ru",
)

_AD_TEXT_RE = re.compile(
    r"(casino|bookmaker|betting|vulkan|1xbet|pin[-\s]?up|av\s*casino|"
    r"werbung|\u0440\u0435\u043a\u043b\u0430\u043c|\u043a\u0430\u0437\u0438\u043d\u043e|"
    r"\u0441\u0442\u0430\u0432\u043a|\u0431\u0443\u043a\u043c\u0435\u043a\u0435\u0440|"
    r"advert|preroll|vast|vpaid|popunder)",
    re.I,
)
_AD_PAYLOAD_KEYS = {
    "ad",
    "ads",
    "adtag",
    "adtagurl",
    "adurl",
    "adurls",
    "adunit",
    "adunits",
    "adsenabled",
    "advert",
    "advertising",
    "vast",
    "vpaid",
    "preroll",
    "prerolls",
    "midroll",
    "midrolls",
    "pauseroll",
    "pauserolls",
    "postroll",
    "postrolls",
    "rollad",
    "banner",
    "banners",
    "commercial",
    "promo",
    "promos",
    "trackingurls",
}


async def startup() -> None:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=15.0),
            # Redirects go through _send, which checks every hop against the host list.
            follow_redirects=False,
            # Connects only to public addresses of a name, checked after it resolves (core/classes/http_public).
            transport=http_public.transport(
                limits=httpx.Limits(max_connections=80, max_keepalive_connections=30), trust_env=False),
            # Video hosts are latency-sensitive and several of them reject or
            # fail through the app-wide xray HTTP_PROXY. Keep player traffic
            # direct; the regular API proxy layer still uses vpn_bridge.
            trust_env=False,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0 Safari/537.36"
                ),
                "Accept-Language": "ru,en;q=0.9",
            },
        )


async def shutdown() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _get_client() -> httpx.AsyncClient:
    if _client is None or _client.is_closed:
        await startup()
    assert _client is not None
    return _client


class _RefusalLog:
    """Refusal lines in the log: at most one per host a minute and LINES a minute for all hosts, so requests to
    random hosts cannot flood the log. Refusals left out are counted into the next line."""

    WINDOW = 60.0
    LINES = 30

    def __init__(self) -> None:
        self._hosts: dict[str, list] = {}  # host -> [when its line was written, refusals since then]
        self._minute: float | None = None
        self._lines = 0
        self._dropped = 0  # refusals over LINES, and those of hosts that went quiet for two minutes

    def __call__(self, host: str, reason: str) -> None:
        now = time.monotonic()
        if self._minute is None or now - self._minute >= self.WINDOW:
            self._minute, self._lines = now, 0
            # A host keeps its count for one more minute, for its own next line; then the count moves on.
            for key, (at, quiet) in list(self._hosts.items()):
                if now - at >= 2 * self.WINDOW:
                    self._dropped += quiet
                    del self._hosts[key]
        entry = self._hosts.get(host)
        if entry is not None and now - entry[0] < self.WINDOW:
            entry[1] += 1
            return
        if self._lines >= self.LINES:
            self._dropped += 1
            return
        self._lines += 1
        quiet = entry[1] if entry is not None else 0
        dropped, self._dropped = self._dropped, 0
        log.info("player proxy refused %s (%s)%s%s", host, reason,
                 f"; {quiet} more for this host since its last line" if quiet else "",
                 f"; {dropped} more for other hosts not logged" if dropped else "")
        self._hosts[host] = [now, 0]


_log_refusal = _RefusalLog()


async def _send(method: str, url: str, *, stream: bool = False, **kwargs: Any) -> httpx.Response:
    """Send with the player client, following redirects by hand: every hop must pass proxy_url_refusal, and the
    client itself connects only to public addresses. A refused hop raises http_public.Refused; the responses
    left behind on the way are closed."""
    client = await _get_client()
    request = client.build_request(method, url, **kwargs)
    for _ in range(_MAX_REDIRECTS + 1):
        host = request.url.host
        refusal = proxy_url_refusal(str(request.url))
        if refusal:
            _log_refusal(host, refusal)
            raise http_public.Refused(f"{refusal}: {host}", request=request)
        try:
            response = await client.send(request, stream=stream, follow_redirects=False)
        except http_public.Refused as exc:
            _log_refusal(host, str(exc))
            raise
        except httpx.InvalidURL as exc:
            # httpx builds the next hop itself, even when it does not follow it; a Location it cannot parse
            # (javascript:, data:) ends the chain as an upstream fault, not a 500.
            raise httpx.RemoteProtocolError(f"bad redirect: {exc}", request=request) from exc
        if response.next_request is None:
            return response
        await response.aclose()
        request = response.next_request
    raise httpx.TooManyRedirects("Exceeded maximum allowed redirects.", request=request)


def _valid_http_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}/"


def _ascii_url(url: str) -> str:
    """Return a header/request-safe URI without decoding already-escaped bytes."""
    try:
        parts = urlsplit(url)
        netloc = parts.netloc.encode("idna").decode("ascii")
        path = quote(parts.path, safe="/:%@!$&'()*+,;=-._~%")
        query = quote(parts.query, safe="=&?/:@!$'()*+,;=-._~%+")
        fragment = quote(parts.fragment, safe="=&?/:@!$'()*+,;=-._~%+")
        return urlunsplit((parts.scheme, netloc, path, query, fragment))
    except Exception:
        return url.encode("ascii", errors="ignore").decode("ascii")


def _proxy_url(url: str, base: str | None = None) -> str:
    if not url:
        return url
    raw = html.unescape(url).strip()
    lower = raw.lower()
    if (
        lower.startswith("#")
        or lower.startswith("about:")
        or lower.startswith("blob:")
        or lower.startswith("data:")
        or lower.startswith("javascript:")
        or lower.startswith("mailto:")
        or lower.startswith("tel:")
    ):
        return raw
    absolute = urljoin(base or "", raw)
    if not _valid_http_url(absolute):
        return raw
    return (
        f"/player/proxy?url={quote(absolute, safe='')}"
        f"&base={quote(base or absolute, safe='')}&pv={_PROXY_VERSION}"
    )


def _guess_base(url: str, base: str | None) -> str:
    if base and _valid_http_url(base):
        return base
    return url


def _path_proxy_url(url: str, base: str, *, document: bool = False) -> str:
    absolute = urljoin(base, html.unescape(url).strip())
    if not _valid_http_url(absolute):
        return url
    parsed = urlsplit(_ascii_url(absolute))
    if parsed.username or parsed.password:
        return _proxy_url(url, base)
    # Keep native ESM resolution and HTML location.search independent of proxy args.
    kind = "documents" if document else "modules"
    return urlunsplit(("", "", f"/player/{kind}/{parsed.scheme}/{parsed.netloc}{parsed.path or '/'}",
                       parsed.query, parsed.fragment))


def _module_proxy_url(url: str, base: str) -> str:
    return _path_proxy_url(url, base)


class _ResourceTagParser(HTMLParser):
    def handle_starttag(self, tag, attrs):
        self.tag = tag
        self.attrs = dict(attrs)


_INTEGRITY_ATTR_RE = re.compile(r"""\s+integrity\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.I)


def _rewrite_resource_tag(tag: str, base: str) -> str:
    parsed = _ResourceTagParser()
    parsed.feed(tag)
    attrs = getattr(parsed, "attrs", {})
    tag_name = getattr(parsed, "tag", "")
    is_module = (
        tag_name == "script" and (attrs.get("type") or "").lower() == "module"
        or tag_name == "link" and "modulepreload" in (attrs.get("rel") or "").lower().split()
    )
    if tag_name in {"script", "link"}:
        # The proxy rewrites these files, so an SRI hash would make the browser block them.
        tag = _INTEGRITY_ATTR_RE.sub("", tag)
    return _rewrite_attrs(tag, base, module=is_module, document=tag_name == "iframe")


def _headers_for_upstream(request: Request, url: str, base: str | None,
                          referer: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lk = key.lower()
        if lk in _PASSTHROUGH_REQUEST_HEADERS:
            headers[key] = value
    upstream_host = urlparse(url).netloc.lower()
    if upstream_host.endswith(_VIDEO_HOST_SUFFIXES):
        for key in [key for key in headers if key.lower() in {"accept", "user-agent"}]:
            headers.pop(key, None)
        headers["Accept"] = "video/webm,video/ogg,video/*;q=0.9,*/*;q=0.5"
        headers["User-Agent"] = _CHROME_UA
    elif not any(key.lower() == "user-agent" for key in headers):
        # setdefault missed the browser's lowercase key, so CDNs got two agents and some answered 403.
        headers["User-Agent"] = _CHROME_UA
    referer = referer or (base if base and _valid_http_url(base) else _origin(url))
    referer = _ascii_url(referer)
    headers["Referer"] = referer
    headers["Origin"] = _origin(referer).rstrip("/")
    headers["Accept-Encoding"] = "identity"
    return headers


_HOST_HEADER_RE = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?")


def _site_origin(request: Request) -> str:
    """The site's own origin as the browser sees it; nginx sends Host and X-Forwarded-Proto."""
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip().lower()
    host = (request.headers.get("host") or "").strip()
    return f"{proto}://{host}" if proto in {"http", "https"} and _HOST_HEADER_RE.fullmatch(host) else ""


def _response_headers(upstream: httpx.Response, *, html_frame: bool = False, origin: str = "") -> dict[str, str]:
    headers: dict[str, str] = {
        "Access-Control-Allow-Origin": "*",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Cache-Control": "no-store" if html_frame else "public, max-age=300",
    }
    for key, value in upstream.headers.items():
        lk = key.lower()
        if lk in _BLOCKED_HEADERS:
            continue
        if lk in {"content-type", "accept-ranges", "content-range", "last-modified", "etag"}:
            headers[key] = value
    if html_frame:
        # The sandboxed frame's origin is opaque, and Safari then refuses fetch/XHR to our own
        # server under 'self' (Kodik's /ftor failed on iPhones), so the origin is named too.
        own = f"'self' {origin}" if origin else "'self'"
        headers["Content-Security-Policy"] = (
            f"default-src {own} data: blob:; "
            f"script-src {own} 'unsafe-inline' 'unsafe-eval' blob:; "
            f"style-src {own} 'unsafe-inline' data:; "
            f"img-src {own} data: blob:; "
            f"font-src {own} data:; "
            f"media-src {own} data: blob: https://*.okcdn.ru https://*.mycdn.me https://*.ok.ru; "
            f"connect-src {own} data: blob: "
            "https://plapi.cdnvideohub.com https://api.ok.ru https://api.mycdn.me "
            "https://api.okcdn.ru https://videotestapi.ok.ru https://apitest.ok.ru; "
            f"frame-src {own} data: blob:; "
            f"worker-src {own} blob:; "
            f"object-src 'none'; base-uri 'none'; form-action 'none'; {_FRAME_SANDBOX}"
        )
    elif (upstream.headers.get("content-type") or "").split(";", 1)[0].strip().lower() in _PAGE_TYPES:
        headers["Content-Security-Policy"] = "sandbox"
    return headers


def _looks_textual(content_type: str) -> bool:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    return any(ct.startswith(prefix) for prefix in _TEXT_TYPES)


def _charset_from_content_type(content_type: str) -> str:
    match = re.search(r"charset\s*=\s*([^\s;]+)", content_type or "", re.I)
    if not match:
        return "utf-8"
    return match.group(1).strip("\"'") or "utf-8"


def _is_javascript(content_type: str, url: str = "") -> bool:
    ct = (content_type or "").split(";", 1)[0].strip().lower()
    return ct in {"application/javascript", "application/x-javascript", "text/javascript"} or url.lower().endswith(".js")


def _decode_text(response: httpx.Response) -> str:
    encoding = response.encoding or "utf-8"
    return response.content.decode(encoding, errors="ignore")


def _rewrite_attrs(text: str, base: str, *, module: bool = False, document: bool = False) -> str:
    def repl_attr(match: re.Match[str]) -> str:
        attr, quote_char, value = match.group(1), match.group(2), match.group(3)
        url = (_path_proxy_url(value, base, document=True) if document
               else _module_proxy_url(value, base) if module else _proxy_url(value, base))
        return f"{attr}{quote_char}{html.escape(url, quote=True)}{quote_char}"

    def repl_srcset(match: re.Match[str]) -> str:
        attr, quote_char, value = match.group(1), match.group(2), match.group(3)
        parts = []
        for item in value.split(","):
            item = item.strip()
            if not item:
                continue
            bits = item.split()
            bits[0] = _proxy_url(bits[0], base)
            parts.append(" ".join(bits))
        return f"{attr}{quote_char}{html.escape(', '.join(parts), quote=True)}{quote_char}"

    text = re.sub(r"(\b(?:src|href|poster|data|action)\s*=\s*)(['\"])(.*?)(\2)", repl_attr, text, flags=re.I | re.S)
    text = re.sub(r"(\bsrcset\s*=\s*)(['\"])(.*?)(\2)", repl_srcset, text, flags=re.I | re.S)
    return _rewrite_css_urls(text, base)


def _rewrite_css_urls(text: str, base: str) -> str:
    def repl_url(match: re.Match[str]) -> str:
        quote_char = match.group(1) or ""
        value = match.group(2).strip()
        return f"url({quote_char}{_proxy_url(value, base)}{quote_char})"

    def repl_import(match: re.Match[str]) -> str:
        quote_char = match.group(1)
        value = match.group(2)
        return f"@import {quote_char}{_proxy_url(value, base)}{quote_char}"

    text = re.sub(r"url\(\s*(['\"]?)(.*?)\1\s*\)", repl_url, text, flags=re.I | re.S)
    return re.sub(r"@import\s+(['\"])(.*?)\1", repl_import, text, flags=re.I | re.S)


def _rewrite_m3u8(text: str, base: str, referer: str | None = None) -> str:
    # Entries resolve against the playlist but keep the page's referer, as a browser would send:
    # Alloha's CDN answers 403 when nested playlists arrive with the master playlist as referer.
    def proxied(value: str) -> str:
        return _proxy_url(urljoin(base, value), referer or base)

    def repl_uri(match: re.Match[str]) -> str:
        return f'{match.group(1)}"{proxied(match.group(2))}"'

    out: list[str] = []
    skip_next_uri = False
    for line in text.splitlines():
        stripped = line.strip()
        lower = stripped.lower()
        if _AD_TEXT_RE.search(stripped):
            skip_next_uri = True
            continue
        if stripped.startswith("#"):
            out.append(re.sub(r'(URI=)"([^"]+)"', repl_uri, line, flags=re.I))
            continue
        if skip_next_uri:
            skip_next_uri = False
            continue
        out.append(proxied(stripped) if stripped else stripped)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _rewrite_javascript(text: str, base: str) -> str:
    parsed = urlparse(base)
    host = parsed.netloc.lower()
    if host.endswith("player.cdnvideohub.com") and "plapi.cdnvideohub.com" in text:
        text = text.replace("https://plapi.cdnvideohub.com", "/player/cvh-api")
    # Only adapt known optional probes; unknown upstream versions keep their behavior.
    if host == "www.gstatic.com" and parsed.path == "/cv/js/sender/v1/cast_sender.js":
        entry = "F=function(a){a.length?E(a.shift(),function(){F(a)}):G()}"
        if text.count(entry) == 1:
            text = text.replace(entry, entry.replace(
                "F=function(a){", "F=function(a){if(window.__asoplayPrepareCast)window.__asoplayPrepareCast(a);",
            ), 1)
        else:
            log.warning("Cast loader changed; leaving its candidate order unchanged")
    if _is_kodik_base(base):
        probe = ('$.ajax({url:cdnCheckLink,type:"GET",cache:!1,'
                 'success:function(e){playerSettings.cdnIsWorking=!0},'
                 'error:function(e){playerSettings.cdnIsWorking=!1}})')
        if text.count(probe) == 1:
            text = text.replace(probe, (
                '(window.__asoplayCheckCdn?window.__asoplayCheckCdn(cdnCheckLink,'
                'function(available){playerSettings.cdnIsWorking=available}):' + probe + ')'
            ), 1)
    # Plyr inlines a sprite from another host; a same-origin one stays <use> links the sandbox blocks.
    if _is_alloha_base(base) and text.count("'/images/allplay.svg'") == 1:
        text = text.replace("'/images/allplay.svg'", "'https://alloha.yani.tv/images/allplay.svg'")
    return text


def _strip_ad_payload(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if str(key).lower() in _AD_PAYLOAD_KEYS:
                continue
            out[key] = _strip_ad_payload(item)
        return out
    if isinstance(value, list):
        return [_strip_ad_payload(item) for item in value]
    return value


def _strip_cvh_ads(content: bytes, content_type: str, url: str) -> bytes:
    parsed = urlparse(url)
    if parsed.netloc.lower() != "plapi.cdnvideohub.com":
        return content
    if "json" not in (content_type or "").lower():
        return content
    try:
        data = json.loads(content.decode(_charset_from_content_type(content_type), errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return content
    cleaned = _strip_ad_payload(data)
    return json.dumps(cleaned, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _is_cvh_iframe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.netloc.lower().endswith("yummyani.me") and parsed.path.endswith("/iframeCVH.html")


def _pick_cvh_item(items: list[dict], episode: int, voice_code: str) -> dict | None:
    def item_episode(item: dict) -> int:
        try:
            return int(item.get("episode") or 0)
        except (TypeError, ValueError):
            return 0

    def norm(value: str) -> str:
        return re.sub(r"\s+", " ", (value or "").strip().lower())

    episode_items = [item for item in items if item_episode(item) == episode] or items
    if voice_code:
        wanted = norm(voice_code)
        for item in episode_items:
            if norm(str(item.get("voiceStudio") or "")) == wanted:
                return item
    return episode_items[0] if episode_items else None


def _cvh_video_sources(data: dict) -> list[dict[str, str]]:
    sources = data.get("sources") if isinstance(data, dict) else {}
    if not isinstance(sources, dict):
        return []
    variants = (
        ("mpegFullHdUrl", "1080p"),
        ("mpegHighUrl", "720p"),
        ("mpegMediumUrl", "480p"),
        ("mpegLowUrl", "360p"),
        ("mpegLowestUrl", "240p"),
        ("mpegTinyUrl", "144p"),
    )
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for key, label in variants:
        value = sources.get(key)
        if not isinstance(value, str) or not value.startswith("http") or value in seen:
            continue
        seen.add(value)
        out.append({"label": label, "url": value})
    if not out:
        for key in ("hlsUrl", "dashUrl"):
            value = sources.get(key)
            if isinstance(value, str) and value.startswith("http") and value not in seen:
                out.append({"label": "Auto", "url": value})
                break
    return out


def _cvh_error_frame(message: str) -> Response:
    safe = html.escape(message, quote=True)
    body = _fill(_ERROR_PAGE, message=safe)
    return Response(body, media_type="text/html", headers={"Cache-Control": "no-store"})


def _clean_video_frame(
    source_url: str,
    base_url: str,
    poster_url: str = "",
    *,
    element_id: str = "av-clean-video",
    sources: list[dict[str, str]] | None = None,
) -> Response:
    source_items: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for item in sources or []:
        raw_url = str(item.get("url") or "").strip()
        if not raw_url or raw_url in seen_urls:
            continue
        seen_urls.add(raw_url)
        source_items.append({
            "label": str(item.get("label") or "Auto"),
            "url": _proxy_url(raw_url, base_url),
        })
    if not source_items and source_url:
        seen_urls.add(source_url)
        source_items.append({"label": "Auto", "url": _proxy_url(source_url, base_url)})
    # No MSE player is bundled, so HLS needs native support; say so instead of failing silently.
    hls_only = bool(seen_urls) and all(urlsplit(url).path.lower().endswith(".m3u8") for url in seen_urls)

    proxied_src = html.escape(source_items[0]["url"] if source_items else "", quote=True)
    safe_id = html.escape(element_id, quote=True)
    proxied_poster = html.escape(_proxy_url(poster_url, base_url), quote=True) if poster_url else ""
    poster_attr = f' poster="{proxied_poster}"' if proxied_poster else ""
    sources_json = json.dumps(source_items, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    body = _fill(_FRAME_PAGE, safe_id=safe_id, proxied_src=proxied_src, poster_attr=poster_attr,
                 sources_json=sources_json, hls_only="true" if hls_only else "false")
    headers = {
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'self' data: blob:; "
            "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src 'self' data: blob:; media-src 'self' data: blob:; "
            f"connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'; {_FRAME_SANDBOX}"
        ),
    }
    return Response(body, media_type="text/html", headers=headers)


async def _cvh_frame(request: Request, iframe_url: str) -> Response:
    params = parse_qs(urlparse(iframe_url).query)
    anime_id = (params.get("anime_id") or [""])[0].strip()
    voice_code = (params.get("dubbing_code") or [""])[0].strip()
    try:
        episode = int((params.get("episode") or ["1"])[0] or 1)
    except ValueError:
        episode = 1
    if not anime_id:
        return _cvh_error_frame("Видео не найдено")

    safe_iframe_url = _ascii_url(iframe_url)
    playlist_url = f"https://plapi.cdnvideohub.com/api/v1/player/sv/playlist?pub=745&aggr=mali&id={quote(anime_id)}"
    try:
        playlist_resp = await _send(
            "GET",
            playlist_url,
            headers={
                "Referer": safe_iframe_url,
                "Origin": "https://ru.yummyani.me",
                "Accept": "application/json",
            },
        )
        if playlist_resp.status_code == 204:
            return _cvh_error_frame("Видео не найдено")
        playlist_resp.raise_for_status()
        playlist = json.loads(_strip_cvh_ads(
            playlist_resp.content,
            playlist_resp.headers.get("content-type", "application/json"),
            playlist_url,
        ).decode("utf-8"))
    except (httpx.HTTPError, json.JSONDecodeError, UnicodeDecodeError):
        log.exception("cvh playlist failed")
        return _cvh_error_frame("Видео не загрузилось")

    items = playlist.get("items") if isinstance(playlist, dict) else []
    if not isinstance(items, list):
        items = []
    item = _pick_cvh_item(items, episode, voice_code)
    vk_id = str((item or {}).get("vkId") or "").strip()
    if not vk_id:
        return _cvh_error_frame("Видео не найдено")

    video_url = f"https://plapi.cdnvideohub.com/api/v1/player/sv/video/{quote(vk_id)}"
    try:
        video_resp = await _send(
            "GET",
            video_url,
            headers={
                "Referer": "https://player.cdnvideohub.com/",
                "Origin": "https://player.cdnvideohub.com",
                "Accept": "application/json",
            },
        )
        video_resp.raise_for_status()
        video_data = video_resp.json()
    except (httpx.HTTPError, json.JSONDecodeError):
        log.exception("cvh video source failed")
        return _cvh_error_frame("Видео не загрузилось")

    source_items = _cvh_video_sources(video_data)
    if not source_items:
        return _cvh_error_frame("Видео не найдено")

    poster_url = video_data.get("thumbUrl") if isinstance(video_data, dict) else ""
    return _clean_video_frame(
        source_items[0]["url"],
        video_url,
        poster_url,
        element_id="av-cvh-video",
        sources=source_items,
    )


def _is_sibnet_iframe_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    return parsed.netloc.lower().endswith("video.sibnet.ru") and parsed.path.endswith("/shell.php")


def _match_first(patterns: tuple[str, ...], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.I | re.S)
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


async def _sibnet_frame(request: Request, iframe_url: str) -> Response:
    try:
        frame_resp = await _send(
            "GET",
            _ascii_url(iframe_url),
            headers={
                "Referer": "https://video.sibnet.ru/",
                "Origin": "https://video.sibnet.ru",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "User-Agent": _CHROME_UA,
            },
        )
        frame_resp.raise_for_status()
    except httpx.HTTPError:
        log.exception("sibnet frame failed")
        return _cvh_error_frame("Видео не загрузилось")

    text = _decode_text(frame_resp)
    source_url = _match_first((
        r"player\.src\(\s*\[\s*\{\s*src\s*:\s*['\"]([^'\"]+\.mp4[^'\"]*)['\"]",
        r"src\s*:\s*['\"]([^'\"]+\.mp4[^'\"]*)['\"]",
        r"['\"]src['\"]\s*:\s*['\"]([^'\"]+\.mp4[^'\"]*)['\"]",
    ), text)
    if not source_url:
        return await _fetch(request, iframe_url, iframe_url, frame=True)

    poster_url = _match_first((
        r"poster\s*:\s*['\"]([^'\"]+)['\"]",
        r"<meta[^>]+property=['\"]og:image['\"][^>]+content=['\"]([^'\"]+)['\"]",
        r"<meta[^>]+content=['\"]([^'\"]+)['\"][^>]+property=['\"]og:image['\"]",
    ), text)
    source_url = urljoin(str(frame_resp.url), source_url)
    poster_url = urljoin(str(frame_resp.url), poster_url) if poster_url else ""
    return _clean_video_frame(source_url, str(frame_resp.url), poster_url, element_id="av-sibnet-video")


_VK_STREAM_KEYS = ("hls_ondemand", "hls")
_VK_MEDIA_HOSTS = ("okcdn.ru", "vkuser.net", "mycdn.me")


def _json_string_field(text: str, key: str) -> str:
    match = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % re.escape(key), text)
    if not match:
        return ""
    try:
        return json.loads(f'"{match.group(1)}"')
    except ValueError:
        return ""


def _is_vk_media_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == h or host.endswith("." + h) for h in _VK_MEDIA_HOSTS)


def _hls_quality_sources(master_url: str, playlist: str) -> list[dict[str, str]]:
    variants: dict[int, str] = {}
    stream_info = ""
    for line in playlist.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-STREAM-INF:"):
            stream_info = line
        elif stream_info and line and not line.startswith("#"):
            height = re.search(r"RESOLUTION=\d+x(\d+)", stream_info)
            url = urljoin(master_url, line)
            if height and _is_vk_media_url(url):
                variants.setdefault(int(height.group(1)), url)
            stream_info = ""
    return [{"label": f"{h}p", "url": variants[h]} for h in sorted(variants, reverse=True)]


async def _vk_frame(request: Request, ref: dict[str, str]) -> Response:
    """VK video in AsoPlay's clean player: no VK UI, no wrapper ad scripts."""
    params = {"oid": ref["oid"], "id": ref["id"], "hd": "2"}
    if ref.get("hash"):
        params["hash"] = ref["hash"]
    try:
        embed = await _send(
            "GET", "https://vk.com/video_ext.php", params=params,
            headers={"Accept": "text/html,application/xhtml+xml", "User-Agent": _CHROME_UA},
            timeout=httpx.Timeout(12.0, connect=5.0),
        )
        embed.raise_for_status()
    except httpx.HTTPError as exc:
        log.info("vk embed unavailable: %s", type(exc).__name__)
        return _cvh_error_frame("Видео не загрузилось")
    text = _decode_text(embed)
    # Stream URLs are bound to this server's IP, so the browser plays them through /player/proxy.
    master = next((url for url in (_json_string_field(text, key) for key in _VK_STREAM_KEYS)
                   if _is_vk_media_url(url)), "")
    if not master:
        return _cvh_error_frame("Видео недоступно")
    sources = [{"label": "Auto", "url": master}]
    try:
        playlist = await _send("GET", master, headers=_headers_for_upstream(request, master, "https://vk.com/"),
                               timeout=httpx.Timeout(8.0, connect=4.0))
        if playlist.status_code == 200:
            sources += _hls_quality_sources(master, _decode_text(playlist))
    except httpx.HTTPError:
        pass  # Auto still plays the adaptive master playlist.
    return _clean_video_frame(master, "https://vk.com/", element_id="av-vk-video", sources=sources)


def _is_kodik_base(base: str) -> bool:
    try:
        host = urlparse(base).netloc.lower()
    except Exception:
        return False
    return host == "kodikplayer.com" or host.endswith(".kodikplayer.com")


def _is_alloha_base(base: str) -> bool:
    try:
        host = urlparse(base).netloc.lower()
    except Exception:
        return False
    return host == "alloha.yani.tv" or host.endswith(".alloha.yani.tv")


def _bridge_script(base: str) -> str:
    base_js = json.dumps(base)
    is_kodik_js = json.dumps(_is_kodik_base(base))
    is_alloha_js = json.dumps(_is_alloha_base(base))
    kodik_skin = (
        f'<link rel="stylesheet" href="/player/kodik-skin.css?v={_PROXY_VERSION}">\n'
        if _is_kodik_base(base) else ""
    )
    return _fill(_BRIDGE_PAGE, kodik_skin=kodik_skin, base_js=base_js, is_kodik_js=is_kodik_js,
                 is_alloha_js=is_alloha_js, proxy_version=_PROXY_VERSION)


def _rewrite_html(text: str, base: str) -> str:
    chunks = re.split(r"(<script\b[^>]*>.*?</script\s*>|<(?:link|iframe)\b[^>]*>)", text, flags=re.I | re.S)
    rewritten: list[str] = []
    for chunk in chunks:
        if re.match(r"<script\b", chunk, flags=re.I):
            tag_match = re.match(r"(<script\b[^>]*>)(.*?)(</script\s*>)", chunk, flags=re.I | re.S)
            if tag_match:
                rewritten.append(_rewrite_resource_tag(tag_match.group(1), base) + tag_match.group(2) + tag_match.group(3))
            else:
                rewritten.append(_rewrite_attrs(chunk, base))
        elif re.match(r"<(?:link|iframe)\b", chunk, flags=re.I):
            rewritten.append(_rewrite_resource_tag(chunk, base))
        else:
            rewritten.append(_rewrite_attrs(chunk, base))
    text = "".join(rewritten)
    bridge = _bridge_script(base)
    if re.search(r"<head[^>]*>", text, re.I):
        return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + bridge, text, count=1, flags=re.I)
    return bridge + text


_HLS_TYPE = "application/vnd.apple.mpegurl"


def _is_playlist(content_type: str, url: str, content: bytes = b"") -> bool:
    return ("mpegurl" in content_type.lower()
            or urlsplit(url).path.lower().endswith((".m3u8", ".m3u"))
            or content.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"#EXTM3U"))


def _rewrite_body(content: bytes, content_type: str, base: str, *, frame: bool = False,
                  referer: str | None = None) -> bytes:
    if len(content) > config.PLAYER_MAX_TEXT_REWRITE:
        return content
    charset = _charset_from_content_type(content_type)
    text = content.decode(charset, errors="ignore")
    ct = content_type.lower()
    if frame or "text/html" in ct or "application/xhtml+xml" in ct:
        text = _rewrite_html(text, base)
    elif _is_playlist(ct, base, content):
        text = _rewrite_m3u8(text, base, referer)
    elif _is_javascript(ct, base):
        text = _rewrite_javascript(text, base)
    elif "css" in ct:
        text = _rewrite_css_urls(text, base)
    elif _looks_textual(ct):
        text = _rewrite_attrs(text, base)
    return text.encode(charset, errors="ignore")


async def _fetch(request: Request, url: str, base: str | None, *, frame: bool = False,
                 referer: str | None = None) -> Response:
    if not _valid_http_url(url):
        raise HTTPException(400, "invalid player url")
    # Выключенный вид плеера (панель) — и его запросы: старые вкладки с открытым кадром не гоняют их через сервер
    if is_disabled_player_url(url) or site_settings.player_off(url):
        raise HTTPException(403, "player disabled")
    blocked, reason = adblock.should_block(url)
    if blocked:
        log.info("player proxy blocked %s (%s)", urlparse(url).netloc, reason)
        return Response(status_code=204, headers={"Access-Control-Allow-Origin": "*"})

    body = await request.body() if request.method not in {"GET", "HEAD"} else None
    # Pages and requests with a body are read whole; media is streamed.
    stream = not frame and request.method in {"GET", "HEAD"}
    try:
        upstream = await _send(
            request.method,
            _ascii_url(url),
            stream=stream,
            content=body,
            headers=_headers_for_upstream(request, url, base, referer),
        )
    except http_public.Refused as exc:
        raise HTTPException(403, "player host not allowed") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"player proxy upstream failed: {exc}") from exc

    final_url = str(upstream.url)
    if is_disabled_player_url(final_url):
        await upstream.aclose()
        raise HTTPException(403, "player disabled")
    blocked, reason = adblock.should_block(final_url)
    if blocked:
        await upstream.aclose()
        log.info("player proxy blocked redirect %s (%s)", urlparse(final_url).netloc, reason)
        return Response(status_code=204, headers={"Access-Control-Allow-Origin": "*"})

    content_type = upstream.headers.get("content-type", "")
    if (not frame and request.method == "GET" and 200 <= upstream.status_code < 300
            and ("text/html" in content_type or "application/xhtml+xml" in content_type)):
        await upstream.aclose()
        return RedirectResponse(_path_proxy_url(final_url, final_url, document=True), status_code=307,
                                headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"})
    headers = _response_headers(upstream, html_frame=frame, origin=_site_origin(request) if frame else "")
    rewrite_base = _guess_base(final_url, base) if frame else final_url

    if request.method == "HEAD":
        upstream_length = upstream.headers.get("content-length")
        if upstream_length:
            headers["Content-Length"] = upstream_length
        await upstream.aclose()
        return Response(
            status_code=upstream.status_code,
            media_type=content_type.split(";", 1)[0] or None,
            headers=headers,
        )

    if frame or _looks_textual(content_type) or final_url.lower().endswith((".m3u8", ".m3u")):
        content = await upstream.aread()
        await upstream.aclose()
        playlist = not frame and _is_playlist(content_type, final_url, content)
        content = _strip_cvh_ads(content, content_type, final_url)
        content = _rewrite_body(content, content_type, rewrite_base, frame=frame,
                                referer=base if base and _valid_http_url(base) else None)
        headers.pop("Content-Length", None)
        if urlparse(final_url).netloc.lower().endswith("player.cdnvideohub.com"):
            headers["Cache-Control"] = "no-store"
        media_type = content_type.split(";", 1)[0] or None
        if playlist:
            # Kodik's CDN serves playlists as text/plain. Elsewhere Safari goes by the .m3u8 in the
            # URL; /player/proxy has none, so the native player refused it ("Сервер с видео не отвечает").
            for key in [k for k in headers if k.lower() == "content-type"]:
                headers.pop(key)
            media_type = _HLS_TYPE
        return Response(
            content=content,
            status_code=upstream.status_code,
            media_type=media_type,
            headers=headers,
        )

    if stream:
        upstream_length = upstream.headers.get("content-length")
        if upstream_length:
            headers["Content-Length"] = upstream_length

        async def stream_body():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            stream_body(),
            status_code=upstream.status_code,
            media_type=content_type.split(";", 1)[0] or None,
            headers=headers,
        )

    content = _strip_cvh_ads(upstream.content, content_type, final_url)
    return Response(
        content=content,
        status_code=upstream.status_code,
        media_type=content_type.split(";", 1)[0] or None,
        headers=headers,
    )


@router.get("/asoplay-shield.js", include_in_schema=False)
def asoplay_shield_js():
    return Response(
        _SHIELD_LOGGER_JS,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/kodik-skin.css", include_in_schema=False)
def kodik_skin_css():
    if not _KODIK_SKIN_CSS.is_file():
        raise HTTPException(404)
    return Response(
        _KODIK_SKIN_CSS.read_bytes(),
        media_type="text/css",
        headers={"Cache-Control": "no-store"},
    )


@router.get("/frame")
async def frame(request: Request, url: str = Query(..., min_length=8)):
    if site_settings.player_off(url):
        raise HTTPException(403, "player disabled")
    vk_ref = vk_video_ref(url)
    if vk_ref:
        return await _vk_frame(request, vk_ref)
    if is_disabled_player_url(url):
        raise HTTPException(403, "player disabled")
    if _is_cvh_iframe_url(url):
        return await _cvh_frame(request, url)
    if _is_sibnet_iframe_url(url):
        return await _sibnet_frame(request, url)
    if not _valid_http_url(url) or urlsplit(url).username or urlsplit(url).password:
        raise HTTPException(400, "invalid player url")
    refusal = proxy_url_refusal(url)
    if refusal:
        _log_refusal(urlsplit(url).hostname or "", refusal)
        raise HTTPException(403, "player host not allowed")
    return RedirectResponse(_path_proxy_url(url, url, document=True), status_code=307)


@router.get("/modules/{scheme}/{host}/{path:path}")
async def player_module(request: Request, scheme: str, host: str, path: str):
    return await _path_resource(request, scheme, host, document=False)


@router.get("/documents/{scheme}/{host}/{path:path}")
async def player_document(request: Request, scheme: str, host: str, path: str):
    return await _path_resource(request, scheme, host, document=True)


async def _path_resource(request: Request, scheme: str, host: str, *, document: bool):
    if scheme not in {"http", "https"} or not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]+)?", host):
        raise HTTPException(400, "invalid resource origin")
    # Read the raw suffix so encoded slashes/query strings keep their upstream meaning.
    kind = "documents" if document else "modules"
    prefix = f"/player/{kind}/{scheme}/{host}/".encode("ascii")
    raw_path = request.scope.get("raw_path", b"").split(b"?", 1)[0]
    if not raw_path.startswith(prefix):
        raise HTTPException(400, "invalid resource path")
    url = f"{scheme}://{host}/" + raw_path[len(prefix):].decode("ascii")
    query = request.scope.get("query_string", b"").decode("ascii")
    if query:
        url += "?" + query
    # Alloha writes the Referer into its page as the embedding domain and checks Yummy's token against it.
    referer = _YUMMY_SITE if document and _is_alloha_base(url) else None
    response = (await _fetch(request, url, url, frame=True, referer=referer) if document
                else await _fetch(request, url, url))
    response.headers["Cache-Control"] = "no-store"
    return response


def _is_kodik_cdn_probe(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc.lower() == "cloud.solodcdn.com"
        and not parsed.params and not parsed.fragment
        and re.fullmatch(r"/test/[0-9a-f]{32}:[0-9]{10}/g", parsed.path) is not None
        and (not parsed.query or re.fullmatch(r"_=[0-9]+", parsed.query) is not None)
    )


@router.get("/cdn-check")
async def kodik_cdn_check(request: Request, url: str = Query(...), base: str = Query(...)):
    if not _is_kodik_cdn_probe(url) or not _valid_http_url(base) or not _is_kodik_base(base):
        raise HTTPException(400, "invalid Kodik CDN probe")
    available, upstream_status, reason = False, None, None
    try:
        client = await _get_client()
        # This is a fixed-host diagnostic, not another general-purpose proxy.
        async with client.stream(
            "GET", url, headers=_headers_for_upstream(request, url, base),
            timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False,
        ) as upstream:
            upstream_status = upstream.status_code
            available = 200 <= upstream_status < 300
            if not available:
                reason = "upstream_http"
    except httpx.HTTPError as exc:
        reason = "network"
        log.info("Kodik CDN probe unavailable: %s", type(exc).__name__)
    return JSONResponse(
        {"available": available, "upstream_status": upstream_status, "reason": reason},
        headers={"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"},
    )


@router.api_route("/proxy", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def proxy(
    request: Request,
    url: str = Query(..., min_length=8),
    base: str | None = Query(None),
):
    if request.method == "OPTIONS":
        return Response(headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"})
    return await _fetch(request, url, base, frame=False)


@router.api_route("/cvh-api/{path:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def cvh_api(request: Request, path: str):
    if request.method == "OPTIONS":
        return Response(headers={"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*"})
    url = f"https://plapi.cdnvideohub.com/{path.lstrip('/')}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    return await _fetch(request, url, "https://player.cdnvideohub.com/", frame=False)


@router.get("/debug/check")
def debug_check(url: str = Query(..., min_length=4)):
    blocked, reason = adblock.should_block(url)
    return {"blocked": blocked, "reason": reason, "proxied": _proxy_url(url, url)}


async def _kodik_mirror(request: Request, kind: str, rest: str):
    if not rest:
        raise HTTPException(404)
    query = request.url.query
    original = f"https://kodikplayer.com/{kind}/{rest}"
    if query:
        original += f"?{query}"
    return await _fetch(request, original, original, frame=True)


@kodik_router.api_route("/season/{rest:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def kodik_season(request: Request, rest: str):
    return await _kodik_mirror(request, "season", rest)


@kodik_router.api_route("/serial/{rest:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def kodik_serial(request: Request, rest: str):
    return await _kodik_mirror(request, "serial", rest)


@kodik_router.api_route("/episode/{rest:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def kodik_episode(request: Request, rest: str):
    return await _kodik_mirror(request, "episode", rest)


@kodik_router.api_route("/seria/{rest:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def kodik_seria(request: Request, rest: str):
    return await _kodik_mirror(request, "seria", rest)


@kodik_router.api_route("/video/{rest:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def kodik_video(request: Request, rest: str):
    return await _kodik_mirror(request, "video", rest)


@kodik_router.api_route("/uv/{rest:path}", methods=["GET", "POST", "HEAD", "OPTIONS"])
async def kodik_uv(request: Request, rest: str):
    return await _kodik_mirror(request, "uv", rest)
