"""
Общее для частей раздела событий соцсети: поддельная база, которая записывает SQL, и поддельное соединение
сервер–сервер.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from core.functions import as_events

AT = datetime(2026, 10, 31, 9, 30)


def flat(sql: str) -> str:
    """SQL одной строкой — для сравнения."""
    return " ".join(sql.split())


class FakeCursor:
    """Курсор: запросы — в журнал соединения, ответ на каждый — от answer(sql, params) → (строки, rowcount)."""

    def __init__(self, conn: "FakeConn"):
        self.conn = conn
        self._rows: list[tuple] = []
        self.rowcount = 0

    def execute(self, sql: str, params: Any = ()) -> int:
        self.conn.sql.append((flat(sql), tuple(params or ())))
        self._rows, self.rowcount = self.conn.answer(flat(sql), tuple(params or ()))
        return self.rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class FakeConn:
    """Соединение: журнал SQL, число commit и rollback; годится и как контекст connect()."""

    def __init__(self, answer: Callable[[str, tuple], tuple[list[tuple], int]] | None = None):
        self.sql: list[tuple[str, tuple]] = []
        self.commits = 0
        self.rollbacks = 0
        self.answer = answer or (lambda sql, params: ([], 1))

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def __enter__(self) -> "FakeConn":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def statements(self, start: str) -> list[tuple[str, tuple]]:
        """Запросы, которые начинаются с start."""
        return [(sql, params) for sql, params in self.sql if sql.startswith(start)]


def linked_answer(linked: set[int], progress: dict[tuple, tuple] | None = None):
    """Ответ базы: живая сессия соцсети — у linked; прогресс серий до записи — progress[(user, mal, ep)]."""
    def answer(sql: str, params: tuple) -> tuple[list[tuple], int]:
        if "FROM `aviev_as_sessions`" in sql:
            return ([(1,)] if params[0] in linked else []), 1
        if sql.startswith("SELECT seconds, duration FROM aviev_episode_progress"):
            row = (progress or {}).get(params)
            return ([row] if row else []), 1
        return [], 1
    return answer


def row(id_: int, kind: str = "episode", *, user: int = 20, mal: int = 52991, episode: int = 6,
        score: int | None = None, at: datetime = AT) -> as_events.Row:
    """Строка очереди."""
    return as_events.Row(id_, user, kind, mal, episode if kind == "episode" else 0, score, at)


class FakeLink:
    """Соединение сервер–сервер: кадры — в sent; answer() — ответ соцсети отправителю."""

    def __init__(self):
        self.ready = True
        self.generation = 1
        self.sent: list[dict[str, Any]] = []
        self.on_events: Callable[[dict[str, Any]], None] | None = None

    def send(self, frame: dict[str, Any]) -> bool:
        if not self.ready:
            return False
        self.sent.append(frame)
        return True

    def answer(self, frame: dict[str, Any]) -> None:
        self.on_events(frame)

    def reconnect(self) -> None:
        """Обрыв и новое соединение."""
        self.ready = False
        self.on_events({"type": "closed", "generation": self.generation})
        self.generation += 1
        self.ready = True
        self.on_events({"type": "ready", "generation": self.generation})
