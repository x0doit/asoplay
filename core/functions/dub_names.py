"""
Реестр имён озвучек: какие озвучки встречались в плеере — из него человек выбирает любимую в настройках.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Имена приходят из ответов /src/dubs (core/api/sections/sources/media.py): только озвучки, субтитры не пишутся. Они
копятся в памяти, и через WINDOW секунд после первого нового одной вставкой ложатся в aviev_dub_names: ключ — имя для
сравнения (source_filters.norm_dub_name), name — как его прислал источник последним, seen — сколько раз встречалось.
Имя — чужой текст: clean_name убирает управляющие и невидимые символы и обрезает до NAME_MAX, страница выводит его
только как текст. Строк не больше ROWS_MAX: после вставки лишние, самые редкие, удаляются.

Список для настроек — listing(): частые первыми, кеш на LIST_TTL секунд. Частые имена SEED в нём есть с первого дня:
полного списка озвучек без ключа нет ни у одного источника, и реестр наполняется по мере просмотра.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import unicodedata
from typing import Any, Iterable

from core.classes.clock import utc_now
from core.classes.db import connect
from core.functions import source_filters

log = logging.getLogger("asoplay.dubs")

WINDOW = 60.0
NAME_MAX = 200
NORM_MAX = 128
ROWS_MAX = 5000
PENDING_MAX = 1000  # новых имён между вставками: остальные придут со следующими сериями
LIST_TTL = 600.0
SEED = ("AniLibria", "AniDub", "AniMedia", "AnimeVost", "Dream Cast", "JAM", "SHIZA Project", "Studio Band")
_VOICE_PREFIX = re.compile(r"^озвучка\s+", re.IGNORECASE)

_pending: dict[str, list[Any]] = {}  # имя для сравнения → [имя, сколько раз встретилось]
_timer: threading.Timer | None = None
_listing: tuple[float, list[str]] = (0.0, [])
_lock = threading.Lock()


def clean_name(raw: Any) -> str:
    """Имя как текст: управляющие символы и разрывы строк — пробелом, невидимые (Cf) и обрывки суррогатов убраны,
    пробелы схлопнуты, не длиннее NAME_MAX."""
    text = "".join(" " if unicodedata.category(ch) in ("Cc", "Zl", "Zp") else ch for ch in str(raw or "")
                   if unicodedata.category(ch) not in ("Cf", "Cs"))
    return " ".join(text.split())[:NAME_MAX].strip()


def note(items: Iterable[Any]) -> None:
    """Запомнить озвучки из ответа /src/dubs; вставка — через WINDOW секунд после первого нового имени."""
    global _timer
    fresh = []
    for item in items or ():
        if not isinstance(item, dict) or item.get("kind") != "voice":
            continue
        name = _VOICE_PREFIX.sub("", clean_name(item.get("name")))
        norm = source_filters.norm_dub_name(name)[:NORM_MAX]
        if name and norm:
            fresh.append((norm, name))
    if not fresh:
        return
    with _lock:
        for norm, name in fresh:
            if norm in _pending:
                _pending[norm][0] = name
                _pending[norm][1] += 1
            elif len(_pending) < PENDING_MAX:
                _pending[norm] = [name, 1]
        if _timer is None:
            _timer = threading.Timer(WINDOW, flush)
            _timer.daemon = True
            _timer.start()


def flush() -> None:
    """Записать накопленное одной вставкой и убрать лишние строки — самые редкие; сбой — в журнал, имена придут снова."""
    global _timer
    with _lock:
        batch = list(_pending.items())
        _pending.clear()
        _timer = None
    if not batch:
        return
    now = utc_now()
    try:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO aviev_dub_names (norm, name, seen, last_seen) VALUES "
                + ", ".join(["(%s, %s, %s, %s)"] * len(batch))
                + " ON DUPLICATE KEY UPDATE name = VALUES(name), seen = seen + VALUES(seen), last_seen = VALUES(last_seen)",
                [value for norm, (name, count) in batch for value in (norm, name, count, now)],
            )
            cur.execute("SELECT COUNT(*) FROM aviev_dub_names")
            extra = int(cur.fetchone()[0]) - ROWS_MAX
            if extra > 0:
                cur.execute("DELETE FROM aviev_dub_names ORDER BY seen, last_seen LIMIT %s", (extra,))
            conn.commit()
    except Exception as exc:  # noqa: BLE001 — реестр не главное: те же имена придут со следующими сериями
        log.warning("Dub names not saved: %s", type(exc).__name__)


def listing() -> list[str]:
    """Имена озвучек для выбора любимой: частые первыми, затем частые из SEED, которых ещё нет; кеш на LIST_TTL.
    База не ответила — один SEED, без кеша: следующий запрос спросит её снова."""
    global _listing
    at, names = _listing
    if names and time.monotonic() - at < LIST_TTL:
        return names
    try:
        with connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT name FROM aviev_dub_names ORDER BY seen DESC, name LIMIT %s", (ROWS_MAX,))
            rows = cur.fetchall()
    except Exception as exc:  # noqa: BLE001 — без базы список из частых имён лучше пустого
        log.warning("Dub names not read: %s", type(exc).__name__)
        return list(SEED)
    names = [name for name in (clean_name(row[0]) for row in rows) if name]
    known = {source_filters.norm_dub_name(name) for name in names}
    names += [name for name in SEED if source_filters.norm_dub_name(name) not in known]
    _listing = (time.monotonic(), names)
    return names
