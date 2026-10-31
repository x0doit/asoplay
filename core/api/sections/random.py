"""
Раздел /random/watchable: случайный тайтл, который точно есть у источника.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Случайность даёт сам каталог old.yummyani.me: anime?sort=random перемешивает весь список, а не одну верхушку рейтинга
(по смещению каталог отдаёт лишь первую тысячу записей). Из 50 записей берётся первая, что проходит фильтры: есть
MAL id, тайтл не скрыт панелью Anime Social, это не клип (music, cm, pv) и не анонс (его ещё нельзя смотреть; онгоинги
проходят), рейтинг, если он есть, не ниже 6; тайтл знает каталог MAL (Jikan), и он не короткий — фильм от 40 минут,
серия от 8, по длительности из Jikan. Не нашлось — ещё не больше двух таких запросов; на всё нажатие — TIMEOUT
секунд. Каталог зовётся как источники: сначала напрямую, а если хост недоступен — через мост VPN
(core/classes/http.py). Не нашлось ничего — причины промахов уходят в журнал.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import Counter
from dataclasses import dataclass, field

import httpx
from fastapi import APIRouter, HTTPException

from core.classes import http
from core.classes.jikan import catalog as jikan_catalog
from core.functions import site_settings

log = logging.getLogger("asoplay")  # тот же журнал, что у core.py до деления

router = APIRouter()

TIMEOUT = 8.0  # на всё нажатие
_CATALOG = "https://old.yummyani.me/api/anime?limit=50&sort=random"
_ROUNDS = 3  # один запрос и не больше двух повторов, если ничего не подошло
_SKIP_TYPES = ("music", "cm", "pv")
_DURATION_PART = re.compile(r"(\d+)\s*(hr|min|sec)")
_UNIT_MINUTES = {"hr": 60, "min": 1, "sec": 1 / 60}


@dataclass
class _Attempt:
    """Ход одного нажатия — для журнала: чего ждём сейчас (каталог или Jikan) и сколько раз что случилось —
    промахи по причинам и тайм-ауты Jikan."""

    waiting: str = ""
    counts: Counter = field(default_factory=Counter)


@router.get("/random/watchable")
async def random_watchable():
    """Случайный тайтл, который можно смотреть; не нашёлся за TIMEOUT секунд — 504, ни одной годной записи — 502.
    Причины промахов — в журнал."""
    attempt = _Attempt()
    try:
        return await asyncio.wait_for(_random_watchable(attempt), timeout=TIMEOUT)
    except asyncio.TimeoutError as exc:
        log.info("random watchable: nothing in %ss, waiting for %s; counts: %s", TIMEOUT, attempt.waiting,
                 dict(attempt.counts))
        raise HTTPException(504, "random pool timed out") from exc


async def _random_watchable(attempt: _Attempt) -> dict:
    """До _ROUNDS перемешанных страниц каталога по 50 записей — первая, что проходит фильтры (см. docstring модуля).
    Ответ — из строки каталога, как и раньше: type — вид у каталога, а не у Jikan."""
    for _ in range(_ROUNDS):
        attempt.waiting = "catalog"
        for a in await _shuffled(attempt):
            mal_id = _candidate(a, attempt.counts)
            if mal_id is None:
                continue
            attempt.waiting = "jikan"
            if not await _known_and_long(mal_id, attempt.counts):
                continue
            return {
                "mal_id": mal_id,
                "anime_id": a.get("anime_id"),
                "title": a.get("title"),
                "year": a.get("year"),
                "type": (a.get("type") or {}).get("alias", ""),
            }
    log.info("random watchable: nothing fits after %s requests; counts: %s", _ROUNDS, dict(attempt.counts))
    raise HTTPException(502, "random pool unreachable")


async def _shuffled(attempt: _Attempt) -> list[dict]:
    """Одна перемешанная страница каталога. Сбой сети, ответ не 200 или не JSON, пустая страница — пустой список,
    причина — в счётчик."""
    try:
        r = await http.request("GET", _CATALOG, timeout=TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    except httpx.HTTPError as exc:
        log.debug("random watchable request failed: %s", exc)
        attempt.counts["network"] += 1
        return []
    if r.status_code != 200:
        attempt.counts["status"] += 1
        return []
    try:
        resp = r.json().get("response")
        items = resp if isinstance(resp, list) else (resp or {}).get("data", [])
    except (ValueError, AttributeError):
        attempt.counts["not_json"] += 1
        return []
    if not items:
        attempt.counts["empty"] += 1
    return items


def _candidate(a: dict, counts: Counter) -> int | None:
    """MAL id записи каталога, если она проходит фильтры: есть MAL id, не скрыт, не клип, не анонс, рейтинг не ниже 6.
    Не проходит — None, причина — в счётчик."""
    mal_id = (a.get("remote_ids") or {}).get("myanimelist_id")
    if not str(mal_id or "").isdigit():
        counts["no_mal_id"] += 1
        return None
    if site_settings.hidden(int(mal_id)):
        counts["hidden"] += 1
        return None
    if (a.get("type") or {}).get("alias", "") in _SKIP_TYPES:
        counts["type"] += 1
        return None
    if (a.get("anime_status") or {}).get("alias") == "announcement":
        counts["announcement"] += 1
        return None
    rating = (a.get("rating") or {}).get("average", 0) or 0
    if rating and rating < 6:
        counts["rating"] += 1
        return None
    return int(mal_id)


async def _known_and_long(mal_id: int, counts: Counter) -> bool:
    """Тайтл знает каталог MAL, и он не короткий. 404 — мимо. MAL недоступен или ответил не карточкой — тайтл
    годится без проверки длительности, как и раньше; тайм-аут Jikan считается отдельно — чтобы 504 из-за него было
    видно в журнале."""
    try:
        content, _, status = await jikan_catalog.fetch(f"anime/{mal_id}", timeout=4)
    except HTTPException as exc:
        # A Jikan outage doesn't invalidate the source's MAL mapping.
        counts["jikan_timeout" if exc.status_code == 504 else "jikan_error"] += 1
        return True
    if status == 404:
        counts["mal_404"] += 1
        return False
    try:
        card = json.loads(content).get("data") or {}
    except (ValueError, AttributeError):
        card = None
    if not isinstance(card, dict):  # битое тело или не карточка — как недоступный Jikan
        counts["jikan_error"] += 1
        return True
    if _too_short(card):
        counts["too_short"] += 1
        return False
    return True


def _too_short(card: dict) -> bool:
    """Коротко по карточке Jikan: фильм (Movie) — меньше 40 минут, остальное — меньше 8 минут на серию.
    Длительность неизвестна — не короткий."""
    minutes = _minutes(card.get("duration") or "")
    return minutes is not None and minutes < (40 if card.get("type") == "Movie" else 8)


def _minutes(duration: str) -> float | None:
    """Длительность из Jikan в минутах: «1 hr 51 min» → 111, «24 min per ep» → 24, «45 sec per ep» → 0.75;
    чисел нет (Unknown) — None."""
    parts = _DURATION_PART.findall(duration)
    return sum(int(n) * _UNIT_MINUTES[unit] for n, unit in parts) if parts else None
