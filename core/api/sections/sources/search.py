"""
Раздел /src/…: поиск тайтла у источника и его серии.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/api/sections/sources: вход (__init__.py) подключает части по порядку. Ответы — списки с ключами найденного:
по ключу результата поиска берутся серии, по ключу серии — озвучки (media.py).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from core.functions import native_sources, site_settings, source_filters, sources

log = logging.getLogger("asoplay")  # тот же журнал, что у core.py до деления

router = APIRouter()


@router.get("/src/search")
async def search(
    q: str = Query(..., min_length=1),
    source: str = "anilibria",
    year: int | None = None,
    mal_id: int | None = None,
):
    """Тайтл у источника: до 20 результатов с ключами, годом и mal_id, на год ±1. Тайтл скрыт панелью — 451,
    источник выключен или упал — пустой список."""
    if mal_id and site_settings.hidden(mal_id):
        # Скрыт из панели Anime Social по жалобе правообладателя — видео не ищем
        raise HTTPException(451, "unavailable")
    if not site_settings.source_enabled(source):
        return []
    if source in sources.NATIVE_SOURCES:
        try:
            results = await sources.source_call(
                source,
                "search",
                lambda route: native_sources.native_search(source, q, mal_id=mal_id, route=route),
                timeout=10.0,
            )
        except Exception as exc:
            log.info("search failed on %s: %s (returning empty)", source, exc)
            return []
        return source_filters.filter_by_year(results, year)

    sources.get_extractor(source)

    def run_search(route):
        """Поиск у источника по маршруту route."""
        ext = sources.extractor(source, route)
        # The async API can be cancelled when a route runs out of time; a thread cannot.
        return sources.call(getattr(ext, "a_search", None) or ext.search, q)

    try:
        results = await sources.source_call(source, "search", run_search, timeout=10.0)
    except Exception as exc:
        # Source-скрейперы anicli-api (animego, dreamcast, yummy_anime_org…)
        # регулярно падают: у них сайт-цель или блокирует запрос, или сменил
        # вёрстку, или rate-limit'ит. Плеер перебирает источники в цепочку и
        # уже обрабатывает «пусто» как «иду дальше» — ему не нужен 502.
        # Возвращаем [] вместо throw, чтобы browser-консоль не пестрила
        # красным. Причину по-прежнему видно в server-логе.
        log.info("search failed on %s: %s (returning empty)", source, exc)
        return []

    out = []
    for r in results[:20]:
        title = getattr(r, "title", None) or getattr(r, "name", None) or str(r)
        thumb = getattr(r, "thumbnail", None) or getattr(r, "poster", None) or getattr(r, "image", None)
        url = getattr(r, "url", None)

        item_year = None
        mal_id_hit = None
        raw_data = getattr(r, "data", None)
        if isinstance(raw_data, dict):
            for k in ("year", "release_year", "releasedOn"):
                v = raw_data.get(k)
                if v:
                    try:
                        item_year = int(str(v)[:4])
                        break
                    except (TypeError, ValueError):
                        pass
            ri = raw_data.get("remote_ids")
            if isinstance(ri, dict):
                mal_id_hit = ri.get("myanimelist_id") or ri.get("mal_id")
                try:
                    mal_id_hit = int(mal_id_hit) if mal_id_hit else None
                except (TypeError, ValueError):
                    mal_id_hit = None
        if item_year is None:
            for attr in ("year", "release_year"):
                v = getattr(r, attr, None)
                if v:
                    try:
                        item_year = int(str(v)[:4])
                        break
                    except (TypeError, ValueError):
                        pass

        out.append({
            "title": title,
            "title_en": getattr(r, "title_english", None) or getattr(r, "alt_title", None),
            "thumbnail": thumb,
            "url": url,
            "year": item_year,
            "key": sources.put(r),
            "source": source,
            "mal_id": mal_id_hit,
        })
    return source_filters.filter_by_year(out, year)


@router.get("/src/episodes")
async def episodes(key: str, source: str = "anilibria"):
    """Серии тайтла по ключу результата поиска: номер, название и ключ серии."""
    if source in sources.NATIVE_SOURCES:
        return await sources.source_call(
            source,
            "episodes",
            lambda route: native_sources.native_episodes(source, key, route=route),
            timeout=12.0,
        )

    search_result = sources.get(key)

    async def run_episodes(route):
        """Тайтл и его серии у источника по маршруту route."""
        found = sources.on_route(source, search_result, route)
        get_anime = getattr(found, "a_get_anime", None) or found.get_anime
        anime = await sources.call(get_anime)
        get_eps = getattr(anime, "a_get_episodes", None) or anime.get_episodes
        return await sources.call(get_eps)

    try:
        episodes_list = await sources.source_call(source, "episodes", run_episodes, timeout=24.0)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("episodes failed")
        raise HTTPException(502, f"episodes failed: {exc}")

    out = []
    for idx, e in enumerate(episodes_list):
        num = getattr(e, "num", None) or getattr(e, "number", None) or idx + 1
        title = getattr(e, "title", None) or getattr(e, "name", None) or f"Эпизод {num}"
        out.append({"num": num, "title": title, "key": sources.put(e)})
    return out
