"""
Соцсеть и состояние сервера в живом соединении: события Anime Social людям AsoPlay, кадры «lists», status.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Соцсеть присылает события людям AsoPlay (core/classes/as_realtime.py): друг зашёл в соцсеть или вышел, изменилась
дружба, страницу заблокировали. Состояние сервера (status — то, что раньше вкладки спрашивали у /health раз в 30
секунд) приходит при подключении и при изменении; его даёт status_provider — связывает core.py. HubSocial — миксин
Hub (core/functions/realtime.py): своего состояния нет.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from core.functions import friends as friend_data, presence_batch, sessions

log = logging.getLogger("asoplay.realtime")

# Задаёт core/core.py: состояние сервера для вкладок
status_provider: Callable[[], dict[str, Any]] = lambda: {"ok": True, "sources": [], "vpn": False}


class HubSocial:
    """Соцсеть и состояние сервера: события Anime Social людям AsoPlay, кадры «lists» и status.

    Миксин Hub (core/functions/realtime.py): своего состояния нет. Берёт у Hub:
    - атрибуты из Hub.__init__: _loop, _status, by_user, conns, friends;
    - методы Hub: close, send;
    - метод HubPresence: set_friends."""
    async def social_snapshot(self) -> dict[str, Any]:
        """Снимок для соцсети при каждом подключении — то же, что запасной пакет: кто на AsoPlay, что смотрит."""
        return await asyncio.to_thread(presence_batch.build_batch)

    async def _to_people(self, user_ids: list[int], frame: dict[str, Any]) -> None:
        """Кадр во все вкладки этих людей."""
        for uid in user_ids:
            for conn_id in list(self.by_user.get(uid, ())):
                conn = self.conns.get(conn_id)
                if conn:
                    await self.send(conn, frame)

    async def on_social_event(self, name: str, data: dict[str, Any], to: list[int]) -> None:
        """Событие соцсети людям AsoPlay (clients.md, 9.2). Кому — решила соцсеть (`to`)."""
        if name == "presence":
            uid = data.get("user_id")
            if not isinstance(uid, int):
                return
            await self._to_people(to, {"type": "social_presence", "user_id": uid,
                                       "online_where": data.get("online_where"), "last_seen": data.get("last_seen")})
        elif name == "profile_friend":
            other, status = data.get("user_id"), data.get("status")
            if not isinstance(other, int) or status not in ("friends", "incoming", "outgoing", "none"):
                return
            for viewer in to:
                if viewer == other:
                    continue
                friend_data.forget_friends(viewer)
                if viewer not in self.by_user:
                    continue
                ids = set(self.friends.get(viewer, frozenset()))
                if status == "friends":
                    ids.add(other)
                else:
                    ids.discard(other)
                self.set_friends(viewer, frozenset(ids))
                await self._to_people([viewer], {"type": "friendship", "user_id": other, "status": status})
        elif name == "account_banned":
            uid = data.get("user_id")
            if not isinstance(uid, int):
                return
            try:
                await asyncio.to_thread(sessions.revoke_user_sessions, uid)
            except Exception:  # noqa: BLE001 — вкладки закроем всё равно; сессии закроет сверка
                log.exception("Could not revoke the sessions of a banned account")
            for conn_id in list(self.by_user.get(uid, ())):
                conn = self.conns.get(conn_id)
                if conn:
                    await self.close(conn, 4001)
        # notify и account_state — для значка соцсети в шапке, client_comment — для веток комментариев:
        # их у AsoPlay пока нет

    async def lists_changed(self, user_id: int, items: list[dict[str, Any]]) -> None:
        """Правило 30 дней перенесло тайтлы человека: кадр «lists» во все его вкладки."""
        await self._to_people([user_id], {"type": "lists", "items": items})

    def lists_changed_from_thread(self, user_id: int, items: list[dict[str, Any]]) -> None:
        """lists_changed из потока (правило 30 дней)."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.lists_changed(user_id, items), self._loop)

    def status(self) -> dict[str, Any]:
        """Состояние сервера для вкладок — от status_provider (его связывает core.py); сбой — «всё хорошо,
        источников нет»."""
        try:
            return status_provider()
        except Exception:  # noqa: BLE001
            log.exception("Could not read the server status for realtime")
            return {"ok": True, "sources": [], "vpn": False}

    async def broadcast_status(self) -> None:
        """Состояние сервера и решения панели Anime Social — всем вкладкам, если что-то поменялось."""
        status = self.status()
        if self._status is not None and status != self._status:
            for conn in list(self.conns.values()):
                await self.send(conn, {"type": "status", "status": status})
        self._status = status

    def status_changed_from_thread(self) -> None:
        """broadcast_status из обычного обработчика (поток)."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.broadcast_status(), self._loop)
