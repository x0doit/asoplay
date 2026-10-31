"""
«Войти через Anime Social»: вход по коду с PKCE, привязка к уже вошедшему, «это вы?» на сайте соцсети,
токены, выход, заголовки доверенного клиента, шифрование токенов. Живую базу и соцсеть не трогают.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Вход раздела: части — в core/tests/as_login/, собираются в порядке PARTS.
"""
import unittest

from core.tests.as_login import client, flow, social, tokens

PARTS = (flow, social, tokens, client)


def load_tests(loader, tests, pattern):
    """Проверки частей — в порядке PARTS."""
    return unittest.TestSuite(loader.loadTestsFromModule(part) for part in PARTS)


if __name__ == "__main__":
    unittest.main()
