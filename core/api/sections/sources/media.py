"""
Раздел /src/…: озвучки серии и видео.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/api/sections/sources: вход (__init__.py) подключает части по порядку. Озвучки — одна на имя и вид
(озвучка или субтитры), с лучшим плеером, VK — последними. Видео проходят фильтр неиграбельных плееров и рекламы
(core/functions/source_filters.py).
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from core.functions import native_sources, source_filters, sources
from core.functions.player_hosts import iframe_preference_score, is_disabled_player_url

log = logging.getLogger("asoplay")  # тот же журнал, что у core.py до деления

router = APIRouter()


@router.get("/src/dubs")
async def dubs_for_episode(key: str, source: str = "anilibria"):
    """Озвучки серии по её ключу: одна на имя и вид, с лучшим плеером, VK — последними."""
    if source in sources.NATIVE_SOURCES:
        return await sources.guard_source(
            source,
            "dubs",
            native_sources.native_dubs(source, key),
            timeout=12.0,
        )

    episode = sources.get(key)

    def run_dubs(route):
        """Озвучки серии у источника по маршруту route."""
        found = sources.on_route(source, episode, route)
        return sources.call(getattr(found, "a_get_sources", None) or found.get_sources)

    try:
        sources_list = await sources.source_call(source, "dubs", run_dubs, timeout=12.0)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("sources failed")
        raise HTTPException(502, f"sources failed: {exc}")

    seen: dict[tuple[str, str], dict] = {}
    scores: dict[tuple[str, str], int] = {}
    for s in sources_list:
        url = getattr(s, "url", None) or ""
        if source_filters.unplayable_player_url(url):
            continue
        name = getattr(s, "title", None) or getattr(s, "name", None) or "—"
        norm = source_filters.norm_dub_name(name)
        if not norm:
            continue
        kind = "sub" if "субтитры" in name.lower() or "sub" in name.lower() else "voice"
        identity = (norm, kind)
        score = iframe_preference_score(url)
        if identity in seen and score <= scores[identity]:
            continue
        scores[identity] = score
        seen[identity] = {"name": name.strip(), "norm": norm, "kind": kind, "key": sources.put(s)}
    return [seen[identity] for identity in source_filters.vk_last(list(seen), scores)]


@router.get("/src/videos")
async def videos(key: str, source: str = "anilibria"):
    """Видео по ключу озвучки или серии: фрейм плеера, файлы и потоки — сначала m3u8 и лучшее качество; всё —
    через фильтр неиграбельных плееров и рекламы."""
    if source in sources.NATIVE_SOURCES:
        vids = await sources.guard_source(
            source,
            "videos",
            native_sources.native_videos(source, key),
            timeout=15.0,
        )
        return await source_filters.filter_source_videos(vids)

    obj = sources.get(key)
    if hasattr(obj, "get_videos") or hasattr(obj, "a_get_videos"):
        name = getattr(obj, "title", None) or getattr(obj, "name", None)
        iframe_url = getattr(obj, "url", None) or ""
        if source_filters.unplayable_player_url(iframe_url):
            return []

        if iframe_url.startswith("http"):
            return await source_filters.filter_source_videos([{
                "url": iframe_url, "quality": None, "type": "iframe",
                "headers": {}, "source_name": name,
            }])

        out: list[dict] = []

        def run_videos(route):
            """Видео озвучки у источника по маршруту route."""
            found = sources.on_route(source, obj, route)
            return sources.call(getattr(found, "a_get_videos", None) or found.get_videos)

        try:
            vids = await sources.source_call(source, "videos", run_videos, timeout=15.0)
            for v in vids:
                url = getattr(v, "url", None) or str(v)
                if not url:
                    continue
                out.append({
                    "url": url, "quality": getattr(v, "quality", None),
                    "type": getattr(v, "type", None) or ("m3u8" if ".m3u8" in url else "mp4"),
                    "headers": getattr(v, "headers", None) or {},
                    "source_name": name,
                })
        except Exception as exc:
            log.debug("videos extract failed: %s", exc)
        return await source_filters.filter_source_videos(out)

    # Legacy branch — extractor gave us an Episode, we'd enumerate sources × videos.
    episode = obj

    def run_video_sources(route):
        """Озвучки серии у источника по маршруту route — для старой ветки «серия → все видео»."""
        found = sources.on_route(source, episode, route)
        return sources.call(getattr(found, "a_get_sources", None) or found.get_sources)

    try:
        sources_list = await sources.source_call(source, "video sources", run_video_sources, timeout=12.0)

        sem = asyncio.Semaphore(8)

        async def fetch_one(s):
            """Видео одной озвучки за 8 секунд; выключенный плеер или ошибка — пусто."""
            if is_disabled_player_url(getattr(s, "url", None) or ""):
                return []
            async with sem:
                try:
                    get_videos = getattr(s, "a_get_videos", None) or s.get_videos
                    vids = await asyncio.wait_for(sources.call(get_videos), timeout=8.0)
                except Exception as exc:
                    log.debug("source videos skipped: %s", exc)
                    return []
                out = []
                name = getattr(s, "title", None) or getattr(s, "name", None)
                for v in vids:
                    url = getattr(v, "url", None) or str(v)
                    if not url:
                        continue
                    quality = getattr(v, "quality", None)
                    vtype = getattr(v, "type", None) or ("m3u8" if ".m3u8" in url else "mp4")
                    headers = getattr(v, "headers", None) or {}
                    out.append({"url": url, "quality": quality, "type": vtype,
                                "headers": headers, "source_name": name})
                return out

        grouped = await sources.guard_source(
            source,
            "videos",
            asyncio.gather(*[fetch_one(s) for s in sources_list], return_exceptions=True),
            timeout=15.0,
        )
        all_videos: list[dict] = []
        for g in grouped:
            if isinstance(g, list):
                all_videos.extend(g)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("videos failed")
        raise HTTPException(502, f"videos failed: {exc}")

    def score(v):
        """Порядок видео: m3u8 раньше mp4, затем по качеству."""
        q = v.get("quality") or 0
        try:
            q = int(str(q).replace("p", ""))
        except ValueError:
            q = 0
        return (1 if v["type"] == "m3u8" else 0, q)

    all_videos.sort(key=score, reverse=True)
    return await source_filters.filter_source_videos(all_videos)
