# AsoPlay

![Status](https://img.shields.io/badge/status-production--ready-22c55e)
![Backend](https://img.shields.io/badge/backend-FastAPI-009688)
![Frontend](https://img.shields.io/badge/frontend-vanilla%20JS-f7df1e)
![Database](https://img.shields.io/badge/database-MySQL%208-4479a1)
![License](https://img.shields.io/badge/license-proprietary-red)

**Язык:** Русский | [English](#english)

**AsoPlay** — сервис для просмотра аниме с каталогом тайтлов, поиском,
SEO-страницами, плеером с несколькими источниками, личными списками,
прогрессом просмотра, публичными профилями и интеграцией с AnimeSocial.
Фронтенд написан на vanilla JavaScript, бэкенд работает на Python/FastAPI,
а пользовательские данные хранятся в MySQL.

> Copyright © Чепела Даниэль Максимович (x0doit). Все права защищены.
> Репозиторий не является open source. Любое использование требует прямого
> письменного разрешения правообладателя.

---

## Содержание

- [Возможности](#возможности)
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

- **Каталог и поиск** на базе данных Jikan, AniList и Shikimori.
- **Страницы тайтлов** `/anime/{mal_id}-{slug}/` с мета-тегами на стороне
  сервера, Open Graph, JSON-LD, canonical URL, sitemap и `<noscript>`
  fallback для просмотра без JavaScript.
- **Плеер с несколькими источниками** с резервным поиском по доступным
  провайдерам и безопасной обработкой iframe `postMessage`.
- **Интеграция AnimeSocial**: авторизация через существующую MySQL-базу
  AnimeSocial и локальные HTTP-only сессии проекта.
- **Личный кабинет**: избранное, продолжить просмотр, списки, статусы,
  оценки, выбранные озвучки, настройки и приватность.
- **Автоматические статусы списков**: auto-watching, auto-completed и
  stale auto-dropped на основе реального прогресса просмотра.
- **Публичные профили** со списками, которые учитывают настройки приватности,
  и графиком активности.
- **Прокси и кэш во время работы** для внешних API с данными и изображениями.
- **Опциональный xray/Shadowsocks bridge** для окружений, где внешние anime API
  недоступны напрямую.

---

## Архитектура

```text
.
├── index.html                  SPA shell и SSR placeholders
├── app.js                      публичный router, каталог, тайтл, player
├── styles.css                  дизайн-система приложения
├── assets/                     статические media assets
├── js/
│   └── account.js              auth UI, личные разделы, store, профили
├── server/
│   ├── main.py                 FastAPI wiring, static serving, sources
│   ├── animesocial.py          AnimeSocial DB auth bridge и sessions
│   ├── animesocial_config.py   AnimeSocial URL/media configuration
│   ├── account_api.py          account compatibility API
│   ├── user_lists.py           list/status/favorite domain и auto-rules
│   ├── activity_log.py         contribution/activity event log
│   ├── profile_pages.py        public profile API и SSR shell
│   ├── title_pages.py          canonical anime pages, sitemap, robots
│   ├── proxies.py              Jikan/AniList/Shiki/translate/image proxies
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

- **Frontend:** HTML, CSS, vanilla JavaScript ES modules.
- **Backend:** Python 3.11+, FastAPI, Uvicorn, httpx.
- **Database:** MySQL 8 / OpenServer-compatible AnimeSocial database.
- **Auth:** существующая users-таблица AnimeSocial + локальные `aviev_sessions`.
- **Metadata:** Jikan, AniList, Shikimori.
- **Network bridge:** optional xray/Shadowsocks через `.env`.

---

## Развертывание владельцем

Этот раздел предназначен только для владельца проекта или лиц, получивших
прямое письменное разрешение. Наличие инструкций по запуску не предоставляет
права использовать, копировать, разворачивать или публиковать проект.

Установить Python dependencies:

```bash
pip install -r server/requirements.txt
```

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
- `SS_ADDRESS`, `SS_PORT`, `SS_METHOD`, `SS_PASSWORD` — optional xray outbound.

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
SEO-ready title pages, a multi-source player, personal lists, watch progress,
public profiles and AnimeSocial integration. The frontend is written in vanilla
JavaScript, the backend runs on Python/FastAPI, and user data is stored in MySQL.

> Copyright © Chepela Daniel Maximovich (x0doit). All rights reserved.
> This repository is not open source. Any use requires direct written permission
> from the copyright owner.

---

## Contents

- [Features](#features)
- [Architecture](#architecture)
- [Stack](#stack)
- [Owner Deployment](#owner-deployment)
- [Configuration](#configuration)
- [Data Model](#data-model)
- [Security](#security)
- [Copyright and License](#copyright-and-license)

---

## Features

- **Catalog and search** powered by Jikan, AniList and Shikimori data.
- **Title pages** at `/anime/{mal_id}-{slug}/` with server-rendered
  metadata, Open Graph tags, JSON-LD, canonical URLs, sitemap support and
  `<noscript>` fallbacks.
- **Multi-source player** with fallback search across supported anime video
  providers and safe iframe `postMessage` handling.
- **AnimeSocial authentication bridge** using the existing AnimeSocial MySQL
  account database with project-local HTTP-only sessions.
- **Personal library** with favorites, continue watching, statuses, ratings,
  dub preferences, settings and privacy controls.
- **Automatic list statuses** driven by real watch progress: auto-watching,
  auto-completed and stale auto-dropped states.
- **Public profiles** with lists that respect privacy settings and activity
  graph rendering.
- **Runtime proxy and cache** for external data APIs and media images.
- **Optional xray/Shadowsocks bridge** for environments where upstream anime
  metadata APIs are blocked.

---

## Architecture

```text
.
├── index.html                  SPA shell and SSR placeholders
├── app.js                      public router, catalog, title page, player
├── styles.css                  application design system
├── assets/                     static media assets
├── js/
│   └── account.js              auth UI, personal sections, store, profiles
├── server/
│   ├── main.py                 FastAPI app wiring, static serving, sources
│   ├── animesocial.py          AnimeSocial DB auth bridge and sessions
│   ├── animesocial_config.py   AnimeSocial URL/media configuration
│   ├── account_api.py          account compatibility API
│   ├── user_lists.py           list/status/favorite domain and auto-rules
│   ├── activity_log.py         contribution/activity event log
│   ├── profile_pages.py        public profile API and SSR shell
│   ├── title_pages.py          canonical anime pages, sitemap, robots
│   ├── proxies.py              Jikan/AniList/Shiki/translate/image proxies
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

- **Frontend:** plain HTML, CSS and JavaScript ES modules.
- **Backend:** Python 3.11+, FastAPI, Uvicorn, httpx.
- **Database:** MySQL 8 / OpenServer-compatible AnimeSocial database.
- **Auth:** existing AnimeSocial users table + project-local `aviev_sessions`.
- **Metadata:** Jikan, AniList, Shikimori.
- **Network bridge:** optional xray/Shadowsocks through `.env`.

---

## Owner Deployment

This section is for the project owner and explicitly licensed operators only.
Publishing operational instructions does not grant permission to use, copy,
deploy, host or redistribute the project.

Install Python dependencies:

```bash
pip install -r server/requirements.txt
```

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
- `SS_ADDRESS`, `SS_PORT`, `SS_METHOD`, `SS_PASSWORD` — optional xray outbound.

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
