"""
Присутствие на AsoPlay: кто сейчас на сайте по его вкладкам и что он смотрит.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Кто на сайте, сообщают вкладки по живому соединению (core/functions/realtime.py): человек на AsoPlay, пока
хоть одна его вкладка открыта и на экране, и ещё GRACE секунд после — перезагрузка страницы не мигает «вышел».
«Смотрит» — у той видимой вкладки, где плеер заиграл последним; показывается друзьям только с разрешения
(core/functions/privacy.py). Всё в памяти процесса: бэкенд работает одним процессом, а после перезапуска вкладки
переподключаются сами. Пакет для соцсети — presence_batch.py, друзья — friends.py.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from core.classes import db
from core.classes.db import connect
from core.functions.privacy import Privacy

# Сколько секунд человек ещё на AsoPlay после того, как закрыл или свернул последнюю вкладку
GRACE = 30
_TITLE_MAX = 100
MAL_MAX = 10_000_000
# Управляющие и невидимые символы: в названии они ломали бы строку «смотрит «…»» у друзей
_CONTROL = re.compile(r"[\x00-\x1f\x7f\U0000200b-\U0000200f\U00002028\U00002029\U0000feff]+")


def clean_title(value: Any) -> str:
    """Название для «смотрит «…»»: без управляющих и невидимых знаков, пробелы сжаты, не длиннее _TITLE_MAX."""
    return " ".join(_CONTROL.sub(" ", value if isinstance(value, str) else "").split())[:_TITLE_MAX]


# ---------- кто сейчас на сайте ----------
@dataclass(frozen=True)
class Mark:
    """Отметка «человек здесь»: когда и, если играет плеер, что смотрит."""
    at: float
    mal_id: int = 0
    title: str = ""


@dataclass(frozen=True)
class Tab:
    """Вкладка человека: на экране ли, что в ней играет и когда она отметилась."""
    visible: bool
    mal_id: int
    title: str
    at: float


class Presence:
    """Вкладки людей. Вкладка на экране держит человека на AsoPlay; когда последняя закрылась или ушла
    в фон, он остаётся ещё grace секунд с тем, что смотрел. «Смотрит» — у той видимой вкладки, где
    плеер заиграл последним."""

    def __init__(self, grace: float = GRACE, clock: Callable[[], float] = time.monotonic):
        """grace — сколько секунд человек ещё здесь после последней вкладки; clock — часы (в проверках — свои)."""
        self.grace = grace
        self._clock = clock
        self._tabs: dict[int, dict[int, Tab]] = {}
        self._left: dict[int, Mark] = {}
        self._lock = threading.Lock()

    def tab(self, user_id: int, tab_id: int, visible: bool, mal_id: int = 0, title: str = "") -> None:
        """Вкладка открылась или сообщила, что изменилось: на экране ли и что играет."""
        with self._lock:
            was = self._here_locked(user_id)
            self._tabs.setdefault(user_id, {})[tab_id] = Tab(visible, mal_id, title, self._clock())
            self._settle_locked(user_id, was)

    def close(self, user_id: int, tab_id: int) -> None:
        """Вкладка закрылась; последняя — человек остаётся ещё grace секунд с тем, что смотрел."""
        with self._lock:
            was = self._here_locked(user_id)
            tabs = self._tabs.get(user_id, {})
            tabs.pop(tab_id, None)
            if not tabs:
                self._tabs.pop(user_id, None)
            self._settle_locked(user_id, was)

    def _here_locked(self, user_id: int) -> Mark | None:
        """Отметка по вкладкам на экране: «смотрит» — у той, где плеер заиграл последним; нет таких — None."""
        shown = [t for t in self._tabs.get(user_id, {}).values() if t.visible]
        if not shown:
            return None
        playing = max((t for t in shown if t.mal_id), key=lambda t: t.at, default=None)
        return Mark(self._clock(), playing.mal_id, playing.title) if playing else Mark(self._clock())

    def _settle_locked(self, user_id: int, was: Mark | None) -> None:
        """Человек ушёл со всех вкладок — запомнить, что он смотрел, на grace секунд."""
        if self._here_locked(user_id):
            self._left.pop(user_id, None)
        elif was:
            self._left[user_id] = Mark(self._clock(), was.mal_id, was.title)

    def current(self) -> dict[int, Mark]:
        """Кто сейчас на AsoPlay: отметки по вкладкам на экране и тех, кто ушёл меньше grace секунд назад."""
        now = self._clock()
        with self._lock:
            for uid in [uid for uid, m in self._left.items() if now - m.at > self.grace]:
                del self._left[uid]
            out = dict(self._left)
            for uid in self._tabs:
                mark = self._here_locked(uid)
                if mark:
                    out[uid] = mark
            return out


presence = Presence()


# ---------- что смотрят ----------
def title_cards(mal_ids: Iterable[int]) -> dict[int, tuple[str, str]]:
    """Название (русское, иначе английское) и постер из кеша страниц тайтлов. Без строки — название из
    браузера и без постера."""
    out: dict[int, tuple[str, str]] = {}
    chunks = list(db.chunks(m for m in mal_ids if m))
    if not chunks:
        return out
    with connect() as conn:
        cur = conn.cursor()
        for chunk in chunks:
            cur.execute(
                f"""SELECT mal_id, title_ru, title_en, poster_url FROM aviev_title_pages
                    WHERE mal_id IN ({', '.join(['%s'] * len(chunk))})""",
                chunk,
            )
            for mal_id, title_ru, title_en, poster_url in cur.fetchall():
                title = clean_title(title_ru or title_en or "")
                poster = str(poster_url or "") if str(poster_url or "").startswith(("https://", "http://")) else ""
                if title or poster:
                    out[int(mal_id)] = (title, poster)
    return out


def watching(mark: Mark | None, privacy: Privacy, titles: dict[int, tuple[str, str]]) -> dict[str, Any] | None:
    """Что показать друзьям из «смотрит»: только с разрешения, название — из кеша страниц или из вкладки."""
    if mark is None or not mark.mal_id or privacy.hide_presence or not privacy.show_watching:
        return None
    title, poster = titles.get(mark.mal_id, ("", ""))
    title = title or mark.title
    return {"mal_id": mark.mal_id, "title": title, "poster": poster, "path": f"/anime/{mark.mal_id}"} if title else None
