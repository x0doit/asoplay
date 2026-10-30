# -*- coding: utf-8 -*-
"""
AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Жалобы «Не работает» из плеера — очередь для раздела «AsoPlay» в /cpanel Anime Social (core/admin/router.py).

  GET  /reports/kinds  → причины для окна жалобы
  POST /reports        → {mal_id, episode, dub, source, kind, text} — жалоба на то, что сейчас в плеере

Жалуются вошедшие, как на сайте соцсети. Одинаковая открытая жалоба того же человека за сутки не плодит
новую строку, а сообщений не больше LIMIT в час.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.functions import rate_limit
from core.classes.db import connect, current_user_required

log = logging.getLogger("asoplay.reports")

router = APIRouter(prefix="/reports")

# Порядок — как в окне жалобы. Первая причина выбрана заранее.
KINDS = {
    "no_video": "Видео не запускается",
    "wrong_episode": "Не та серия",
    "no_sound": "Нет звука или он не совпадает с видео",
    "bad_quality": "Плохое качество",
    "wrong_dub": "Не та озвучка",
    "other": "Другое",
}
STATUSES = ("open", "fixed", "rejected")
TEXT_MAX = 300
LIMIT = 10
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")
_SOURCE = re.compile(r"^[a-z0-9_-]{1,32}$")
# Плеер — имя его хоста: kodikplayer.com, alloha.tv…
_PLAYER = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
_limiter = rate_limit.SlidingWindow(LIMIT, 3600)


class ReportIn(BaseModel):
    mal_id: int = Field(gt=0, lt=10_000_000)
    episode: int | None = Field(default=None, ge=1, le=65535)
    dub: str = Field(default="", max_length=200)
    source: str = Field(default="", max_length=64)
    player: str = Field(default="", max_length=100)
    kind: str
    text: str = Field(default="", max_length=2000)


def clean(value: str, limit: int) -> str:
    return " ".join(_CONTROL.sub(" ", value).split())[:limit]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def title_name(cur: Any, mal_id: int) -> str:
    cur.execute("SELECT title_ru, title_en FROM aviev_title_pages WHERE mal_id = %s", (mal_id,))
    row = cur.fetchone()
    return clean(str(row[0] or row[1] or ""), 255) if row else ""


@router.get("/kinds")
def report_kinds() -> dict[str, Any]:
    return {"kinds": [{"code": code, "title": title} for code, title in KINDS.items()], "text_max": TEXT_MAX}


@router.post("")
def report_create(body: ReportIn, user: dict[str, Any] = Depends(current_user_required)) -> dict[str, Any]:
    if body.kind not in KINDS:
        raise HTTPException(422, "kind")
    source, player = body.source.strip().lower(), body.player.strip().lower()
    if (source and not _SOURCE.match(source)) or (player and not _PLAYER.match(player)):
        raise HTTPException(422, "source")
    uid = int(user["id"])
    dub, text = clean(body.dub, 128), clean(body.text, TEXT_MAX)
    now = _now()
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT id FROM aviev_reports
               WHERE user_id = %s AND mal_id = %s AND episode <=> %s AND source = %s AND kind = %s
                     AND status = 'open' AND created_at > %s
               ORDER BY id DESC LIMIT 1""",
            (uid, body.mal_id, body.episode, source, body.kind, now - timedelta(days=1)),
        )
        same = cur.fetchone()
        if same:
            if text:
                cur.execute("UPDATE aviev_reports SET text = %s, dub = %s, updated_at = %s WHERE id = %s",
                            (text, dub, now, same[0]))
                conn.commit()
            return {"ok": True, "id": int(same[0]), "repeated": True}
        wait = _limiter.retry_after(str(uid))
        if wait:
            raise HTTPException(429, "rate_limited", headers={"Retry-After": str(wait)})
        title = title_name(cur, body.mal_id)
        cur.execute(
            """INSERT INTO aviev_reports
                   (user_id, mal_id, title, episode, dub, source, player, kind, text, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (uid, body.mal_id, title, body.episode, dub, source, player, body.kind, text, now, now),
        )
        report_id = int(cur.lastrowid)
        conn.commit()
    _limiter.hit(str(uid))
    return {"ok": True, "id": report_id, "repeated": False}
