"""
Устойчивость прокси: VPN-мост и выбор маршрута, счётчик сбоев источника, кеш и общий запрос, сроки и
восстановление, каталог и русский поиск. Живую базу, VPN и HTTP-кеш не трогают.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Вход раздела: части — в core/tests/proxy_resilience/, собираются в порядке PARTS.
"""
import unittest

from core.tests.proxy_resilience import catalog, faults, resilience, routes

PARTS = (routes, faults, resilience, catalog)


def load_tests(loader, tests, pattern):
    """Проверки частей — в порядке PARTS."""
    return unittest.TestSuite(loader.loadTestsFromModule(part) for part in PARTS)


if __name__ == "__main__":
    unittest.main()
