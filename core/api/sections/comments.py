"""
Раздел /comments: комментарии к тайтлам и чат серии через соцсеть (clients.md, раздел 12) — список, один комментарий,
написать или ответить, удалить свой, «Нравится», жалоба.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Объекты — только asoplay:anime:{mal_id} и asoplay:episode:{mal_id}.{серия}. Название и адрес ветки ставит сервер из
своего каталога, а не браузер. Запросы к соцсети, кеш гостей и коды отказов — core/functions/comments.py.
"""
from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, conint

from core.functions import comments, sessions
from core.modules import title_cache

router = APIRouter(prefix="/comments")
_SESSION = Cookie(default=None, alias=sessions.COOKIE_NAME)
_USER = Depends(sessions.current_user_required)  # действия — только вошедшему


class CommentIn(BaseModel):
    """Новый комментарий: объект, текст, ветка и на чей ответ, момент серии (только для серии)."""
    object: str = Field(..., max_length=150)
    text: str = Field(..., max_length=5000)
    thread: conint(ge=1) | None = None
    reply_to: conint(ge=1) | None = None
    moment: conint(ge=0, le=86400) | None = None


class ReportIn(BaseModel):
    """Жалоба: причина из списка соцсети и необязательный комментарий."""
    reason: Literal["spam", "insult", "adult", "violence", "fraud", "other"]
    comment: str = Field("", max_length=300)


def _parse(key: str) -> tuple[int, int | None]:
    """(mal_id, серия) или 422 bad_object — чужие ключи не проксируем."""
    parsed = comments.parse_object(key)
    if parsed is None:
        raise HTTPException(422, "bad_object")
    return parsed


@router.get("")
def comments_list(
    request: Request,
    object: str = Query(..., max_length=150),
    sort: Literal["new", "old", "top", "moment"] = "new",
    thread: int | None = Query(None, ge=1),
    cursor: str | None = Query(None, max_length=200),
    since: int | None = Query(None, alias="from", ge=0, le=86400),
    until: int | None = Query(None, alias="to", ge=0, le=86400),
    limit: int = Query(20, ge=1, le=100),
    session: str | None = _SESSION,
) -> Any:
    """Комментарии объекта: корневые или ответы ветки (thread), страницы курсором; чат серии — sort=moment с отрезком
    from–to. Гостю — из короткого кеша."""
    _parse(object)
    if sort != "moment" and (since is not None or until is not None):
        raise HTTPException(422, "from_to_need_moment")
    if limit > (100 if sort == "moment" else 50):
        raise HTTPException(422, "limit_too_big")
    params = {"object": object, "sort": sort, "thread": thread, "cursor": cursor, "from": since, "to": until,
              "limit": limit}
    return comments.read(request, session, "/clients/comments", params)


@router.get("/item/{comment_id}")
def comment_item(request: Request, comment_id: int = Path(..., ge=1), session: str | None = _SESSION) -> Any:
    """Один комментарий — после события added вкладка берёт его отсюда; без кеша."""
    return comments.read(request, session, f"/clients/comments/{comment_id}", cache=False)


async def _thread_title(mal_id: int, episode: int | None) -> tuple[str, str]:
    """Название и адрес ветки из каталога сервера: «Название» или «Название, N серия» и канонический адрес
    (для серии — с ?ep=N)."""
    info = await title_cache.lookup(mal_id)
    if info is not None and "title_ru" not in info:  # только что загружен из каталога: полная запись — в кеше
        info = await asyncio.to_thread(title_cache.load_cached, mal_id)
    if info is None:
        raise HTTPException(404, "title_not_found")
    name = info.get("title_ru") or info.get("title_en") or f"MAL {mal_id}"
    url = title_cache.canonical_url(mal_id, info["slug"])
    if episode is not None:
        name, url = f"{name}, {episode} серия", f"{url}?ep={episode}"
    return name[:200], url


@router.post("")
async def comment_add(payload: CommentIn, request: Request, user: dict[str, Any] = _USER,
                      session: str | None = _SESSION) -> Any:
    """Написать или ответить. Момент — только у комментария к серии (чат серии)."""
    mal_id, episode = _parse(payload.object)
    text = payload.text.strip()
    if not text:
        raise HTTPException(422, "empty_text")
    if payload.moment is not None and episode is None:
        raise HTTPException(422, "moment_needs_episode")
    title, url = await _thread_title(mal_id, episode)
    body: dict[str, Any] = {"object": payload.object, "text": text, "title": title, "url": url}
    for key in ("thread", "reply_to", "moment"):
        if getattr(payload, key) is not None:
            body[key] = getattr(payload, key)
    return await asyncio.to_thread(comments.write, request, session, "POST", "/clients/comments", body)


@router.delete("/{comment_id}")
def comment_delete(request: Request, comment_id: int = Path(..., ge=1), user: dict[str, Any] = _USER,
                   session: str | None = _SESSION) -> Any:
    """Удалить свой комментарий (корневой — вместе с веткой)."""
    return comments.write(request, session, "DELETE", f"/clients/comments/{comment_id}")


@router.put("/{comment_id}/like")
def comment_like(request: Request, comment_id: int = Path(..., ge=1), user: dict[str, Any] = _USER,
                 session: str | None = _SESSION) -> Any:
    """«Нравится»."""
    return comments.write(request, session, "PUT", f"/clients/comments/{comment_id}/like")


@router.delete("/{comment_id}/like")
def comment_unlike(request: Request, comment_id: int = Path(..., ge=1), user: dict[str, Any] = _USER,
                   session: str | None = _SESSION) -> Any:
    """Снять «Нравится»."""
    return comments.write(request, session, "DELETE", f"/clients/comments/{comment_id}/like")


@router.post("/{comment_id}/report")
def comment_report(payload: ReportIn, request: Request, comment_id: int = Path(..., ge=1),
                   user: dict[str, Any] = _USER, session: str | None = _SESSION) -> Any:
    """Пожаловаться: на свой — 403 own_content, повтор за сутки — 409 already_reported (коды соцсети)."""
    body = {"reason": payload.reason, **({"comment": payload.comment.strip()} if payload.comment.strip() else {})}
    return comments.write(request, session, "POST", f"/clients/comments/{comment_id}/report", body)
