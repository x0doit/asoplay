"""
Временный аккаунт проверки: создаётся прямо в базе на прогон и удаляется после.

Автор: Чепела Даниил Максимович.

Проверки не действуют от имени настоящих людей (правило движка Anime Social): только этот аккаунт или
несуществующий id. id выдаёт сама база, как при обычной регистрации: явный id сдвинул бы счётчик AUTO_INCREMENT
таблицы Just_accounts, а удаление строки его не откатывает. Всё удаляется по выданному id, аккаунт — ещё и по alias;
alias, занятый не временным аккаунтом проверки, останавливает проверку. Подключение к базе — из кода приложения.
"""
from __future__ import annotations

import logging
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HANDLE = "asoplay-guard"  # alias временного аккаунта
_EMAIL, _NAME = f"{HANDLE}@invalid.test", ("Проверка", "AsoPlay")
USER_TABLES = ("aviev_user_lists", "aviev_watch_history", "aviev_episode_progress", "aviev_activity",
               "aviev_title_ratings", "aviev_dub_prefs", "aviev_account_settings", "aviev_privacy", "aviev_import_marks",
               "aviev_sessions")


class TempAccount:
    """Аккаунт с данными AsoPlay из seed — список (SQL, строки без user_id) — и сессией; with отдаёт токен сессии."""

    def __init__(self, seed: list[tuple[str, list[tuple]]]):
        """seed: INSERT с первым параметром user_id и строки остальных параметров."""
        self.seed = seed

    def __enter__(self) -> str:
        """Создать аккаунт, его данные и сессию; вернуть токен сессии."""
        sys.path.insert(0, str(ROOT))  # только для инструмента: подключение к базе берём из кода приложения
        logging.disable(logging.CRITICAL)
        from core.classes.db import connect
        self.connect = connect
        self.token = secrets.token_hex(32)
        now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
        with connect() as conn:
            cur = conn.cursor()
            self._remove_leftover(cur)
            cur.execute("INSERT INTO Just_accounts (username, email, password, name, lastname, alias, banned) "
                        "VALUES (%s, %s, '!', %s, %s, %s, 0)", (HANDLE, _EMAIL, *_NAME, HANDLE))
            self.user_id = int(cur.lastrowid)
            for sql, rows in self.seed:
                cur.executemany(sql, [(self.user_id, *r) for r in rows])
            cur.execute("INSERT INTO aviev_sessions (token, user_id, created_at, last_seen_at, user_agent, ip, revoked) "
                        "VALUES (%s, %s, %s, %s, %s, %s, 0)", (self.token, self.user_id, now, now, HANDLE, "127.0.0.1"))
            conn.commit()
        return self.token

    def _remove_leftover(self, cur) -> None:
        """Остаток прогона, который упал, — только наш: тот же alias, email и имя. Чужой alias — остановка."""
        cur.execute("SELECT user_id, email, name, lastname FROM Just_accounts WHERE alias = %s", (HANDLE,))
        for user_id, email, name, lastname in cur.fetchall():
            if (email, name, lastname) != (_EMAIL, *_NAME):
                raise SystemExit(f"alias {HANDLE} занят не временным аккаунтом проверки (user_id {user_id}) — остановка")
            self._clean(cur, int(user_id))

    @staticmethod
    def _clean(cur, user_id: int) -> None:
        """Удалить всё, что принадлежит временному аккаунту user_id."""
        for table in USER_TABLES:
            cur.execute(f"DELETE FROM `{table}` WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM Just_accounts WHERE user_id = %s AND alias = %s", (user_id, HANDLE))

    def __exit__(self, *exc) -> None:
        """Удалить аккаунт со всеми данными и проверить это свежим соединением."""
        with self.connect() as conn:  # connect() не фиксирует сам: commit обязателен
            self._clean(conn.cursor(), self.user_id)
            conn.commit()
        with self.connect() as conn:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM Just_accounts WHERE user_id = %s OR alias = %s", (self.user_id, HANDLE))
            left = cur.fetchone()[0]
            for table in USER_TABLES:
                cur.execute(f"SELECT COUNT(*) FROM `{table}` WHERE user_id = %s", (self.user_id,))
                left += cur.fetchone()[0]
        if left:
            raise SystemExit(f"временный аккаунт {self.user_id} удалён не полностью: {left} строк")
