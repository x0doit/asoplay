"""
Кеш страниц тайтлов: адрес страницы, чтение и запись aviev_title_pages, заполнение из каталога.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Отсюда берут данные страница тайтла (core/modules/title_pages.py) и её HTML (title_render.py), а карта сайта
(seo.py) — адреса. Кеш заполняет только сервер, из своего каталога: тайтла нет — fetch_and_store берёт его из Jikan
(русское название — с Shikimori), запись устарела (fresh_until, TTL_FRESH) — страница отдаётся из кеша, а
refresh_later обновляет её в фоне: одна загрузка на тайтл, не больше REFRESH_PER_MINUTE в минуту. Каталог
недоступен — запись остаётся прежней, попытка — при следующем заходе. Данные от браузера сюда не попадают: их
читают поисковики и превью ссылок. Адрес страницы — /anime/{mal_id}-{slug}/: slug латиницей из названия; занят
другим тайтлом — с суффиксом mal_id, не получился — mal-{mal_id}.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from datetime import timedelta
from typing import Any

from fastapi import HTTPException

from core.classes import http_cache
from core.classes.clock import utc_now
from core.classes.db import connect
from core.classes.http_cache import SHIKI_HEADERS, SHIKI_ORIGIN
from core.classes.jikan import catalog as jikan_catalog
from core.config.config import SITE_URL
from core.functions import title_completion

log = logging.getLogger("asoplay.title_cache")

TTL_FRESH = timedelta(days=7)
REFRESH_PER_MINUTE = 30


# ---------- slug helpers ----------
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    """Латинский slug из названия, до 80 знаков; слишком короткий или из одних цифр — пусто."""
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


def canonical_path(mal_id: int, slug: str) -> str:
    """Путь страницы тайтла: /anime/{mal_id}-{slug}/."""
    return f"/anime/{mal_id}-{slug}/"


def canonical_url(mal_id: int, slug: str) -> str:
    """Полный адрес страницы тайтла на сайте."""
    return f"{SITE_URL}{canonical_path(mal_id, slug)}"


# ---------- cache read/write ----------
def _row_to_dict(row: tuple) -> dict[str, Any]:
    """Строка aviev_title_pages → словарь: genres_json — в список genres, score — в float."""
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


def load_cached(mal_id: int) -> dict[str, Any] | None:
    """Тайтл из кеша страниц; нет его там — None."""
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


def stale(info: dict[str, Any]) -> bool:
    """Запись кеша устарела: срок fresh_until прошёл или его нет."""
    return info.get("fresh_until") is None or info["fresh_until"] <= utc_now()


def store_title(payload: dict[str, Any], *, sweep: bool = True) -> dict[str, Any]:
    """Записать тайтл из каталога в кеш — новый или поверх прежнего. Запись узнала, что тайтл вышел целиком, —
    досмотр и списки тем, кто уже досмотрел его последнюю серию (title_completion.title_finished; sweep=False — без
    этого, для подсчёта досылки). Возвращает mal_id, slug, cached_at и fresh_until."""
    now = utc_now()
    fresh_until = now + TTL_FRESH
    mal_id = int(payload["mal_id"])
    # slug — из латинского названия: кириллицу ASCII-срез всё равно вырезал бы (/anime/1-cowboy-bebop/)
    slug = _slugify(payload.get("title_en") or payload.get("title_ru") or payload.get("title_jp") or "")
    if not slug or slug == "anime":
        slug = f"mal-{mal_id}"
    # slug uniqueness — if taken by a different mal_id, suffix the mal_id
    finished = sweep and str(payload.get("airing_status") or "").strip() == title_completion.FINISHED
    found = None
    with connect() as conn:
        cur = conn.cursor()
        before = title_completion.last_episode(cur, mal_id) if finished else None
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
        total = int(payload.get("episodes_total") or 0)
        if finished and total and before != total:  # тайтл впервые известен как вышедший — или сменилось число серий
            found = title_completion.title_finished(conn, payload, total, now)
        conn.commit()
    if found:
        title_completion.announce(*found)
    return {"mal_id": mal_id, "slug": slug, "cached_at": now.isoformat(), "fresh_until": fresh_until.isoformat()}


# ---------- заполнение из каталога ----------
async def _russian_title(mal_id: int) -> str:
    """Русское название с Shikimori — тем же кешированным запросом, что /shiki/anime/{id}; сбой — пусто."""
    try:
        content, _, status = await http_cache.cached_fetch(
            "GET", f"{SHIKI_ORIGIN}/api/animes/{mal_id}", ttl=86400, timeout=10, headers=SHIKI_HEADERS)
        if status == 200:
            return str(json.loads(content).get("russian") or "").strip()
    except Exception as exc:  # noqa: BLE001 — без русского названия страница остаётся с названием из каталога
        log.info("No Russian title for %s from Shikimori: %s", mal_id, type(exc).__name__)
    return ""


async def fetch_and_store(mal_id: int, *, sweep: bool = True) -> dict[str, Any] | None:
    """Взять тайтл из каталога Jikan (русское название — с Shikimori) и записать в кеш (sweep — как у store_title).
    None — такого тайтла в MAL нет (404); каталог недоступен — HTTPException 503, ответил не тем — 502."""
    content, _, status = await jikan_catalog.fetch(f"anime/{mal_id}/full", timeout=10)
    if status == 404:
        return None
    if status != 200:
        raise HTTPException(503, "Title catalog unavailable")
    data = json.loads(content).get("data")
    if not isinstance(data, dict) or data.get("mal_id") != mal_id:
        raise HTTPException(502, "Invalid title catalog response")
    images = data.get("images") or {}
    jpg = images.get("jpg") or {}
    payload: dict[str, Any] = {
        "mal_id": mal_id,
        "title_ru": await _russian_title(mal_id),
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
    return store_title(payload, sweep=sweep)


_loop: asyncio.AbstractEventLoop | None = None  # цикл событий сервера — для refresh_soon из потоков
_refreshing: set[int] = set()       # тайтлы, чья загрузка уже идёт
_refreshed_at: list[float] = []     # когда назначены загрузки последней минуты
_tasks: set[asyncio.Task] = set()   # ссылки на задачи, пока они идут


def refresh_later(mal_id: int) -> bool:
    """Устаревшую страницу — обновить из каталога в фоне (из цикла событий). Один тайтл — одна загрузка; больше
    REFRESH_PER_MINUTE за минуту — до следующего захода. True — загрузка назначена."""
    now = time.monotonic()
    while _refreshed_at and now - _refreshed_at[0] >= 60:
        _refreshed_at.pop(0)
    if mal_id in _refreshing or len(_refreshed_at) >= REFRESH_PER_MINUTE:
        return False
    _refreshing.add(mal_id)
    _refreshed_at.append(now)
    task = asyncio.get_running_loop().create_task(_refresh(mal_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return True


def bind_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Цикл событий сервера — для refresh_soon (core.py при старте)."""
    global _loop
    _loop = loop


def refresh_soon(mal_id: int) -> None:
    """refresh_later из потока обработчика — через пару секунд: действие, которое спросило о тайтле (досмотр
    серии), к тому времени уже зафиксировано, и проверка досмотра после загрузки его увидит."""
    loop = _loop
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(loop.call_later, 2.0, refresh_later, mal_id)


async def _refresh(mal_id: int) -> None:
    """Фоновая загрузка тайтла; каталог недоступен или ответил не тем — прежняя запись остаётся."""
    try:
        await fetch_and_store(mal_id)
    except Exception as exc:  # noqa: BLE001 — страница остаётся прежней, попытка — при следующем заходе
        log.info("Title page %s kept as it was: catalog refresh failed (%s)", mal_id, exc)
    finally:
        _refreshing.discard(mal_id)


# ---------- для панели Anime Social ----------
def forget(mal_id: int) -> None:
    """Сбросить страницу тайтла: следующий заход заново возьмёт его из каталога (кнопка «Сбросить кеш» в панели)."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM aviev_title_pages WHERE mal_id = %s", (mal_id,))
        conn.commit()


async def lookup(mal_id: int) -> dict[str, Any] | None:
    """Тайтл из кеша страниц, а нет его там — из каталога (заодно в кеш). None — такого тайтла в MAL нет;
    каталог недоступен — HTTPException 503."""
    info = await asyncio.to_thread(load_cached, mal_id)
    return info if info is not None else await fetch_and_store(mal_id)
