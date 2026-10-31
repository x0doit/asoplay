"""
HTML страниц тайтлов: оболочка SPA (index.html) с мета-тегами, превью Open Graph и JSON-LD.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Сайт — одностраничное приложение, но у каждого тайтла есть HTML по настоящему адресу: настоящие title, description
и robots, превью Open Graph, описание TVSeries / Movie в JSON-LD из кеша страниц (core/modules/title_cache.py).
Его читают поисковики и превью ссылок, а SPA загружается поверх того же HTML. Без тайтла — общая оболочка (главная,
каталог, личные разделы), для тайтла, которого нет в MAL, — заглушка 404. read_shell и override_head берёт и
страница профиля (core/modules/profile_pages.py).
"""
from __future__ import annotations

import html
import json
import re
from typing import Any

from fastapi.responses import HTMLResponse

from core.config import env
from core.config.config import AUTHOR_NAME, AUTHOR_URL, SITE_NAME, SITE_URL
from core.modules import title_cache

_INDEX_HTML = env.ROOT / "index.html"


# ---------- HTML rendering ----------
_CACHE_SHELL: tuple[str, float] | None = None


def read_shell() -> str:
    """Текст index.html — оболочки SPA; в памяти он держится 2 секунды, чтобы не читать диск на каждый запрос."""
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


def override_head(shell: str, *, title: str | None = None,
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


def render_title_page(info: dict[str, Any]) -> HTMLResponse:
    """Страница тайтла: title, description и robots в head, canonical, Open Graph и JSON-LD; данные тайтла для SPA
    (av-preload) и noscript для поисковиков без JS."""
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
    canonical = title_cache.canonical_url(int(info["mal_id"]), info["slug"])

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

    shell = read_shell()
    shell = override_head(shell, title=page_title, description=page_description,
                          robots="index, follow, max-image-preview:large")
    shell = shell.replace("<!--av:head-extra-->", head_extra, 1)
    shell = shell.replace("<!--av:preload-->", preload_script, 1)
    shell = shell.replace("<!--av:noscript-->", noscript_fallback, 1)
    # no-cache (не max-age=300), чтобы обновления cache-buster у /app.js и
    # /styles.css доходили до пользователя сразу, а не через 5 минут кеша
    # самой HTML-оболочки. Тело shell'а маленькое, revalidate дёшев.
    return HTMLResponse(shell, headers={"Cache-Control": "no-cache"})


def render_generic() -> HTMLResponse:
    """Home / catalog shell — no title-specific meta, but we keep a conservative
    `<title>` and a sitewide OG card."""
    shell = read_shell()
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


def render_missing_title(mal_id: int, slug: str) -> HTMLResponse:
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
    shell = read_shell()
    shell = override_head(shell,
                          title=page_title,
                          description="Запрошенная страница аниме не найдена.",
                          robots="noindex, follow")
    shell = shell.replace("<!--av:head-extra-->", head_extra, 1)
    shell = shell.replace("<!--av:preload-->", "", 1)
    shell = shell.replace("<!--av:noscript-->", noscript, 1)
    return HTMLResponse(shell, status_code=404,
                        headers={"Cache-Control": "public, max-age=60"})
