"""
События просмотра для соцсети: очередь aviev_as_events в транзакции действия, где пишется событие, отправка по
соединению сервер–сервер с подтверждением и досылка задним числом. Без сети и без живой базы.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Вход раздела: части — в core/tests/as_events/, собираются в порядке PARTS.
"""
import unittest

from core.tests.as_events import backfill, moments, queue, sender

PARTS = (queue, moments, sender, backfill)


def load_tests(loader, tests, pattern):
    """Проверки частей — в порядке PARTS."""
    return unittest.TestSuite(loader.loadTestsFromModule(part) for part in PARTS)


if __name__ == "__main__":
    unittest.main()
