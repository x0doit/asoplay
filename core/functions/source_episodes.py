"""
Серии yummy_anime поштучно: серия, номер которой не разобрать, пропускается, а не роняет весь список.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

anicli-api 0.8.6 (yummy_anime.Anime.a_get_episodes) делает int() от номера сразу для всех серий. Одна запись
вроде «1168.5» у «Ван-Писа» — и раздел /src/episodes отвечает 502 на весь тайтл. Здесь тот же сырой список видео
разбирается так же, как в библиотеке: через её API-клиент (значит, тем же маршрутом), alloha пропускается, видео
собираются в серии по номеру в порядке ответа, серия — её же класс Episode. Номер проверяется у каждой серии
отдельно. Номер серии у нас целый везде: прогресс, «Списки», автопереход, события в соцсеть (§14). Поэтому дробные
спецвыпуски пока пропускаются. Предупреждение — одно на тайтл и источник за жизнь процесса, с сырыми значениями.
Библиотека не правится: исправление — запросом её авторам.
"""
from __future__ import annotations

import logging
from typing import Any

from anicli_api.source.yummy_anime import Episode

from core.functions import source_health

log = logging.getLogger("asoplay.sources")

# (источник, id тайтла у источника), о которых уже предупредили
_warned: set[tuple[str, Any]] = set()


def episode_number(raw: Any) -> int | None:
    """Номер серии так же, как его читает библиотека (int()), только дробный и нечисловой — None, а не исключение."""
    if isinstance(raw, float):
        return int(raw) if raw.is_integer() else None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def yummy_anime(anime: Any) -> list[Episode]:
    """Серии тайтла yummy_anime (anime — результат a_get_anime) из сырого списка его видео. Клиент библиотеки прячет
    сбой в ответ: код 0 (запрос не прошёл или пришёл не JSON), 5xx и 429 — SourceDown, сбой источника; другой код
    (404 и т. п.) — SourceRefused, быстрый отказ. Раздел отвечает 502 в обоих случаях."""
    anime_id = anime.data["anime_id"]
    resp = await anime.async_api.get_anime_videos(id=anime_id)
    if not resp.success:
        code = resp.status_code
        failure = source_health.SourceDown if code == 0 or code >= 500 or code == 429 else source_health.SourceRefused
        raise failure(f"yummy_anime videos: status {code}: {resp.error}")
    groups: dict[Any, list[dict]] = {}
    for video in resp.data.get("response") or []:
        if "alloha" in (video.get("iframe_url") or ""):  # как в библиотеке: этот плеер она не разбирает
            continue
        groups.setdefault(video.get("number"), []).append(video)
    episodes: list[Episode] = []
    skipped: list[Any] = []
    for raw, videos in groups.items():
        num = episode_number(raw)
        if num is None:
            skipped.append(raw)
            continue
        episodes.append(Episode(ordinal=num, title="Episode", data=videos, http=anime.http, http_async=anime.http_async,
                                sync_api=anime.sync_api, async_api=anime.async_api))
    if skipped and ("yummy_anime", anime_id) not in _warned:
        _warned.add(("yummy_anime", anime_id))
        log.warning("yummy_anime: anime %s, episodes skipped, the number is not whole: %s", anime_id,
                    ", ".join(repr(r) for r in skipped[:10]))
    return episodes
