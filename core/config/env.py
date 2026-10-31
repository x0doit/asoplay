"""
Окружение AsoPlay: корень проекта и загрузка .env.

Автор: Чепела Даниил Максимович.

load() зовёт core.py первой строкой сборки приложения, до импорта остальных модулей: настройки в config.py читают
окружение при импорте и должны увидеть значения из .env. Сам импорт этого модуля .env не трогает, поэтому проверки,
которые собирают отдельные модули, видят только своё окружение.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT / ".env"


def load() -> None:
    """Подгрузить .env в окружение процесса; уже заданные переменные не перезаписываются."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return  # python-dotenv не обязателен: обычные переменные окружения работают и без него
    load_dotenv(ENV_FILE, override=False)
