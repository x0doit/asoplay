"""
Фильтры найденного у источников: год, имена озвучек, неиграбельные и VK-плееры, реклама.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Общие для маршрутов /src/… (core/api/sections/sources) и своих источников (core/functions/native_sources.py). VK —
последний выбор: такие озвучки идут после всех остальных, а сырые страницы VK без id видео отбрасываются
(core/functions/player_hosts.py). Видео проходят через фильтр рекламы (core/classes/adblock).
"""
from __future__ import annotations

import re as _re

from core.classes.adblock import engine as adblock
from core.functions.player_hosts import VK_SCORE, is_disabled_player_url, vk_video_ref


def filter_by_year(results: list[dict], year: int | None) -> list[dict]:
    """Результаты на год ±1 от year, без года — тоже; не осталось ни одного — все как были."""
    if not year:
        return results

    def _y(x):
        """Год результата числом; нет его или не число — 0."""
        try:
            return int(str(x.get("year") or 0))
        except (TypeError, ValueError):
            return 0

    filtered = [r for r in results if _y(r) == 0 or abs(_y(r) - year) <= 1]
    return filtered or results


def norm_dub_name(name: str) -> str:
    """Имя озвучки для сравнения: без «Озвучка», «Субтитры» и пометок языка, без знаков, строчными."""
    s = (name or "").lower().strip()
    for prefix in ("озвучка ", "субтитры ", "оригинал ", "original ", "voice ", "sub "):
        if s.startswith(prefix):
            s = s[len(prefix):]
    s = _re.sub(r"\((ru|en|jp|ja|рус|русс?кий|англ\w*|яп\w*)\)", "", s)
    s = _re.sub(r"[^\w\s]+", " ", s)
    s = _re.sub(r"\s+", " ", s).strip()
    return s


def unplayable_player_url(url: str) -> bool:
    """Плеер, который не сыграть: выключенный хост, кроме VK со ссылкой на видео."""
    # VK plays only through the clean frame, which needs the owner/video ids.
    return is_disabled_player_url(url) and not vk_video_ref(url)


def vk_last(identities: list, scores: dict) -> list:
    """Озвучки VK — после всех остальных; порядок внутри групп прежний."""
    # VK is the last resort: its dubs follow every other player, order kept.
    return sorted(identities, key=lambda identity: scores[identity] <= VK_SCORE)


async def filter_source_videos(videos: list[dict]) -> list[dict]:
    """Видео без неиграбельных плееров — через фильтр рекламы."""
    return await adblock.filter_videos([
        video for video in videos if not unplayable_player_url(video.get("url", ""))
    ])
