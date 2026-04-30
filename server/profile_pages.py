# -*- coding: utf-8 -*-
"""
AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

Публичный профиль /@handle — и JSON-хендлы, и серверный рендер HTML для
краулеров. Handle = alias из Just_accounts (например edward), либо
profile{user_id} если alias пуст.

Приватность:
- если hide_lists → списки не попадают в HTML и JSON public-view,
- если hide_activity → блок активности тоже не попадает.
Никакого CSS-скрытия: данные просто не отдаются.
"""
from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import HTMLResponse, Response

from server.animesocial import (
    connect, current_user_optional, resolve_handle, _serialize_user,
)
from server.account_api import read_privacy
from server.activity_log import graph_for_user_public, fetch_recent_paged
from server.title_pages import _read_shell, _override_head, SITE_URL, SITE_NAME, AUTHOR_NAME

log = logging.getLogger("animeviev.profile_pages")

router = APIRouter()


STATUSES = ("watching", "planned", "completed", "dropped", "postponed")


# ---------- public JSON ----------
def _counts_for(user_id: int) -> dict[str, int]:
    out = {s: 0 for s in STATUSES}
    out["favorite"] = 0
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            """SELECT status, COUNT(*) FROM aviev_user_lists
                WHERE user_id=%s AND status IS NOT NULL GROUP BY status""",
            (user_id,),
        )
        for status, n in cur.fetchall():
            if status in out:
                out[status] = int(n)
        cur.execute(
            "SELECT COUNT(*) FROM aviev_user_lists WHERE user_id=%s AND is_favorite=1",
            (user_id,),
        )
        out["favorite"] = int(cur.fetchone()[0] or 0)
    return out


def _items_for(user_id: int, status: str | None) -> list[dict[str, Any]]:
    with connect() as conn:
        cur = conn.cursor()
        if status == "favorite":
            cur.execute(
                """SELECT mal_id, status, status_source, is_favorite,
                          title, poster_url, updated_at
                    FROM aviev_user_lists
                    WHERE user_id=%s AND is_favorite=1
                    ORDER BY updated_at DESC LIMIT 200""",
                (user_id,),
            )
        elif status in STATUSES:
            cur.execute(
                """SELECT mal_id, status, status_source, is_favorite,
                          title, poster_url, updated_at
                    FROM aviev_user_lists
                    WHERE user_id=%s AND status=%s
                    ORDER BY updated_at DESC LIMIT 200""",
                (user_id, status),
            )
        else:
            return []
        rows = cur.fetchall()
    return [
        {
            "mal_id": int(r[0]),
            "status": r[1],
            "status_source": r[2],
            "is_favorite": bool(r[3]),
            "title": r[4] or "",
            "poster_url": r[5] or "",
            "updated_at": r[6].isoformat() if r[6] else None,
        }
        for r in rows
    ]


@router.get("/profile/{handle}/summary")
def profile_summary(
    handle: str,
    days: int = Query(default=30, ge=1, le=365),
    viewer: dict[str, Any] | None = Depends(current_user_optional),
) -> dict[str, Any]:
    target = resolve_handle(handle)
    if not target:
        raise HTTPException(404, "profile not found")
    is_owner = bool(viewer and int(viewer["id"]) == int(target["id"]))
    privacy = read_privacy(target["id"])
    out: dict[str, Any] = {
        "user": {k: target[k] for k in (
            "id", "handle", "profile_path", "name", "avatar", "avatar_small",
        )},
        "is_owner": is_owner,
        "privacy": privacy,
        "counts": None,
        "activity": None,
    }
    if not privacy["hide_lists"] or is_owner:
        out["counts"] = _counts_for(target["id"])
    if not privacy["hide_activity"] or is_owner:
        out["activity"] = graph_for_user_public(target["id"], days=days)
    return out


@router.get("/profile/{handle}/activity")
def profile_activity(
    handle: str,
    days: int = Query(default=30, ge=1, le=365),
    viewer: dict[str, Any] | None = Depends(current_user_optional),
) -> dict[str, Any]:
    target = resolve_handle(handle)
    if not target:
        raise HTTPException(404, "profile not found")
    is_owner = bool(viewer and int(viewer["id"]) == int(target["id"]))
    privacy = read_privacy(target["id"])
    if privacy["hide_activity"] and not is_owner:
        return {"hidden": True}
    return graph_for_user_public(target["id"], days=days)


@router.get("/profile/{handle}/activity/recent")
def profile_activity_recent(
    handle: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=10, ge=1, le=50),
    group: str = Query(default="all"),
    date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    viewer: dict[str, Any] | None = Depends(current_user_optional),
) -> dict[str, Any]:
    target = resolve_handle(handle)
    if not target:
        raise HTTPException(404, "profile not found")
    is_owner = bool(viewer and int(viewer["id"]) == int(target["id"]))
    privacy = read_privacy(target["id"])
    if privacy["hide_activity"] and not is_owner:
        return {"hidden": True, "items": [], "has_more": False}
    return fetch_recent_paged(target["id"], offset=offset, limit=limit,
                              group=group, day=date)


@router.get("/profile/{handle}/lists")
def profile_lists(
    handle: str,
    status: str = "watching",
    viewer: dict[str, Any] | None = Depends(current_user_optional),
) -> dict[str, Any]:
    target = resolve_handle(handle)
    if not target:
        raise HTTPException(404, "profile not found")
    is_owner = bool(viewer and int(viewer["id"]) == int(target["id"]))
    privacy = read_privacy(target["id"])
    if privacy["hide_lists"] and not is_owner:
        return {"hidden": True, "items": []}
    if status not in STATUSES and status != "favorite":
        raise HTTPException(400, "unknown status")
    return {"hidden": False, "items": _items_for(target["id"], status), "is_owner": is_owner}


# ---------- SSR ----------
def _render_shell(head_extra: str, noscript: str, *,
                  title: str | None = None, description: str | None = None,
                  robots: str | None = None) -> HTMLResponse:
    shell = _read_shell()
    shell = _override_head(shell, title=title, description=description, robots=robots)
    shell = shell.replace("<!--av:head-extra-->", head_extra, 1)
    shell = shell.replace("<!--av:preload-->", "", 1)
    shell = shell.replace("<!--av:noscript-->", noscript, 1)
    # no-cache: см. title_pages.py, та же причина — избегаем застревания
    # пользователя на старой cache-buster версии app.js/styles.css.
    return HTMLResponse(shell, headers={"Cache-Control": "no-cache"})


@router.get("/@{handle}", response_class=HTMLResponse)
@router.get("/@{handle}/", response_class=HTMLResponse)
def profile_page(handle: str):
    target = resolve_handle(handle)
    if not target:
        # не кидаем HTML-404 — пусть SPA шеллом покажет свою 404-страницу,
        # если handle в принципе не резолвится.
        raise HTTPException(404, "profile not found")

    privacy = read_privacy(target["id"])
    show_lists = not privacy["hide_lists"]
    show_activity = not privacy["hide_activity"]

    canonical = f"{SITE_URL}/@{target['handle']}"
    name = target["name"] or target["handle"]
    description = (
        f"Профиль {name} на {SITE_NAME} — списки аниме и активность пользователя."
        if show_lists or show_activity
        else f"Профиль {name} на {SITE_NAME} — пользователь ограничил публичную видимость."
    )

    jsonld = {
        "@context": "https://schema.org",
        "@type": "ProfilePage",
        "name": f"{name} — {SITE_NAME}",
        "url": canonical,
        "inLanguage": "ru",
        "mainEntity": {
            "@type": "Person",
            "name": name,
            "identifier": target["handle"],
            "image": target.get("avatar") or None,
        },
    }

    # Индексируем только «живые» профили: хотя бы один блок открыт И
    # у пользователя есть подтверждённое имя. Иначе no-index.
    indexable = (show_lists or show_activity) and bool(target.get("name"))
    robots = "index, follow" if indexable else "noindex, follow"

    page_title = f"{name} — {SITE_NAME}"
    head_extra = (
        f'<link rel="canonical" href="{html.escape(canonical)}">\n'
        f'<meta property="og:type" content="profile">\n'
        f'<meta property="og:site_name" content="{html.escape(SITE_NAME)}">\n'
        f'<meta property="og:title" content="{html.escape(name)}">\n'
        f'<meta property="og:description" content="{html.escape(description)}">\n'
        f'<meta property="og:url" content="{html.escape(canonical)}">\n'
        + (f'<meta property="og:image" content="{html.escape(target.get("avatar") or "")}">\n'
           if target.get("avatar") else "")
        + f'<script type="application/ld+json">{json.dumps(jsonld, ensure_ascii=False)}</script>\n'
    )

    noscript_parts = [f'<h1>{html.escape(name)}</h1>']
    if show_lists:
        counts = _counts_for(target["id"])
        bits = [f'{k}: {v}' for k, v in counts.items() if v]
        if bits:
            noscript_parts.append(f"<p>{' · '.join(html.escape(b) for b in bits)}</p>")
    else:
        noscript_parts.append("<p>Списки пользователя ограничены настройками приватности.</p>")
    if not show_activity:
        noscript_parts.append("<p>Активность скрыта настройками приватности.</p>")
    noscript_parts.append(f'<p><a href="{SITE_URL}">{html.escape(SITE_NAME)}</a></p>')

    return _render_shell(
        head_extra, "<noscript>" + "".join(noscript_parts) + "</noscript>",
        title=page_title, description=description, robots=robots,
    )
