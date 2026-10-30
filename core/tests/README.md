# Проверки AsoPlay

Проверки ядра лежат здесь, в `core/tests/`. Проверки фронтенда — в `design/EDDAS/tests/`, сторожа рефакторинга —
в `tools/guard/`, браузерный QA — в `tools/qa/`.

## Как запускать

Все команды — из корня проекта:

```bash
python -B core/tests/run.py                      # все разделы ядра, каждый в своём процессе
python -B core/tests/run.py admin_api presence   # только эти разделы
python -m core.tests.test_presence               # один раздел, если Python видит корень (venv на сервере)
node --test design/EDDAS/tests/*.test.cjs        # фронтенд
python -B tools/guard/all.py                     # быстрые сторожа
python -B tools/guard/all.py --full              # всё: ядро, фронтенд, ответы сервера, стили, QA
```

`run.py` выполняет раздел так же, как `python -m core.tests.test_{раздел}`, и сам кладёт корень проекта в путь
импорта. Встраиваемый Python (OSPanel, `python313._pth`) не добавляет туда текущую папку даже для `-m`.

Раздел — это `test_{раздел}.py`. Большой раздел — вход `test_{раздел}.py` и папка частей `{раздел}/`:

- вход собирает части по порядку через `load_tests`;
- общее для частей лежит в `{раздел}/lib.py`;
- ни вход, ни части не трогают `sys.path`.

## Что нужно каждому разделу

| Раздел | Что проверяет | Что нужно |
|---|---|---|
| `admin_api` | панель Anime Social (`core/admin`) и что её решения делают с сайтом | ничего |
| `as_login` | вход через Anime Social, токены, доверенный клиент | ничего; без пакета `cryptography` две проверки шифрования пропускаются |
| `as_realtime` | связь хаба с realtime соцсети | ничего |
| `config_env` | настройки старта видят `.env` | ничего: `.env` временный |
| `embedded_jikan` | встроенный каталог Jikan | PHP и зависимости из `composer.lock` (`composer install` в `core/classes/jikan/php`) — как и самому сайту |
| `login_rate_limit` | лимит попыток входа по паролю | ничего |
| `player_diagnostics`, `player_modules`, `player_selection` | плеер: диагностика, модули, выбор источника | ничего |
| `presence` | присутствие и друзья из Anime Social | ничего |
| `profile_posters` | постеры и названия в ленте активности | ничего |
| `proxy_resilience` | VPN-мост, маршруты, кеш и сроки запросов | ничего |
| `realtime` | живое соединение `/ws` | ничего |

Ни один раздел не ходит в сеть и не трогает живую базу: соединение с базой, соцсеть и чужие сайты подменены.

Проверкам фронтенда (`design/EDDAS/tests/*.test.cjs`) тоже ничего не нужно, кроме Node 20 и новее. До шага 6
рефакторинга фронтенд лежит в корне проекта (`app.js`, `js/`), и проверки читают его оттуда.

## Сторожа и QA

| Что | Что нужно |
|---|---|
| `lines`, `imports`, `docblocks`, `contract`, `decls`, `player` | ничего; их базы лежат в git (`tools/guard/baseline/`) |
| `responses` | запущенный сервер, база данных (временный аккаунт), `AV_ADMIN_SECRET` в `.env`; база ответов — через `--save` |
| `styles` | запущенный сервер, Chrome, playwright-core; база — через `--save` |
| `firstload` | запущенный сервер (или `--site`), Chrome, playwright-core; база — через `--save` |
| `sql_trace` | копия кода до шага (git worktree): `python -B tools/guard/sql_trace.py <копия> [account\|lists]`; база данных не нужна |
| QA, `tools/qa/eddas-qa.cjs` | запущенный сервер, Chrome, playwright-core; снимки — в `docs/internal/eddas/qa` |

Некоторые базы снимаются на месте через `--save` и в git не лежат:

- `tools/guard/baseline/responses.json` — ответы локальной базы;
- `tools/guard/baseline/styles/` — снимки стилей и записанные ответы API;
- `tools/guard/baseline/firstload-*.json` — замеры этой машины.

Снимать их — в начале шага, на коде до изменений, а сравнивать — в тот же день: календарь активности считается от
сегодня.

Из чистой копии (только файлы из git) всё зелёное, кроме сторожей `responses`, `styles` и `firstload`: им сначала
нужна база, снятая через `--save`.

## Переменные окружения

- `PW_CORE` — папка пакета playwright-core, если он не установлен рядом (иначе `require("playwright-core")`).
- `CHROME` — путь к `chrome.exe`; без него берётся установленный Chrome (channel `chrome`).
- `SITE` — адрес сервера для `styles` и QA, по умолчанию `http://127.0.0.1:8787`.
- `QA_OUT` — куда класть снимки QA.
- `AV_DB_PORT=1` — страховка. Проверка на подменённой базе (`sql_trace`) ставит её в своём процессе: случайное
  соединение упадёт, а не запишет в настоящую базу. С ней можно запускать и `core/tests` — живая база им не нужна.

## Правило

Проверки никогда не действуют от имени живых людей — ни локально, ни на dev. Можно только:

- временный аккаунт, который проверка создаёт и удаляет сама (`tools/guard/temp_account.py`). id ему выдаёт база:
  явных id в таблицах `Just_*` со счётчиком AUTO_INCREMENT нет;
- несуществующий id `999999999`;
- подменённые ответы. В QA зритель `qa-viewer` (id 900001) — это подменённый ответ `/auth/me`, в базу он не пишется.
