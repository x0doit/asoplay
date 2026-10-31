"""
Раздел /random/watchable: случайный тайтл, который точно есть у источника.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

До 15 попыток взять случайную запись каталога old.yummyani.me: с MAL id, не скрытую панелью Anime Social, не клип и
не короткую, с рейтингом не ниже 6 и известную каталогу MAL; на всё — 8 секунд.
"""
from __future__ import annotations

import asyncio
import logging
import random as _random

import httpx
from fastapi import APIRouter, HTTPException

from core.classes import http
from core.classes.jikan import catalog as jikan_catalog
from core.functions import site_settings

log = logging.getLogger("asoplay")  # тот же журнал, что у core.py до деления

router = APIRouter()

_OLDYUMMY_CATALOG_MAX = 10000


@router.get("/random/watchable")
async def random_watchable():
    """Случайный тайтл, который можно смотреть; не нашёлся за 8 секунд — 504, ни одной годной
    записи — 502."""
    try:
        return await asyncio.wait_for(_random_watchable(), timeout=8)
    except asyncio.TimeoutError as exc:
        raise HTTPException(504, "random pool timed out") from exc


async def _random_watchable():
    """До 15 случайных записей каталога old.yummyani.me — первая, что проходит фильтры
    (см. docstring модуля)."""
    client = await http.get_client()
    tried: set[int] = set()
    for _ in range(15):
        offset = _random.randint(0, _OLDYUMMY_CATALOG_MAX - 1)
        if offset in tried:
            continue
        tried.add(offset)
        try:
            r = await client.get(
                f"https://old.yummyani.me/api/anime?limit=1&offset={offset}",
                timeout=8, headers={"User-Agent": "Mozilla/5.0"},
            )
            if r.status_code != 200:
                continue
            data = r.json()
            resp = data.get("response")
            items = resp if isinstance(resp, list) else (resp or {}).get("data", [])
            if not items:
                continue
            a = items[0]
            mal_id = (a.get("remote_ids") or {}).get("myanimelist_id")
            if not str(mal_id or "").isdigit() or site_settings.hidden(int(mal_id)):
                continue
            type_alias = (a.get("type") or {}).get("alias", "")
            if type_alias in ("music", "cm", "pv"):
                continue
            dur = a.get("duration", 0) or 0
            if type_alias == "movie" and dur and dur < 40 * 60:
                continue
            if type_alias != "movie" and dur and dur < 8 * 60:
                continue
            rating = (a.get("rating") or {}).get("average", 0) or 0
            if rating and rating < 6:
                continue
            try:
                _, _, status = await jikan_catalog.fetch(f"anime/{mal_id}", timeout=4)
                if status == 404:
                    continue
            except HTTPException:
                pass  # A Jikan outage doesn't invalidate the source's MAL mapping.
            return {
                "mal_id": int(mal_id),
                "anime_id": a.get("anime_id"),
                "title": a.get("title"),
                "year": a.get("year"),
                "type": type_alias,
            }
        except httpx.HTTPError as exc:
            log.debug("random watchable attempt failed: %s", exc)
            continue
    raise HTTPException(502, "random pool unreachable")
