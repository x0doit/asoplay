"""
Админ-API: тайтлы — поиск, пометки модераторов (скрыт, закреплён, 18+) и сброс кеша.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/admin: вход router.py подключает части по порядку.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.admin import auth
from core.admin.audit import _now, audit, unix
from core.admin.auth import AdminError, _after, admin_user, ok
from core.classes import http
from core.classes.db import connect
from core.classes.jikan import catalog as jikan_catalog
from core.config.config import SITE_URL
from core.functions import site_settings, video_reports
from core.modules import title_pages

router = APIRouter()

MAL_MAX = 10_000_000
# Панель ждёт ответа до 8 секунд: каталог спрашиваем не дольше CATALOG_WAIT, а запрос к нему досчитывается в фоне
# (и кладёт тайтл в кеш к следующему разу)
CATALOG_WAIT = 5.0


def _title_item(mal_id: int, name: str, flags: tuple | None) -> dict[str, Any]:
    """Тайтл для панели: название, пометки, кто и когда их менял, ссылка на страницу."""
    hidden, pinned, adult, note, updated_by, updated_at = flags or (0, 0, 0, "", None, None)
    return {
        "mal_id": int(mal_id), "title": name or "", "hidden": bool(hidden), "pinned": bool(pinned),
        "adult": bool(adult), "note": note or "", "updated_by": int(updated_by) if updated_by else None,
        "updated_at": unix(updated_at), "url": f"{SITE_URL}/anime/{int(mal_id)}",
    }


def _in(ids: list[int]) -> str:
    """Заполнители %s для SQL «IN (…)» по числу id."""
    return ", ".join(["%s"] * len(ids))


def _names_for(cur: Any, ids: list[int]) -> dict[int, str]:
    """Названия тайтлов из кеша страниц: русское, иначе английское."""
    if not ids:
        return {}
    cur.execute(f"SELECT mal_id, title_ru, title_en FROM aviev_title_pages WHERE mal_id IN ({_in(ids)})", ids)
    return {int(m): (ru or en or "") for m, ru, en in cur.fetchall()}


def _flags_for(ids: list[int]) -> dict[int, tuple]:
    """Пометки модераторов для этих тайтлов (у кого их нет — тех в ответе нет)."""
    if not ids:
        return {}
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(f"""SELECT mal_id, hidden, pinned, adult, note, updated_by, updated_at FROM aviev_title_flags
                         WHERE mal_id IN ({_in(ids)})""", ids)
        return {int(r[0]): tuple(r[1:]) for r in cur.fetchall()}


def _flagged(offset: int) -> list[dict[str, Any]]:
    """Тайтлы, которых касались модераторы, — свежие правки первыми."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT f.mal_id, COALESCE(NULLIF(p.title_ru, ''), p.title_en, ''),
                      f.hidden, f.pinned, f.adult, f.note, f.updated_by, f.updated_at
                 FROM aviev_title_flags f LEFT JOIN aviev_title_pages p ON p.mal_id = f.mal_id
                ORDER BY f.updated_at DESC, f.mal_id DESC LIMIT %s OFFSET %s""",
            (auth.PAGE + 1, offset),
        )
        return [_title_item(r[0], r[1], tuple(r[2:])) for r in cur.fetchall()]


def _cached_matches(q: str) -> dict[int, str]:
    """Тайтлы из кеша страниц, в названии которых есть q, — свежие первыми."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT mal_id, COALESCE(NULLIF(title_ru, ''), title_en, '') FROM aviev_title_pages
                WHERE title_ru LIKE CONCAT('%%', %s, '%%') OR title_en LIKE CONCAT('%%', %s, '%%')
                   OR title_jp LIKE CONCAT('%%', %s, '%%')
                ORDER BY cached_at DESC LIMIT %s""",
            (q, q, q, auth.PAGE),
        )
        return {int(m): name for m, name in cur.fetchall()}


def _query_id(q: str) -> int | None:
    """Поиск по номеру в MAL или по ссылке на страницу AsoPlay: …/anime/1535-death-note."""
    if q.isdigit():
        return int(q) if 0 < int(q) < MAL_MAX else None
    found = re.search(r"/anime/(\d{1,7})(?:\D|$)", q)
    return int(found.group(1)) if found else None


async def _catalog(coro: Any) -> Any:
    """Ответ каталога, если он успел за CATALOG_WAIT; иначе TimeoutError, а запрос досчитывается в фоне."""
    task = asyncio.ensure_future(coro)
    task.add_done_callback(lambda done: done.cancelled() or done.exception())
    return await asyncio.wait_for(asyncio.shield(task), CATALOG_WAIT)


async def _title_name(mal_id: int) -> str | None:
    """Название тайтла; None — такого тайтла в MAL нет. Каталог недоступен или не успел — пустое название:
    пометить тайтл (например, скрыть по жалобе) можно и тогда."""
    try:
        info = await _catalog(title_pages.lookup(mal_id))
    except (HTTPException, asyncio.TimeoutError):
        return ""
    if info is None:
        return None
    return info.get("title_ru") or info.get("title_en") or ""


@router.get("/titles")
async def titles_list(q: str = "", cursor: str = "", _admin: int = Depends(admin_user)) -> dict[str, Any]:
    """GET /api/admin/titles — без q тайтлы с пометками; с q — поиск по названию, номеру MAL или ссылке."""
    q = " ".join(q.split())[:200]
    if not q:
        offset = _after(cursor)
        items = await asyncio.to_thread(_flagged, offset)
        return ok({"items": items[:auth.PAGE], "next_cursor": str(offset + auth.PAGE) if len(items) > auth.PAGE else None})
    found: dict[int, str] = {}
    mal_id = _query_id(q)
    if mal_id:
        name = await _title_name(mal_id)
        if name is not None:
            found[mal_id] = name
    else:
        found.update(await asyncio.to_thread(_cached_matches, q))
        if len(found) < auth.PAGE and len(q) >= 3:
            try:
                for row in await _catalog(jikan_catalog.search_titles(q, auth.PAGE)):
                    if isinstance(row.get("mal_id"), int) and row["mal_id"] not in found:
                        found[row["mal_id"]] = row.get("title_english") or row.get("title") or ""
            except (HTTPException, asyncio.TimeoutError):
                pass  # каталог недоступен или не успел — хватит найденного у нас
    ids = list(found)[:auth.PAGE]
    flags = await asyncio.to_thread(_flags_for, ids)
    return ok({"items": [_title_item(m, found[m], flags.get(m)) for m in ids], "next_cursor": None})


class TitleIn(BaseModel):
    """Пометки тайтла от панели: скрыт, закреплён, 18+ и заметка; пустое поле — не менять."""

    hidden: bool | None = None
    pinned: bool | None = None
    adult: bool | None = None
    note: str | None = Field(default=None, max_length=1000)


def _save_title(mal_id: int, changes: dict[str, Any], admin: int, name: str) -> tuple[dict[str, Any], bool]:
    """Панель присылает все четыре поля; сохраняется и пишется в журнал только изменившееся. Второе значение —
    поменялось ли что-нибудь."""
    now = _now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("""SELECT hidden, pinned, adult, note, updated_by, updated_at FROM aviev_title_flags
                        WHERE mal_id = %s FOR UPDATE""", (mal_id,))
        row = cur.fetchone()
        current = {"hidden": False, "pinned": False, "adult": False, "note": ""}
        if row:
            current = {"hidden": bool(row[0]), "pinned": bool(row[1]), "adult": bool(row[2]), "note": row[3] or ""}
        changed = {key: value for key, value in changes.items() if value != current[key]}
        if not changed:
            flags = (current["hidden"], current["pinned"], current["adult"], current["note"],
                     row[4] if row else None, row[5] if row else None)
            return _title_item(mal_id, name, flags), False
        merged = current | changed
        cur.execute(
            """INSERT INTO aviev_title_flags (mal_id, hidden, pinned, adult, note, updated_by, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON DUPLICATE KEY UPDATE hidden = VALUES(hidden), pinned = VALUES(pinned), adult = VALUES(adult),
                   note = VALUES(note), updated_by = VALUES(updated_by), updated_at = VALUES(updated_at)""",
            (mal_id, int(bool(merged["hidden"])), int(bool(merged["pinned"])), int(bool(merged["adult"])),
             merged["note"], admin, now),
        )
        audit(cur, admin, "title.update", f"title:{mal_id}", changed)
        conn.commit()
    return _title_item(mal_id, name, (merged["hidden"], merged["pinned"], merged["adult"], merged["note"], admin, now)), True


@router.put("/titles/{mal_id}")
async def titles_put(mal_id: int, body: TitleIn, admin: int = Depends(admin_user)) -> dict[str, Any]:
    """PUT /api/admin/titles/{mal_id} — сохранить пометки тайтла; изменение сразу уходит вкладкам."""
    if not 0 < mal_id < MAL_MAX:
        raise AdminError(422, "validation_failed", "Неверный номер MAL")
    changes = {key: value for key, value in body.model_dump().items() if value is not None}
    if "note" in changes:
        changes["note"] = video_reports.clean(changes["note"], 1000)
    name = await _title_name(mal_id)
    if name is None:
        raise AdminError(404, "title_not_found", "Такого тайтла в MyAnimeList нет")
    item, saved = await asyncio.to_thread(_save_title, mal_id, changes, admin, name)
    if saved:
        await asyncio.to_thread(site_settings.state.load)
        site_settings.changed({"titles"})
    return ok(item)


def _purge(mal_id: int, admin: int) -> dict[str, Any]:
    """Сбросить кеш тайтла везде, где он есть, и записать это в журнал."""
    removed = jikan_catalog.forget_title(mal_id) + int(http.forget_shiki_title(mal_id))
    title_pages.forget(mal_id)
    with connect() as conn:
        cur = conn.cursor()
        audit(cur, admin, "title.purge", f"title:{mal_id}", {"removed": removed})
        conn.commit()
    return {"mal_id": mal_id, "removed": removed}


@router.post("/titles/{mal_id}/purge")
def titles_purge(mal_id: int, admin: int = Depends(admin_user)) -> dict[str, Any]:
    """Сбросить кеш тайтла: карточку и серии из каталога, Shikimori и страницу тайтла. Серии и плееры
    источники отдают без кеша — их сбрасывать не нужно."""
    if not 0 < mal_id < MAL_MAX:
        raise AdminError(422, "validation_failed", "Неверный номер MAL")
    return ok(_purge(mal_id, admin))
