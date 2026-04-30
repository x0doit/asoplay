# -*- coding: utf-8 -*-
"""
AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Canonical public pages for individual anime titles + sitemap + robots.

The site is still a single-page app for logged-in behaviour, but every anime
has a server-rendered HTML shell at a real URL. The shell carries real meta
tags, an Open Graph preview, and a JSON-LD TVSeries / Movie description
pulled from the `aviev_title_pages` cache. That is what crawlers and link
previewers read. The SPA boots on top of the same HTML and takes over for
logged-in interactions.

The aviev_title_pages cache is populated "on first visit": the frontend calls
/title-pages/record when it has loaded a fresh Jikan payload, and the server
stores the slim subset it needs for SEO. A TTL keeps the cache from drifting.
"""
from __future__ import annotations

import html
import json
import logging
import os
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, Field, conint

from server.animesocial import connect
from server.proxies import cached_fetch

log = logging.getLogger("animeviev.title_pages")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_INDEX_HTML = _PROJECT_ROOT / "index.html"

SITE_URL = os.environ.get("AV_SITE_URL", "http://127.0.0.1:8787").rstrip("/")
SITE_NAME = os.environ.get("AV_SITE_NAME", "AnimeViev")
AUTHOR_NAME = "Чепела Даниэль Максимович"
AUTHOR_URL = "https://crazydev.pro/"
TTL_FRESH = timedelta(days=7)
TTL_STALE = timedelta(days=30)


router = APIRouter()


# ---------- slug helpers ----------
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    if not text:
        return ""
    # transliterate lossy but predictable; latin-only slugs work best for URLs
    norm = unicodedata.normalize("NFKD", text)
    ascii_only = norm.encode("ascii", "ignore").decode("ascii").lower().strip()
    slug = _NON_ALNUM.sub("-", ascii_only).strip("-")
    # Очень короткие («ii», «3», «2007») или цифровые slug'и выглядят
    # бессмысленно в URL — лучше вернуть пусто и собрать fallback `mal-{id}`.
    if len(slug) < 3 or slug.isdigit():
        return ""
    return slug[:80]


def _canonical_path(mal_id: int, slug: str) -> str:
    return f"/anime/{mal_id}-{slug}/"


def _canonical_url(mal_id: int, slug: str) -> str:
    return f"{SITE_URL}{_canonical_path(mal_id, slug)}"


# ---------- cache read/write ----------
def _row_to_dict(row: tuple) -> dict[str, Any]:
    keys = [
        "mal_id", "slug", "title_ru", "title_en", "title_jp",
        "synopsis", "poster_url", "banner_url", "year", "kind",
        "airing_status", "episodes_total", "score", "genres_json",
        "studios", "cached_at", "fresh_until", "publish_state",
    ]
    data: dict[str, Any] = dict(zip(keys, row))
    genres = []
    if data.get("genres_json"):
        try:
            genres = json.loads(data["genres_json"])
        except (TypeError, json.JSONDecodeError):
            genres = []
    data["genres"] = genres if isinstance(genres, list) else []
    if data.get("score") is not None:
        data["score"] = float(data["score"])
    return data


def _load_cached(mal_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT mal_id, slug, title_ru, title_en, title_jp, synopsis,
                      poster_url, banner_url, year, kind, airing_status,
                      episodes_total, score, genres_json, studios,
                      cached_at, fresh_until, publish_state
                FROM aviev_title_pages WHERE mal_id=%s LIMIT 1""",
            (mal_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return _row_to_dict(row)


def _store_title(payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.utcnow()
    fresh_until = now + TTL_FRESH
    mal_id = int(payload["mal_id"])
    # Для slug'а предпочитаем латинский title — он уже без кириллицы/кандзи
    # и даёт читаемые URL вида /anime/1-cowboy-bebop/. Cyrillic NFKD
    # полностью вырезается ASCII-страйпом, так что русское название как
    # источник slug'а нам ничего не даёт.
    slug = _slugify(payload.get("title_en") or payload.get("title_ru") or payload.get("title_jp") or "")
    if not slug or slug == "anime":
        slug = f"mal-{mal_id}"
    # slug uniqueness — if taken by a different mal_id, suffix the mal_id
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT mal_id FROM aviev_title_pages WHERE slug=%s LIMIT 1",
            (slug,),
        )
        clash = cur.fetchone()
        if clash and int(clash[0]) != mal_id:
            slug = f"{slug}-{mal_id}"

        cur.execute(
            """INSERT INTO aviev_title_pages
                (mal_id, slug, title_ru, title_en, title_jp, synopsis,
                 poster_url, banner_url, year, kind, airing_status,
                 episodes_total, score, genres_json, studios,
                 snapshot_json, cached_at, fresh_until, publish_state)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'live')
                ON DUPLICATE KEY UPDATE
                    slug=VALUES(slug),
                    title_ru=VALUES(title_ru),
                    title_en=VALUES(title_en),
                    title_jp=VALUES(title_jp),
                    synopsis=VALUES(synopsis),
                    poster_url=VALUES(poster_url),
                    banner_url=VALUES(banner_url),
                    year=VALUES(year),
                    kind=VALUES(kind),
                    airing_status=VALUES(airing_status),
                    episodes_total=VALUES(episodes_total),
                    score=VALUES(score),
                    genres_json=VALUES(genres_json),
                    studios=VALUES(studios),
                    snapshot_json=VALUES(snapshot_json),
                    cached_at=VALUES(cached_at),
                    fresh_until=VALUES(fresh_until)""",
            (
                mal_id,
                slug,
                (payload.get("title_ru") or "")[:500],
                (payload.get("title_en") or "")[:500],
                (payload.get("title_jp") or "")[:500],
                (payload.get("synopsis") or "")[:20000],
                (payload.get("poster_url") or "")[:500],
                (payload.get("banner_url") or "")[:500],
                payload.get("year"),
                (payload.get("kind") or "")[:24],
                (payload.get("airing_status") or "")[:24],
                payload.get("episodes_total"),
                payload.get("score"),
                json.dumps(payload.get("genres") or [], ensure_ascii=False),
                (payload.get("studios") or "")[:500],
                json.dumps(payload.get("snapshot") or {}, ensure_ascii=False) if payload.get("snapshot") else None,
                now,
                fresh_until,
            ),
        )
        cur.execute("DELETE FROM aviev_title_refresh_queue WHERE mal_id=%s", (mal_id,))
        conn.commit()
    return {"mal_id": mal_id, "slug": slug, "cached_at": now.isoformat(), "fresh_until": fresh_until.isoformat()}


# ---------- frontend-triggered recording ----------
class RecordIn(BaseModel):
    mal_id: conint(ge=1, le=10_000_000)
    title_ru: str = Field("", max_length=500)
    title_en: str = Field("", max_length=500)
    title_jp: str = Field("", max_length=500)
    synopsis: str = Field("", max_length=20_000)
    poster_url: str = Field("", max_length=500)
    banner_url: str = Field("", max_length=500)
    year: int | None = None
    kind: str = Field("", max_length=24)
    airing_status: str = Field("", max_length=24)
    episodes_total: int | None = None
    score: float | None = None
    genres: list[str] = Field(default_factory=list)
    studios: str = Field("", max_length=500)


@router.post("/title-pages/record")
def title_record(payload: RecordIn) -> dict[str, Any]:
    """Called by the frontend after loading a title. We cache the slim
    version we need for SEO; crawlers hitting /anime/{id}/ later will see
    real metadata rather than an empty SPA shell."""
    return _store_title(payload.dict())


# ---------- on-demand Jikan backfill ----------
async def _fetch_and_store(mal_id: int) -> dict[str, Any] | None:
    try:
        content, _, status = await cached_fetch(
            "GET", f"https://api.jikan.moe/v4/anime/{mal_id}/full", ttl=3600, timeout=10
        )
    except HTTPException:
        return None
    if status != 200:
        return None
    try:
        data = json.loads(content.decode("utf-8")).get("data") or {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not data:
        return None
    images = data.get("images") or {}
    jpg = images.get("jpg") or {}
    payload: dict[str, Any] = {
        "mal_id": mal_id,
        "title_ru": "",
        "title_en": data.get("title_english") or data.get("title") or "",
        "title_jp": data.get("title_japanese") or "",
        "synopsis": (data.get("synopsis") or "").strip(),
        "poster_url": jpg.get("large_image_url") or jpg.get("image_url") or "",
        "banner_url": jpg.get("large_image_url") or "",
        "year": data.get("year"),
        "kind": (data.get("type") or "").strip(),
        "airing_status": (data.get("status") or "").strip(),
        "episodes_total": data.get("episodes"),
        "score": data.get("score"),
        "genres": [g.get("name") for g in (data.get("genres") or []) if g.get("name")],
        "studios": ", ".join(s.get("name") for s in (data.get("studios") or []) if s.get("name")),
    }
    return _store_title(payload)


# ---------- HTML rendering ----------
_CACHE_SHELL: tuple[str, float] | None = None


def _read_shell() -> str:
    global _CACHE_SHELL
    # In dev, index.html changes often; cache for 2 s to avoid repeated disk reads
    # but stay fresh.
    import time as _t

    now = _t.time()
    if _CACHE_SHELL and now - _CACHE_SHELL[1] < 2:
        return _CACHE_SHELL[0]
    text = _INDEX_HTML.read_text(encoding="utf-8")
    _CACHE_SHELL = (text, now)
    return text


def _override_head(shell: str, *, title: str | None = None,
                   description: str | None = None, robots: str | None = None) -> str:
    """Заменяет существующий <title> / <meta name=description> / <meta name=robots>
    в HTML-оболочке. Без этого SSR-вставка `head_extra` добавляла ВТОРОЙ
    <title>, и браузер показывал первый (дефолтный) из index.html."""
    if title is not None:
        shell = re.sub(
            r"<title>[^<]*</title>",
            f"<title>{html.escape(title)}</title>",
            shell, count=1,
        )
    if description is not None:
        shell = re.sub(
            r'<meta\s+name=(?:"|\')description(?:"|\')\s+content=(?:"|\')[^"\'>]*(?:"|\')\s*/?>',
            f'<meta name="description" content="{html.escape(description)}" />',
            shell, count=1, flags=re.IGNORECASE,
        )
    if robots is not None:
        shell = re.sub(
            r'<meta\s+name=(?:"|\')robots(?:"|\')\s+content=(?:"|\')[^"\'>]*(?:"|\')\s*/?>',
            f'<meta name="robots" content="{html.escape(robots)}" />',
            shell, count=1, flags=re.IGNORECASE,
        )
    return shell


def _render_title_page(info: dict[str, Any]) -> HTMLResponse:
    title_ru = info.get("title_ru") or ""
    title_en = info.get("title_en") or ""
    title_jp = info.get("title_jp") or ""
    main_title = title_ru or title_en or f"Anime #{info['mal_id']}"
    synopsis = (info.get("synopsis") or "").strip()
    short_desc = synopsis[:160] + ("…" if len(synopsis) > 160 else "")
    poster = info.get("poster_url") or ""
    year = info.get("year") or ""
    kind = info.get("kind") or ""
    episodes_total = info.get("episodes_total") or ""
    score = info.get("score")
    canonical = _canonical_url(int(info["mal_id"]), info["slug"])

    all_titles = [t for t in [title_ru, title_en, title_jp] if t]
    alt_titles = ", ".join(all_titles[1:])

    jsonld: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "Movie" if kind.lower() == "movie" else "TVSeries",
        "name": main_title,
        "alternateName": all_titles[1:] if len(all_titles) > 1 else None,
        "image": poster or None,
        "description": synopsis or None,
        "url": canonical,
        "inLanguage": "ru",
        "author": {"@type": "Person", "name": AUTHOR_NAME, "url": AUTHOR_URL},
        "publisher": {"@type": "Organization", "name": SITE_NAME, "url": SITE_URL},
    }
    if year:
        jsonld["datePublished"] = str(year)
    if episodes_total and kind.lower() != "movie":
        jsonld["numberOfEpisodes"] = int(episodes_total)
    if score is not None:
        jsonld["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": float(score),
            "bestRating": 10,
            "ratingCount": 1,
        }
    jsonld = {k: v for k, v in jsonld.items() if v is not None}

    page_title = f"{main_title} — смотреть онлайн | {SITE_NAME}"
    page_description = short_desc or (main_title + " — смотреть онлайн в русской озвучке")
    head_extra = (
        f'<meta name="author" content="{html.escape(AUTHOR_NAME)} (x0doit)">\n'
        f'<link rel="canonical" href="{html.escape(canonical)}">\n'
        f'<meta property="og:type" content="video.tv_show">\n'
        f'<meta property="og:site_name" content="{html.escape(SITE_NAME)}">\n'
        f'<meta property="og:title" content="{html.escape(main_title)}">\n'
        f'<meta property="og:description" content="{html.escape(short_desc)}">\n'
        f'<meta property="og:url" content="{html.escape(canonical)}">\n'
        + (f'<meta property="og:image" content="{html.escape(poster)}">\n' if poster else '')
        + f'<meta name="twitter:card" content="summary_large_image">\n'
        + (f'<meta property="og:locale" content="ru_RU">\n')
        + f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>\n'
    )

    preload_json = {
        "kind": "anime",
        "mal_id": int(info["mal_id"]),
        "slug": info["slug"],
        "canonical": canonical,
        "title_ru": title_ru,
        "title_en": title_en,
        "title_jp": title_jp,
        "synopsis": synopsis,
        "poster_url": poster,
        "banner_url": info.get("banner_url") or "",
        "year": year,
        "kind": kind,
        "airing_status": info.get("airing_status") or "",
        "episodes_total": info.get("episodes_total"),
        "score": score,
        "genres": info.get("genres") or [],
    }
    # <script type="application/json"> is RAWTEXT — HTML entities are NOT
    # decoded inside. We only need to break the closing tag to keep the parser
    # from leaving the script context early.
    preload_script = (
        '<script id="av-preload" type="application/json">'
        + json.dumps(preload_json, ensure_ascii=False).replace("</", "<\\/")
        + "</script>"
    )

    # Visible title for crawlers that do not execute JS: we slip a short
    # `<noscript>` block inside <body> with the main title and description.
    noscript_fallback = (
        f'<noscript><h1>{html.escape(main_title)}</h1>'
        + (f'<p>{html.escape(alt_titles)}</p>' if alt_titles else '')
        + (f'<p>{html.escape(synopsis)}</p>' if synopsis else '')
        + f'<p><a href="{SITE_URL}">{html.escape(SITE_NAME)}</a> — каталог аниме</p>'
        + '</noscript>'
    )

    shell = _read_shell()
    shell = _override_head(shell, title=page_title, description=page_description,
                           robots="index, follow, max-image-preview:large")
    shell = shell.replace("<!--av:head-extra-->", head_extra, 1)
    shell = shell.replace("<!--av:preload-->", preload_script, 1)
    shell = shell.replace("<!--av:noscript-->", noscript_fallback, 1)
    # no-cache (не max-age=300), чтобы обновления cache-buster у /app.js и
    # /styles.css доходили до пользователя сразу, а не через 5 минут кеша
    # самой HTML-оболочки. Тело shell'а маленькое, revalidate дёшев.
    return HTMLResponse(shell, headers={"Cache-Control": "no-cache"})


def _render_generic() -> HTMLResponse:
    """Home / catalog shell — no title-specific meta, but we keep a conservative
    `<title>` and a sitewide OG card."""
    shell = _read_shell()
    head_extra = (
        f'<meta name="author" content="{html.escape(AUTHOR_NAME)} (x0doit)">\n'
        f'<meta property="og:type" content="website">\n'
        f'<meta property="og:site_name" content="{html.escape(SITE_NAME)}">\n'
        f'<meta property="og:title" content="{html.escape(SITE_NAME)} — смотреть аниме онлайн">\n'
        f'<meta property="og:url" content="{SITE_URL}/">\n'
    )
    shell = shell.replace("<!--av:head-extra-->", head_extra, 1)
    shell = shell.replace("<!--av:preload-->", "", 1)
    shell = shell.replace("<!--av:noscript-->", "", 1)
    # no-cache (не max-age=300), чтобы обновления cache-buster у /app.js и
    # /styles.css доходили до пользователя сразу, а не через 5 минут кеша
    # самой HTML-оболочки. Тело shell'а маленькое, revalidate дёшев.
    return HTMLResponse(shell, headers={"Cache-Control": "no-cache"})


# ---------- public routes ----------
@router.get("/anime/{stub}/", response_class=HTMLResponse)
@router.get("/anime/{stub}", response_class=HTMLResponse)
async def canonical_title(stub: str):
    m = re.match(r"^(\d{1,9})(?:-([a-z0-9-]+))?$", stub)
    if not m:
        raise HTTPException(404, "bad anime path")
    mal_id = int(m.group(1))
    incoming_slug = m.group(2) or ""

    info = _load_cached(mal_id)
    if info is None:
        info = await _fetch_and_store(mal_id)
        if info is None:
            # ID нет ни в кеше, ни в MAL (например, уже удалён или это невалидный
            # id типа 2). Отдаём HTML-оболочку SPA со статусом 404 + noindex,
            # чтобы:
            #  - пользователь при перезагрузке не получал сырой JSON detail;
            #  - SPA у себя показала человекочитаемый экран с подсказкой;
            #  - краулеры не индексировали несуществующий тайтл.
            return _render_missing_title(mal_id, incoming_slug)
        info = _load_cached(mal_id) or info

    canonical_slug = info["slug"]
    if incoming_slug != canonical_slug:
        return RedirectResponse(_canonical_path(mal_id, canonical_slug), status_code=301)

    return _render_title_page(info)


def _render_missing_title(mal_id: int, slug: str) -> HTMLResponse:
    """SSR-заглушка для удалённого/несуществующего MAL ID."""
    canonical = f"{SITE_URL}/anime/{mal_id}"
    page_title = f"Аниме #{mal_id} не найдено — {SITE_NAME}"
    head_extra = f'<link rel="canonical" href="{html.escape(canonical)}">\n'
    search_hint = slug.replace("-", " ") if slug else ""
    noscript = (
        "<noscript>"
        f"<h1>Аниме не найдено</h1>"
        f"<p>Идентификатор #{mal_id} отсутствует в MyAnimeList.</p>"
        + (f'<p>Попробуйте найти: <a href="/search?q={html.escape(search_hint)}">{html.escape(search_hint)}</a></p>'
           if search_hint else "")
        + f'<p><a href="{SITE_URL}">На главную {html.escape(SITE_NAME)}</a></p>'
        + "</noscript>"
    )
    shell = _read_shell()
    shell = _override_head(shell,
                           title=page_title,
                           description="Запрошенная страница аниме не найдена.",
                           robots="noindex, follow")
    shell = shell.replace("<!--av:head-extra-->", head_extra, 1)
    shell = shell.replace("<!--av:preload-->", "", 1)
    shell = shell.replace("<!--av:noscript-->", noscript, 1)
    return HTMLResponse(shell, status_code=404,
                        headers={"Cache-Control": "public, max-age=60"})


@router.get("/sitemap.xml")
def sitemap() -> Response:
    now = datetime.utcnow()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT mal_id, slug, cached_at FROM aviev_title_pages
                WHERE publish_state='live'
                ORDER BY cached_at DESC LIMIT 5000"""
        )
        rows = cur.fetchall()
    xml = ['<?xml version="1.0" encoding="UTF-8"?>']
    xml.append('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">')
    xml.append(f"<url><loc>{html.escape(SITE_URL)}/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>")
    for path in ("/top", "/season", "/trending", "/movies"):
        xml.append(
            f"<url><loc>{html.escape(SITE_URL + path)}</loc><changefreq>daily</changefreq><priority>0.8</priority></url>"
        )
    for mal_id, slug, cached_at in rows:
        loc = html.escape(_canonical_url(int(mal_id), slug))
        lastmod = (cached_at or now).strftime("%Y-%m-%d")
        xml.append(
            f"<url><loc>{loc}</loc><lastmod>{lastmod}</lastmod><changefreq>weekly</changefreq><priority>0.6</priority></url>"
        )
    xml.append("</urlset>")
    return Response(
        "\n".join(xml),
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=1800"},
    )


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> PlainTextResponse:
    lines = [
        f"# {SITE_NAME} — (c) {AUTHOR_NAME} ({AUTHOR_URL})",
        "User-agent: *",
        "Allow: /",
        "Disallow: /account/",
        "Disallow: /auth/",
        "Disallow: /my/",
        "Disallow: /login",
        "Disallow: /proxy/",
        "Disallow: /shiki/",
        "Disallow: /src/",
        "Disallow: /random",
        "Disallow: /health",
        "Disallow: /sources",
        "Disallow: /title-pages/",
        f"Sitemap: {SITE_URL}/sitemap.xml",
    ]
    return PlainTextResponse(
        "\n".join(lines) + "\n",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------- shell routes for the SPA ----------
def register_shell_routes(app) -> None:
    """Attach catch-all HTML routes for SPA-driven paths (home, catalog,
    personal sections). Must be called AFTER all API routers are attached —
    FastAPI dispatches by registration order, so /auth/*, /account/*, /search,
    /episodes… must already be bound to win over these shell responders."""

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def _home():
        return _render_generic()

    for path in ("/top", "/season", "/trending", "/movies",
                 "/catalog", "/filter", "/random", "/login", "/search"):
        _attach_shell(app, path)

    # /my/favorites, /my/history, /my/watching, /my/settings …
    @app.get("/my/{tail:path}", response_class=HTMLResponse, include_in_schema=False)
    def _personal_shell(tail: str):
        return _render_generic()


def _attach_shell(app, path: str) -> None:
    @app.get(path, response_class=HTMLResponse, include_in_schema=False)
    def _shell():
        return _render_generic()
