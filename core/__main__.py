"""
Запуск AsoPlay: python -m core из корня проекта.

Автор: Чепела Даниил Максимович.

Адрес и порт берутся из AV_BIND_HOST и AV_BIND_PORT (по умолчанию 127.0.0.1:8787).
"""
import uvicorn

# Сначала сборка приложения: core.py первым делом подгружает .env, а настройки в config читают окружение при импорте
from core import core
from core.config import config


def main() -> None:
    """Поднять uvicorn с приложением AsoPlay."""
    # Кадры вкладок — короткий JSON; ping-кадр раз в 25 секунд замечает оборванное соединение
    uvicorn.run(core.app, host=config.bind_host(), port=config.bind_port(), log_level="info",
                ws_max_size=64 * 1024, ws_ping_interval=25, ws_ping_timeout=20)


if __name__ == "__main__":
    main()
