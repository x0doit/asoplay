# -*- coding: utf-8 -*-
"""
AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Админ-API для раздела «AsoPlay» в /cpanel Anime Social (задача движку, пункт 10).

Панель — единственный клиент: она ходит на http://127.0.0.1:8787/api/admin/… с этой же машины и подписывает
каждый запрос общим секретом AV_ADMIN_SECRET (он же — в настройках раздела на стороне движка):

    X-AsoPlay-Admin-User: id администратора Anime Social — для журнала действий
    X-AsoPlay-Admin: "{ts}.{hex}",
        hex = HMAC-SHA256(secret, ts + "\\n" + METHOD + "\\n" + path_with_query + "\\n" + admin_user + "\\n" + body)

path_with_query — путь с запросом ровно так, как он ушёл (уже закодированный), body — сырое тело ("" у GET).
id администратора входит в подпись (в задаче движку его не было): иначе кто угодно на пути запроса мог бы записать
действие в журнал на другого. Подпись старше MAX_AGE секунд отклоняется, повтор изменяющего запроса — тоже.
Через публичный прокси (есть X-Forwarded-For или X-Real-IP) API не отвечает вовсе. Ответы — как в API v2:
{ok: true, data} или {ok: false, error: {code, message}}. Кто из администраторов что видит (admin — всё,
chief_moderator и moderator — жалобы и тайтлы), решает панель.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from server import jikan_catalog, proxies, site_settings, source_health, title_pages, video_reports
from server.animesocial import connect
from server.title_pages import SITE_URL

log = logging.getLogger("asoplay.admin")

router = APIRouter(prefix="/api/admin")

MAX_AGE = 60
PAGE = 50
MAL_MAX = 10_000_000
# Панель ждёт ответа до 8 секунд: каталог спрашиваем не дольше CATALOG_WAIT, а запрос к нему досчитывается в фоне
# (и кладёт тайтл в кеш к следующему разу)
CATALOG_WAIT = 5.0
_LOOPBACK = ("127.0.0.1", "::1")
# Когда запущен процесс — ставит main.py при старте
STARTED_AT = time.time()


class AdminError(Exception):
    """Ответ панели с ошибкой: {ok: false, error: {code, message}}. message панель показывает под своим текстом:
    коротко, по-русски и без секретов."""

    def __init__(self, status: int, code: str, message: str = ""):
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = message or code


def error_response(_request: Request, exc: AdminError) -> JSONResponse:
    return JSONResponse({"ok": False, "error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}


def _secret() -> bytes:
    return os.environ.get("AV_ADMIN_SECRET", "").strip().encode("utf-8")


def enabled() -> bool:
    return bool(_secret())


def sign(secret: bytes, ts: str, method: str, path_with_query: str, admin_user: str, body: bytes) -> str:
    message = f"{ts}\n{method.upper()}\n{path_with_query}\n{admin_user}\n".encode("utf-8") + body
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


class _Seen:
    """Подписи изменяющих запросов за последние 2·MAX_AGE секунд: повтор перехваченного запроса не пройдёт.
    Чтение повторять можно — панель вправе открыть одну страницу дважды за секунду."""

    def __init__(self) -> None:
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def first(self, signature: str, now: float) -> bool:
        with self._lock:
            for key in [k for k, until in self._until.items() if until < now]:
                del self._until[key]
            if signature in self._until:
                return False
            self._until[signature] = now + 2 * MAX_AGE
            return True


_seen = _Seen()


async def admin_user(request: Request) -> int:
    """Зависимость каждого метода: проверить подпись панели и вернуть id администратора."""
    host = request.client.host if request.client else ""
    if host not in _LOOPBACK or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
        raise AdminError(404, "not_found", "Не найдено")
    secret = _secret()
    if not secret:
        raise AdminError(503, "not_configured", "AV_ADMIN_SECRET не задан в .env AsoPlay")
    ts, _, given = request.headers.get("x-asoplay-admin", "").partition(".")
    if not ts.isdigit() or len(given) != 64:
        raise AdminError(401, "bad_signature", "Нет подписи X-AsoPlay-Admin")
    now = time.time()
    if abs(now - int(ts)) > MAX_AGE:
        raise AdminError(401, "expired", f"Подпись старше {MAX_AGE} секунд")
    user = request.headers.get("x-asoplay-admin-user", "")
    if not user.isdigit() or not 0 < int(user) < 2**32:
        raise AdminError(400, "bad_admin_user", "Нет X-AsoPlay-Admin-User")
    path = request.scope.get("raw_path", b"").decode("latin-1") or request.url.path
    query = request.scope.get("query_string", b"").decode("latin-1")
    expected = sign(secret, ts, request.method, path + (f"?{query}" if query else ""), user, await request.body())
    if not hmac.compare_digest(expected, given.lower()):
        raise AdminError(401, "bad_signature", "Подпись не сходится")
    if request.method != "GET" and not _seen.first(expected, now):
        raise AdminError(401, "replayed", "Этот запрос уже был")
    return int(user)


# ---------- журнал действий ----------
def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def unix(value: datetime | None) -> int | None:
    return int(value.replace(tzinfo=timezone.utc).timestamp()) if value else None


def audit(cur: Any, user_id: int, action: str, target: str, details: dict[str, Any] | None = None) -> None:
    """Записать действие в той же транзакции, что и само изменение. Не влезает в 2000 символов — вместо
    обрезанного (и уже не JSON) — что именно меняли."""
    text = json.dumps(details or {}, ensure_ascii=False)
    if len(text) > 2000:
        text = json.dumps({"changed": sorted(details or {})}, ensure_ascii=False)[:2000]
    cur.execute(
        "INSERT INTO aviev_admin_audit (user_id, action, target, details, at) VALUES (%s, %s, %s, %s, %s)",
        (user_id, action, target[:128], text, _now()),
    )


def _after(cursor: str) -> int:
    """Курсор страницы — id последней строки прошлой страницы."""
    if not cursor:
        return 0
    if not cursor.isdigit():
        raise AdminError(422, "validation_failed", "Неверный курсор")
    return int(cursor)


def _page(items: list[dict[str, Any]], key: str = "id") -> dict[str, Any]:
    more = len(items) > PAGE
    items = items[:PAGE]
    return {"items": items, "next_cursor": str(items[-1][key]) if more else None}


# ---------- жалобы на видео ----------
_REPORT_COLUMNS = ("id, mal_id, title, episode, dub, source, player, kind, text, user_id, created_at, status, note, "
                   "updated_by, updated_at")


def _report(row: tuple) -> dict[str, Any]:
    (rid, mal_id, title, episode, dub, source, player, kind, text, user_id, created, status, note,
     updated_by, updated) = row
    return {
        "id": int(rid), "mal_id": int(mal_id), "title": title, "episode": episode, "dub": dub, "source": source,
        "source_name": site_settings.SOURCE_NAMES.get(source, source), "player": player,
        "kind": kind, "kind_title": video_reports.KINDS.get(kind, kind), "text": text, "user_id": int(user_id),
        "created": unix(created), "status": status, "note": note,
        "updated_by": int(updated_by) if updated_by else None, "updated_at": unix(updated),
        "url": f"{SITE_URL}/anime/{int(mal_id)}",
    }


@router.get("/reports")
def reports_list(status: str = "open", cursor: str = "", _admin: int = Depends(admin_user)) -> dict[str, Any]:
    if status not in video_reports.STATUSES:
        raise AdminError(422, "validation_failed", "Статус — open, fixed или rejected")
    after = _after(cursor)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_REPORT_COLUMNS} FROM aviev_reports WHERE status = %s"
            + (" AND id < %s" if after else "") + " ORDER BY id DESC LIMIT %s",
            (status, after, PAGE + 1) if after else (status, PAGE + 1),
        )
        rows = cur.fetchall()
        cur.execute("SELECT status, COUNT(*) FROM aviev_reports GROUP BY status")
        counts = {s: 0 for s in video_reports.STATUSES} | {str(s): int(n) for s, n in cur.fetchall()}
    return ok(_page([_report(r) for r in rows]) | {"counts": counts})


class ReportPatch(BaseModel):
    status: str
    note: str = Field(default="", max_length=1000)


@router.patch("/reports/{report_id}")
def reports_patch(report_id: int, body: ReportPatch, admin: int = Depends(admin_user)) -> dict[str, Any]:
    if body.status not in video_reports.STATUSES:
        raise AdminError(422, "validation_failed", "Статус — open, fixed или rejected")
    note = video_reports.clean(body.note, 1000)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM aviev_reports WHERE id = %s FOR UPDATE", (report_id,))
        found = cur.fetchone()
        if not found:
            raise AdminError(404, "not_found", "Такой жалобы нет")
        cur.execute("UPDATE aviev_reports SET status = %s, note = %s, updated_by = %s, updated_at = %s WHERE id = %s",
                    (body.status, note, admin, _now(), report_id))
        # Действия называются так, как их показывает панель: report.fixed | report.rejected | report.open
        audit(cur, admin, f"report.{body.status}", f"report:{report_id}", {"from": found[0], "note": note})
        conn.commit()
        cur.execute(f"SELECT {_REPORT_COLUMNS} FROM aviev_reports WHERE id = %s", (report_id,))
        row = cur.fetchone()
    return ok(_report(row))


# ---------- журнал ----------
@router.get("/audit")
def audit_list(cursor: str = "", _admin: int = Depends(admin_user)) -> dict[str, Any]:
    after = _after(cursor)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, user_id, action, target, details, at FROM aviev_admin_audit"
            + (" WHERE id < %s" if after else "") + " ORDER BY id DESC LIMIT %s",
            (after, PAGE + 1) if after else (PAGE + 1,),
        )
        rows = cur.fetchall()
    items = [{"id": int(i), "user_id": int(u), "action": a, "target": t, "details": site_settings.parse_json(d) or {},
              "at": unix(at)} for i, u, a, t, d, at in rows]
    return ok(_page(items))


# ---------- настройки сайта ----------
class NoticeIn(BaseModel):
    text: str = Field(default="", max_length=site_settings.NOTICE_MAX)
    until: int | None = Field(default=None, ge=0)


class FeaturesIn(BaseModel):
    comments: bool | None = None
    password_login: bool | None = None
    as_login: bool | None = None


class SourceIn(BaseModel):
    id: str = Field(min_length=1, max_length=32)
    enabled: bool = True
    order: int = Field(default=0, ge=0, le=1000)


class SettingsIn(BaseModel):
    notice: NoticeIn | None = None
    features: FeaturesIn | None = None
    sources: list[SourceIn] | None = Field(default=None, max_length=50)


def _settings_view() -> dict[str, Any]:
    from server import as_login
    current = site_settings.state.settings
    return {
        "notice": dict(current.notice),
        "features": dict(current.features),
        "sources": site_settings.sources_view(),
        # Что можно включить: вход через Anime Social — только при ключах клиента в .env AsoPlay,
        # комментариев на AsoPlay пока нет
        "available": {"as_login": as_login.enabled(), "password_login": True, "comments": False},
    }


@router.get("/settings")
def settings_get(_admin: int = Depends(admin_user)) -> dict[str, Any]:
    return ok(_settings_view())


def _sources_key(sources: Any) -> list[tuple[str, bool, int]]:
    return sorted((s["id"], bool(s.get("enabled", True)), int(s.get("order", 0))) for s in sources)


@router.put("/settings")
def settings_put(body: SettingsIn, admin: int = Depends(admin_user)) -> dict[str, Any]:
    """Панель присылает настройки целиком; сохраняется, объявляется вкладкам и пишется в журнал только то,
    что изменилось."""
    current = site_settings.state.settings
    changes: dict[str, Any] = {}
    if body.notice is not None:
        notice = {"text": video_reports.clean(body.notice.text, site_settings.NOTICE_MAX), "until": body.notice.until or None}
        if notice != {"text": current.notice.get("text") or "", "until": current.notice.get("until")}:
            changes["notice"] = notice
    if body.features is not None:
        features = dict(current.features) | {k: v for k, v in body.features.model_dump().items() if v is not None}
        if not features["password_login"] and not features["as_login"]:
            raise AdminError(422, "no_sign_in_left", "Нельзя выключить и вход по почте, и вход через Anime Social")
        if features != dict(current.features):
            changes["features"] = features
    if body.sources is not None:
        known = {s["id"] for s in site_settings.sources_view()}
        unknown = sorted({s.id for s in body.sources} - known)
        if unknown:
            raise AdminError(422, "unknown_source", "Нет таких источников: " + ", ".join(unknown))
        sources = [s.model_dump() for s in body.sources]
        if _sources_key(sources) != _sources_key(site_settings.sources_view()):
            changes["sources"] = sources
    if not changes:
        return ok(_settings_view())
    now = _now()
    with connect() as conn:
        cur = conn.cursor()
        for name, value in changes.items():
            cur.execute(
                """INSERT INTO aviev_site_settings (name, value, updated_by, updated_at) VALUES (%s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE value = VALUES(value), updated_by = VALUES(updated_by), updated_at = VALUES(updated_at)""",
                (name, json.dumps(value, ensure_ascii=False), admin, now),
            )
        # Как показывает панель: settings.notice | settings.features | settings.sources
        for name, value in changes.items():
            audit(cur, admin, f"settings.{name}", f"settings:{name}", {name: value})
        conn.commit()
    site_settings.state.load()
    site_settings.changed(set(changes))
    return ok(_settings_view())


# ---------- тайтлы ----------
def _title_item(mal_id: int, name: str, flags: tuple | None) -> dict[str, Any]:
    hidden, pinned, adult, note, updated_by, updated_at = flags or (0, 0, 0, "", None, None)
    return {
        "mal_id": int(mal_id), "title": name or "", "hidden": bool(hidden), "pinned": bool(pinned),
        "adult": bool(adult), "note": note or "", "updated_by": int(updated_by) if updated_by else None,
        "updated_at": unix(updated_at), "url": f"{SITE_URL}/anime/{int(mal_id)}",
    }


def _in(ids: list[int]) -> str:
    return ", ".join(["%s"] * len(ids))


def _names_for(cur: Any, ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    cur.execute(f"SELECT mal_id, title_ru, title_en FROM aviev_title_pages WHERE mal_id IN ({_in(ids)})", ids)
    return {int(m): (ru or en or "") for m, ru, en in cur.fetchall()}


def _flags_for(ids: list[int]) -> dict[int, tuple]:
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
            (PAGE + 1, offset),
        )
        return [_title_item(r[0], r[1], tuple(r[2:])) for r in cur.fetchall()]


def _cached_matches(q: str) -> dict[int, str]:
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT mal_id, COALESCE(NULLIF(title_ru, ''), title_en, '') FROM aviev_title_pages
                WHERE title_ru LIKE CONCAT('%%', %s, '%%') OR title_en LIKE CONCAT('%%', %s, '%%')
                   OR title_jp LIKE CONCAT('%%', %s, '%%')
                ORDER BY cached_at DESC LIMIT %s""",
            (q, q, q, PAGE),
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
    q = " ".join(q.split())[:200]
    if not q:
        offset = _after(cursor)
        items = await asyncio.to_thread(_flagged, offset)
        return ok({"items": items[:PAGE], "next_cursor": str(offset + PAGE) if len(items) > PAGE else None})
    found: dict[int, str] = {}
    mal_id = _query_id(q)
    if mal_id:
        name = await _title_name(mal_id)
        if name is not None:
            found[mal_id] = name
    else:
        found.update(await asyncio.to_thread(_cached_matches, q))
        if len(found) < PAGE and len(q) >= 3:
            try:
                for row in await _catalog(jikan_catalog.search_titles(q, PAGE)):
                    if isinstance(row.get("mal_id"), int) and row["mal_id"] not in found:
                        found[row["mal_id"]] = row.get("title_english") or row.get("title") or ""
            except (HTTPException, asyncio.TimeoutError):
                pass  # каталог недоступен или не успел — хватит найденного у нас
    ids = list(found)[:PAGE]
    flags = await asyncio.to_thread(_flags_for, ids)
    return ok({"items": [_title_item(m, found[m], flags.get(m)) for m in ids], "next_cursor": None})


class TitleIn(BaseModel):
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
    removed = jikan_catalog.forget_title(mal_id) + int(proxies.forget_shiki_title(mal_id))
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


# ---------- обзор ----------
# Какие источники загрузились в этом процессе — ставит main.py
loaded_sources: Any = lambda: []


@lru_cache(maxsize=1)
def _version() -> str:
    """Версия сборки — та же, что в адресах файлов сайта (index.html, app.js?v=…)."""
    try:
        text = (Path(__file__).resolve().parent.parent / "index.html").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    found = re.search(r"app\.js\?v=([\w.-]+)", text)
    return found.group(1) if found else "unknown"


def _summary() -> dict[str, Any]:
    now = _now()
    day, week, month = now - timedelta(days=1), now - timedelta(days=7), now - timedelta(days=30)
    loaded = set(loaded_sources())
    sources = []
    for source in site_settings.sources_view():
        window = source_health.window(source["id"])
        sources.append({
            "id": source["id"], "name": source["name"], "enabled": source["enabled"],
            "ok": source["id"] in loaded and source_health.is_available(source["id"]),
            "fail_rate_24h": window["fail_rate_24h"], "latency_ms": window["latency_ms"],
            "requests_24h": window["requests_24h"],
        })
    with connect() as conn:
        cur = conn.cursor()
        # «Активен» — была открыта вкладка AsoPlay (сессия отмечается при каждом запросе и раз в 5 минут)
        cur.execute(
            """SELECT COUNT(DISTINCT user_id),
                      COUNT(DISTINCT CASE WHEN last_seen_at >= %s THEN user_id END),
                      COUNT(DISTINCT CASE WHEN last_seen_at >= %s THEN user_id END),
                      COUNT(DISTINCT CASE WHEN last_seen_at >= %s THEN user_id END)
                 FROM aviev_sessions""",
            (day, week, month),
        )
        total, active_1d, active_7d, active_30d = (int(x or 0) for x in cur.fetchone())
        cur.execute("""SELECT COUNT(*) FROM (SELECT user_id FROM aviev_sessions GROUP BY user_id
                                             HAVING MIN(created_at) >= %s) AS fresh""", (week,))
        new_7d = int(cur.fetchone()[0])
        # Серия засчитана, когда её смотрели дольше 10 секунд (watch_continue)
        cur.execute("""SELECT COUNT(DISTINCT user_id, mal_id, meta) FROM aviev_activity
                        WHERE day >= %s AND kind = 'watch_continue' AND at >= %s""", (day.date(), day))
        episodes_1d = int(cur.fetchone()[0])
        cur.execute("SELECT COALESCE(SUM(seconds), 0) FROM aviev_watch_time WHERE hour > %s", (day,))
        minutes_1d = int(cur.fetchone()[0]) // 60
        cur.execute(
            """SELECT mal_id, COUNT(DISTINCT user_id) AS viewers FROM aviev_activity
                WHERE day >= %s AND kind IN ('watch_start', 'watch_continue') AND mal_id IS NOT NULL
                GROUP BY mal_id ORDER BY viewers DESC, mal_id LIMIT 10""",
            (week.date(),),
        )
        top = [(int(m), int(v)) for m, v in cur.fetchall()]
        names = _names_for(cur, [m for m, _ in top])
        cur.execute("SELECT COUNT(*) FROM aviev_reports WHERE status = 'open'")
        reports_open = int(cur.fetchone()[0])
    from server import realtime
    return {
        "version": _version(),
        "started_at": int(STARTED_AT),
        "health": {"ok": any(s["enabled"] and s["ok"] for s in sources), "sources": sources},
        "users": {"total": total, "active_1d": active_1d, "active_7d": active_7d, "active_30d": active_30d,
                  "new_7d": new_7d},
        "watch": {"episodes_1d": episodes_1d, "minutes_1d": minutes_1d},
        "top_titles_7d": [{"mal_id": m, "title": names.get(m, ""), "viewers": v, "hidden": site_settings.hidden(m)}
                          for m, v in top],
        "online": {"users": len(realtime.hub.by_user), "tabs": len(realtime.hub.conns)},
        "reports_open": reports_open,
    }


@router.get("/summary")
def summary(_admin: int = Depends(admin_user)) -> dict[str, Any]:
    return ok(_summary())
