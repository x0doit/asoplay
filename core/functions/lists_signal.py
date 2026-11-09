"""
Сигнал соцсети «списки человека изменились» (clients.md, раздел 17): кадр {"type": "lists_changed", "user_id": N} по
соединению сервер–сервер. Соцсеть сбрасывает свой ответ блока «Аниме», и открытые страницы профиля перечитывают его.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Изменения одного человека за WINDOW секунд уходят одним кадром: первое ставит отправку через WINDOW, следующие до
неё в неё и входят — к отправке все записи уже сохранены. Звать — только через lists.changed() (core/functions/lists.py)
из любого потока. Соединения нет — кадр не нужен: соцсеть держит ответ 10 минут и перечитает его сама.
"""
from __future__ import annotations

import logging
import threading

log = logging.getLogger("asoplay.lists")

WINDOW = 2.0
_pending: set[int] = set()  # люди, кадр которым уже ждёт отправки
_lock = threading.Lock()


def changed(user_id: int) -> None:
    """Через WINDOW секунд — сигнал соцсети; уже ждёт — это изменение войдёт в тот же кадр."""
    from core.functions import realtime  # хаб и соединение с соцсетью; списки — ниже realtime
    uid = int(user_id)
    with _lock:
        if uid in _pending:
            return
        _pending.add(uid)
    if not realtime.hub.later_from_thread(WINDOW, _send, uid):
        with _lock:
            _pending.discard(uid)  # сервер не запущен (проверки, скрипты) — слать некому


def _send(user_id: int) -> None:
    """Отправить кадр (в цикле хаба)."""
    from core.functions import realtime
    with _lock:
        _pending.discard(user_id)
    if not realtime.hub.social.send({"type": "lists_changed", "user_id": user_id}):
        log.debug("No Anime Social link for lists_changed of %s; its cached answer expires by itself", user_id)
