"""
Животновост‑клиент: https://api.animevost.org
Надёжный источник с прямыми mp4 ссылками (480p + 720p) на русском озвучании.
Широчайшее покрытие — от новых релизов до классики.
"""
from __future__ import annotations

import re
import logging
from typing import Any

import httpx

log = logging.getLogger("animevost")

BASE = "https://api.animevost.org/v1"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
TIMEOUT = 12.0


class Animevost:
    """Stateless клиент. Все операции идут через простой HTTP."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=TIMEOUT,
        )

    async def close(self) -> None:
        await self._client.aclose()

    # ---- search ----
    async def search(self, query: str, limit: int = 20) -> list[dict]:
        """Возвращает список аниме. Ключ `id` ссылается на вост‑anime."""
        if not query:
            return []
        try:
            r = await self._client.post(f"{BASE}/search", data={"name": query})
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log.warning("animevost search failed: %s", exc)
            return []

        out: list[dict] = []
        for raw in (data.get("data") or [])[:limit]:
            out.append(self._format_item(raw))
        return out

    # ---- episodes ----
    async def episodes(self, vost_id: int | str) -> list[dict]:
        """Возвращает список эпизодов с прямыми MP4 (std+hd)."""
        try:
            r = await self._client.post(f"{BASE}/playlist", data={"id": str(vost_id)})
            r.raise_for_status()
            data = r.json()
        except Exception as exc:
            log.warning("animevost playlist failed: %s", exc)
            return []

        if not isinstance(data, list):
            return []

        episodes: list[dict] = []
        for e in data:
            name = (e.get("name") or "").strip()
            num = self._parse_episode_num(name) or len(episodes) + 1
            qualities: list[dict] = []
            # video.animetop.info поддерживает HTTPS — подменяем для избежания mixed-content
            if e.get("hd"):
                qualities.append({"url": e["hd"].replace("http://", "https://", 1), "quality": 720, "type": "mp4"})
            if e.get("std"):
                qualities.append({"url": e["std"].replace("http://", "https://", 1), "quality": 480, "type": "mp4"})
            if not qualities:
                continue
            episodes.append({
                "num": num,
                "name": name,
                "preview": e.get("preview"),
                "qualities": qualities,
            })
        episodes.sort(key=lambda x: x["num"])
        return episodes

    # ---- helpers ----
    @staticmethod
    def _parse_episode_num(name: str) -> int | None:
        m = re.match(r"\s*(\d+)", name or "")
        return int(m.group(1)) if m else None

    @staticmethod
    def _format_item(raw: dict) -> dict:
        full_title = raw.get("title") or ""
        ru, en, note = Animevost._split_title(full_title)
        return {
            "id": raw.get("id"),
            "title": full_title,
            "title_ru": ru,
            "title_en": en,
            "year": raw.get("year"),
            "genre": raw.get("genre"),
            "director": raw.get("director"),
            "description": raw.get("description"),
            "image": raw.get("urlImagePreview"),
            "rating": raw.get("rating"),
            "votes": raw.get("votes"),
            "note": note,
        }

    @staticmethod
    def _split_title(full: str) -> tuple[str, str, str]:
        """Разбиваем 'Русское / English [note]' → (ru, en, note)."""
        note = ""
        m = re.search(r"\[([^\]]+)\]\s*$", full)
        base = full
        if m:
            note = m.group(1).strip()
            base = full[: m.start()].strip()
        if "/" in base:
            ru, en = base.split("/", 1)
            return ru.strip(), en.strip(), note
        return base.strip(), "", note


# Singleton для удобной регистрации
_instance: Animevost | None = None


def get_animevost() -> Animevost:
    global _instance
    if _instance is None:
        _instance = Animevost()
    return _instance
