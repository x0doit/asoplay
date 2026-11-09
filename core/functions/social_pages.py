"""
Страница человека в соцсети: заблокирована или удалена. Открытый профиль AsoPlay такому человеку не отдаётся — как
соцсеть не показывает его страницу.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Знает это соцсеть: поле deactivated (banned, deleted) у человека в GET /users?ids= API v2 — с ключами клиента, без
токена (clients.md, раздел 10). Ответ помнится CACHE_SECONDS, бан из события account_banned сбрасывает память сразу
(core/functions/realtime_social.py). Соцсеть не ответила — берётся прошлый ответ, а без него страница считается
открытой: профиль одного человека не должен пропадать у всех, пока соцсеть недоступна.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict

from core.classes import as_api

log = logging.getLogger("asoplay.profile")

CACHE_SECONDS = 300
CACHE_MAX = 10_000  # людей в памяти; сверх — сначала просроченные, потом самые давние
_cache: OrderedDict[int, tuple[str | None, float]] = OrderedDict()  # id → (deactivated, когда спросили), давние первыми
_lock = threading.Lock()


def _remember(user_id: int, state: str | None, now: float) -> None:
    """Запомнить ответ (под _lock); сверх CACHE_MAX выкинуть просроченные, а если мало — самые давние."""
    _cache[user_id] = (state, now)
    _cache.move_to_end(user_id)
    if len(_cache) <= CACHE_MAX:
        return
    for uid in [uid for uid, (_, at) in _cache.items() if now - at >= CACHE_SECONDS]:
        del _cache[uid]
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)


def deactivated(user_id: int) -> str | None:
    """Что с его страницей в соцсети: banned, deleted или None — открыта."""
    now = time.monotonic()
    with _lock:
        known = _cache.get(user_id)
    if known and now - known[1] < CACHE_SECONDS:
        return known[0]
    try:
        data = as_api.call("GET", f"/users?ids={int(user_id)}")
    except as_api.ASApiError as exc:
        log.warning("Could not ask the social network whether page %s is open: %s", user_id, exc)
        return known[0] if known else None
    items = data.get("items") if isinstance(data, dict) else None
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
    state = item.get("deactivated") if isinstance(item.get("deactivated"), str) else None
    with _lock:
        _remember(user_id, state, now)
    return state


def closed(user_id: int) -> bool:
    """Страница человека в соцсети заблокирована или удалена."""
    return deactivated(user_id) is not None


def forget(user_id: int) -> None:
    """Страница изменилась (пришёл бан): следующий вопрос — снова в соцсеть."""
    with _lock:
        _cache.pop(user_id, None)
