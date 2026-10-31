"""
Жалобы «Не работает» из плеера: причины, статусы и общие помощники — для раздела /reports и панели Anime Social.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Жалобу шлёт плеер (core/api/sections/reports.py), очередь разбирает раздел «AsoPlay» в /cpanel Anime Social
(core/admin/reports.py). Причины — в порядке окна жалобы. Жалуются вошедшие, как на сайте соцсети: одинаковая
открытая жалоба того же человека за сутки не плодит новую строку, а сообщений не больше LIMIT в час.
"""
from __future__ import annotations

import re
from typing import Any

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


def clean(value: str, limit: int) -> str:
    """Строка без управляющих символов и лишних пробелов, не длиннее limit."""
    return " ".join(_CONTROL.sub(" ", value).split())[:limit]


def title_name(cur: Any, mal_id: int) -> str:
    """Название тайтла для жалобы из кеша страниц — русское, иначе английское; нет в кеше — пусто."""
    cur.execute("SELECT title_ru, title_en FROM aviev_title_pages WHERE mal_id = %s", (mal_id,))
    row = cur.fetchone()
    return clean(str(row[0] or row[1] or ""), 255) if row else ""
