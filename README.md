# AsoPlay

![Status](https://img.shields.io/badge/status-production--ready-22c55e)
![Backend](https://img.shields.io/badge/backend-FastAPI-009688)
![Frontend](https://img.shields.io/badge/frontend-vanilla%20JS-f7df1e)
![Database](https://img.shields.io/badge/database-MySQL%208-4479a1)
![License](https://img.shields.io/badge/license-proprietary-red)

**Язык:** Русский | [English](#english)

**AsoPlay** — сервис для просмотра аниме с каталогом тайтлов, поиском,
SEO-страницами, AsoPlayer, личными списками, прогрессом просмотра,
публичными профилями и интеграцией с AnimeSocial. Фронтенд написан на vanilla
JavaScript, бэкенд работает на Python/FastAPI, а пользовательские данные
хранятся в MySQL.

> Copyright © Чепела Даниэль Максимович (x0doit). Все права защищены.
> Репозиторий не является open source. Любое использование требует прямого
> письменного разрешения правообладателя.

---

## Содержание

- [Возможности](#возможности)
- [Дизайн-система EDDAS](#дизайн-система-eddas)
- [AsoPlayer и Shield](#asoplayer-и-shield)
- [Архитектура](#архитектура)
- [Стек](#стек)
- [Развертывание владельцем](#развертывание-владельцем)
- [Конфигурация](#конфигурация)
- [Модель данных](#модель-данных)
- [Безопасность](#безопасность)
- [Авторские права и лицензия](#авторские-права-и-лицензия)
- [English](#english)

---

## Возможности

- **Каталог MAL** через встроенную PHP-библиотеку Jikan 5. Публичный API
  Jikan не используется; AniList дополняет баннеры, Shikimori даёт русские названия.
- **Страницы тайтлов** `/anime/{mal_id}-{slug}/` с мета-тегами на стороне
  сервера, Open Graph, JSON-LD, canonical URL, sitemap и `<noscript>`
  fallback для просмотра без JavaScript.
- **AsoPlayer** с несколькими источниками, резервным поиском по доступным
  провайдерам, быстрыми скриншотами, фирменным контекстным меню и безопасной
  обработкой iframe `postMessage`.
- **AsoPlay Shield**: same-origin player proxy, request-level filtering,
  runtime AdGuard-backed правила, popup/navigation hardening и аккуратная
  работа с sandbox iframe.
- **Интеграция AnimeSocial**: авторизация через существующую MySQL-базу
  AnimeSocial и локальные HTTP-only сессии проекта.
- **Личный кабинет**: избранное, продолжить просмотр, списки, статусы,
  оценки, выбранные озвучки, настройки и приватность.
- **Друзья и «онлайн на AsoPlay»**: страница «Друзья» как в AnimeSocial — кто сейчас на
  AsoPlay и что смотрит, кто в сети в соцсети, кто когда был, поиск, заявки (принять, отклонить,
  отменить), «Возможные друзья», «Удалить из друзей», «Написать» (сообщение уходит в переписку
  соцсети) и колонка «Смотрят сейчас»; блок «Друзья» на странице тайтла; в самой соцсети друзья
  видят «онлайн на AsoPlay». Всё меняется сразу — по живому соединению вкладки с сервером и
  сервера с realtime соцсети — и настраивается в приватности.
- **Раздел «AsoPlay» в панели AnimeSocial**: администраторы соцсети управляют сайтом из её `/cpanel`.
  Кнопка «Не работает» в плеере отправляет жалобу — как на сайте соцсети, вместе с серией, озвучкой
  и источником — в очередь модераторов. Тайтл можно скрыть по жалобе правообладателя (страница
  остаётся, видео нет, для поисковиков — 451), закрепить в карусели главной («Рекомендуем»),
  пометить 18+ (метка и вопрос «Мне есть 18» перед видео), сбросить его кеш. Ещё — плашка-объявление
  на сайте, выключатели входа по почте и через AnimeSocial, включение и порядок источников видео,
  «Обзор» (здоровье источников, люди, просмотры, топ недели) и журнал действий. Правка из панели
  сразу видна на открытых страницах.
- **Автоматические статусы списков**: auto-watching, auto-completed и
  stale auto-dropped на основе реального прогресса просмотра.
- **Реальная логика просмотра**: прогресс фиксируется по событиям плеера, а не
  по времени нахождения на странице.
- **Публичные профили** со списками, которые учитывают настройки приватности,
  и графиком активности.
- **Прокси, health-state и кэш во время работы** для внешних API, источников
  видео, данных и изображений.
- **Опциональный xray bridge (VLESS + Reality или Shadowsocks)** для окружений,
  где внешние anime API недоступны напрямую.
- **Интерфейс на дизайн-системе EDDAS**, общей с AnimeSocial: светлая и тёмная
  тема, телефон как приложение. Подробнее — в следующем разделе.

---

## Дизайн-система EDDAS

Фронтенд собран на EDDAS — дизайн-системе социальной сети AnimeSocial, чтобы оба
сервиса выглядели как одно семейство. Функциональность от этого не меняется.

- **Файлы системы** лежат в `assets/eddas/`:
  - `css/tokens.css` — токены светлой и тёмной темы и подключение шрифтов;
  - `css/eddas.css` — компоненты `ed-*` (кнопки, поля, вкладки, чипы, меню, окна,
    пустые состояния);
  - `css/app.css` — каркас и общие примитивы: шапка, меню слева, нижняя панель и
    лист «Ещё» на телефоне, страница, формы, плитки, списки;
  - `css/fontawesome.css` и `fonts/fontawesome/` — Font Awesome Free 7.3.1 из
    npm-пакета `@fortawesome/fontawesome-free`;
  - `fonts/onest/` и `fonts/unbounded/` — шрифты Onest и Unbounded с лицензиями OFL.
- `tokens.css` и `eddas.css` скопированы из дизайн-системы AnimeSocial без
  изменений. `app.css` содержит только нужную AsoPlay часть каркаса соцсети.
  Файлы обновляются из того же источника; Font Awesome — только из npm.
- **`styles.css`** описывает только экраны AsoPlay: постеры, карусель, каталог,
  страницу тайтла, плеер, серии, вход, списки, настройки и профиль. В стилях только
  токены `var(--…)`, без своих цветов, градиентов, свечений и стекла.
- **Ширина**: колонка контента до 1440px (в соцсети 1200), чтобы плеер рядом с меню и списком серий
  оставался крупным. Серии стоят справа от плеера на экранах от 1312px, на более узких — сеткой под ним.
- **Шрифты**: Onest везде, Unbounded — только логотип и hero на странице входа.
  Внешние CDN, Google Fonts и kit.fontawesome.com не используются: всё грузится с
  того же сервера.
- **Тема**: как в системе, светлая или тёмная. Переключатель есть в меню слева, в
  листе «Ещё», в меню профиля и в настройках. Выбор хранится в `localStorage` под
  ключом `as-theme`, как в AnimeSocial, и применяется до отрисовки, без вспышки.
- **Телефон**:
  - страница не масштабируется;
  - шапка закреплена;
  - страница не пружинит за краями;
  - навигация — нижняя панель и лист «Ещё»;
  - выбор озвучки и окна — листы снизу, которые смахиваются пальцем;
  - подборки на главной листаются вбок, карусель — свайпом;
  - постеры в каталоге и списках идут в две колонки;
  - если повернуть телефон боком, плеер вместе с выбором озвучки помещается в экран.
- **Ленты вбок** (сезоны, вкладки, график активности): с мышью видна тонкая полоса
  прокрутки, ленту можно крутить колесом и тянуть мышью; на сенсорных экранах она
  листается пальцем.
- **Профиль**: сверху активность с графиком, под ней списки и история действий в
  две колонки (на узком экране одна под другой).
- **Плеер**: AsoPlayer и обложка Kodik используют тёмную палитру EDDAS. Документ
  плеера не видит токены сайта, поэтому значения тёмной темы записаны в нём явно.

---

## AsoPlayer и Shield

AsoPlayer — это не отдельная библиотека, а прикладной player-слой проекта:
страница управляет выбором источника, прогрессом, автопереходом серий,
контекстным меню и пользовательскими действиями, а iframe-bridge отвечает за
безопасную связь с фактическим плеером.

Ключевые возможности:

- Контекстное меню по правому клику: `Сделать скриншот`,
  `Копировать ссылку на аниме`, `Открыть AnimeSocial`.
- Быстрые PNG-скриншоты текущего кадра с чистыми именами файлов:
  `AsoPlay-{mal_id}-ep{episode}-{hh}-{mm}-{ss}.png`.
- Отдельный Kodik skin в `assets/player/kodik-skin.css` с акцентом тёмной
  темы EDDAS `#9db3cc`.
- Same-origin player proxy для iframe и media URL.
- AsoPlay Shield для фильтрации рекламных URL, блокировки popup/navigation
  сценариев и защиты от шумных third-party scripts внутри sandbox.
- Отдельные служебные player-ресурсы: `/player/asoplay-shield.js`,
  `/player/kodik-skin.css`, `/player/frame`, `/player/proxy`,
  `/player/cvh-api/*`.

---

## Архитектура

```text
.
├── index.html                  каркас EDDAS (шапка, меню, нижняя панель) и SSR placeholders
├── app.js                      публичный router, каталог, тайтл, player
├── styles.css                  экраны AsoPlay на токенах EDDAS
├── assets/                     статические media assets
│   ├── eddas/                  дизайн-система EDDAS: токены, компоненты, каркас, шрифты, значки
│   └── player/
│       └── kodik-skin.css      AsoPlayer/Kodik visual skin
├── js/
│   ├── account.js              auth UI, личные разделы, store, профили
│   ├── navigation.js           каркас: лист «Ещё», поиск в шапке, тема, активный раздел
│   ├── profile-activity.js     группировка дневника и календарь активности
│   ├── realtime.js             живое соединение вкладки с сервером (WebSocket /ws)
│   ├── site.js                 решения панели AnimeSocial: плашка, скрытые и 18+ тайтлы, вход
│   └── trailer.js              окно трейлера
├── core/                       бэкенд, пакет Python: запуск python -m core
│   ├── __main__.py             запуск uvicorn (адрес и порт — AV_BIND_HOST, AV_BIND_PORT)
│   ├── core.py                 FastAPI wiring, static serving, sources
│   ├── requirements.txt        Python runtime dependencies
│   ├── api/sections/           разделы API сайта
│   │   ├── account/            /account/…: вход __init__.py подключает части в порядке маршрутов —
│   │   │                       favorites, history, progress, ratings, settings, import_storage
│   │   └── lists/              /account/lists/…: entries (записи), progress_event (события плеера)
│   ├── admin/                  админ-API для раздела «AsoPlay» в /cpanel AnimeSocial (HMAC)
│   │   ├── router.py           вход: подключает части в порядке маршрутов
│   │   ├── auth.py             подпись, повторы, ошибки, обёртка ответов, страницы
│   │   └── reports.py, audit.py, settings.py, titles.py, summary.py   разделы панели
│   ├── modules/
│   │   ├── title_pages.py      canonical anime pages, sitemap, robots
│   │   ├── profile_pages.py    public profile API и SSR shell
│   │   └── player/routes.py    same-origin player proxy, Shield, iframe bridge
│   ├── functions/
│   │   ├── social_login.py     вход через AnimeSocial (OAuth 2.0 + PKCE) и его сессии
│   │   ├── presence.py         присутствие на AsoPlay и друзья из AnimeSocial
│   │   ├── realtime.py         WebSocket /ws: вкладки, события друзьям, состояние сервера
│   │   ├── site_settings.py    что решила панель: пометки тайтлов, плашка, функции, источники
│   │   ├── video_reports.py    жалобы «Не работает» из плеера
│   │   ├── lists.py            list/status/favorite domain и auto-rules
│   │   ├── progress.py         позиции в сериях, «досмотрено», пороги «Смотрю»
│   │   ├── watch_history.py    история «Смотрю» и авто-статус после «не смотрел»
│   │   ├── privacy.py          флаги приватности и их чтение
│   │   ├── watch_time.py       сколько посмотрели — для «Обзора» в панели
│   │   ├── activity.py         contribution/activity event log
│   │   ├── rate_limit.py       лимит попыток входа и IP посетителя за прокси
│   │   ├── source_health.py    source failure/cooldown state
│   │   └── player_hosts.py     политика плееров: VK, неиграющие плееры
│   ├── classes/
│   │   ├── db.py               AnimeSocial DB auth bridge и sessions
│   │   ├── as_api.py           клиент API v2 AnimeSocial
│   │   ├── as_realtime.py      соединение сервер–сервер с realtime AnimeSocial
│   │   ├── token_box.py        шифрование токенов соцсети в базе (AES-GCM)
│   │   ├── http.py             Jikan/AniList/Shiki/translate/image proxies
│   │   ├── jikan/catalog.py    встроенный каталог MAL, кэш и лимит запросов
│   │   ├── jikan/php/          PHP-мост, Composer manifest и lockfile
│   │   ├── adblock/engine.py   AdGuard-backed request filtering
│   │   ├── vpn/bridge.py       optional xray runtime bridge
│   │   ├── sources/animevost.py native AnimeVost source adapter
│   │   └── sources/oldyummy.py native old.yummyani source adapter
│   ├── config/
│   │   ├── env.py              корень проекта и загрузка .env при старте
│   │   ├── config.py           все настройки и переменные окружения в одном месте
│   │   ├── animesocial.py      AnimeSocial URL/media configuration
│   │   ├── animesocial.json    public URL/media mapping для AnimeSocial
│   │   ├── db.php              формат DB-конфига AnimeSocial
│   │   ├── server/             шаблон xray-конфига и unit systemd
│   │   └── cache/              рабочие файлы: HTTP-кеш, adblock, xray (вне git)
│   ├── tests/                  проверки ядра: раздел — test_{раздел}.py, большой — вход и папка частей;
│   │                           запуск run.py, как запускать и что нужно — README.md
│   └── migrations/
│       ├── 0000_schema.sql                       базовая схема aviev_*
│       ├── 2026-04-30_lists_privacy_activity.sql списки, приватность, активность, compatibility
│       ├── 2026-10-30_admin_panel.sql            жалобы, пометки тайтлов, настройки сайта, журнал, время просмотра
│       ├── 2026-10-30_as_sessions.sql            сессии входа через AnimeSocial
│       └── 2026-10-30_presence.sql               настройки присутствия на AsoPlay
├── design/EDDAS/tests/         проверки фронтенда (Node); до шага 6 читают app.js и js/ из корня
├── tools/
│   ├── guard/                  сторожа рефакторинга: длины, импорты, docblock, контракт, ответы, стили (all.py)
│   └── qa/                     браузерный QA экранов: eddas-qa.cjs и сценарии в scenarios/
├── .env.example                шаблон environment variables
├── COPYRIGHT                   proprietary copyright notice
└── .gitignore                  ignored local/runtime artifacts
```

---

## Стек

- **Frontend:** HTML, CSS, vanilla JavaScript ES modules, дизайн-система EDDAS
  (как в AnimeSocial), шрифты Onest и Unbounded, Font Awesome Free 7.3.1 — локально.
- **Backend:** Python 3.11+, FastAPI, Uvicorn, httpx.
- **Database:** MySQL 8 / OpenServer-compatible AnimeSocial database.
- **Auth:** существующая users-таблица AnimeSocial + локальные `aviev_sessions`.
- **Metadata:** Jikan, AniList, Shikimori.
- **Embedded parser:** Jikan 5.0.0, PHP 8.4.4+ с DOM и mbstring.
- **Player layer:** same-origin iframe/media proxy, sandbox bridge,
  request-level ad filtering.
- **Network bridge:** optional xray (VLESS + Reality или Shadowsocks) через `.env`.

---

## Развертывание владельцем

Этот раздел предназначен только для владельца проекта или лиц, получивших
прямое письменное разрешение. Наличие инструкций по запуску не предоставляет
права использовать, копировать, разворачивать или публиковать проект.

Установить Python dependencies:

```bash
pip install -r core/requirements.txt
```

Установить зафиксированные зависимости встроенного парсера:

```bash
composer --working-dir=core/classes/jikan/php install --no-dev --no-scripts --no-plugins --optimize-autoloader
```

Используйте актуальную исправленную версию PHP 8.4/8.5. На Windows бэкенд
находит PHP в OSPanel, на Linux использует `php` из PATH. Другой путь можно
задать через `AV_JIKAN_PHP` в `.env`. Отдельный PHP-сайт, Jikan REST, Docker,
MongoDB и поисковый сервер не нужны. Бэкенд получает HTML MyAnimeList,
а библиотека Jikan разбирает его в дочернем CLI-процессе без сетевого доступа.
Результаты кэшируются; одинаковые запросы объединяются, новые запросы к MAL
ограничены одним в секунду на процесс AsoPlay. Доступ к MAL всё ещё необходим:
это встроенный парсер, а не локальная копия всей базы MAL. `/health` показывает
версию и готовность движка в поле `catalog`; это не гарантия доступности MAL.

Создать runtime configuration:

```bash
copy .env.example .env
```

Применить database schema строго по порядку:

```bash
mysql -u root AnimeSocial < core/migrations/0000_schema.sql
mysql -u root AnimeSocial < core/migrations/2026-04-30_lists_privacy_activity.sql
mysql -u root AnimeSocial < core/migrations/2026-10-30_admin_panel.sql
mysql -u root AnimeSocial < core/migrations/2026-10-30_as_sessions.sql
mysql -u root AnimeSocial < core/migrations/2026-10-30_presence.sql
```

Запустить приложение:

```bash
python -B -m core
```

Локальный адрес по умолчанию:

```text
http://127.0.0.1:8787
```

Health endpoint:

```text
GET /health
```

Ожидаемый ответ:

```json
{
  "ok": true,
  "sources": ["..."],
  "vpn": true,
  "db": {
    "ok": true,
    "aviev_schema_present": true
  },
  "adblock": {
    "enabled": true,
    "ready": true
  },
  "source_health": {
    "...": {
      "available": true
    }
  }
}
```

---

## Конфигурация

Backend читает runtime settings из `.env`, `core/config/db.php` и
`core/config/animesocial.json`; все переменные собраны в `core/config/config.py`.

Основные группы переменных:

- `AV_BIND_HOST`, `AV_BIND_PORT` — адрес FastAPI.
- `AV_SITE_URL`, `AV_SITE_NAME` — canonical SEO URLs и имя сайта.
- `AV_ALLOWED_ORIGINS` — CORS origins для development/production.
- `AV_DB_*` — optional DB overrides.
- `AV_USER_*` — optional AnimeSocial users-table/column overrides.
- `AV_AUTH_VERIFIER` — override password verification strategy.
- `AV_COOKIE_SECURE`, `AV_COOKIE_SAMESITE`, `AV_SESSION_TTL_DAYS` — session
  cookie behavior.
- `AV_ANIMESOCIAL_SITE_URL` — public host override для AnimeSocial.
- `AV_AS_CLIENT_ID`, `AV_AS_CLIENT_SECRET` — ключи клиента AsoPlay в AnimeSocial
  («Панель → Система → Сайты-клиенты»); уходят в каждом запросе к API вместе с адресом и
  браузером человека.
- `AV_AS_LOGIN`, `AV_TOKEN_KEY` — вход через AnimeSocial: включатель и ключ шифрования
  токенов соцсети (32 байта в base64). `AV_AS_API_URL`, `AV_AS_REDIRECT_URI` — адреса
  API и возврата, если они не выводятся из адреса соцсети и текущего запроса. `AV_AS_ORIGINS` —
  дополнительные адреса сайта соцсети, которым `/auth/as/whoami` отвечает с cookie.
- `AV_AS_CA` — каким сертификатам доверять в запросах к соцсети: путь к файлу CA или
  `system` — хранилище системы (локальный движок в OSPanel с самоподписанным
  сертификатом). Пусто — набор certifi.
- `AV_ADMIN_SECRET` — общий секрет подписи запросов раздела «AsoPlay» панели AnimeSocial
  (тот же, что в настройках раздела у соцсети). Панель ходит на `127.0.0.1:8787/api/admin/…` с этой
  же машины; через публичный прокси API не отвечает. Пусто — API выключен.
- `AV_ADBLOCK_ENABLED`, `AV_ADBLOCK_FILTER_URLS`,
  `AV_ADBLOCK_CACHE_TTL_SECONDS` — player Shield filtering behavior.
- `AV_ADBLOCK_REGEX_LIMIT`, `AV_ADBLOCK_SNIPPET_LIMIT` — parser safety limits
  для runtime filters.
- `AV_PLAYER_PROXY_MAX_TEXT_REWRITE` — лимит размера текстовых player-ресурсов
  для rewriting.
- `AV_VPN_ENABLED` — `0` временно отключает xray bridge, не стирая параметры
  сервера.
- `AV_VPN_URL` — ссылка `vless://` (VLESS + Reality) для xray bridge. Запросы
  идут напрямую; через bridge повторяется только запрос к хосту, недоступному
  напрямую, и такой хост 10 минут ходит через bridge первым. Плееры всегда идут
  напрямую.
- `SS_ADDRESS`, `SS_PORT`, `SS_METHOD`, `SS_PASSWORD` — Shadowsocks для старых
  серверов; используются, только если `AV_VPN_URL` не задан.

Production secrets должны храниться только на сервере. Нельзя коммитить реальные
пароли, токены, proxy credentials, session data или приватные ключи.

---

## Модель данных

Проект создает и использует только таблицы с префиксом `aviev_`. Существующие
таблицы AnimeSocial остаются источником identity и login credentials.

Вход по паролю защищён от перебора теми же лимитами, что и вход на сайте AnimeSocial:
не больше 20 неудачных попыток с одного адреса и 8 на один логин за 15 минут, дальше
ответ 429 с `Retry-After`. Адрес посетителя берётся из `X-Real-IP`, который выставляет
nginx перед AsoPlay; заголовкам прокси сервер верит только у запросов с 127.0.0.1.

Вход через AnimeSocial (`core/functions/social_login.py`) — OAuth 2.0 + PKCE поверх API v2 соцсети:
кнопка ведёт на `/auth/as/start`, соцсеть спрашивает «Войти в AsoPlay?» и возвращает код
на `/auth/as/callback`, AsoPlay меняет его на токены и открывает свою сессию. Сессия
устройства соцсети хранится в `aviev_as_sessions` с зашифрованными токенами и
завершается при выходе из AsoPlay; вход на самом сайте соцсети при этом остаётся.
Кто вошёл в соцсеть в этом браузере, страница узнаёт у соцсети (`/api/v2/auth/web/session`):
такому человеку страница входа показывает кнопку «Продолжить как Имя Фамилия» и под ней
«Войти через почту», остальным — сразу форму почты, а гостю на любой странице — карточку
«Имя Фамилия, это вы?». Обратно сайт соцсети спрашивает AsoPlay через `/auth/as/whoami` и
входит под тем же аккаунтом через `/auth/as/to-social`. Функция включается `AV_AS_LOGIN=1`, когда задан
`AV_TOKEN_KEY`, установлен пакет `cryptography`, а движок соцсети разрешил адрес возврата.
Форма почты с включённой соцсетью тоже входит через неё (`POST /auth/login` с ключами клиента):
пароль проверяет соцсеть, вход — только по почте, как на её сайте, и у человека появляется та же
сессия AsoPlay в соцсети, что после кнопки. Без ключей клиента форма сверяет пароль по таблице
соцсети и пускает по логину или почте. Кто уже вошёл по почте раньше, тем же входом через соцсеть
связывает с ней свою сессию; если в соцсети открыт другой аккаунт, вход отклоняется, а не
подменяет человека.

Живое соединение (`core/functions/realtime.py`, `js/realtime.js`). Каждая вкладка держит WebSocket `/ws`
со страниц самого AsoPlay (Origin проверяется): сообщает, на экране ли она и что играет в плеере, —
только когда это меняется; сервер присылает состояние (источники, VPN — раньше вкладки спрашивали
`/health` раз в 30 секунд) и события друзей. Обрыв замечают ping-кадры WebSocket, вкладка
переподключается сама.

Присутствие и друзья (`core/functions/presence.py`). Человек на AsoPlay, пока хоть одна его вкладка открыта
и на экране, и ещё 30 секунд после — перезагрузка не мигает «вышел». Друзьям, которые сейчас на
сайте, событие уходит сразу. Соцсети сервер сообщает присутствие по постоянному соединению
сервер–сервер (`core/classes/as_realtime.py`): при подключении — снимок, дальше — каждое изменение; обратно
приходят события его людей: друг зашёл в соцсеть или вышел, изменилась дружба, страницу заблокировали.
Пока соединения нет, работает запасной пакет `POST /clients/presence` раз в минуту. Друзья видят в
соцсети «онлайн на AsoPlay» и «смотрит «…»». Соцсеть принимает отметку у тех, у кого есть сессия AsoPlay в ней, а ссылку «смотрит» —
только на адресе сайта клиента из панели соцсети. Страница `/my/friends` берёт друзей из соцсети
(`GET /users/{id}/friends/ids` и `GET /users?ids=` по токену сессии соцсети), а кто на AsoPlay и что
смотрит — у этого сервера; блок «Друзья» на странице тайтла добавляет, у кого тайтл в списках. В
`aviev_privacy` две настройки: `hide_presence` никому не показывает, что человек здесь,
`show_watching` (по умолчанию выключено) показывает друзьям, что он смотрит; скрытые списки в блок не
попадают.

Основные таблицы проекта:

- `aviev_sessions` — HTTP-only login sessions.
- `aviev_user_lists` — canonical title status + favorite flag.
- `aviev_watch_history` — continue-watching feed.
- `aviev_episode_progress` — per-episode progress.
- `aviev_title_ratings` — personal ratings.
- `aviev_dub_prefs` — preferred dubbing per title.
- `aviev_account_settings` — account toggles.
- `aviev_privacy` — public profile privacy controls and presence on AsoPlay.
- `aviev_as_sessions` — AnimeSocial device sessions behind sign-in with AnimeSocial.
- `aviev_activity` — contribution/activity graph events.
- `aviev_title_pages` — canonical SEO title-page cache.
- `aviev_title_refresh_queue` — title page refresh queue.
- `aviev_import_marks` — one-time localStorage import markers.

`aviev_activity` использует unique deduplication index:

```sql
UNIQUE KEY uq_act_dedup (user_id, day, kind, mal_id, meta(64))
```

---

## Безопасность

- Сессионные токены хранятся на сервере и передаются через HTTP-only cookies.
- Личные API-методы доступны только при активной авторизованной сессии.
- Публичные профили отдают данные с учетом настроек приватности пользователя.
- Плеер принимает iframe `postMessage` только от ожидаемого окна источника.
- AsoPlayer использует явные bridge-сообщения для скриншотов, контекстного меню
  и закрытия меню при кликах внутри sandbox iframe.
- AsoPlay Shield проксирует player-ресурсы через same-origin слой, фильтрует
  рекламные URL и блокирует popup/top-navigation сценарии.
- Прокси ограничены конкретными сценариями для API и media-файлов.
- `robots.txt` закрывает внутренние account/auth/proxy/source endpoints.

---

## Авторские права и лицензия

Этот репозиторий **не является open source**.

Copyright © Чепела Даниэль Максимович (x0doit). Все права защищены.

Никакая лицензия третьим лицам не предоставляется. Без прямого письменного
разрешения правообладателя запрещено использовать, копировать, скачивать,
зеркалировать, изменять, запускать, разворачивать, хостить, распространять,
сублицензировать, перепродавать, публиковать, переупаковывать, использовать для
обучения моделей, включать в datasets или создавать производные работы на основе
этого репозитория или любой его части.

Полный proprietary notice: [`COPYRIGHT`](./COPYRIGHT).

Запросы по лицензированию: <https://crazydev.pro/>

---

<a id="english"></a>

# AsoPlay

**Language:** [Русский](#asoplay) | English

**AsoPlay** is an anime viewing service with a searchable title catalog,
SEO-ready title pages, AsoPlayer, personal lists, watch progress, public
profiles and AnimeSocial integration. The frontend is written in vanilla
JavaScript, the backend runs on Python/FastAPI, and user data is stored in MySQL.

> Copyright © Chepela Daniel Maximovich (x0doit). All rights reserved.
> This repository is not open source. Any use requires direct written permission
> from the copyright owner.

---

## Contents

- [Features](#features)
- [EDDAS Design System](#eddas-design-system)
- [AsoPlayer and Shield](#asoplayer-and-shield)
- [Architecture](#architecture)
- [Stack](#stack)
- [Owner Deployment](#owner-deployment)
- [Configuration](#configuration)
- [Data Model](#data-model)
- [Security](#security)
- [Copyright and License](#copyright-and-license)

---

## Features

- **MAL catalog** through the embedded Jikan 5 PHP library, without the public
  Jikan API. AniList supplies banners; Shikimori supplies Russian titles.
- **Title pages** at `/anime/{mal_id}-{slug}/` with server-rendered
  metadata, Open Graph tags, JSON-LD, canonical URLs, sitemap support and
  `<noscript>` fallbacks.
- **AsoPlayer** with multiple sources, fallback search across supported video
  providers, quick screenshots, branded context menu and safe iframe
  `postMessage` handling.
- **AsoPlay Shield:** same-origin player proxy, request-level filtering,
  runtime AdGuard-backed rules, popup/navigation hardening and careful sandbox
  iframe handling.
- **AnimeSocial authentication bridge** using the existing AnimeSocial MySQL
  account database with project-local HTTP-only sessions.
- **Personal library** with favorites, continue watching, statuses, ratings,
  dub preferences, settings and privacy controls.
- **Friends and «онлайн на AsoPlay»**: a friends page laid out like AnimeSocial's, showing who is
  on AsoPlay now and what they watch, who is online on the social network and when others were
  last seen, with a search, friend requests (accept, decline, cancel), «Возможные друзья»,
  «Удалить из друзей», «Написать» (the message goes to the social network dialog) and a
  «Смотрят сейчас» column; a «Друзья» block on title pages; on the social network itself friends
  see «онлайн на AsoPlay». Everything updates at once — over the tab's live connection to the server
  and the server's link to the social network's realtime — and follows the privacy settings.
- **The «AsoPlay» section in the AnimeSocial panel**: social network admins run the site from its
  `/cpanel`. The player's «Не работает» button files a report — like reports on the social network,
  with the episode, dub and source — into the moderators' queue. A title can be hidden after a
  copyright complaint (the page stays, the video goes, search engines get 451), pinned to the home
  carousel («Рекомендуем»), marked 18+ (a label and a «Мне есть 18» question before the video), and
  have its cache reset. There is also a site notice, switches for mail sign-in and sign-in with
  AnimeSocial, enabling and ordering video sources, an overview (source health, people, watching,
  the week's top) and an audit log. A change in the panel shows on open pages at once.
- **Automatic list statuses** driven by real watch progress: auto-watching,
  auto-completed and stale auto-dropped states.
- **Real watch-progress logic** driven by player events instead of wall-clock
  time spent on the page.
- **Public profiles** with lists that respect privacy settings and activity
  graph rendering.
- **Runtime proxy, health state and cache** for external APIs, video sources,
  data and media images.
- **Optional xray bridge (VLESS + Reality or Shadowsocks)** for environments
  where upstream anime metadata APIs are blocked.
- **EDDAS interface** shared with AnimeSocial: light and dark themes, and a phone
  layout that behaves like an app. See the next section.

---

## EDDAS Design System

The frontend is built on EDDAS, the design system of the AnimeSocial social
network, so both services look like one family. The functionality is unchanged.

- **System files** live in `assets/eddas/`:
  - `css/tokens.css` holds the light and dark theme tokens and the font faces;
  - `css/eddas.css` holds the `ed-*` components (buttons, fields, tabs, chips,
    menus, dialogs, empty states);
  - `css/app.css` holds the shell and shared primitives: header, side menu, phone
    tab bar and "Ещё" sheet, page layout, forms, tiles, lists;
  - `css/fontawesome.css` and `fonts/fontawesome/` hold Font Awesome Free 7.3.1
    from the npm package `@fortawesome/fontawesome-free`;
  - `fonts/onest/` and `fonts/unbounded/` hold the Onest and Unbounded fonts with
    their OFL licenses.
- `tokens.css` and `eddas.css` are unmodified copies from the AnimeSocial design
  system. `app.css` keeps only the part of the AnimeSocial shell that AsoPlay
  needs. Update them from the same source; update Font Awesome only from npm.
- **`styles.css`** covers only AsoPlay screens: posters, carousel, catalog, title
  page, player, episodes, sign-in, lists, settings and profile. It uses `var(--…)`
  tokens only, with no own colors, gradients, glows or glass.
- **Width:** the content column is up to 1440px (AnimeSocial uses 1200), so the
  player stays large next to the side menu and the episode list. Episodes sit beside
  the player from 1312px and become a grid under it on narrower screens.
- **Fonts:** Onest everywhere, Unbounded only for the wordmark and the sign-in
  hero. No external CDN, Google Fonts or kit.fontawesome.com: everything is served
  by the same server.
- **Theme:** system, light or dark. The switch is in the side menu, the "Ещё"
  sheet, the profile menu and the settings. The choice is stored in `localStorage`
  under `as-theme`, as in AnimeSocial, and is applied before the first paint.
- **Phone:**
  - the page does not zoom;
  - the header stays fixed;
  - the page does not bounce at its edges;
  - navigation uses the bottom tab bar and the "Ещё" sheet;
  - the dub picker and dialogs open as bottom sheets that can be swiped away;
  - home rows scroll sideways and the carousel turns with a swipe;
  - posters in the catalog and lists sit in two columns;
  - with the phone on its side, the player and the dub picker fit on the screen.
- **Sideways strips** (seasons, tabs, the activity graph): a mouse user sees a thin
  scrollbar and can scroll with the wheel or drag the strip; touch screens scroll
  them with a finger.
- **Profile:** activity with the graph on top, then the lists and the action
  history in two columns (stacked on narrow screens).
- **Player:** AsoPlayer and the Kodik skin use the EDDAS dark palette. The player
  document cannot see the site tokens, so the dark-theme values are written in it
  explicitly.

---

## AsoPlayer and Shield

AsoPlayer is not a standalone library; it is the application-level player layer
inside AsoPlay. The page owns source selection, progress, auto-next behavior,
context-menu actions and user workflows, while the iframe bridge handles safe
communication with the actual embedded player.

Key capabilities:

- Right-click context menu: `Сделать скриншот`, `Копировать ссылку на аниме`,
  `Открыть AnimeSocial`.
- Quick PNG screenshots of the current frame with clean filenames:
  `AsoPlay-{mal_id}-ep{episode}-{hh}-{mm}-{ss}.png`.
- Dedicated Kodik skin in `assets/player/kodik-skin.css` with the EDDAS
  dark-theme accent `#9db3cc`.
- Same-origin player proxy for iframe and media URLs.
- AsoPlay Shield for filtering ad URLs, blocking popup/navigation flows and
  reducing noisy third-party behavior inside sandboxed players.
- Dedicated internal player resources: `/player/asoplay-shield.js`,
  `/player/kodik-skin.css`, `/player/frame`, `/player/proxy`,
  `/player/cvh-api/*`.

---

## Architecture

```text
.
├── index.html                  EDDAS shell (header, menu, tab bar) and SSR placeholders
├── app.js                      public router, catalog, title page, player
├── styles.css                  AsoPlay screens on EDDAS tokens
├── assets/                     static media assets
│   ├── eddas/                  EDDAS design system: tokens, components, shell, fonts, icons
│   └── player/
│       └── kodik-skin.css      AsoPlayer/Kodik visual skin
├── js/
│   ├── account.js              auth UI, personal sections, store, profiles
│   ├── navigation.js           shell: "Ещё" sheet, header search, theme, active section
│   ├── profile-activity.js     diary grouping and activity calendar
│   ├── realtime.js             the tab's live connection to the server (WebSocket /ws)
│   ├── site.js                 AnimeSocial panel decisions: notice, hidden and 18+ titles, sign-in
│   └── trailer.js              trailer dialog
├── core/                       backend, a Python package: started with python -m core
│   ├── __main__.py             uvicorn start (address and port: AV_BIND_HOST, AV_BIND_PORT)
│   ├── core.py                 FastAPI app wiring, static serving, sources
│   ├── requirements.txt        Python runtime dependencies
│   ├── api/sections/           site API sections
│   │   ├── account/            /account/…: the __init__.py entry includes the parts in route order —
│   │   │                       favorites, history, progress, ratings, settings, import_storage
│   │   └── lists/              /account/lists/…: entries, progress_event (player events)
│   ├── admin/                  admin API for the «AsoPlay» section in AnimeSocial /cpanel (HMAC)
│   │   ├── router.py           entry: includes the parts in route order
│   │   ├── auth.py             signature, replays, errors, response envelope, pages
│   │   └── reports.py, audit.py, settings.py, titles.py, summary.py   panel sections
│   ├── modules/
│   │   ├── title_pages.py      canonical anime pages, sitemap, robots
│   │   ├── profile_pages.py    public profile API and SSR shell
│   │   └── player/routes.py    same-origin player proxy, Shield, iframe bridge
│   ├── functions/
│   │   ├── social_login.py     sign-in with AnimeSocial (OAuth 2.0 + PKCE) and its sessions
│   │   ├── presence.py         presence on AsoPlay and friends from AnimeSocial
│   │   ├── realtime.py         WebSocket /ws: tabs, events to friends, server status
│   │   ├── site_settings.py    what the panel decided: title flags, notice, features, sources
│   │   ├── video_reports.py    «Не работает» reports from the player
│   │   ├── lists.py            list/status/favorite domain and auto-rules
│   │   ├── progress.py         episode positions, «watched», «Watching» thresholds
│   │   ├── watch_history.py    the «Watching» history and auto status after «not watched»
│   │   ├── privacy.py          privacy flags and reading them
│   │   ├── watch_time.py       watched time for the panel overview
│   │   ├── activity.py         contribution/activity event log
│   │   ├── rate_limit.py       login attempt limit and visitor IP behind the proxy
│   │   ├── source_health.py    source failure/cooldown state
│   │   └── player_hosts.py     player policy: VK, players that do not play
│   ├── classes/
│   │   ├── db.py               AnimeSocial DB auth bridge and sessions
│   │   ├── as_api.py           AnimeSocial API v2 client
│   │   ├── as_realtime.py      server-to-server link with AnimeSocial realtime
│   │   ├── token_box.py        encryption of social network tokens at rest (AES-GCM)
│   │   ├── http.py             Jikan/AniList/Shiki/translate/image proxies
│   │   ├── jikan/catalog.py    embedded MAL catalog, cache and rate limiting
│   │   ├── jikan/php/          PHP bridge, Composer manifest and lockfile
│   │   ├── adblock/engine.py   AdGuard-backed request filtering
│   │   ├── vpn/bridge.py       optional xray runtime bridge
│   │   ├── sources/animevost.py native AnimeVost source adapter
│   │   └── sources/oldyummy.py native old.yummyani source adapter
│   ├── config/
│   │   ├── env.py              project root and loading .env at start-up
│   │   ├── config.py           every setting and environment variable in one place
│   │   ├── animesocial.py      AnimeSocial URL/media configuration
│   │   ├── animesocial.json    AnimeSocial public URL/media mapping
│   │   ├── db.php              AnimeSocial DB config format
│   │   ├── server/             xray config template and the systemd unit
│   │   └── cache/              work files: HTTP cache, adblock, xray (not in git)
│   ├── tests/                  core checks: a section is test_{section}.py, a big one is an entry and a parts
│   │                           folder; run.py runs them, README.md says how and what each needs
│   └── migrations/
│       ├── 0000_schema.sql                       base aviev_* schema
│       ├── 2026-04-30_lists_privacy_activity.sql lists, privacy, activity and compatibility
│       ├── 2026-10-30_admin_panel.sql            reports, title flags, site settings, audit log, watch time
│       ├── 2026-10-30_as_sessions.sql            sign-in with AnimeSocial sessions
│       └── 2026-10-30_presence.sql               presence settings
├── design/EDDAS/tests/         frontend checks (Node); until step 6 they read app.js and js/ from the root
├── tools/
│   ├── guard/                  refactoring guards: lengths, imports, docblocks, contract, responses, styles (all.py)
│   └── qa/                     browser QA of the screens: eddas-qa.cjs and the scenarios in scenarios/
├── .env.example                environment template
├── COPYRIGHT                   proprietary copyright notice
└── .gitignore                  ignored local/runtime artifacts
```

---

## Stack

- **Frontend:** plain HTML, CSS and JavaScript ES modules, the EDDAS design system
  (shared with AnimeSocial), Onest and Unbounded fonts, Font Awesome Free 7.3.1, all
  served locally.
- **Backend:** Python 3.11+, FastAPI, Uvicorn, httpx.
- **Database:** MySQL 8 / OpenServer-compatible AnimeSocial database.
- **Auth:** existing AnimeSocial users table + project-local `aviev_sessions`.
- **Metadata:** Jikan, AniList, Shikimori.
- **Embedded parser:** Jikan 5.0.0, PHP 8.4.4+ with DOM and mbstring.
- **Player layer:** same-origin iframe/media proxy, sandbox bridge,
  request-level ad filtering.
- **Network bridge:** optional xray (VLESS + Reality or Shadowsocks) through `.env`.

---

## Owner Deployment

This section is for the project owner and explicitly licensed operators only.
Publishing operational instructions does not grant permission to use, copy,
deploy, host or redistribute the project.

Install Python dependencies:

```bash
pip install -r core/requirements.txt
```

Install the pinned parser dependencies:

```bash
composer --working-dir=core/classes/jikan/php install --no-dev --no-scripts --no-plugins --optimize-autoloader
```

Use a currently patched PHP 8.4/8.5 release. Windows detects OSPanel PHP;
Linux uses `php` from PATH. Set `AV_JIKAN_PHP` in `.env` to override the path.
No separate PHP website, Jikan REST deployment, Docker, MongoDB or search
server is required. Python fetches MyAnimeList HTML and Jikan parses it in a
CLI child process without network access. Results are cached, concurrent
identical requests are coalesced, and uncached MAL requests are limited to
one per second per AsoPlay process. MAL must remain reachable: this is an
embedded parser, not an offline copy of its database. `/health.catalog`
reports the parser version and runtime readiness, not MAL availability.

Create runtime configuration:

```bash
copy .env.example .env
```

Apply database schema in order:

```bash
mysql -u root AnimeSocial < core/migrations/0000_schema.sql
mysql -u root AnimeSocial < core/migrations/2026-04-30_lists_privacy_activity.sql
mysql -u root AnimeSocial < core/migrations/2026-10-30_admin_panel.sql
mysql -u root AnimeSocial < core/migrations/2026-10-30_as_sessions.sql
mysql -u root AnimeSocial < core/migrations/2026-10-30_presence.sql
```

Start the application:

```bash
python -B -m core
```

Default local URL:

```text
http://127.0.0.1:8787
```

Health endpoint:

```text
GET /health
```

Expected response:

```json
{
  "ok": true,
  "sources": ["..."],
  "vpn": true,
  "db": {
    "ok": true,
    "aviev_schema_present": true
  },
  "adblock": {
    "enabled": true,
    "ready": true
  },
  "source_health": {
    "...": {
      "available": true
    }
  }
}
```

---

## Configuration

The backend reads runtime settings from `.env`, `core/config/db.php` and
`core/config/animesocial.json`; every variable is declared in `core/config/config.py`.

Important environment groups:

- `AV_BIND_HOST`, `AV_BIND_PORT` — FastAPI bind address.
- `AV_SITE_URL`, `AV_SITE_NAME` — canonical SEO URLs and site name.
- `AV_ALLOWED_ORIGINS` — CORS origins for development or production.
- `AV_DB_*` — optional DB overrides.
- `AV_USER_*` — optional AnimeSocial users-table/column overrides.
- `AV_AUTH_VERIFIER` — password verification strategy override.
- `AV_COOKIE_SECURE`, `AV_COOKIE_SAMESITE`, `AV_SESSION_TTL_DAYS` — session
  cookie behavior.
- `AV_ANIMESOCIAL_SITE_URL` — AnimeSocial public host override.
- `AV_AS_CLIENT_ID`, `AV_AS_CLIENT_SECRET` — AsoPlay's client keys in AnimeSocial
  («Панель → Система → Сайты-клиенты»); sent with every API request together with the
  person's address and browser.
- `AV_AS_LOGIN`, `AV_TOKEN_KEY` — sign-in with AnimeSocial: the switch and the key that
  encrypts social network tokens (32 bytes in base64). `AV_AS_API_URL`,
  `AV_AS_REDIRECT_URI` — API and return addresses when they cannot be derived from the
  AnimeSocial address and the current request. `AV_AS_ORIGINS` — extra social network site
  addresses that `/auth/as/whoami` answers with cookies.
- `AV_AS_CA` — which certificates to trust in requests to the social network: a CA file path or
  `system` for the system store (a local OSPanel engine with a self-signed certificate).
  Empty means the certifi bundle.
- `AV_ADMIN_SECRET` — the shared secret that signs requests from the «AsoPlay» section of the
  AnimeSocial panel (the same as in the section settings on the social network). The panel calls
  `127.0.0.1:8787/api/admin/…` from the same machine; through the public proxy the API does not answer.
  Empty switches the API off.
- `AV_ADBLOCK_ENABLED`, `AV_ADBLOCK_FILTER_URLS`,
  `AV_ADBLOCK_CACHE_TTL_SECONDS` — player Shield filtering behavior.
- `AV_ADBLOCK_REGEX_LIMIT`, `AV_ADBLOCK_SNIPPET_LIMIT` — parser safety limits
  for runtime filters.
- `AV_PLAYER_PROXY_MAX_TEXT_REWRITE` — maximum textual player resource size for
  rewriting.
- `AV_VPN_ENABLED` — `0` temporarily disables the xray bridge without erasing the
  server settings.
- `AV_VPN_URL` — `vless://` share link (VLESS + Reality) for the xray bridge.
  Requests go out directly; only a request to a host that is unreachable
  directly is retried through the bridge, and that host then uses the bridge
  first for 10 minutes. Players always connect directly.
- `SS_ADDRESS`, `SS_PORT`, `SS_METHOD`, `SS_PASSWORD` — Shadowsocks for older
  servers; used only when `AV_VPN_URL` is not set.

Production secrets must stay outside Git. Never commit real passwords, tokens,
private proxy credentials, session data or private keys.

---

## Data Model

The application creates and owns only tables with the `aviev_` prefix. Existing
AnimeSocial tables remain the source of identity and login credentials.

Password login is protected against guessing with the same limits as the AnimeSocial
site: at most 20 failed attempts from one address and 8 per login within 15 minutes,
then status 429 with `Retry-After`. The visitor address comes from `X-Real-IP`, set by
the nginx in front of AsoPlay; proxy headers are trusted only on requests from 127.0.0.1.

Sign-in with AnimeSocial (`core/functions/social_login.py`) is OAuth 2.0 + PKCE on top of the social
network's API v2: the button leads to `/auth/as/start`, the social network asks «Войти в
AsoPlay?» and returns a code to `/auth/as/callback`, and AsoPlay exchanges it for tokens and
opens its own session. The social network device session is kept in `aviev_as_sessions`
with encrypted tokens and ends when the user signs out of AsoPlay; the sign-in on the social
network site itself stays. The page asks the social network who is signed in in this browser
(`/api/v2/auth/web/session`): such a person sees «Продолжить как Имя Фамилия» with «Войти через
почту» under it, everyone else sees the mail form, and a guest on any page gets a «Имя Фамилия,
это вы?» card. In the other direction the social network site asks AsoPlay through
`/auth/as/whoami` and signs in as the same account through `/auth/as/to-social`. The feature turns on with
`AV_AS_LOGIN=1` once `AV_TOKEN_KEY` is set, the `cryptography` package is installed and the
social network engine allows the return address. With the social network on, the email form
signs in through it too (`POST /auth/login` with the client keys): the social network checks the
password, sign-in is by email only, as on its site, and the person gets the same AsoPlay session on
the social network as after the button. Without client keys the form checks the password against the
social network's table and accepts a login or an email. Someone signed in by email earlier links
their session to the social network through the same AnimeSocial sign-in; if another account is open
on the social network, the sign-in is refused instead of switching the person.

The live connection (`core/functions/realtime.py`, `js/realtime.js`). Every tab holds a WebSocket `/ws`
opened from AsoPlay's own pages (the Origin is checked): it says whether it is on screen and what the
player is playing, only when that changes; the server sends its status (sources, VPN — tabs used to
ask `/health` every 30 seconds) and friends' events. WebSocket ping frames notice a dead link, and the
tab reconnects by itself.

Presence and friends (`core/functions/presence.py`). A person is on AsoPlay while at least one of their tabs
is open and on screen, and for 30 seconds after, so a reload does not flash «left». Friends who are on
the site get the event at once. The server reports presence to the social network over a standing
server-to-server link (`core/classes/as_realtime.py`): a snapshot on connect, then every change; back come
its people's events: a friend came to the social network or left, a friendship changed, a page was
banned. While the link is down, the `POST /clients/presence` batch every minute takes over. Friends see
«онлайн на AsoPlay» and «смотрит «…»» on the social network. The social network accepts the mark for people who have
an AsoPlay session on it, and the «watching» link only on the client's site address from the social
network panel. `/my/friends` takes friends from the social network (`GET /users/{id}/friends/ids` and
`GET /users?ids=` with the social session token) and who is on AsoPlay and what they watch from this
server; the «Друзья» block on a title page adds whose lists have the title. `aviev_privacy` holds two
settings: `hide_presence` shows nobody that the person is here, `show_watching` (off by default) shows
friends what they watch; hidden lists stay out of the block.

Core project tables:

- `aviev_sessions` — HTTP-only login sessions.
- `aviev_user_lists` — canonical title status + favorite flag.
- `aviev_watch_history` — continue-watching feed.
- `aviev_episode_progress` — per-episode progress.
- `aviev_title_ratings` — personal ratings.
- `aviev_dub_prefs` — preferred dubbing per title.
- `aviev_account_settings` — account toggles.
- `aviev_privacy` — public profile privacy controls and presence on AsoPlay.
- `aviev_as_sessions` — AnimeSocial device sessions behind sign-in with AnimeSocial.
- `aviev_activity` — contribution/activity graph events.
- `aviev_title_pages` — canonical SEO title-page cache.
- `aviev_title_refresh_queue` — title page refresh queue.
- `aviev_import_marks` — one-time localStorage import markers.

`aviev_activity` uses a unique deduplication index:

```sql
UNIQUE KEY uq_act_dedup (user_id, day, kind, mal_id, meta(64))
```

---

## Security

- Session tokens are stored on the server and sent through HTTP-only cookies.
- Private account endpoints require authenticated sessions.
- Public profile endpoints enforce privacy rules on the server.
- The player accepts iframe `postMessage` events only from the expected source
  window.
- AsoPlayer uses explicit bridge messages for screenshots, context-menu actions
  and menu closing when clicks happen inside sandboxed iframes.
- AsoPlay Shield routes player resources through a same-origin layer, filters
  ad URLs and blocks popup/top-navigation flows.
- Proxy endpoints are constrained to specific API/media scenarios.
- `robots.txt` blocks internal account/auth/proxy/source endpoints.

---

## Copyright and License

This repository is **not open source**.

Copyright © Chepela Daniel Maximovich (x0doit). All rights reserved.

No license is granted to any third party. Without direct written permission from
the copyright owner, no person or organization may use, copy, download, mirror,
modify, run, deploy, host, distribute, sublicense, resell, publish, repackage,
train on, index into a dataset, or create derivative works from this repository
or any part of it.

Full proprietary notice: [`COPYRIGHT`](./COPYRIGHT).

Licensing inquiries: <https://crazydev.pro/>
