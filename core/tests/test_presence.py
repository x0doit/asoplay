"""
Присутствие на AsoPlay и друзья из Anime Social: вкладки и «смотрит», пакет для соцсети, друзья,
заявки и действия, «Написать», приватность. Живую базу и соцсеть не трогают.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Вход раздела: части — в core/tests/presence/, собираются в порядке PARTS.
"""
import unittest

from core.tests.presence import friend_page, friends, privacy, store

PARTS = (store, friends, friend_page, privacy)


def load_tests(loader, tests, pattern):
    """Проверки частей — в порядке PARTS."""
    return unittest.TestSuite(loader.loadTestsFromModule(part) for part in PARTS)


if __name__ == "__main__":
    unittest.main()
