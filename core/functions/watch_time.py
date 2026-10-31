"""
Время просмотра для «Обзора» в панели Anime Social (aviev_watch_time).

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Сколько видео посмотрели — для «Обзора» в панели Anime Social (aviev_watch_time). Плеер шлёт событие раз в
30 секунд; засчитываем, на сколько продвинулось видео с прошлого события той же серии. Перемотка вперёд
(видео ушло дальше, чем вдвое больше прошедшего времени), пауза и перемотка назад не засчитываются. Прошлые
точки живут в памяти процесса.
"""
from __future__ import annotations

import threading
import time
from typing import Any

_last_points: dict[tuple[int, int, int], tuple[float, int]] = {}
_points_lock = threading.Lock()


def watched_seconds(user_id: int, mal_id: int, episode: int, position: int,
                    clock: Any = time.monotonic) -> int:
    """Сколько секунд видео засчитать за время с прошлого события этой серии у этого человека."""
    now = clock()
    key = (user_id, mal_id, episode)
    with _points_lock:
        prev = _last_points.get(key)
        _last_points[key] = (now, position)
        if len(_last_points) > 20_000:
            for old in [k for k, (at, _) in _last_points.items() if now - at > 600]:
                del _last_points[old]
    if prev is None:
        return 0
    wall, step = now - prev[0], position - prev[1]
    if step <= 0 or wall <= 0 or wall > 300 or step > wall * 2 + 10:
        return 0
    return int(step)
