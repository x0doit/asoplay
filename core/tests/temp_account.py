"""
Временный аккаунт проверки: создаётся прямо в базе на прогон и удаляется после.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Проверки не действуют от имени настоящих людей (правило движка Anime Social): только такой аккаунт или
несуществующий id. id выдаёт сама база, как при обычной регистрации: явный id сдвинул бы счётчик AUTO_INCREMENT
таблицы Just_accounts, а удаление строки его не откатывает. Всё удаляется по выданному id, аккаунт — ещё и по alias;
alias, занятый не временным аккаунтом проверки, останавливает проверку. Подключение к базе — из кода приложения.
Пользуются сторож ответов (tools/guard/responses.py) и проверки с базой (test_*_db.py).
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from core.classes.db import connect

HANDLE = "asoplay-guard"  # alias временного аккаунта по умолчанию
_NAME = ("Проверка", "AsoPlay")
USER_TABLES = ("aviev_user_lists", "aviev_watch_history", "aviev_episode_progress", "aviev_activity",
               "aviev_title_ratings", "aviev_dub_prefs", "aviev_account_settings", "aviev_privacy", "aviev_import_marks",
               "aviev_sessions")


class TempAccount:
    """Аккаунт с данными AsoPlay из seed и сессией; with отдаёт токен сессии, user_id — его id в базе."""

    def __init__(self, seed: list[tuple[str, list[tuple]]], handle: str = HANDLE):
        """seed: INSERT с первым параметром user_id и строки остальных параметров; handle — alias аккаунта."""
        self.seed = seed
        self.handle = handle
        self.email = f"{handle}@invalid.test"

    def __enter__(self) -> str:
        """Создать аккаунт, его данные и сессию; вернуть токен сессии."""
        self.token = secrets.token_hex(32)
        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        with connect() as conn:
            cur = conn.cursor()
            self._remove_leftover(cur)
            cur.execute("INSERT INTO Just_accounts (username, email, password, name, lastname, alias, banned) "
                        "VALUES (%s, %s, '!', %s, %s, %s, 0)", (self.handle, self.email, *_NAME, self.handle))
            self.user_id = int(cur.lastrowid)
            for sql, rows in self.seed:
                cur.executemany(sql, [(self.user_id, *r) for r in rows])
            cur.execute("INSERT INTO aviev_sessions (token, user_id, created_at, last_seen_at, user_agent, ip, revoked) "
                        "VALUES (%s, %s, %s, %s, %s, %s, 0)",
                        (self.token, self.user_id, now, now, self.handle, "127.0.0.1"))
            conn.commit()
        return self.token

    def _remove_leftover(self, cur) -> None:
        """Остаток прогона, который упал, — только наш: тот же alias, email и имя. Чужой alias — остановка."""
        cur.execute("SELECT user_id, email, name, lastname FROM Just_accounts WHERE alias = %s", (self.handle,))
        for user_id, email, name, lastname in cur.fetchall():
            if (email, name, lastname) != (self.email, *_NAME):
                raise SystemExit(f"alias {self.handle} занят не временным аккаунтом проверки (user_id {user_id})"
                                 " — остановка")
            self._clean(cur, int(user_id))

    def _clean(self, cur, user_id: int) -> None:
        """Удалить всё, что принадлежит временному аккаунту user_id."""
        for table in USER_TABLES:
            cur.execute(f"DELETE FROM `{table}` WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM Just_accounts WHERE user_id = %s AND alias = %s", (user_id, self.handle))

    def __exit__(self, *exc) -> None:
        """Удалить аккаунт со всеми данными и проверить это свежим соединением."""
        with connect() as conn:  # connect() не фиксирует сам: commit обязателен
            self._clean(conn.cursor(), self.user_id)
            conn.commit()
        with connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM Just_accounts WHERE user_id = %s OR alias = %s",
                        (self.user_id, self.handle))
            left = cur.fetchone()[0]
            for table in USER_TABLES:
                cur.execute(f"SELECT COUNT(*) FROM `{table}` WHERE user_id = %s", (self.user_id,))
                left += cur.fetchone()[0]
        if left:
            raise SystemExit(f"временный аккаунт {self.user_id} удалён не полностью: {left} строк")
