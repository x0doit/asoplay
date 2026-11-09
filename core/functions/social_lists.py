"""
Блок «Аниме» в профиле соцсети (clients.md, раздел 17): что человек смотрит на AsoPlay — «Смотрю», «В планах»,
«Просмотрено». Числа — целиком, записи — до PER_STATUS последних в статусе; «Брошено», «Отложено» и избранное не входят.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Адреса — только на сайт AsoPlay (SITE_URL): тайтл, списки (/@handle/lists — профиль сразу у списков), настройки и
постер — через свой /proxy/img, и только с хоста, который /proxy/img примет; иначе постера нет. Название — текст без
HTML, до TITLE_MAX знаков. Скрыл человек списки — {"hidden": true, "settings_url"} без чисел и записей. Отвечает
GET /api/social/lists/{user_id} (core/admin/social.py); об изменениях соцсеть узнаёт сигналом
(core/functions/lists_signal.py).
"""
from __future__ import annotations

import calendar
import html
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from core.classes.accounts import resolve_handle
from core.classes.db import connect
from core.config.config import SITE_URL
from core.functions import image_hosts, site_settings
from core.functions.privacy import read_privacy

STATUSES = ("watching", "planned", "completed")
PER_STATUS = 20
TITLE_MAX = 200
_TAG = re.compile(r"<[^>]*>")


def _ts(at: datetime | None) -> int | None:
    """Время UTC из базы — секунды Unix."""
    return calendar.timegm(at.timetuple()) if at else None


def _title(raw: str | None) -> str:
    """Название обычным текстом: без тегов и сущностей HTML, пробелы схлопнуты, не длиннее TITLE_MAX."""
    return " ".join(html.unescape(_TAG.sub(" ", raw or "")).split())[:TITLE_MAX]


def _poster(url: str | None) -> str | None:
    """Постер через /proxy/img сайта; адрес не картинки или хост, которого /proxy/img не возьмёт, — без постера."""
    if not url or image_hosts.url_refusal(url, site_settings.img_hosts()) is not None:
        return None
    return f"{SITE_URL}/proxy/img?url={quote(url, safe='')}"


def _lists_url(user_id: int) -> str:
    """Все списки человека на AsoPlay: профиль /@alias (без него — /@profile{id}), сразу у списков."""
    person = resolve_handle(f"profile{user_id}")
    return f"{SITE_URL}/@{person['handle'] if person else f'profile{user_id}'}/lists"


def _rows(cur, user_id: int) -> tuple[dict[str, int], dict[str, list[tuple]], dict[int, int], dict[int, str]]:
    """Числа по статусам, записи до PER_STATUS в каждом, серии «Смотрю» и запасные названия из кеша страниц."""
    cur.execute("""SELECT status, COUNT(*) FROM aviev_user_lists
                    WHERE user_id=%s AND status IN (%s, %s, %s) GROUP BY status""", (user_id, *STATUSES))
    counts = {status: 0 for status in STATUSES} | {str(s): int(n) for s, n in cur.fetchall()}
    rows: dict[str, list[tuple]] = {}
    for status in STATUSES:
        cur.execute("""SELECT mal_id, title, poster_url, updated_at FROM aviev_user_lists
                        WHERE user_id=%s AND status=%s ORDER BY updated_at DESC, mal_id DESC LIMIT %s""",
                    (user_id, status, PER_STATUS))
        rows[status] = list(cur.fetchall())
    episodes: dict[int, int] = {}
    watching = [int(r[0]) for r in rows["watching"]]
    if watching:
        cur.execute(f"""SELECT mal_id, last_episode FROM aviev_watch_history
                         WHERE user_id=%s AND mal_id IN ({','.join(['%s'] * len(watching))})""", (user_id, *watching))
        episodes = {int(m): int(e) for m, e in cur.fetchall() if e}
    untitled = sorted({int(r[0]) for found in rows.values() for r in found if not _title(r[1])})
    names: dict[int, str] = {}
    if untitled:
        cur.execute(f"""SELECT mal_id, title_ru, title_en FROM aviev_title_pages
                         WHERE mal_id IN ({','.join(['%s'] * len(untitled))})""", tuple(untitled))
        names = {int(m): _title(ru) or _title(en) for m, ru, en in cur.fetchall()}
    return counts, rows, episodes, names


def block(user_id: int) -> dict[str, Any]:
    """Ответ блока для человека user_id (id соцсети и AsoPlay один)."""
    if read_privacy(user_id)["hide_lists"]:
        return {"hidden": True, "settings_url": f"{SITE_URL}/my/settings"}
    with connect() as conn:
        counts, rows, episodes, names = _rows(conn.cursor(), user_id)
    items: dict[str, list[dict[str, Any]]] = {}
    for status in STATUSES:
        items[status] = [{
            "mal_id": int(mal_id),
            "title": _title(title) or names.get(int(mal_id), ""),
            "poster_url": _poster(poster),
            "url": f"{SITE_URL}/anime/{int(mal_id)}",
            "episode": episodes.get(int(mal_id)) if status == "watching" else None,
            "updated_at": _ts(updated_at),
        } for mal_id, title, poster, updated_at in rows[status]]
    latest = max((r[3] for found in rows.values() for r in found if r[3]), default=None)
    return {"hidden": False, "counts": counts, "items": items, "lists_url": _lists_url(user_id),
            "updated_at": _ts(latest)}
