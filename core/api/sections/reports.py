"""
Раздел /reports: жалоба «Не работает» из плеера и причины для её окна.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

  GET  /reports/kinds  → причины для окна жалобы
  POST /reports        → {mal_id, episode, dub, source, kind, text} — жалоба на то, что сейчас в плеере

Жалуются вошедшие. Одинаковая открытая жалоба того же человека за сутки не плодит новую строку, а сообщений не больше
LIMIT в час (core/functions/video_reports.py). Очередь жалоб разбирает панель Anime Social (core/admin/reports.py).
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.classes.clock import utc_now
from core.classes.db import connect
from core.functions import rate_limit, video_reports
from core.functions.sessions import current_user_required

router = APIRouter(prefix="/reports")

_SOURCE = re.compile(r"^[a-z0-9_-]{1,32}$")
# Плеер — имя его хоста: kodikplayer.com, alloha.tv…
_PLAYER = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
_limiter = rate_limit.SlidingWindow(video_reports.LIMIT, 3600)


class ReportIn(BaseModel):
    """Жалоба из плеера: тайтл и серия, озвучка, источник и плеер, причина и текст."""

    mal_id: int = Field(gt=0, lt=10_000_000)
    episode: int | None = Field(default=None, ge=1, le=65535)
    dub: str = Field(default="", max_length=200)
    source: str = Field(default="", max_length=64)
    player: str = Field(default="", max_length=100)
    kind: str
    text: str = Field(default="", max_length=2000)


@router.get("/kinds")
def report_kinds() -> dict[str, Any]:
    """Причины для окна жалобы — в его порядке — и сколько знаков можно написать."""
    return {"kinds": [{"code": code, "title": title} for code, title in video_reports.KINDS.items()],
            "text_max": video_reports.TEXT_MAX}


@router.post("")
def report_create(body: ReportIn, user: dict[str, Any] = Depends(current_user_required)) -> dict[str, Any]:
    """Жалоба вошедшего. Такая же открытая за сутки — та же строка (новый текст — поверх); сверх лимита — 429."""
    if body.kind not in video_reports.KINDS:
        raise HTTPException(422, "kind")
    source, player = body.source.strip().lower(), body.player.strip().lower()
    if (source and not _SOURCE.match(source)) or (player and not _PLAYER.match(player)):
        raise HTTPException(422, "source")
    uid = int(user["id"])
    dub, text = video_reports.clean(body.dub, 128), video_reports.clean(body.text, video_reports.TEXT_MAX)
    now = utc_now()
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
        title = video_reports.title_name(cur, body.mal_id)
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
