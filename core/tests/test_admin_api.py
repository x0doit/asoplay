"""
Панель Anime Social (core/admin: маршрутизатор и части) и что её решения делают с сайтом: жалобы
«Не работает», флаги тайтлов, настройки, обзор. Живую базу и соцсеть не трогают.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Вход раздела: части — в core/tests/admin_api/, собираются в порядке PARTS.
"""
import unittest

from core.tests.admin_api import reports, settings, signature, site, summary, titles

PARTS = (signature, reports, settings, titles, summary, site)


def load_tests(loader, tests, pattern):
    """Проверки частей — в порядке PARTS."""
    return unittest.TestSuite(loader.loadTestsFromModule(part) for part in PARTS)


if __name__ == "__main__":
    unittest.main()
