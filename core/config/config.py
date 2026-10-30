"""
Настройки AsoPlay в одном месте: файлы конфигурации, рабочие файлы и все переменные окружения.

Автор: Чепела Даниил Максимович.

Секреты живут только в .env (шаблон — .env.example), подгружает его env.load() при старте приложения. Что читается
один раз при старте, здесь — константы: они считаются при первом импорте модуля, а в приложении это всегда после
env.load(). Что читается при каждом обращении, здесь — функции: значение берётся в момент вызова, как и до переноса
сюда (проверки подменяют такие переменные на ходу). Выражения перенесены дословно: какие значения считаются «да»
и что берётся по умолчанию, у каждой переменной своё, и выравнивать это нельзя — изменилось бы поведение.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from core.config.env import ROOT

# ---------- файлы ----------
CONFIG_DIR = ROOT / "core" / "config"
DB_PHP = CONFIG_DIR / "db.php"  # конфиг БД в формате Anime Social; на сервере — файл владельца
ANIMESOCIAL_JSON = CONFIG_DIR / "animesocial.json"  # адреса и медиа Anime Social; на сервере — файл владельца
XRAY_TEMPLATE = CONFIG_DIR / "server" / "xray.template.json"
# Рабочие файлы, вне git: HTTP-кеш, списки adblock, конфиг и журнал запущенного xray
CACHE_DIR = CONFIG_DIR / "cache"
ADBLOCK_CACHE_DIR = CACHE_DIR / "adblock"
XRAY_RUNTIME = CACHE_DIR / "xray-runtime.json"
XRAY_LOG = CACHE_DIR / "xray.log"
# Старые полные конфиги xray (теперь их задают AV_VPN_URL или AV_VPN_CONFIG) — там же, где были
LEGACY_VPN_FILES = (Path.home() / ".asoplay" / "vpn.json", ROOT / "vpn.txt")

# ---------- сайт (при старте) ----------
SITE_URL = os.environ.get("AV_SITE_URL", "http://127.0.0.1:8787").rstrip("/")
SITE_NAME = os.environ.get("AV_SITE_NAME", "AsoPlay")
AUTHOR_NAME = "Чепела Даниэль Максимович"
AUTHOR_URL = "https://crazydev.pro/"
# Страницы, которым можно ходить в API из браузера: AV_ALLOWED_ORIGINS — свой список через запятую или «*»,
# без него — адреса локальной разработки
_DEV_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:8787",
    "http://localhost:8787",
]
_allowed = os.environ.get("AV_ALLOWED_ORIGINS", "").strip()
if _allowed == "*":
    ALLOWED_ORIGINS: list[str] = ["*"]
elif _allowed:
    ALLOWED_ORIGINS = [o.strip() for o in _allowed.split(",") if o.strip()]
else:
    ALLOWED_ORIGINS = _DEV_ORIGINS

# ---------- вход и сессии (при старте) ----------
SESSION_TTL_DAYS = int(os.environ.get("AV_SESSION_TTL_DAYS", "30") or 30)
COOKIE_SECURE = os.environ.get("AV_COOKIE_SECURE", "0") in ("1", "true", "yes", "on")
COOKIE_SAMESITE = os.environ.get("AV_COOKIE_SAMESITE", "lax").lower()
AUTH_VERIFIER = (os.environ.get("AV_AUTH_VERIFIER") or "auto").strip().lower()

# ---------- плеер (при старте) ----------
PLAYER_MAX_TEXT_REWRITE = int(os.environ.get("AV_PLAYER_PROXY_MAX_TEXT_REWRITE", str(3 * 1024 * 1024)))


# ---------- запуск ----------
def bind_host() -> str:
    """Адрес, на котором слушает uvicorn."""
    return os.environ.get("AV_BIND_HOST", "127.0.0.1")


def bind_port() -> int:
    """Порт uvicorn."""
    return int(os.environ.get("AV_BIND_PORT", "8787"))


# ---------- база Anime Social ----------
def _php_defines(path: Path) -> dict[str, str]:
    """define('NAME', 'value') из PHP-конфига соцсети."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    return {
        m.group(1): m.group(2)
        for m in re.finditer(
            r'define\s*\(\s*["\']([A-Z_]+)["\']\s*,\s*["\']([^"\']*)["\']\s*\)',
            text,
        )
    }


def db_settings() -> dict[str, Any]:
    """Подключение к БД: db.php, поверх — AV_DB_* (пустой AV_DB_PASS — тоже значение)."""
    php = _php_defines(DB_PHP)
    return {
        "host": os.environ.get("AV_DB_HOST") or php.get("DBHOST", "127.0.0.1"),
        "user": os.environ.get("AV_DB_USER") or php.get("DBUSER", "root"),
        "password": os.environ.get("AV_DB_PASS", php.get("DBPASS", "")),
        "database": os.environ.get("AV_DB_NAME") or php.get("DBNAME", "AnimeSocial"),
        "prefix": os.environ.get("AV_DB_PREFIX") or php.get("DBPREFIX", "Just"),
        "port": int(os.environ.get("AV_DB_PORT", "3306") or 3306),
    }


def user_table() -> str | None:
    """AV_USER_TABLE — таблица людей, если автоопределение ошиблось."""
    return os.environ.get("AV_USER_TABLE")


def user_column(env_key: str) -> str | None:
    """Колонка таблицы людей из AV_USER_ID_COL, AV_USER_LOGIN_COL, AV_USER_EMAIL_COL, AV_USER_HASH_COL и т. п."""
    return os.environ.get(env_key)


def user_banned_values() -> str:
    """AV_USER_BANNED_VALUES — значения колонки статуса, при которых вход закрыт, через запятую."""
    return os.environ.get("AV_USER_BANNED_VALUES", "").strip()


def register_url_override() -> str:
    """AV_REGISTER_URL — старая настройка: перекрывает страницу регистрации из animesocial.json."""
    return os.environ.get("AV_REGISTER_URL", "").strip()


# ---------- Anime Social: API, вход, realtime ----------
def animesocial_site_url() -> str | None:
    """AV_ANIMESOCIAL_SITE_URL — сайт соцсети поверх animesocial.json."""
    return os.environ.get("AV_ANIMESOCIAL_SITE_URL")


def as_api_url() -> str:
    """AV_AS_API_URL — адрес API v2, если он не «сайт соцсети + /api/v2»."""
    return os.environ.get("AV_AS_API_URL", "").strip().rstrip("/")


def as_client_keys() -> tuple[str, str]:
    """Ключи клиента AsoPlay в соцсети: AV_AS_CLIENT_ID и AV_AS_CLIENT_SECRET."""
    return (os.environ.get("AV_AS_CLIENT_ID", "").strip(), os.environ.get("AV_AS_CLIENT_SECRET", "").strip())


def as_ca() -> str:
    """AV_AS_CA — сертификат соцсети (путь к файлу или «system»); пусто — certifi."""
    return os.environ.get("AV_AS_CA", "").strip()


def as_login_flag() -> bool:
    """AV_AS_LOGIN — включён ли вход через Anime Social."""
    return os.environ.get("AV_AS_LOGIN", "0").strip().lower() in ("1", "true", "yes", "on")


def as_redirect_uri() -> str:
    """AV_AS_REDIRECT_URI — адрес возврата после входа, если его нельзя вывести из запроса."""
    return os.environ.get("AV_AS_REDIRECT_URI", "").strip()


def as_origins() -> list[str]:
    """AV_AS_ORIGINS — ещё адреса сайта соцсети через запятую (кроме того, что в animesocial.json)."""
    return [o for o in os.environ.get("AV_AS_ORIGINS", "").split(",") if o.strip()]


def token_key() -> str:
    """AV_TOKEN_KEY — ключ шифрования токенов соцсети в базе (base64, 32 байта)."""
    return os.environ.get("AV_TOKEN_KEY", "").strip()


def admin_secret() -> bytes:
    """AV_ADMIN_SECRET — общий секрет с панелью Anime Social для подписи админ-API."""
    return os.environ.get("AV_ADMIN_SECRET", "").strip().encode("utf-8")


# ---------- списки ----------
def lists_sweep_at() -> str:
    """AV_LISTS_SWEEP_AT — когда идёт правило 30 дней: UTC «ЧЧ:ММ» (по умолчанию 04:00), off — не по расписанию."""
    return os.environ.get("AV_LISTS_SWEEP_AT", "04:00").strip() or "04:00"


# ---------- каталог Jikan ----------
def jikan_php() -> str:
    """AV_JIKAN_PHP — PHP для встроенного Jikan; пусто — PHP из OSPanel или из PATH."""
    return os.getenv("AV_JIKAN_PHP", "").strip()


def ospanel_dir() -> Path:
    """Папка OSPanel на Windows (переменная OSPanel, иначе C:/OSPanel)."""
    return Path(os.getenv("OSPanel", "C:/OSPanel"))


# ---------- VPN-мост (xray) ----------
def vpn_enabled() -> bool:
    """AV_VPN_ENABLED=0 выключает мост; по умолчанию он включён."""
    return os.environ.get("AV_VPN_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def vpn_url() -> str:
    """AV_VPN_URL — ссылка VLESS (Reality) для моста."""
    return os.environ.get("AV_VPN_URL", "").strip()


def ss_address() -> str:
    """SS_ADDRESS — сервер запасного Shadowsocks."""
    return os.environ.get("SS_ADDRESS", "").strip()


def ss_password() -> str:
    """SS_PASSWORD — пароль запасного Shadowsocks."""
    return os.environ.get("SS_PASSWORD", "").strip()


def ss_port() -> str | None:
    """SS_PORT — порт запасного Shadowsocks, строкой: число из неё делает мост."""
    return os.environ.get("SS_PORT")


def ss_method() -> str:
    """SS_METHOD — шифр запасного Shadowsocks."""
    return os.environ.get("SS_METHOD", "chacha20-ietf-poly1305")


def vpn_config_file() -> str:
    """AV_VPN_CONFIG — путь к полному конфигу xray вместо шаблона."""
    return os.environ.get("AV_VPN_CONFIG", "").strip()


def vpn_watchdog_interval() -> float:
    """AV_VPN_WATCHDOG_INTERVAL — как часто сторож проверяет порт моста, секунды (не меньше 0,25)."""
    return max(0.25, float(os.environ.get("AV_VPN_WATCHDOG_INTERVAL", "0.5")))


# ---------- блокировка рекламы ----------
def _csv(name: str) -> tuple[str, ...]:
    """Список через запятую из переменной окружения."""
    raw = os.environ.get(name, "")
    return tuple(x.strip() for x in raw.split(",") if x.strip())


def adblock_enabled() -> bool:
    """AV_ADBLOCK_ENABLED=0 выключает фильтр; без переменной он включён."""
    raw = os.environ.get("AV_ADBLOCK_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def adblock_filter_urls() -> tuple[str, ...]:
    """AV_ADBLOCK_FILTER_URLS — свои списки фильтров вместо встроенных."""
    return _csv("AV_ADBLOCK_FILTER_URLS")


def adblock_allow_hosts() -> tuple[str, ...]:
    """AV_ADBLOCK_ALLOW_HOSTS — хосты, которые фильтр не трогает."""
    return _csv("AV_ADBLOCK_ALLOW_HOSTS")


def adblock_cache_ttl() -> int:
    """AV_ADBLOCK_CACHE_TTL — сколько секунд списки фильтров живут в кеше (не меньше часа)."""
    return max(3600, int(os.environ.get("AV_ADBLOCK_CACHE_TTL", "86400")))


def adblock_regex_limit() -> int:
    """AV_ADBLOCK_REGEX_LIMIT — сколько правил-регулярок держать."""
    return int(os.environ.get("AV_ADBLOCK_REGEX_LIMIT", "25000"))


def adblock_snippet_limit() -> int:
    """AV_ADBLOCK_SNIPPET_LIMIT — сколько правил-фрагментов держать."""
    return int(os.environ.get("AV_ADBLOCK_SNIPPET_LIMIT", "25000"))
