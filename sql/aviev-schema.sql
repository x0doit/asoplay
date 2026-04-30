-- AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
-- All rights reserved. See /COPYRIGHT for full terms.
--
-- Tables owned by the anime-site project. Every name starts with `aviev_` —
-- existing AnimeSocial tables (prefix `Just*`) are intentionally untouched.
--
-- Apply with:
--     mysql -u root AnimeSocial < sql/aviev-schema.sql
-- The script is idempotent (CREATE TABLE IF NOT EXISTS everywhere), so running
-- it on a freshly-bootstrapped database or on one that already has partial
-- tables is safe.

SET NAMES utf8mb4;
SET time_zone = '+00:00';

--
-- aviev_sessions — server-side login sessions for this site (not for AnimeSocial).
-- Identified by a random 64-char token stored in an HTTP-only cookie.
--
CREATE TABLE IF NOT EXISTS `aviev_sessions` (
    `token`         CHAR(64)        NOT NULL,
    `user_id`       INT UNSIGNED    NOT NULL,
    `created_at`    DATETIME        NOT NULL,
    `last_seen_at`  DATETIME        NOT NULL,
    `user_agent`    VARCHAR(255)    NULL,
    `ip`            VARCHAR(64)     NULL,
    `revoked`       TINYINT(1)      NOT NULL DEFAULT 0,
    PRIMARY KEY (`token`),
    KEY `ix_sessions_user` (`user_id`, `revoked`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_favorites — user favorites (replaces old av_favs in localStorage).
--
CREATE TABLE IF NOT EXISTS `aviev_favorites` (
    `user_id`     INT UNSIGNED    NOT NULL,
    `mal_id`      INT UNSIGNED    NOT NULL,
    `title`       VARCHAR(500)    NOT NULL DEFAULT '',
    `poster_url`  VARCHAR(500)    NOT NULL DEFAULT '',
    `added_at`    DATETIME        NOT NULL,
    PRIMARY KEY (`user_id`, `mal_id`),
    KEY `ix_favorites_added` (`user_id`, `added_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_watch_history — "Продолжить просмотр" feed: one row per title.
--
CREATE TABLE IF NOT EXISTS `aviev_watch_history` (
    `user_id`          INT UNSIGNED    NOT NULL,
    `mal_id`           INT UNSIGNED    NOT NULL,
    `last_episode`     SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    `episode_seconds`  INT UNSIGNED    NOT NULL DEFAULT 0,
    `episode_duration` INT UNSIGNED    NOT NULL DEFAULT 0,
    `episodes_total`   SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    `title`            VARCHAR(500)    NOT NULL DEFAULT '',
    `poster_url`       VARCHAR(500)    NOT NULL DEFAULT '',
    `updated_at`       DATETIME        NOT NULL,
    PRIMARY KEY (`user_id`, `mal_id`),
    KEY `ix_history_updated` (`user_id`, `updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_episode_progress — granular resume point per (title, episode).
--
CREATE TABLE IF NOT EXISTS `aviev_episode_progress` (
    `user_id`      INT UNSIGNED      NOT NULL,
    `mal_id`       INT UNSIGNED      NOT NULL,
    `episode_num`  SMALLINT UNSIGNED NOT NULL,
    `seconds`      INT UNSIGNED      NOT NULL DEFAULT 0,
    `duration`     INT UNSIGNED      NOT NULL DEFAULT 0,
    `updated_at`   DATETIME          NOT NULL,
    PRIMARY KEY (`user_id`, `mal_id`, `episode_num`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_title_ratings — user's own 1..10 rating of a title.
--
CREATE TABLE IF NOT EXISTS `aviev_title_ratings` (
    `user_id`   INT UNSIGNED     NOT NULL,
    `mal_id`    INT UNSIGNED     NOT NULL,
    `score`     TINYINT UNSIGNED NOT NULL,
    `set_at`    DATETIME         NOT NULL,
    PRIMARY KEY (`user_id`, `mal_id`),
    CONSTRAINT `ck_rating_range` CHECK (`score` BETWEEN 1 AND 10)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_dub_prefs — saved dub choice per title (normalized name).
--
CREATE TABLE IF NOT EXISTS `aviev_dub_prefs` (
    `user_id`    INT UNSIGNED NOT NULL,
    `mal_id`     INT UNSIGNED NOT NULL,
    `dub_norm`   VARCHAR(128) NOT NULL,
    `updated_at` DATETIME     NOT NULL,
    PRIMARY KEY (`user_id`, `mal_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_account_settings — per-user feature toggles (autonext, etc).
--
CREATE TABLE IF NOT EXISTS `aviev_account_settings` (
    `user_id`       INT UNSIGNED NOT NULL,
    `autonext`      TINYINT(1)   NOT NULL DEFAULT 1,
    `settings_json` TEXT         NULL,
    `updated_at`    DATETIME     NOT NULL,
    PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_title_pages — canonical public pages. One row = one crawlable
-- /anime/{mal_id}-{slug}/ page. `snapshot_json` holds the raw payload we
-- rendered from, so HTML can be rebuilt without re-hitting Jikan/AniList.
--
CREATE TABLE IF NOT EXISTS `aviev_title_pages` (
    `mal_id`         INT UNSIGNED    NOT NULL,
    `slug`           VARCHAR(200)    NOT NULL,
    `title_ru`       VARCHAR(500)    NOT NULL DEFAULT '',
    `title_en`       VARCHAR(500)    NOT NULL DEFAULT '',
    `title_jp`       VARCHAR(500)    NOT NULL DEFAULT '',
    `synopsis`       TEXT            NULL,
    `poster_url`     VARCHAR(500)    NOT NULL DEFAULT '',
    `banner_url`     VARCHAR(500)    NOT NULL DEFAULT '',
    `year`           SMALLINT UNSIGNED NULL,
    `kind`           VARCHAR(24)     NOT NULL DEFAULT '',
    `airing_status`  VARCHAR(24)     NOT NULL DEFAULT '',
    `episodes_total` SMALLINT UNSIGNED NULL,
    `score`          DECIMAL(3,1)    NULL,
    `genres_json`    TEXT            NULL,
    `studios`        VARCHAR(500)    NOT NULL DEFAULT '',
    `snapshot_json`  MEDIUMTEXT      NULL,
    `cached_at`      DATETIME        NOT NULL,
    `fresh_until`    DATETIME        NOT NULL,
    `publish_state`  ENUM('live','dormant','hidden') NOT NULL DEFAULT 'live',
    PRIMARY KEY (`mal_id`),
    UNIQUE KEY `uq_title_slug` (`slug`),
    KEY `ix_title_fresh` (`fresh_until`),
    KEY `ix_title_state` (`publish_state`, `fresh_until`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_title_refresh_queue — queued titles whose cached_at has expired
-- (or who were touched manually).
--
CREATE TABLE IF NOT EXISTS `aviev_title_refresh_queue` (
    `mal_id`       INT UNSIGNED NOT NULL,
    `enqueued_at`  DATETIME     NOT NULL,
    `reason`       VARCHAR(64)  NOT NULL DEFAULT 'ttl',
    PRIMARY KEY (`mal_id`),
    KEY `ix_refresh_age` (`enqueued_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_import_marks — one row per user after a successful one-time import
-- of old localStorage data into the account. Prevents re-asking.
--
CREATE TABLE IF NOT EXISTS `aviev_import_marks` (
    `user_id`    INT UNSIGNED NOT NULL,
    `kind`       VARCHAR(32)  NOT NULL,
    `imported_at` DATETIME    NOT NULL,
    `n_items`    INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (`user_id`, `kind`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
