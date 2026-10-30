"""
Запуск AsoPlay: python -m core из корня проекта.

Автор: Чепела Даниил Максимович.

Адрес и порт берутся из AV_BIND_HOST и AV_BIND_PORT (по умолчанию 127.0.0.1:8787). Их читаем после импорта
приложения: оно само подгружает .env, и значения оттуда тоже действуют.
"""
import os

import uvicorn

from core.core import app


def main() -> None:
    """Поднять uvicorn с приложением AsoPlay."""
    host = os.environ.get("AV_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("AV_BIND_PORT", "8787"))
    # Кадры вкладок — короткий JSON; ping-кадр раз в 25 секунд замечает оборванное соединение
    uvicorn.run(app, host=host, port=port, log_level="info",
                ws_max_size=64 * 1024, ws_ping_interval=25, ws_ping_timeout=20)


if __name__ == "__main__":
    main()
