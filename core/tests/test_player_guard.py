"""
Прокси плеера ходит только к хостам плееров: белый список на всех входах, перенаправления вручную с проверкой
каждого шага, публичные адреса после разрешения имени, CSP sandbox в документах и на ответах-страницах, журнал
отказов с ограничением.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Вход раздела: части — в core/tests/player_guard/, собираются в порядке PARTS.
"""
import unittest

from core.tests.player_guard import hosts, network, redirects, sandbox

PARTS = (hosts, redirects, network, sandbox)


def load_tests(loader, tests, pattern):
    """Проверки частей — в порядке PARTS."""
    return unittest.TestSuite(loader.loadTestsFromModule(part) for part in PARTS)


if __name__ == "__main__":
    unittest.main()
