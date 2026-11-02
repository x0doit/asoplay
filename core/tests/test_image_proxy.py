"""
Прокси картинок /proxy/img: хосты каталога и панели, проверка адреса на каждом шаге, мост — DoH по самому мосту
и CONNECT на проверенный адрес, только картинки из списка типов, без сжатия и не больше 8 МБ, ответ не исполняется
как страница, старая копия и одна выборка, пределы выборок, кеш с пределом.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Вход раздела: части — в core/tests/image_proxy/, собираются в порядке PARTS.
"""
import unittest

from core.tests.image_proxy import bridge, bridge_safety, cache, fetch, flow, hosts, limits, routes

PARTS = (hosts, fetch, routes, bridge, bridge_safety, flow, limits, cache)


def load_tests(loader, tests, pattern):
    """Проверки частей — в порядке PARTS."""
    return unittest.TestSuite(loader.loadTestsFromModule(part) for part in PARTS)


if __name__ == "__main__":
    unittest.main()
