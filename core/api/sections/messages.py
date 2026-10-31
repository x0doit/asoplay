"""
«Написать» другу: POST /account/messages/{id} — сообщение в переписку Anime Social.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Кто может писать и антифлуд — по правилам соцсети; запрос — от имени человека через friend_data.social_call
(core/functions/friends.py).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from core.config import animesocial
from core.functions import friends as friend_data, sessions
from core.functions.sessions import current_user_required

router = APIRouter(prefix="/account")


class MessageIn(BaseModel):
    """Текст сообщения для «Написать»."""
    text: str = Field(..., max_length=8000)


@router.post("/messages/{peer_id}")
def send_message(peer_id: int, payload: MessageIn, request: Request,
                 user: dict[str, Any] = Depends(current_user_required),
                 session: str | None = Cookie(default=None, alias=sessions.COOKIE_NAME)) -> dict[str, Any]:
    """«Написать» у друга: сообщение уходит в переписку Anime Social, как из окна «Написать» на сайте
    соцсети. Кто может писать и антифлуд (40 сообщений в минуту) — по правилам соцсети."""
    text = payload.text.strip()
    if not 0 < peer_id < 2 ** 31 or not text or len(text) > 4000:
        raise HTTPException(400, "bad message")
    friend_data.social_call(request, session, "POST", f"/dialogs/{peer_id}/messages", {"text": text})
    # Переписка с этим человеком на сайте соцсети: /im?uid= ведёт в /dialogs?uid=
    return {"ok": True, "dialog_url": f"{animesocial.path_for('messages')}?uid={peer_id}"}
