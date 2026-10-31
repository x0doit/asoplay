"""
Свои источники — old.yummyani.me и animevost: поиск, серии, озвучки и видео в том же виде, что у anicli-api.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Клиенты — core/classes/sources; маршрут (напрямую или через мост VPN) и ключи найденного — core/functions/sources.py.
Маршруты /src/… (core/api/sections/sources) зовут эти функции для своих источников и дальше работают с ключами так же,
как у источников anicli-api.
"""
from __future__ import annotations

from fastapi import HTTPException

from core.classes.sources.animevost import Animevost
from core.classes.sources.oldyummy import OldYummy
from core.functions import source_filters, sources
from core.functions.player_hosts import iframe_preference_score


async def native_search(source: str, q: str, mal_id: int | None = None, route: str = "direct"):
    """Поиск у своего источника — результаты в виде /src/search с ключами; у oldyummy совпавшие по mal_id —
    вперёд."""
    if source == "animevost":
        vost: Animevost = sources.native("animevost", route)
        results = await vost.search(q, limit=20)
        out = []
        for item in results:
            k = sources.put({
                "type": "anime", "source": "animevost",
                "vost_id": item["id"], "title": item["title_ru"] or item["title"],
            })
            out.append({
                "title": item["title_ru"] or item["title"],
                "title_en": item.get("title_en"),
                "thumbnail": item.get("image"),
                "url": f"https://animevost.org/tip/tv/{item['id']}-x.html",
                "key": k, "source": "animevost",
                "year": item.get("year"), "note": item.get("note"),
            })
        return out

    if source == "oldyummy":
        oy: OldYummy = sources.native("oldyummy", route)
        results = await oy.search(q, limit=20)
        if mal_id:
            exact = [r for r in results if r.get("mal_id") == mal_id]
            if exact:
                results = exact + [r for r in results if r not in exact]
        out = []
        for item in results:
            k = sources.put({
                "type": "anime", "source": "oldyummy",
                "anime_id": item["id"], "title": item["title"],
                "mal_id": item.get("mal_id"),
            })
            out.append({
                "title": item["title"],
                "title_en": item.get("title_en"),
                "thumbnail": item.get("thumbnail"),
                "url": f"https://old.yummyani.me/catalog/item/{item.get('alias','')}",
                "key": k, "source": "oldyummy",
                "year": item.get("year"),
                "mal_id": item.get("mal_id"),
            })
        return out

    raise HTTPException(400, f"unknown native source {source}")


async def native_episodes(source: str, key: str, route: str = "direct"):
    """Серии по ключу тайтла: у animevost — с качествами, у oldyummy — собранные по номерам из списка видео."""
    obj = sources.get(key)

    if source == "animevost" and obj.get("source") == "animevost":
        vost: Animevost = sources.native("animevost", route)
        eps = await vost.episodes(obj["vost_id"])
        out = []
        for e in eps:
            ek = sources.put({"type": "episode", "source": "animevost", "qualities": e["qualities"]})
            out.append({"num": e["num"], "title": e["name"], "key": ek, "preview": e.get("preview")})
        return out

    if source == "oldyummy" and obj.get("source") == "oldyummy":
        oy: OldYummy = sources.native("oldyummy", route)
        raw = await oy.videos(obj["anime_id"])
        by_num: dict[int, list[dict]] = {}
        for v in raw:
            try:
                num = int(str(v.get("number") or "0").strip() or 0)
            except (TypeError, ValueError):
                num = 0
            if not num:
                continue
            by_num.setdefault(num, []).append(v)
        out = []
        for num in sorted(by_num.keys()):
            dubs = by_num[num]
            ek = sources.put({"type": "episode", "source": "oldyummy", "dubs": dubs})
            out.append({"num": num, "title": None, "key": ek, "preview": None})
        return out

    raise HTTPException(400, "bad episode source")


async def native_dubs(source: str, key: str):
    """Озвучки серии oldyummy: одна на имя и вид, с лучшим плеером, VK — последними; у других — пусто."""
    obj = sources.get(key)
    if source == "oldyummy" and obj.get("source") == "oldyummy":
        seen: dict[tuple[str, str], dict] = {}
        for v in obj.get("dubs", []):
            d = v.get("data") or {}
            name = (d.get("dubbing") or d.get("player") or "—").strip()
            norm = source_filters.norm_dub_name(name)
            if not norm:
                continue
            iframe_url = v.get("iframe_url") or ""
            if source_filters.unplayable_player_url(iframe_url):
                continue
            kind = "sub" if "субтитры" in name.lower() else "voice"
            identity = (norm, kind)
            score = iframe_preference_score(iframe_url)
            if identity in seen and score <= seen[identity].get("_score", -1):
                continue
            dk = sources.put({"type": "dub", "source": "oldyummy",
                       "iframe_url": iframe_url, "name": name})
            seen[identity] = {"name": name, "norm": norm, "kind": kind, "key": dk, "_score": score}
        scores = {identity: item["_score"] for identity, item in seen.items()}
        return [
            {item_key: item_value for item_key, item_value in seen[identity].items() if item_key != "_score"}
            for identity in source_filters.vk_last(list(seen), scores)
        ]
    return []


async def native_videos(source: str, key: str):
    """Видео по ключу: у animevost — файлы серии по качествам, у oldyummy — фрейм плеера озвучки."""
    obj = sources.get(key)

    if source == "animevost" and obj.get("source") == "animevost":
        return [
            {"url": q["url"], "quality": q.get("quality"), "type": q.get("type", "mp4"),
             "headers": {}, "source_name": "Animevost"}
            for q in obj.get("qualities", [])
        ]

    if source == "oldyummy" and obj.get("source") == "oldyummy":
        iframe = obj.get("iframe_url") or ""
        if iframe.startswith("//"):
            iframe = "https:" + iframe
        return [{
            "url": iframe, "quality": None, "type": "iframe",
            "headers": {}, "source_name": obj.get("name", "OldYummy"),
        }]

    raise HTTPException(400, "bad video source")
