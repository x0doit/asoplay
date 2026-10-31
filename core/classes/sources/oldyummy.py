"""
Клиент old.yummyani.me (api.yani.tv): поиск тайтлов и все серии тайтла со всеми озвучками.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Своё REST-API (/api/*, описано в Swagger): покрытие больше, чем у yummy_anime из anicli-api, и часто есть тайтлы,
которых нет в других источниках (включая спин-оффы вроде «Re:Zero. Перерыв с нуля 3»). Недоступный хост —
исключение сети: вызывающий (core/functions/sources.py) может повторить через мост VPN; остальные сбои — пустой
список.
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger("oldyummy")

BASE = "https://old.yummyani.me/api"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
TIMEOUT = 12.0


class OldYummy:
    """Клиент old.yummyani.me: прямой по умолчанию, с proxy — для повтора через мост VPN."""

    def __init__(self, proxy: str | None = None) -> None:
        """Клиент httpx; proxy задают только экземпляру для повтора через мост VPN."""
        # Прямой клиент по умолчанию; экземпляр с proxy нужен только для повтора
        # через VPN, когда хост недоступен напрямую.
        self._client = httpx.AsyncClient(
            headers={"User-Agent": UA, "Accept": "application/json"},
            timeout=TIMEOUT,
            follow_redirects=True,
            trust_env=False,
            proxy=proxy,
        )

    async def close(self) -> None:
        """Закрыть HTTP-клиент."""
        await self._client.aclose()

    # ---- search ----
    async def search(self, query: str, limit: int = 20) -> list[dict]:
        """Поиск тайтлов — до limit результатов; ответ не 200 или сбой — пусто."""
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
        except httpx.TransportError:
            raise  # an unreachable host is not an empty result: the caller may retry via VPN
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
        """Все видео тайтла — серии со всеми озвучками сразу; ответ не 200 или сбой — пусто."""
        try:
            r = await self._client.get(f"{BASE}/anime/{anime_id}/videos")
            if r.status_code != 200:
                return []
            data = r.json()
        except httpx.TransportError:
            raise
        except Exception as exc:
            log.warning("oldyummy videos failed (id=%s): %s", anime_id, exc)
            return []
        return data.get("response", []) or []

    # ---- helpers ----
    @staticmethod
    def _format_item(raw: dict) -> dict:
        """Запись поиска → словарь: id и адрес тайтла, названия, год, картинка, описание, id в MAL, число серий, тип."""
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
