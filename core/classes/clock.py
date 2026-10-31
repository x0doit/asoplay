"""
Время для базы: UTC без пояса — так время хранится во всех таблицах AsoPlay.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Одна функция на весь код вместо прежних пяти _now и прямых datetime.utcnow(): utcnow устарел с Python 3.12
и в 3.13 предупреждает при каждом вызове, а значение у замены то же самое.
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Сейчас по UTC без пояса, с микросекундами — то же значение, что давал datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
