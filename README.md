# AsoPlay

![Status](https://img.shields.io/badge/status-production--ready-22c55e)
![Backend](https://img.shields.io/badge/backend-FastAPI-009688)
![Frontend](https://img.shields.io/badge/frontend-vanilla%20JS-f7df1e)
![Database](https://img.shields.io/badge/database-MySQL%208-4479a1)
![License](https://img.shields.io/badge/license-proprietary-red)

**AsoPlay** is a proprietary anime web platform built as a compact full-stack
application: a vanilla HTML/CSS/JavaScript frontend, a Python/FastAPI backend,
server-side account integration with AnimeSocial, canonical SEO title pages, a
multi-source player, progress tracking, user lists, public profiles and
runtime API proxying.

The project is intentionally lean: no frontend build step, no Node dependency
chain, no generated application code in the repository. The server owns API
integration, caching, authentication, database access, SEO rendering and static
asset delivery.

> Copyright © Чепела Даниэль Максимович (x0doit). All rights reserved.
> This repository is proprietary software. No license is granted.

---

## Highlights

- **Catalog and discovery** powered by Jikan, AniList and Shikimori metadata.
- **Canonical title pages** at `/anime/{mal_id}-{slug}/` with server-rendered
  metadata, Open Graph tags, JSON-LD, canonical URLs, sitemap support and
  `<noscript>` fallbacks.
- **Multi-source player** with fallback discovery across supported anime video
  providers and safe iframe `postMessage` handling.
- **AnimeSocial authentication bridge** using the existing AnimeSocial MySQL
  account database with project-local HTTP-only sessions.
- **Personal library** with favorites, continue watching, statuses, ratings,
  dub preferences, settings and privacy controls.
- **Automatic list rules** driven by real watch progress: auto-watching,
  auto-completed and stale auto-dropped states.
- **Public profiles** with privacy-aware lists and activity graph rendering.
- **Runtime proxy/cache layer** for external APIs and media images.
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

## Runtime Stack

- **Frontend:** plain HTML, CSS and ES modules.
- **Backend:** Python 3.11+, FastAPI, Uvicorn, httpx.
- **Database:** MySQL 8 / OpenServer-compatible AnimeSocial database.
- **Auth:** existing AnimeSocial users table + project-local `aviev_sessions`.
- **External metadata:** Jikan, AniList, Shikimori.
- **Optional network bridge:** xray with Shadowsocks settings from `.env`.

---

## Deployment

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

Expected shape:

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
- `AV_DB_*` — optional DB overrides when `animesocial-db.php` is not enough.
- `AV_USER_*` — optional AnimeSocial users-table/column overrides.
- `AV_AUTH_VERIFIER` — password verification strategy override.
- `AV_COOKIE_SECURE`, `AV_COOKIE_SAMESITE`, `AV_SESSION_TTL_DAYS` — session
  cookie behavior.
- `AV_ANIMESOCIAL_SITE_URL` — AnimeSocial public host override.
- `SS_ADDRESS`, `SS_PORT`, `SS_METHOD`, `SS_PASSWORD` — optional xray outbound.

Production secrets must stay outside Git. Use `.env` on the server and never
commit real passwords, tokens, private proxy credentials or session data.

---

## Database Model

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
- `aviev_title_refresh_queue` — refresh queue for title pages.
- `aviev_import_marks` — one-time localStorage import markers.

`aviev_activity` uses a unique deduplication index for stable activity graphs:

```sql
UNIQUE KEY uq_act_dedup (user_id, day, kind, mal_id, meta(64))
```

---

## Security Notes

- Session tokens are stored server-side and sent through HTTP-only cookies.
- Personal endpoints require authenticated sessions.
- Public profile endpoints enforce privacy on the server.
- The player validates iframe `postMessage` source windows before accepting
  progress events.
- Image and API proxy endpoints are constrained to known use cases and hosts.
- Robots rules block internal account/auth/proxy/source endpoints.

---

## Copyright and License

This repository is **not open source**.

Copyright © Чепела Даниэль Максимович (x0doit). All rights reserved.

No license is granted to any third party. Without direct written permission from
the copyright owner, no person or organization may use, copy, download, mirror,
modify, adapt, run, deploy, host, distribute, sublicense, resell, publish,
repackage, train on, index into a dataset, or create derivative works from this
repository or any part of it.

The full proprietary notice is available in [`COPYRIGHT`](./COPYRIGHT).

For licensing inquiries: <https://crazydev.pro/>
