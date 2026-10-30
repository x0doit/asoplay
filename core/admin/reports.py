"""
Админ-API: жалобы «Не работает» — список по статусу и решение модератора.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть core/admin: вход router.py подключает части по порядку.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.admin import auth
from core.admin.audit import audit, unix
from core.admin.auth import AdminError, _after, _page, admin_user, ok
from core.classes.clock import utc_now
from core.classes.db import connect
from core.config.config import SITE_URL
from core.functions import site_settings, video_reports

router = APIRouter()

_REPORT_COLUMNS = ("id, mal_id, title, episode, dub, source, player, kind, text, user_id, created_at, status, note, "
                   "updated_by, updated_at")


def _report(row: tuple) -> dict[str, Any]:
    """Жалоба для панели: что было в плеере, кто и когда пожаловался, что решил модератор, ссылка на тайтл."""
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
    """GET /api/admin/reports — жалобы с этим статусом, свежие первыми, и сколько их в каждом статусе."""
    if status not in video_reports.STATUSES:
        raise AdminError(422, "validation_failed", "Статус — open, fixed или rejected")
    after = _after(cursor)
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {_REPORT_COLUMNS} FROM aviev_reports WHERE status = %s"
            + (" AND id < %s" if after else "") + " ORDER BY id DESC LIMIT %s",
            (status, after, auth.PAGE + 1) if after else (status, auth.PAGE + 1),
        )
        rows = cur.fetchall()
        cur.execute("SELECT status, COUNT(*) FROM aviev_reports GROUP BY status")
        counts = {s: 0 for s in video_reports.STATUSES} | {str(s): int(n) for s, n in cur.fetchall()}
    return ok(_page([_report(r) for r in rows]) | {"counts": counts})


class ReportPatch(BaseModel):
    """Решение модератора по жалобе: новый статус и заметка."""

    status: str
    note: str = Field(default="", max_length=1000)


@router.patch("/reports/{report_id}")
def reports_patch(report_id: int, body: ReportPatch, admin: int = Depends(admin_user)) -> dict[str, Any]:
    """PATCH /api/admin/reports/{id} — сменить статус жалобы и записать это в журнал."""
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
                    (body.status, note, admin, utc_now(), report_id))
        # Действия называются так, как их показывает панель: report.fixed | report.rejected | report.open
        audit(cur, admin, f"report.{body.status}", f"report:{report_id}", {"from": found[0], "note": note})
        conn.commit()
        cur.execute(f"SELECT {_REPORT_COLUMNS} FROM aviev_reports WHERE id = %s", (report_id,))
        row = cur.fetchone()
    return ok(_report(row))
