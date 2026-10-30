"""
Приватность человека: что из его жизни на AsoPlay видно другим.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

hide_lists и hide_activity — прятать списки и дневник в публичном профиле; hide_presence — не показывать, что
человек на AsoPlay (друзьям здесь и в Anime Social); show_watching — показывать друзьям, что он смотрит
(core/functions/presence.py). Всё по умолчанию выключено. Меняет настройки раздел /account/privacy.
"""
from __future__ import annotations

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
