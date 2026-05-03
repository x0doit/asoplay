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
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlparse, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from server import adblock

log = logging.getLogger("animeviev.player_proxy")

router = APIRouter(prefix="/player")
kodik_router = APIRouter()

_client: httpx.AsyncClient | None = None
_MAX_TEXT_REWRITE = int(os.environ.get("AV_PLAYER_PROXY_MAX_TEXT_REWRITE", str(3 * 1024 * 1024)))
_PROXY_VERSION = "20260503-player18"
_KODIK_SKIN_CSS = Path(__file__).resolve().parent.parent / "assets" / "player" / "kodik-skin.css"
_SHIELD_LOGGER_JS = (
    '(function(){var q=[],c={};try{["log","warn","error","info","debug"].forEach(function(m){'
    'var f=typeof console!=="undefined"&&console[m];if(typeof f==="function")c[m]=f.bind(console);});}catch(_){}'
    'function flush(){try{var x;while((x=q.shift())){var f=c[x.m]||c.info;'
    'if(typeof f==="function")f.apply(console,x.a);}}catch(_){}}'
    'function l(m,a){try{q.push({m:m,a:a});'
    'if(q.length>40)q.splice(0,q.length-40);setTimeout(flush,0);}catch(_){}}'
    'try{Object.defineProperty(window,"__asoplayShieldLog",{value:l,configurable:true});}'
    'catch(_){window.__asoplayShieldLog=l;}setInterval(flush,250);})();\n'
)
_CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

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
    "content-type",
    "range",
    "user-agent",
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
            follow_redirects=True,
            limits=httpx.Limits(max_connections=80, max_keepalive_connections=30),
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


def _headers_for_upstream(request: Request, url: str, base: str | None) -> dict[str, str]:
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
    else:
        headers.setdefault("User-Agent", _CHROME_UA)
    referer = base if base and _valid_http_url(base) else _origin(url)
    referer = _ascii_url(referer)
    headers["Referer"] = referer
    headers["Origin"] = _origin(referer).rstrip("/")
    headers["Accept-Encoding"] = "identity"
    return headers


def _response_headers(upstream: httpx.Response, *, html_frame: bool = False) -> dict[str, str]:
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
        headers["Content-Security-Policy"] = (
            "default-src 'self' data: blob:; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; "
            "style-src 'self' 'unsafe-inline' data:; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "media-src 'self' data: blob: https://*.okcdn.ru https://*.mycdn.me https://*.ok.ru; "
            "connect-src 'self' data: blob: "
            "https://plapi.cdnvideohub.com https://api.ok.ru https://api.mycdn.me "
            "https://api.okcdn.ru https://videotestapi.ok.ru https://apitest.ok.ru; "
            "frame-src 'self' data: blob:; "
            "worker-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'; form-action 'none'"
        )
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


def _rewrite_attrs(text: str, base: str) -> str:
    def repl_attr(match: re.Match[str]) -> str:
        attr, quote_char, value = match.group(1), match.group(2), match.group(3)
        return f"{attr}{quote_char}{html.escape(_proxy_url(value, base), quote=True)}{quote_char}"

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


def _rewrite_m3u8(text: str, base: str) -> str:
    def repl_uri(match: re.Match[str]) -> str:
        return f'{match.group(1)}"{_proxy_url(match.group(2), base)}"'

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
        proxied = _proxy_url(stripped, base) if stripped else stripped
        out.append(proxied)
    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _rewrite_javascript(text: str, base: str) -> str:
    host = urlparse(base).netloc.lower()
    if host.endswith("player.cdnvideohub.com") and "plapi.cdnvideohub.com" in text:
        text = text.replace("https://plapi.cdnvideohub.com", "/player/cvh-api")
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
    episode_items = [item for item in items if int(item.get("episode") or 0) == episode] or items
    if voice_code:
        wanted = voice_code.strip().lower()
        for item in episode_items:
            if str(item.get("voiceStudio") or "").strip().lower() == wanted:
                return item
    return episode_items[0] if episode_items else None


def _pick_cvh_video_source(data: dict) -> str:
    sources = data.get("sources") if isinstance(data, dict) else {}
    if not isinstance(sources, dict):
        return ""
    for key in (
        "mpegFullHdUrl",
        "mpegHighUrl",
        "mpegMediumUrl",
        "mpegLowUrl",
        "mpegLowestUrl",
        "mpegTinyUrl",
        "hlsUrl",
        "dashUrl",
    ):
        value = sources.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    return ""


def _cvh_error_frame(message: str) -> Response:
    safe = html.escape(message, quote=True)
    body = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{{margin:0;width:100%;height:100%;background:#050507;color:#f4f4f5;font:600 16px Arial,sans-serif}}
body{{display:grid;place-items:center}}.box{{text-align:center;opacity:.78}}
</style></head><body><div class="box">{safe}</div></body></html>"""
    return Response(body, media_type="text/html", headers={"Cache-Control": "no-store"})


def _clean_video_frame(
    source_url: str,
    base_url: str,
    poster_url: str = "",
    *,
    element_id: str = "av-clean-video",
) -> Response:
    proxied_src = html.escape(_proxy_url(source_url, base_url), quote=True)
    safe_id = html.escape(element_id, quote=True)
    proxied_poster = html.escape(_proxy_url(poster_url, base_url), quote=True) if poster_url else ""
    poster_attr = f' poster="{proxied_poster}"' if proxied_poster else ""
    body = f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
html,body{{margin:0;width:100%;height:100%;background:#050507;overflow:hidden}}
body{{display:flex;position:relative}}
video{{width:100%;height:100%;background:#050507;object-fit:contain}}
#av-start{{position:absolute;left:50%;top:50%;width:72px;height:72px;margin:-36px 0 0 -36px;border:0;border-radius:50%;
background:rgba(255,91,103,.94);box-shadow:0 14px 40px rgba(0,0,0,.35);cursor:pointer;display:grid;place-items:center}}
#av-start span{{display:block;width:0;height:0;border-top:15px solid transparent;border-bottom:15px solid transparent;
border-left:22px solid #fff;margin-left:6px}}
#av-start.hidden{{display:none}}
</style></head><body>
<video id="{safe_id}" src="{proxied_src}"{poster_attr} controls playsinline preload="metadata"></video>
<button id="av-start" type="button" aria-label="Play"><span></span></button>
<script>
(() => {{
  const video = document.getElementById("{safe_id}");
  const start = document.getElementById("av-start");
  const send = (key, value) => {{
    try {{ parent.postMessage({{ key, value }}, "*"); }} catch (_) {{}}
  }};
  const sendTime = () => {{
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    const time = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    if (duration > 0) send("kodik_player_duration_update", duration);
    if (time > 0) send("kodik_player_time_update", time);
  }};
  const showStart = () => {{ if (video.paused && !video.ended) start.classList.remove("hidden"); }};
  const hideStart = () => start.classList.add("hidden");
  const tryPlay = () => {{
    hideStart();
    const p = video.play();
    if (p && typeof p.catch === "function") p.catch(showStart);
  }};
  start.addEventListener("click", tryPlay);
  video.addEventListener("loadedmetadata", sendTime);
  video.addEventListener("canplay", () => {{ sendTime(); if (video.paused) showStart(); }});
  video.addEventListener("timeupdate", sendTime);
  video.addEventListener("playing", hideStart);
  video.addEventListener("play", () => send("kodik_player_play"));
  video.addEventListener("pause", () => {{ send("kodik_player_pause"); showStart(); }});
  video.addEventListener("ended", () => send("kodik_player_video_ended"));
  video.addEventListener("error", showStart);
  setInterval(sendTime, 1000);
  try {{ video.load(); }} catch (_) {{}}
  setTimeout(() => {{ if (video.paused) showStart(); }}, 500);
}})();
</script></body></html>"""
    headers = {
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'self' data: blob:; "
            "script-src 'unsafe-inline'; style-src 'unsafe-inline'; "
            "img-src 'self' data: blob:; media-src 'self' data: blob:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'"
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

    client = await _get_client()
    safe_iframe_url = _ascii_url(iframe_url)
    playlist_url = f"https://plapi.cdnvideohub.com/api/v1/player/sv/playlist?pub=745&aggr=mali&id={quote(anime_id)}"
    try:
        playlist_resp = await client.get(
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
        video_resp = await client.get(
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

    source_url = _pick_cvh_video_source(video_data)
    if not source_url:
        return _cvh_error_frame("Видео не найдено")

    poster_url = video_data.get("thumbUrl") if isinstance(video_data, dict) else ""
    return _clean_video_frame(source_url, video_url, poster_url, element_id="av-cvh-video")


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
    client = await _get_client()
    try:
        frame_resp = await client.get(
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


def _is_kodik_base(base: str) -> bool:
    try:
        host = urlparse(base).netloc.lower()
    except Exception:
        return False
    return host == "kodikplayer.com" or host.endswith(".kodikplayer.com")


def _bridge_script(base: str) -> str:
    base_js = json.dumps(base)
    is_kodik_js = json.dumps(_is_kodik_base(base))
    kodik_skin = (
        f'<link rel="stylesheet" href="/player/kodik-skin.css?v={_PROXY_VERSION}">\n'
        if _is_kodik_base(base) else ""
    )
    return f"""
{kodik_skin}
<script src="/player/asoplay-shield.js"></script>
<script>
(() => {{
  "use strict";
  const BASE = {base_js};
  const IS_KODIK = {is_kodik_js};
  const UPSTREAM_ORIGIN = new URL(BASE).origin;
  const LOCAL_ORIGIN = location.origin;
  const AD_RE = /(casino|bookmaker|betting|vulkan|1xbet|pin[-\\s]?up|av\\s*casino|werbung|реклам|казино|ставк|букмекер|advert|preroll|vast|vpaid|popunder)/i;
  const ATTRS = new Set(["src", "href", "poster", "data", "action"]);
  const AD_EXTRA_RE = /(buzzoola|adfox|doubleclick|googlesyndication|googletag|\\/ad(?:[/?#]|$)|\\/ads(?:[/?#]|$))/i;
  const shieldStyleA = "background:#ff5c66;color:#fff;padding:3px 7px;border-radius:5px;font-weight:800";
  const shieldStyleB = "color:#dc2626;font-weight:800";
  const pauseStyleA = "background:#2563eb;color:#fff;padding:3px 7px;border-radius:5px;font-weight:800";
  const pauseStyleB = "color:#2563eb;font-weight:800";
  const extensionStyleA = "background:#f59e0b;color:#111827;padding:3px 7px;border-radius:5px;font-weight:900";
  const extensionStyleB = "color:#b45309;font-weight:800";
  const nativeConsole = {{
    log: console.log ? console.log.bind(console) : () => {{}},
    warn: console.warn ? console.warn.bind(console) : () => {{}},
    error: console.error ? console.error.bind(console) : () => {{}},
    info: console.info ? console.info.bind(console) : () => {{}},
    debug: console.debug ? console.debug.bind(console) : () => {{}}
  }};
  const shieldConsole = window.__asoplayShieldLog || ((method, args) => {{
    const fn = nativeConsole[method] || nativeConsole.info;
    return fn(...args);
  }});
  const shieldInfo = (...args) => shieldConsole("info", args);
  const shieldState = {{ windowStart: 0, hits: 0, mutedUntil: 0 }};
  const shieldLog = () => {{
    try {{
      const now = Date.now();
      if (now < shieldState.mutedUntil) return;
      if (!shieldState.windowStart || now - shieldState.windowStart > 5000) {{
        shieldState.windowStart = now;
        shieldState.hits = 0;
      }}
      shieldState.hits += 1;
      if (shieldState.hits >= 3) {{
        shieldInfo(
          "%cAsoPlay Shield:%c У Kodik настоящий понос, но у Даниэля Сэмпая есть волшебная \\"Антиспам\\" палочка, которая блокирует его запросы. ^-^",
          shieldStyleA,
          shieldStyleB
        );
        shieldState.mutedUntil = now + 60000;
        shieldState.windowStart = now;
        shieldState.hits = 0;
        return;
      }}
      if (shieldState.hits === 1) {{
        shieldInfo(
          "%cAsoPlay Shield:%c Kodik пытается засрать твое видео рекламой, но обосрался. Сэмпай Даниэль защитил твои глазки от мусора.",
          shieldStyleA,
          shieldStyleB
        );
      }}
    }} catch (_) {{}}
  }};
  const storageShieldState = {{ windowStart: 0, hits: 0, mutedUntil: 0 }};
  const storageShieldLog = () => {{
    try {{
      const now = Date.now();
      if (now < storageShieldState.mutedUntil) return;
      if (!storageShieldState.windowStart || now - storageShieldState.windowStart > 5000) {{
        storageShieldState.windowStart = now;
        storageShieldState.hits = 0;
      }}
      storageShieldState.hits += 1;
      if (storageShieldState.hits >= 3) {{
        shieldInfo(
          "%cAsoPlay Shield:%c Kodik устроил storage-истерику, но антиспам Сэмпая Даниэля отправил ее в угол подумать. ^-^",
          shieldStyleA,
          shieldStyleB
        );
        storageShieldState.mutedUntil = now + 60000;
        storageShieldState.windowStart = now;
        storageShieldState.hits = 0;
        return;
      }}
      if (storageShieldState.hits === 1) {{
        shieldInfo(
          "%cAsoPlay Shield:%c Kodik полез в localStorage за мутной фигней, но Сэмпай Даниэль выдал ему безопасную пустышку.",
          shieldStyleA,
          shieldStyleB
        );
      }}
    }} catch (_) {{}}
  }};
  const pauseShieldState = {{ mutedUntil: 0 }};
  const pauseShieldLog = () => {{
    try {{
      const now = Date.now();
      if (now < pauseShieldState.mutedUntil) return;
      shieldInfo(
        "%cAsoPlay Shield:%c Пауза поставлена. Серия терпеливо ждёт, пока ты вернёшься к просмотру.",
        pauseStyleA,
        pauseStyleB
      );
      pauseShieldState.mutedUntil = now + 30000;
    }} catch (_) {{}}
  }};
  const extensionShieldState = {{ shown: false }};
  const extensionShieldLog = () => {{
    try {{
      if (extensionShieldState.shown) return;
      extensionShieldState.shown = true;
      shieldInfo(
        "%cAsoPlay Shield:%c Какие-то расширения пытались прочитать cookies sandbox-iframe, но Даниэль Сэмпай послал их НаАХОООЙ. :З",
        extensionStyleA,
        extensionStyleB
      );
    }} catch (_) {{}}
  }};
  queueMicrotask(() => {{
    let blocked = false;
    try {{
      void document.cookie;
    }} catch (err) {{
      const text = String((err && err.name) || "") + " " + String((err && err.message) || "");
      blocked = /SecurityError|sandbox|cookie/i.test(text);
    }}
    if (blocked || window.top !== window) extensionShieldLog();
  }});
  const patchConsoleMethod = (name) => {{
    const native = nativeConsole[name];
    try {{
      console[name] = (...args) => {{
        const text = args.map(x => {{
          try {{
            if (typeof x === "string") return x;
            if (x && typeof x.message === "string") return x.message;
            return JSON.stringify(x);
          }} catch (_) {{
            return String(x);
          }}
        }}).join(" ");
        if (/AUTO\\s+PAUSED/i.test(text)) {{
          pauseShieldLog();
          return;
        }}
        if (/Unable to send stat|VAST ended|noad|original_manifest/i.test(text)) {{
          shieldLog();
          return;
        }}
        return shieldConsole(name, args);
      }};
    }} catch (_) {{}}
  }};
  ["log", "warn", "error", "info", "debug"].forEach(patchConsoleMethod);
  const makeStorage = () => {{
    const data = new Map();
    return {{
      get length() {{ return data.size; }},
      clear() {{ data.clear(); }},
      getItem(key) {{
        key = String(key);
        return data.has(key) ? data.get(key) : null;
      }},
      key(index) {{
        return Array.from(data.keys())[Number(index)] ?? null;
      }},
      removeItem(key) {{ data.delete(String(key)); }},
      setItem(key, value) {{ data.set(String(key), String(value)); }}
    }};
  }};
  const ensureStorage = (name) => {{
    try {{
      const storage = window[name];
      const probe = "__av_probe__";
      storage.setItem(probe, "1");
      storage.removeItem(probe);
    }} catch (_) {{
      try {{
        Object.defineProperty(window, name, {{
          configurable: true,
          enumerable: true,
          value: makeStorage()
        }});
      }} catch (_) {{}}
    }}
  }};
  ensureStorage("localStorage");
  ensureStorage("sessionStorage");
  const passthrough = (value) => {{
    const s = String(value || "").trim();
    return !s || s[0] === "#" || /^(about|blob|data|javascript|mailto|tel):/i.test(s);
  }};
  const isPlayerInternalPath = (value) =>
    /^\\/player\\/(?:frame|proxy|cvh-api)(?:[/?#]|$)|^\\/player\\/(?:asoplay-shield\\.js|kodik-skin\\.css)(?:[?#]|$)/i
      .test(String(value || "").trim());
  const isLocalProxyPath = (value) => isPlayerInternalPath(value);
  const isLocalProxyUrl = (value) => {{
    try {{
      const u = new URL(String(value || ""), LOCAL_ORIGIN);
      return u.origin === LOCAL_ORIGIN && isLocalProxyPath(u.pathname);
    }} catch (_) {{
      return false;
    }}
  }};
  const unwrapProxyTarget = (value) => {{
    const raw = String(value || "");
    try {{
      const u = new URL(raw, LOCAL_ORIGIN);
      if (u.origin === LOCAL_ORIGIN && isLocalProxyPath(u.pathname)) {{
        return u.searchParams.get("url") || raw;
      }}
    }} catch (_) {{}}
    return raw;
  }};
  const isAdTarget = (value) => {{
    const raw = String(value || "");
    const unwrapped = unwrapProxyTarget(raw);
    return AD_RE.test(raw) || AD_EXTRA_RE.test(raw) || AD_RE.test(unwrapped) || AD_EXTRA_RE.test(unwrapped);
  }};
  const absolute = (value) => {{
    if (isLocalProxyPath(value)) return LOCAL_ORIGIN + String(value || "").trim();
    try {{ return new URL(String(value || ""), BASE).href; }} catch (_) {{ return String(value || ""); }}
  }};
  const normalizeUpstream = (url) => {{
    try {{
      const u = new URL(url);
      if (u.origin === LOCAL_ORIGIN && !isPlayerInternalPath(u.pathname)) {{
        return UPSTREAM_ORIGIN + u.pathname + u.search + u.hash;
      }}
      return u.href;
    }} catch (_) {{
      return url;
    }}
  }};
  const proxied = (value) => {{
    if (isAdTarget(value)) shieldLog();
    if (passthrough(value)) return value;
    if (isLocalProxyUrl(value)) return String(value || "");
    const u = normalizeUpstream(absolute(value));
    if (isLocalProxyUrl(u)) {{
      if (isAdTarget(u)) shieldLog();
      return u;
    }}
    if (isAdTarget(u)) shieldLog();
    return "/player/proxy?url=" + encodeURIComponent(u) + "&base=" + encodeURIComponent(BASE) + "&pv={_PROXY_VERSION}";
  }};
  const rewriteHtml = (html) => String(html || "").replace(
    /\\b(src|href|poster|data|action)\\s*=\\s*(['"])(.*?)\\2/gi,
    (_, attr, q, val) => attr + "=" + q + proxied(val).replace(/"/g, "&quot;") + q
  );
  const sanitizeMessage = (message) => message;

  window.open = () => null;
  try {{ Object.defineProperty(window, "opener", {{ value: null, configurable: false }}); }} catch (_) {{}}
  const normalizeTargetOrigin = (origin) => {{
    if (!origin || origin === "*") return origin;
    try {{
      const wanted = new URL(String(origin)).origin;
      if (wanted !== LOCAL_ORIGIN) return "*";
    }} catch (_) {{}}
    return origin;
  }};
  const patchPostMessageTarget = (win) => {{
    try {{
      if (!win || typeof win.postMessage !== "function" || win.postMessage.__avProxyPatched) return;
      const native = win.postMessage.bind(win);
      const patched = (message, targetOrigin, transfer) => {{
        message = sanitizeMessage(message);
        targetOrigin = normalizeTargetOrigin(targetOrigin);
        if (typeof transfer !== "undefined") return native(message, targetOrigin, transfer);
        return native(message, targetOrigin);
      }};
      Object.defineProperty(patched, "__avProxyPatched", {{ value: true }});
      win.postMessage = patched;
    }} catch (_) {{}}
  }};
  patchPostMessageTarget(window.parent);
  patchPostMessageTarget(window.top);
  if (window.HTMLIFrameElement && HTMLIFrameElement.prototype) {{
    const contentWindowDesc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, "contentWindow");
    const proxiedWindows = new WeakMap();
    if (contentWindowDesc && contentWindowDesc.get) {{
      try {{
        Object.defineProperty(HTMLIFrameElement.prototype, "contentWindow", {{
          configurable: true,
          enumerable: contentWindowDesc.enumerable,
          get() {{
            const win = contentWindowDesc.get.call(this);
            if (!win) return win;
            if (proxiedWindows.has(win)) return proxiedWindows.get(win);
            const proxy = new Proxy(win, {{
              get(target, prop, receiver) {{
                if (prop === "postMessage") {{
                  return (message, targetOrigin, transfer) => {{
                    message = sanitizeMessage(message);
                    targetOrigin = normalizeTargetOrigin(targetOrigin);
                    if (typeof transfer !== "undefined") return target.postMessage(message, targetOrigin, transfer);
                    return target.postMessage(message, targetOrigin);
                  }};
                }}
                if (prop === "fetch" && typeof target.fetch === "function") {{
                  return (input, init) => {{
                    const url = input && input.url ? input.url : input;
                    try {{
                      if (input && input.url && typeof target.Request !== "undefined") {{
                        return target.fetch(new target.Request(proxied(url), input), init);
                      }}
                    }} catch (_) {{}}
                    return target.fetch(proxied(url), init);
                  }};
                }}
                let value;
                try {{
                  value = Reflect.get(target, prop, target);
                }} catch (_) {{
                  storageShieldLog();
                  return undefined;
                }}
                return typeof value === "function" ? value.bind(target) : value;
              }}
            }});
            proxiedWindows.set(win, proxy);
            return proxy;
          }}
        }});
      }} catch (_) {{}}
    }}
  }}
  if (window.Window && Window.prototype.postMessage) {{
    const nativePostMessage = Window.prototype.postMessage;
    Window.prototype.postMessage = function(message, targetOrigin, transfer) {{
      message = sanitizeMessage(message);
      targetOrigin = normalizeTargetOrigin(targetOrigin);
      if (arguments.length >= 3) return nativePostMessage.call(this, message, targetOrigin, transfer);
      return nativePostMessage.call(this, message, targetOrigin);
    }};
  }}
  const fallbackMessageOrigin = () => {{
    try {{
      return document.referrer ? new URL(document.referrer).origin : LOCAL_ORIGIN;
    }} catch (_) {{
      return LOCAL_ORIGIN;
    }}
  }};
  const wrapMessageEvent = (event) => {{
    if (!event || event.type !== "message" || event.origin !== "null") return event;
    return new Proxy(event, {{
      get(target, prop, receiver) {{
        if (prop === "origin") return fallbackMessageOrigin();
        let value;
        try {{
          value = Reflect.get(target, prop, target);
        }} catch (_) {{
          storageShieldLog();
          return undefined;
        }}
        return typeof value === "function" ? value.bind(target) : value;
      }}
    }});
  }};
  const nativeAddEventListener = EventTarget.prototype.addEventListener;
  const nativeRemoveEventListener = EventTarget.prototype.removeEventListener;
  const messageListeners = new WeakMap();
  const wrapMessageListener = (listener) => {{
    if (!listener || (typeof listener !== "function" && typeof listener.handleEvent !== "function")) return listener;
    if (messageListeners.has(listener)) return messageListeners.get(listener);
    const wrapped = typeof listener === "function"
      ? function(event) {{ return listener.call(this, wrapMessageEvent(event)); }}
      : {{
          handleEvent(event) {{ return listener.handleEvent(wrapMessageEvent(event)); }}
        }};
    messageListeners.set(listener, wrapped);
    return wrapped;
  }};
  EventTarget.prototype.addEventListener = function(type, listener, options) {{
    if (String(type).toLowerCase() === "message" && listener) {{
      return nativeAddEventListener.call(this, type, wrapMessageListener(listener), options);
    }}
    return nativeAddEventListener.call(this, type, listener, options);
  }};
  EventTarget.prototype.removeEventListener = function(type, listener, options) {{
    if (String(type).toLowerCase() === "message" && listener) {{
      return nativeRemoveEventListener.call(this, type, messageListeners.get(listener) || listener, options);
    }}
    return nativeRemoveEventListener.call(this, type, listener, options);
  }};
  try {{
    let assignedOnMessage = null;
    Object.defineProperty(window, "onmessage", {{
      configurable: true,
      enumerable: true,
      get() {{ return assignedOnMessage; }},
      set(listener) {{
        if (assignedOnMessage) nativeRemoveEventListener.call(window, "message", wrapMessageListener(assignedOnMessage));
        assignedOnMessage = listener;
        if (listener && (typeof listener === "function" || typeof listener.handleEvent === "function")) {{
          nativeAddEventListener.call(window, "message", wrapMessageListener(listener));
        }}
      }}
    }});
  }} catch (_) {{}}
  addEventListener("click", (event) => {{
    const a = event.target && event.target.closest ? event.target.closest("a[target], a[href]") : null;
    if (!a) return;
    if (isAdTarget(a.href || "") || a.target === "_blank") {{
      if (isAdTarget(a.href || "")) shieldLog();
      event.preventDefault();
      event.stopImmediatePropagation();
    }}
  }}, true);

  const nativeFetch = window.fetch ? window.fetch.bind(window) : null;
  if (nativeFetch) {{
    window.fetch = (input, init) => {{
      const url = input && input.url ? input.url : input;
      if (typeof Request !== "undefined" && input instanceof Request) {{
        return nativeFetch(new Request(proxied(url), input), init);
      }}
      return nativeFetch(proxied(url), init);
    }};
  }}
  const XHR = window.XMLHttpRequest;
  if (XHR) {{
    const open = XHR.prototype.open;
    XHR.prototype.open = function(method, url, ...rest) {{
      return open.call(this, method, proxied(url), ...rest);
    }};
  }}
  if (navigator.sendBeacon) {{
    navigator.sendBeacon = () => false;
  }}
  if (window.WebSocket) {{
    const NativeWS = window.WebSocket;
    window.WebSocket = function(url, protocols) {{
      if (isAdTarget(url)) {{
        shieldLog();
        throw new Error("Blocked ad websocket");
      }}
      return new NativeWS(url, protocols);
    }};
  }}
  if (window.Worker) {{
    const NativeWorker = window.Worker;
    window.Worker = function(url, options) {{
      return new NativeWorker(proxied(url), options);
    }};
    window.Worker.prototype = NativeWorker.prototype;
  }}
  if (window.SharedWorker) {{
    const NativeSharedWorker = window.SharedWorker;
    window.SharedWorker = function(url, options) {{
      return new NativeSharedWorker(proxied(url), options);
    }};
    window.SharedWorker.prototype = NativeSharedWorker.prototype;
  }}

  const setAttr = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function(name, value) {{
    if (IS_KODIK && String(name).toLowerCase() === "src" && String(this.tagName || "").toUpperCase() === "VIDEO") {{
      try {{
        this.crossOrigin = "anonymous";
        setAttr.call(this, "crossorigin", "anonymous");
      }} catch (_) {{}}
    }}
    if (ATTRS.has(String(name).toLowerCase())) value = proxied(value);
    return setAttr.call(this, name, value);
  }};
  const patchProp = (proto, prop) => {{
    const desc = Object.getOwnPropertyDescriptor(proto, prop);
    if (!desc || !desc.set) return;
    Object.defineProperty(proto, prop, {{
      configurable: true,
      enumerable: desc.enumerable,
      get: desc.get,
      set(value) {{
        if (IS_KODIK && prop === "src" && String(this.tagName || "").toUpperCase() === "VIDEO") {{
          try {{
            this.crossOrigin = "anonymous";
            setAttr.call(this, "crossorigin", "anonymous");
          }} catch (_) {{}}
        }}
        return desc.set.call(this, proxied(value));
      }}
    }});
  }};
  const propTargets = [
    [window.HTMLScriptElement && HTMLScriptElement.prototype, "src"],
    [window.HTMLIFrameElement && HTMLIFrameElement.prototype, "src"],
    [window.HTMLImageElement && HTMLImageElement.prototype, "src"],
    [window.HTMLMediaElement && HTMLMediaElement.prototype, "src"],
    [window.HTMLSourceElement && HTMLSourceElement.prototype, "src"],
    [window.HTMLTrackElement && HTMLTrackElement.prototype, "src"],
    [window.HTMLLinkElement && HTMLLinkElement.prototype, "href"],
    [window.HTMLAnchorElement && HTMLAnchorElement.prototype, "href"],
    [window.HTMLVideoElement && HTMLVideoElement.prototype, "poster"],
  ];
  propTargets.forEach(([proto, prop]) => proto && patchProp(proto, prop));

  const patchHtmlProp = (proto, prop) => {{
    const desc = Object.getOwnPropertyDescriptor(proto, prop);
    if (!desc || !desc.set) return;
    Object.defineProperty(proto, prop, {{
      configurable: true,
      enumerable: desc.enumerable,
      get: desc.get,
      set(value) {{ return desc.set.call(this, rewriteHtml(value)); }}
    }});
  }};
  patchHtmlProp(Element.prototype, "innerHTML");
  patchHtmlProp(Element.prototype, "outerHTML");
  const nativeInsertAdjacentHTML = Element.prototype.insertAdjacentHTML;
  if (nativeInsertAdjacentHTML) {{
    Element.prototype.insertAdjacentHTML = function(position, html) {{
      return nativeInsertAdjacentHTML.call(this, position, rewriteHtml(html));
    }};
  }}
  const nativeWrite = document.write.bind(document);
  const nativeWriteln = document.writeln.bind(document);
  document.write = (...items) => nativeWrite(...items.map(rewriteHtml));
  document.writeln = (...items) => nativeWriteln(...items.map(rewriteHtml));

  const prepareScreenshotVideo = (video) => {{
    if (!IS_KODIK || !video || String(video.tagName || "").toUpperCase() !== "VIDEO") return;
    try {{
      video.crossOrigin = "anonymous";
      video.setAttribute("crossorigin", "anonymous");
    }} catch (_) {{}}
  }};
  const postScreenshot = (payload) => {{
    try {{ window.parent.postMessage(payload, "*"); }} catch (_) {{}}
  }};
  const postScreenshotError = (message) => {{
    postScreenshot({{ type: "asoplay:screenshot-error", message: String(message || "Скриншот недоступен") }});
  }};
  const setScreenshotBusy = (busy) => {{
    try {{
      document.querySelectorAll(".asoplay-screenshot-button").forEach((button) => {{
        button.classList.toggle("asoplay-screenshot-busy", !!busy);
      }});
    }} catch (_) {{}}
  }};
  const captureScreenshot = () => {{
    if (!IS_KODIK) return;
    const video = Array.from(document.querySelectorAll("video")).find((item) =>
      item && item.videoWidth > 0 && item.videoHeight > 0
    );
    if (!video) {{
      postScreenshotError("Кадр еще не готов. Запустите видео и попробуйте снова.");
      return;
    }}
    prepareScreenshotVideo(video);
    try {{
      setScreenshotBusy(true);
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("canvas unavailable");
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const payload = {{
        type: "asoplay:screenshot",
        width: canvas.width,
        height: canvas.height,
        time: Number(video.currentTime) || 0,
        title: document.title || ""
      }};
      const done = (dataUrl) => {{
        setScreenshotBusy(false);
        payload.dataUrl = dataUrl;
        postScreenshot(payload);
      }};
      if (canvas.toBlob && window.FileReader) {{
        canvas.toBlob((blob) => {{
          if (!blob) {{
            setScreenshotBusy(false);
            postScreenshotError("Не удалось сохранить кадр.");
            return;
          }}
          const reader = new FileReader();
          reader.onload = () => done(String(reader.result || ""));
          reader.onerror = () => {{
            setScreenshotBusy(false);
            postScreenshotError("Не удалось прочитать кадр.");
          }};
          reader.readAsDataURL(blob);
        }}, "image/png");
      }} else {{
        done(canvas.toDataURL("image/png"));
      }}
    }} catch (err) {{
      setScreenshotBusy(false);
      const name = err && err.name ? String(err.name) : "";
      postScreenshotError(name === "SecurityError"
        ? "Источник не разрешил снять кадр из видео."
        : "Не удалось сделать скриншот.");
    }}
  }};
  const screenshotIcon =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<path class="fill" d="M9.2 4.5h5.6l1.2 2h3A2.5 2.5 0 0 1 21.5 9v8A2.5 2.5 0 0 1 19 19.5H5A2.5 2.5 0 0 1 2.5 17V9A2.5 2.5 0 0 1 5 6.5h3l1.2-2Zm2.8 4A4.3 4.3 0 1 0 12 17a4.3 4.3 0 0 0 0-8.5Zm0 2A2.3 2.3 0 1 1 12 15a2.3 2.3 0 0 1 0-4.5Z"/></svg>';
  const markScreenshotUi = (root) => {{
    if (!IS_KODIK) return;
    const scope = root && root.querySelectorAll ? root : document;
    const buttons = [];
    try {{
      if (scope.matches && (scope.matches(".share_button") || scope.matches(".fp-share_button"))) buttons.push(scope);
      buttons.push(...scope.querySelectorAll(".share_button, .fp-share_button"));
    }} catch (_) {{}}
    buttons.forEach((button) => {{
      if (!button || button.__asoplayScreenshotButton) return;
      button.__asoplayScreenshotButton = true;
      button.classList.add("asoplay-screenshot-button");
      button.setAttribute("title", "Сделать скриншот");
      button.setAttribute("aria-label", "Сделать скриншот");
      try {{ button.innerHTML = screenshotIcon; }} catch (_) {{}}
    }});
    try {{
      scope.querySelectorAll(".get_code_heading").forEach((item) => {{
        item.textContent = "Сделать скриншот";
      }});
      scope.querySelectorAll("video").forEach(prepareScreenshotVideo);
    }} catch (_) {{}}
  }};
  if (IS_KODIK) {{
    document.addEventListener("click", (event) => {{
      const button = event.target && event.target.closest
        ? event.target.closest(".asoplay-screenshot-button, .share_button, .fp-share_button")
        : null;
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      captureScreenshot();
    }}, true);
  }}

  const clean = (root) => {{
    if (!root || !root.querySelectorAll) return;
    const selector = "iframe, script, img, video, source, track, a, link, embed, object";
    const nodes = root.matches && root.matches(selector)
      ? [root, ...root.querySelectorAll(selector)]
      : [...root.querySelectorAll(selector)];
    nodes.forEach((el) => {{
      for (const attr of ATTRS) {{
        if (el.hasAttribute && el.hasAttribute(attr)) el.setAttribute(attr, el.getAttribute(attr));
      }}
      prepareScreenshotVideo(el);
      const marker = [el.id, el.className, el.title, el.alt, el.textContent].join(" ");
      if (AD_RE.test(marker) && !/video|player/i.test(marker)) el.remove();
    }});
  }};
  new MutationObserver((items) => {{
    for (const item of items) for (const node of item.addedNodes) {{
      if (node.nodeType !== 1) continue;
      clean(node);
      markScreenshotUi(node);
    }}
  }}).observe(document.documentElement, {{ childList: true, subtree: true }});
  clean(document);
  markScreenshotUi(document);
}})();
</script>
"""


def _rewrite_html(text: str, base: str) -> str:
    chunks = re.split(r"(<script\b[^>]*>.*?</script\s*>)", text, flags=re.I | re.S)
    rewritten: list[str] = []
    for chunk in chunks:
        if re.match(r"<script\b", chunk, flags=re.I):
            tag_match = re.match(r"(<script\b[^>]*>)(.*?)(</script\s*>)", chunk, flags=re.I | re.S)
            if tag_match:
                rewritten.append(_rewrite_attrs(tag_match.group(1), base) + tag_match.group(2) + tag_match.group(3))
            else:
                rewritten.append(_rewrite_attrs(chunk, base))
        else:
            rewritten.append(_rewrite_attrs(chunk, base))
    text = "".join(rewritten)
    bridge = _bridge_script(base)
    if re.search(r"<head[^>]*>", text, re.I):
        return re.sub(r"(<head[^>]*>)", lambda m: m.group(1) + bridge, text, count=1, flags=re.I)
    return bridge + text


def _rewrite_body(content: bytes, content_type: str, base: str, *, frame: bool = False) -> bytes:
    if len(content) > _MAX_TEXT_REWRITE:
        return content
    charset = _charset_from_content_type(content_type)
    text = content.decode(charset, errors="ignore")
    ct = content_type.lower()
    if frame or "text/html" in ct or "application/xhtml+xml" in ct:
        text = _rewrite_html(text, base)
    elif "mpegurl" in ct or base.lower().endswith((".m3u8", ".m3u")):
        text = _rewrite_m3u8(text, base)
    elif _is_javascript(ct, base):
        text = _rewrite_javascript(text, base)
    elif "css" in ct:
        text = _rewrite_css_urls(text, base)
    elif _looks_textual(ct):
        text = _rewrite_attrs(text, base)
    return text.encode(charset, errors="ignore")


async def _fetch(request: Request, url: str, base: str | None, *, frame: bool = False) -> Response:
    if not _valid_http_url(url):
        raise HTTPException(400, "invalid player url")
    blocked, reason = adblock.should_block(url)
    if blocked:
        log.info("player proxy blocked %s (%s)", urlparse(url).netloc, reason)
        return Response(status_code=204, headers={"Access-Control-Allow-Origin": "*"})

    client = await _get_client()
    body = await request.body() if request.method not in {"GET", "HEAD"} else None
    stream_cm = None
    try:
        if frame or request.method not in {"GET", "HEAD"}:
            upstream = await client.request(
                request.method,
                _ascii_url(url),
                content=body,
                headers=_headers_for_upstream(request, url, base),
            )
        else:
            stream_cm = client.stream(
                request.method,
                _ascii_url(url),
                headers=_headers_for_upstream(request, url, base),
            )
            upstream = await stream_cm.__aenter__()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"player proxy upstream failed: {exc}") from exc

    final_url = str(upstream.url)
    blocked, reason = adblock.should_block(final_url)
    if blocked:
        if stream_cm is not None:
            await stream_cm.__aexit__(None, None, None)
        log.info("player proxy blocked redirect %s (%s)", urlparse(final_url).netloc, reason)
        return Response(status_code=204, headers={"Access-Control-Allow-Origin": "*"})

    content_type = upstream.headers.get("content-type", "")
    headers = _response_headers(upstream, html_frame=frame)
    rewrite_base = _guess_base(final_url, base) if frame else final_url

    if request.method == "HEAD":
        upstream_length = upstream.headers.get("content-length")
        if upstream_length:
            headers["Content-Length"] = upstream_length
        if stream_cm is not None:
            await stream_cm.__aexit__(None, None, None)
        return Response(
            status_code=upstream.status_code,
            media_type=content_type.split(";", 1)[0] or None,
            headers=headers,
        )

    if frame or _looks_textual(content_type) or final_url.lower().endswith((".m3u8", ".m3u")):
        content = await upstream.aread() if stream_cm is not None else upstream.content
        if stream_cm is not None:
            await stream_cm.__aexit__(None, None, None)
        content = _strip_cvh_ads(content, content_type, final_url)
        content = _rewrite_body(content, content_type, rewrite_base, frame=frame)
        headers.pop("Content-Length", None)
        if urlparse(final_url).netloc.lower().endswith("player.cdnvideohub.com"):
            headers["Cache-Control"] = "no-store"
        return Response(
            content=content,
            status_code=upstream.status_code,
            media_type=content_type.split(";", 1)[0] or None,
            headers=headers,
        )

    if stream_cm is not None:
        upstream_length = upstream.headers.get("content-length")
        if upstream_length:
            headers["Content-Length"] = upstream_length

        async def stream_body():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await stream_cm.__aexit__(None, None, None)

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
    if _is_cvh_iframe_url(url):
        return await _cvh_frame(request, url)
    if _is_sibnet_iframe_url(url):
        return await _sibnet_frame(request, url)
    return await _fetch(request, url, url, frame=True)


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
