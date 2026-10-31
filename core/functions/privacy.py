"""
Приватность человека: что из его жизни на AsoPlay видно другим.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

hide_lists и hide_activity — прятать списки и дневник в публичном профиле; hide_presence — не показывать, что
человек на AsoPlay (друзьям здесь и в Anime Social); show_watching — показывать друзьям, что он смотрит
(core/functions/presence.py). Всё по умолчанию выключено. Меняет настройки раздел /account/privacy.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from core.classes import db
from core.classes.db import connect

PRIVACY_FIELDS = ("hide_lists", "hide_activity", "hide_presence", "show_watching")


def read_privacy(user_id: int) -> dict[str, bool]:
    """Доступ без auth-gate — для рендера публичного профиля."""
    with connect() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT {', '.join(PRIVACY_FIELDS)} FROM aviev_privacy WHERE user_id=%s",
            (user_id,),
        )
        row = cur.fetchone()
    return {f: bool(row[i]) if row else False for i, f in enumerate(PRIVACY_FIELDS)}


@dataclass(frozen=True)
class Privacy:
    """Что человек разрешил: не показывать присутствие, показывать друзьям «смотрит», прятать списки."""
    hide_presence: bool = False
    show_watching: bool = False
    hide_lists: bool = False


OPEN = Privacy()


def for_users(user_ids: Iterable[int]) -> dict[int, Privacy]:
    """Настройки людей; у кого строки нет — всё по умолчанию (OPEN)."""
    out: dict[int, Privacy] = {}
    chunks = list(db.chunks(user_ids))
    if not chunks:
        return out
    with connect() as conn:
        cur = conn.cursor()
        for chunk in chunks:
            cur.execute(
                f"""SELECT user_id, hide_presence, show_watching, hide_lists FROM aviev_privacy
                    WHERE user_id IN ({', '.join(['%s'] * len(chunk))})""",
                chunk,
            )
            for uid, hide_presence, show_watching, hide_lists in cur.fetchall():
                out[int(uid)] = Privacy(bool(hide_presence), bool(show_watching), bool(hide_lists))
    return out
