"""
Комментарии в живом соединении: вкладка подписывается на ветки, которые у неё на экране (тайтл и текущая серия), и
получает события этих веток от соцсети — новый, удалённый комментарий и «Нравится» (clients.md, 9.2).

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Часть Hub (core/functions/realtime.py). Соцсеть шлёт события всех веток AsoPlay без подписки; здесь они расходятся
только по вкладкам, подписанным на объект (индекс by_object), — без перебора всех соединений. Подписка есть и у гостей:
комментарии открыты всем. События без гарантии доставки: после переподключения вкладка подписывается снова и
перечитывает первую страницу.
"""
from __future__ import annotations

from typing import Any

from core.functions.comments import OBJECT

MAX_WATCH = 2  # тайтл и текущая серия
ACTIONS = {"added", "deleted", "liked"}


def _int_or_none(value: Any, top: int) -> bool:
    """Целое от 0 до top или None (bool не годится)."""
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= top)


class HubComments:
    """Подписки вкладок на ветки комментариев и рассылка событий соцсети по ним."""

    def watch_comments(self, conn: Any, objects: Any) -> None:
        """Кадр comments.watch: до MAX_WATCH ключей объектов AsoPlay; неверные отбрасываются, прежние заменяются."""
        wanted = frozenset(o for o in objects[:MAX_WATCH] if isinstance(o, str) and OBJECT.match(o)) \
            if isinstance(objects, list) else frozenset()
        self.unwatch_comments(conn)
        conn.comment_objects = wanted
        for key in wanted:
            self.by_object.setdefault(key, set()).add(conn.id)

    def unwatch_comments(self, conn: Any) -> None:
        """Снять подписки вкладки (новая подписка или уход вкладки)."""
        for key in conn.comment_objects:
            ids = self.by_object.get(key)
            if ids is not None:
                ids.discard(conn.id)
                if not ids:
                    del self.by_object[key]
        conn.comment_objects = frozenset()

    async def comment_event(self, data: dict[str, Any]) -> None:
        """Событие client_comment — кадр «comment» вкладкам, подписанным на этот объект; неверное событие — мимо."""
        key, action = data.get("object"), data.get("action")
        comment_id, thread, moment = data.get("comment_id"), data.get("thread"), data.get("moment")
        if not (isinstance(key, str) and OBJECT.match(key) and action in ACTIONS
                and isinstance(comment_id, int) and not isinstance(comment_id, bool) and comment_id > 0
                and _int_or_none(thread, 2**63) and _int_or_none(moment, 86400)):
            return
        frame = {"type": "comment", "object": key, "comment_id": comment_id, "thread": thread, "moment": moment,
                 "action": action}
        if _int_or_none(data.get("likes_count"), 2**31) and data.get("likes_count") is not None:
            frame["likes_count"] = data["likes_count"]
        for conn_id in list(self.by_object.get(key, ())):
            conn = self.conns.get(conn_id)
            if conn:
                await self.send(conn, frame)
