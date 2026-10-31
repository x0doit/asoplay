"""
Живое соединение браузера с AsoPlay — WebSocket /ws — вместо опроса сервера по таймеру.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

  браузер → сервер  {"type": "visible", "value": true}                  вкладка на экране / в фоне
                    {"type": "watching", "mal_id": 52991, "title": "…"}  плеер заиграл; mal_id null — перестал
                    {"type": "ping"}                                     → {"type": "pong"}
  сервер → браузер  {"type": "hello", "user_id": 17 | null, "status": {...}}   сразу после подключения
                    {"type": "status", "status": {...}}                 изменились источники или VPN
                    {"type": "presence", "user": {...}, "on_asoplay": true, "watching": {...} | null}
                    {"type": "social_presence", "user_id": 17, "online_where": "site", "last_seen": …}  друг в соцсети
                    {"type": "friendship", "user_id": 17, "status": "friends"}   изменилась дружба

Подключаться можно только со страниц самого AsoPlay (заголовок Origin): иначе чужой сайт по cookie человека
слушал бы, кто из его друзей где. Обрыв соединения и «вышел» сервер замечает по ping-кадрам WebSocket (uvicorn,
раз в 25 секунд) — других регулярных запросов нет. Хаб — core/functions/realtime.py, через модуль.
"""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from core.functions import rate_limit, realtime, sessions

log = logging.getLogger("asoplay.realtime")

router = APIRouter()

# Задаёт core/core.py: адреса страниц, которым тоже можно подключаться (фронтенд для разработки на другом порту)
extra_origins: set[str] = set()


def _origin_ok(ws: WebSocket) -> bool:
    """Страница того же адреса, что и сервер (заголовок Host проходит nginx как есть), или адрес из
    AV_ALLOWED_ORIGINS — фронтенд для разработки на другом порту."""
    origin = (ws.headers.get("origin") or "").strip()
    if not origin:
        return False
    parts = urlsplit(origin)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return False
    host = (ws.headers.get("host") or "").strip().lower()
    return parts.netloc.lower() == host or origin.rstrip("/") in extra_origins


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    """Живое соединение вкладки: проверка Origin, вход по cookie сессии, лимиты вкладок, hello, затем кадры
    до закрытия."""
    if not _origin_ok(ws):
        await ws.close(code=1008)
        return
    ip = rate_limit.client_ip(ws)
    session = ws.cookies.get(sessions.COOKIE_NAME) or ""
    user = await asyncio.to_thread(realtime.session_user, session) if session else None
    if not realtime.hub.has_room(user, ip):
        await ws.close(code=1013)
        return
    await ws.accept()
    conn = realtime.hub.join(ws, user, session, ip)
    try:
        await realtime.hub.send(conn, {"type": "hello", "user_id": conn.user_id or None,
                                       "status": realtime.hub.status()})
        if conn.user_id:
            realtime.hub.spawn(realtime.hub.load_friends(conn))
        while True:
            await realtime.hub.handle(conn, await ws.receive_text())
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 — сломанный кадр или обрыв: соединение закрывается, вкладка переподключится
        log.debug("Realtime connection %s ended with an error", conn.id, exc_info=True)
    finally:
        await realtime.hub.leave(conn)
