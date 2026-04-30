"""
old.yummyani.me / api.yani.tv — нативный клиент.
Своя SWAGGER-документированная REST-апи (/api/*), покрытие больше, чем у
anicli-api yummy_anime, и часто попадаются тайтлы, которых нет в других
источниках (включая spin-off'ы типа "Re:Zero. Перерыв с нуля 3").
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

log = logging.getLogger("oldyummy")

BASE = "https://old.yummyani.me/api"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TIMEOUT = 12.0


class OldYummy:
    def __init__(self) -> None:
        # Клиент читает HTTP(S)_PROXY из env — так же как основной backend,
        # значит VPN (xray) подхватывается автоматически.
        self._client = httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=TIMEOUT,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ---- search ----
    async def search(self, query: str, limit: int = 20) -> list[dict]:
        if not query:
            return []
        try:
            r = await self._client.get(
                f"{BASE}/search",
                params={"q": query, "limit": limit, "offset": 0},
            )
            if r.status_code != 200:
                return []
            data = r.json()
        except Exception as exc:
            log.warning("oldyummy search failed: %s", exc)
            return []

        items = data.get("response", []) or []
        out: list[dict] = []
        for raw in items[:limit]:
            out.append(self._format_item(raw))
        return out

    # ---- videos (возвращает сразу все эпизоды со всеми озвучками) ----
    async def videos(self, anime_id: int | str) -> list[dict]:
        try:
            r = await self._client.get(f"{BASE}/anime/{anime_id}/videos")
            if r.status_code != 200:
                return []
            data = r.json()
        except Exception as exc:
            log.warning("oldyummy videos failed (id=%s): %s", anime_id, exc)
            return []
        return data.get("response", []) or []

    # ---- helpers ----
    @staticmethod
    def _format_item(raw: dict) -> dict:
        poster = raw.get("poster") or {}
        thumb = (poster.get("small") or poster.get("medium") or poster.get("big") or "").lstrip("/")
        if thumb and not thumb.startswith("http"):
            thumb = "https://" + thumb
        return {
            "id": raw.get("anime_id"),
            "alias": raw.get("anime_url"),
            "title": raw.get("title") or "",
            "title_en": (raw.get("other_titles") or [None])[0] if raw.get("other_titles") else None,
            "year": raw.get("year"),
            "thumbnail": thumb,
            "description": raw.get("description"),
            "mal_id": (raw.get("remote_ids") or {}).get("myanimelist_id"),
            "episodes": (raw.get("episodes") or {}).get("count"),
            "type": (raw.get("type") or {}).get("alias"),
        }


_instance: OldYummy | None = None


def get_oldyummy() -> OldYummy:
    global _instance
    if _instance is None:
        _instance = OldYummy()
    return _instance
