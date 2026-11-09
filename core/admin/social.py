"""
Запросы соцсети к AsoPlay от её сервера (clients.md, раздел 17): блок «Аниме» в профиле — GET /api/social/lists/{user_id}.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Как админ-API (core/admin/auth.py): только напрямую на петлю (с X-Forwarded-For или X-Real-IP, с чужого адреса — 404),
секрет AV_ADMIN_SECRET, допуск ±MAX_AGE, те же ошибки и обёртка ответа. Подпись своя — X-AsoPlay-Social: «{ts}.{hex}»,
hex = HMAC-SHA256(секрет, "social\\n" + ts + "\\n" + METHOD + "\\n" + путь с запросом + "\\n" + тело), чтобы подпись
профиля нельзя было выдать за подпись администратора. Данные блока — core/functions/social_lists.py.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

from fastapi import APIRouter, Depends, Path, Request

from core.admin.auth import MAX_AGE, AdminError, ok
from core.config import config
from core.functions import social_lists

router = APIRouter(prefix="/api/social")
_LOOPBACK = ("127.0.0.1", "::1")


def sign(secret: bytes, ts: str, method: str, path_with_query: str, body: bytes) -> str:
    """Подпись запроса соцсети: HMAC-SHA256 от «social», времени, метода, пути с запросом и тела."""
    message = f"social\n{ts}\n{method.upper()}\n{path_with_query}\n".encode("utf-8") + body
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


async def social_peer(request: Request) -> None:
    """Зависимость маршрута: запрос пришёл от сервера соцсети напрямую и подписан её секретом."""
    host = request.client.host if request.client else ""
    if host not in _LOOPBACK or request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip"):
        raise AdminError(404, "not_found", "Не найдено")
    secret = config.admin_secret()
    if not secret:
        raise AdminError(503, "not_configured", "AV_ADMIN_SECRET не задан в .env AsoPlay")
    ts, _, given = request.headers.get("x-asoplay-social", "").partition(".")
    if not ts.isdigit() or len(given) != 64:
        raise AdminError(401, "bad_signature", "Нет подписи X-AsoPlay-Social")
    if abs(time.time() - int(ts)) > MAX_AGE:
        raise AdminError(401, "expired", f"Подпись старше {MAX_AGE} секунд")
    path = request.scope.get("raw_path", b"").decode("latin-1") or request.url.path
    query = request.scope.get("query_string", b"").decode("latin-1")
    expected = sign(secret, ts, request.method, path + (f"?{query}" if query else ""), await request.body())
    if not hmac.compare_digest(expected, given.lower()):
        raise AdminError(401, "bad_signature", "Подпись не сходится")


@router.get("/lists/{user_id}")
def lists_block(user_id: int = Path(ge=1, lt=2**32), _peer: None = Depends(social_peer)) -> dict[str, Any]:
    """Блок «Аниме» человека: скрытые списки — hidden и ссылка на настройки, иначе числа, записи и ссылки."""
    return ok(social_lists.block(user_id))
