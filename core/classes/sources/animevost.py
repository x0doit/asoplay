"""
Клиент AnimeVost (https://api.animevost.org): поиск и серии с прямыми mp4 (480p и 720p) в русской озвучке.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Надёжный источник с широким покрытием — от новых релизов до классики. Клиент без состояния: всё — простыми
HTTP-запросами. Недоступный хост — исключение сети, а не пустой ответ: вызывающий (core/functions/sources.py) может
повторить через мост VPN; остальные сбои — в журнал и пустой список.
"""
from __future__ import annotations

import re
import logging

import httpx

log = logging.getLogger("animevost")

BASE = "https://api.animevost.org/v1"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36"
TIMEOUT = 12.0


class Animevost:
    """Stateless клиент. Все операции идут через простой HTTP."""

    def __init__(self, proxy: str | None = None) -> None:
        """Клиент httpx; proxy задают только экземпляру для повтора через мост VPN."""
        # proxy задаётся только у экземпляра для повтора через VPN.
        self._client = httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=TIMEOUT,
            trust_env=False,
            proxy=proxy,
        )

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
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
        except httpx.TransportError:
            raise  # an unreachable host is not an empty result: the caller may retry via VPN
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
        except httpx.TransportError:
            raise
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
        """Номер серии из начала её названия («12 серия» → 12); числа нет — None."""
        m = re.match(r"\s*(\d+)", name or "")
        return int(m.group(1)) if m else None

    @staticmethod
    def _format_item(raw: dict) -> dict:
        """Запись поиска → словарь: id, названия (русское, английское, пометка), год, жанр, описание,
        картинка, рейтинг."""
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
