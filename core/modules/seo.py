"""
Карта сайта и robots.txt: /sitemap.xml и /robots.txt.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

В карте сайта — главная, разделы каталога и страницы тайтлов из кеша (core/modules/title_cache.py): до 5000
последних, без скрытых панелью Anime Social. robots.txt закрывает от поисковиков аккаунт, вход, прокси и служебные
адреса и называет карту сайта.
"""
from __future__ import annotations

import html

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse, Response

from core.classes.clock import utc_now
from core.classes.db import connect
from core.config.config import AUTHOR_NAME, AUTHOR_URL, SITE_NAME, SITE_URL
from core.functions import site_settings
from core.modules import title_cache

router = APIRouter()


@router.get("/sitemap.xml")
def sitemap() -> Response:
    """Карта сайта: главная, разделы каталога и до 5000 страниц тайтлов из кеша, кроме скрытых панелью."""
    now = utc_now()
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
    for mal_id, slug, cached_at in (r for r in rows if not site_settings.hidden(int(r[0]))):
        loc = html.escape(title_cache.canonical_url(int(mal_id), slug))
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
    """robots.txt: что закрыто от поисковиков и где карта сайта."""
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
