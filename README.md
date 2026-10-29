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
│   └── trailer.js              окно трейлера
├── server/
│   ├── main.py                 FastAPI wiring, static serving, sources
│   ├── animesocial.py          AnimeSocial DB auth bridge и sessions
│   ├── animesocial_config.py   AnimeSocial URL/media configuration
│   ├── account_api.py          account compatibility API
│   ├── adblock.py              AdGuard-backed request filtering
│   ├── user_lists.py           list/status/favorite domain и auto-rules
│   ├── activity_log.py         contribution/activity event log
│   ├── profile_pages.py        public profile API и SSR shell
│   ├── title_pages.py          canonical anime pages, sitemap, robots
│   ├── proxies.py              Jikan/AniList/Shiki/translate/image proxies
│   ├── jikan_catalog.py        встроенный каталог MAL, кэш и лимит запросов
│   ├── jikan/                 PHP-мост, Composer manifest и lockfile
│   ├── player_proxy.py         same-origin player proxy, Shield, iframe bridge
│   ├── source_health.py        source failure/cooldown state
│   ├── vpn_bridge.py           optional xray runtime bridge
│   ├── animevost.py            native AnimeVost source adapter
│   ├── oldyummy.py             native old.yummyani source adapter
│   └── requirements.txt        Python runtime dependencies
├── sql/
│   ├── aviev-schema.sql        базовая схема aviev_*
│   └── aviev-schema-pass2.sql  списки, приватность, активность, compatibility
├── animesocial.json            public URL/media mapping для AnimeSocial
├── animesocial-db.php          формат DB-конфига AnimeSocial
├── vpn.template.json           шаблон xray-конфига
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
pip install -r server/requirements.txt
```

Установить зафиксированные зависимости встроенного парсера:

```bash
composer --working-dir=server/jikan install --no-dev --no-scripts --no-plugins --optimize-autoloader
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
mysql -u root AnimeSocial < sql/aviev-schema.sql
mysql -u root AnimeSocial < sql/aviev-schema-pass2.sql
```

Запустить приложение:

```bash
python -B -m server.main
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

Backend читает runtime settings из `.env`, `animesocial-db.php` и
`animesocial.json`.

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

Основные таблицы проекта:

- `aviev_sessions` — HTTP-only login sessions.
- `aviev_user_lists` — canonical title status + favorite flag.
- `aviev_watch_history` — continue-watching feed.
- `aviev_episode_progress` — per-episode progress.
- `aviev_title_ratings` — personal ratings.
- `aviev_dub_prefs` — preferred dubbing per title.
- `aviev_account_settings` — account toggles.
- `aviev_privacy` — public profile privacy controls.
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
│   └── trailer.js              trailer dialog
├── server/
│   ├── main.py                 FastAPI app wiring, static serving, sources
│   ├── animesocial.py          AnimeSocial DB auth bridge and sessions
│   ├── animesocial_config.py   AnimeSocial URL/media configuration
│   ├── account_api.py          account compatibility API
│   ├── adblock.py              AdGuard-backed request filtering
│   ├── user_lists.py           list/status/favorite domain and auto-rules
│   ├── activity_log.py         contribution/activity event log
│   ├── profile_pages.py        public profile API and SSR shell
│   ├── title_pages.py          canonical anime pages, sitemap, robots
│   ├── proxies.py              Jikan/AniList/Shiki/translate/image proxies
│   ├── jikan_catalog.py        embedded MAL catalog, cache and rate limiting
│   ├── jikan/                 PHP bridge, Composer manifest and lockfile
│   ├── player_proxy.py         same-origin player proxy, Shield, iframe bridge
│   ├── source_health.py        source failure/cooldown state
│   ├── vpn_bridge.py           optional xray runtime bridge
│   ├── animevost.py            native AnimeVost source adapter
│   ├── oldyummy.py             native old.yummyani source adapter
│   └── requirements.txt        Python runtime dependencies
├── sql/
│   ├── aviev-schema.sql        base aviev_* schema
│   └── aviev-schema-pass2.sql  lists, privacy, activity and compatibility
├── animesocial.json            AnimeSocial public URL/media mapping
├── animesocial-db.php          AnimeSocial DB config format
├── vpn.template.json           xray config template
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
pip install -r server/requirements.txt
```

Install the pinned parser dependencies:

```bash
composer --working-dir=server/jikan install --no-dev --no-scripts --no-plugins --optimize-autoloader
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
mysql -u root AnimeSocial < sql/aviev-schema.sql
mysql -u root AnimeSocial < sql/aviev-schema-pass2.sql
```

Start the application:

```bash
python -B -m server.main
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

The backend reads runtime settings from `.env`, `animesocial-db.php` and
`animesocial.json`.

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

Core project tables:

- `aviev_sessions` — HTTP-only login sessions.
- `aviev_user_lists` — canonical title status + favorite flag.
- `aviev_watch_history` — continue-watching feed.
- `aviev_episode_progress` — per-episode progress.
- `aviev_title_ratings` — personal ratings.
- `aviev_dub_prefs` — preferred dubbing per title.
- `aviev_account_settings` — account toggles.
- `aviev_privacy` — public profile privacy controls.
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
