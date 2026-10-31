"""
Кеш страниц тайтлов: адрес страницы, чтение и запись aviev_title_pages, заполнение из каталога.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Отсюда берут данные страница тайтла (core/modules/title_pages.py) и её HTML (title_render.py), а карта сайта
(seo.py) — адреса. Кеш заполняется «при первом заходе»: фронтенд, загрузив свежие данные Jikan, шлёт
/title-pages/record, и сервер хранит то, что нужно для SEO; тайтла в кеше нет — fetch_and_store берёт его из
каталога. Запись свежа TTL_FRESH (fresh_until). Адрес страницы — /anime/{mal_id}-{slug}/: slug латиницей из
названия; занят другим тайтлом — с суффиксом mal_id, не получился — mal-{mal_id}.
"""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from datetime import timedelta
from typing import Any

from fastapi import HTTPException

from core.classes.clock import utc_now
from core.classes.db import connect
from core.classes.jikan import catalog as jikan_catalog
from core.config.config import SITE_URL

TTL_FRESH = timedelta(days=7)


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


def store_title(payload: dict[str, Any]) -> dict[str, Any]:
    """Записать тайтл в кеш — новый или поверх прежнего — и убрать его из очереди обновления.
    Возвращает mal_id, slug, cached_at и fresh_until."""
    now = utc_now()
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


# ---------- on-demand Jikan backfill ----------
async def fetch_and_store(mal_id: int) -> dict[str, Any] | None:
    """Взять тайтл из каталога Jikan и записать в кеш. None — такого тайтла в MAL нет (404); каталог недоступен —
    HTTPException 503, ответил не тем — 502."""
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
    return store_title(payload)


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
