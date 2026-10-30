# -*- coding: utf-8 -*-
"""
AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
All rights reserved. See /COPYRIGHT for full terms.

То, что администраторы Anime Social решают в разделе «AsoPlay» панели (core/admin/router.py), — для всего сайта:

  - пометки тайтлов (aviev_title_flags): hidden — скрыт по жалобе правообладателя, pinned — закреплён в карусели
    главной, adult — 18+;
  - настройки (aviev_site_settings): notice — плашка на сайте, features — какие функции включены,
    sources — какие источники видео включены и в каком порядке.

Сайт спрашивает это на каждой странице, поэтому всё держится в памяти процесса: читается из базы при старте и
меняется вместе с базой в момент правки из панели (бэкенд работает одним процессом). Вкладкам состояние уходит
вместе с состоянием сервера по живому соединению (core/functions/realtime.py) — правка из панели видна сразу.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from core.classes.db import connect

log = logging.getLogger("asoplay.site_settings")

NOTICE_MAX = 500

# Источники видео, которые перебирает плеер, и порядок по умолчанию — тот, что был зашит в app.js.
# animevost сюда не входит: плееру он не отдаёт озвучек.
SOURCES = (
    ("yummy_anime", "YummyAnime"),
    ("oldyummy", "OldYummy"),
    ("animego", "AnimeGO"),
    ("yummy_anime_org", "YummyAnime.org"),
    ("dreamcast", "DreamCast"),
    ("anilibria", "AniLibria"),
    ("anilibme", "AniLib"),
    ("sameband", "SameBand"),
)
SOURCE_NAMES = dict(SOURCES)


@dataclass(frozen=True)
class Flags:
    hidden: frozenset[int] = frozenset()
    pinned: tuple[int, ...] = ()           # свежезакреплённые первыми
    adult: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Settings:
    notice: dict[str, Any] = field(default_factory=lambda: {"text": "", "until": None})
    features: dict[str, bool] = field(default_factory=lambda: {"comments": True, "password_login": True, "as_login": True})
    sources: tuple[dict[str, Any], ...] = ()


class _State:
    def __init__(self) -> None:
        self.flags = Flags()
        self.settings = Settings()
        self._lock = threading.Lock()

    def load(self) -> None:
        """Прочитать всё из базы. Нет таблиц (pass-5 не применён) или базы — всё по умолчанию."""
        try:
            with connect() as conn:
                cur = conn.cursor()
                cur.execute("SELECT mal_id, hidden, pinned, adult FROM aviev_title_flags ORDER BY updated_at DESC, mal_id DESC")
                rows = cur.fetchall()
                cur.execute("SELECT name, value FROM aviev_site_settings")
                stored = {name: parse_json(value) for name, value in cur.fetchall()}
        except Exception:  # noqa: BLE001 — без базы сайт работает с настройками по умолчанию
            log.exception("Could not read the panel settings; using defaults")
            return
        defaults = Settings()
        notice = stored.get("notice") if isinstance(stored.get("notice"), dict) else {}
        features = stored.get("features") if isinstance(stored.get("features"), dict) else {}
        sources = stored.get("sources") if isinstance(stored.get("sources"), list) else []
        with self._lock:
            self.flags = Flags(
                hidden=frozenset(int(r[0]) for r in rows if r[1]),
                pinned=tuple(int(r[0]) for r in rows if r[2]),
                adult=frozenset(int(r[0]) for r in rows if r[3]),
            )
            self.settings = Settings(
                notice={"text": str(notice.get("text") or "")[:NOTICE_MAX], "until": _int_or_none(notice.get("until"))},
                features={k: bool(features.get(k, v)) for k, v in defaults.features.items()},
                sources=tuple(s for s in sources if isinstance(s, dict) and s.get("id") in SOURCE_NAMES),
            )


def parse_json(value: Any) -> Any:
    try:
        return json.loads(value or "null")
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else None


state = _State()

# Кого позвать, когда из панели что-то поменяли (core/functions/realtime.py рассылает вкладкам)
listeners: list[Callable[[set[str]], None]] = []


def changed(what: set[str]) -> None:
    for listener in list(listeners):
        try:
            listener(what)
        except Exception:  # noqa: BLE001
            log.exception("A panel settings listener failed")


# ---------- для остального сайта ----------
def hidden(mal_id: Any) -> bool:
    return mal_id in state.flags.hidden


def adult(mal_id: Any) -> bool:
    return mal_id in state.flags.adult


def pinned() -> tuple[int, ...]:
    return state.flags.pinned


def visible(items: list[Any], key: str = "mal_id") -> list[Any]:
    """Список тайтлов без скрытых по жалобе правообладателя. Элемент — словарь с id тайтла в MAL."""
    hidden_ids = state.flags.hidden
    if not hidden_ids:
        return items
    return [item for item in items if not (isinstance(item, dict) and item.get(key) in hidden_ids)]


def notice() -> dict[str, Any] | None:
    """Плашка для сайта, пока она действует."""
    current = state.settings.notice
    text, until = current.get("text") or "", current.get("until")
    if not text or (until and until <= time.time()):
        return None
    return {"text": text, "until": until}


def feature(name: str) -> bool:
    return bool(state.settings.features.get(name, True))


def sources_view() -> list[dict[str, Any]]:
    """Все источники плеера с тем, что про них решила панель: включён ли и какое место в порядке."""
    stored = {s["id"]: s for s in state.settings.sources}
    out = []
    for index, (sid, name) in enumerate(SOURCES):
        item = stored.get(sid, {})
        order = item.get("order")
        out.append({"id": sid, "name": name, "enabled": bool(item.get("enabled", True)),
                    "order": order if isinstance(order, int) and not isinstance(order, bool) else index + 1})
    out.sort(key=lambda s: (s["order"], [x[0] for x in SOURCES].index(s["id"])))
    return out


def source_order() -> list[str]:
    """Включённые источники в том порядке, в каком их перебирает плеер."""
    return [s["id"] for s in sources_view() if s["enabled"]]


def source_enabled(source: str) -> bool:
    if source not in SOURCE_NAMES:
        return True
    return source in source_order()


def public_state() -> dict[str, Any]:
    """Что нужно каждой странице: плашка, скрытые, 18+ и закреплённые тайтлы, способы входа, порядок источников."""
    from core.functions import social_login
    flags = state.flags
    return {
        "notice": notice(),
        "hidden": sorted(flags.hidden),
        "adult": sorted(flags.adult),
        "pinned": list(flags.pinned),
        "login": {"social": social_login.enabled() and feature("as_login"), "password": feature("password_login")},
        "sources": source_order(),
    }
