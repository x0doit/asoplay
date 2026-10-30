"""
Проверка: настройки, которые config читает при старте, видят .env.

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

core.py первым делом зовёт env.load(), и только потом импортируется config — иначе SITE_URL, COOKIE_SECURE и другие
настройки старта молча взяли бы значения по умолчанию (на проде — адрес разработки и cookie без Secure). Проверка
подставляет временный .env и собирает приложение так же, как python -m core; контроль — импорт config раньше сборки
(он обязан .env не увидеть, иначе проверка ничего не доказывает).
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROBE = """
import logging, sys
sys.path.insert(0, {root!r})
logging.disable(logging.CRITICAL)
import core.config.env as env
env.ENV_FILE = __import__("pathlib").Path({env_file!r})
if {config_first}:
    from core.config import config
from core import core
from core.config import config
print(config.SITE_NAME, config.COOKIE_SECURE, config.SITE_URL)
"""


class StartSettingsSeeEnvFile(unittest.TestCase):
    def setUp(self):
        folder = tempfile.mkdtemp()
        self.env_file = Path(folder) / ".env"
        self.env_file.write_text("AV_SITE_NAME=CheckName\nAV_COOKIE_SECURE=1\nAV_SITE_URL=https://check.test/\n",
                                 encoding="utf-8")

    def probe(self, config_first: bool) -> str:
        env = {k: v for k, v in os.environ.items() if k not in ("AV_SITE_NAME", "AV_COOKIE_SECURE", "AV_SITE_URL")}
        code = PROBE.format(root=str(ROOT), env_file=str(self.env_file), config_first=config_first)
        out = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        return out.stdout.strip().splitlines()[-1]

    def test_the_app_loads_env_before_config(self):
        self.assertEqual(self.probe(config_first=False), "CheckName True https://check.test")

    def test_control_config_imported_too_early_misses_env(self):
        self.assertEqual(self.probe(config_first=True), "AsoPlay False http://127.0.0.1:8787")


if __name__ == "__main__":
    unittest.main()
