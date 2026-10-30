-- AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
-- All rights reserved. See /COPYRIGHT for full terms.
--
-- Pass-5 migration: раздел «AsoPlay» в /cpanel Anime Social — жалобы «Не работает», пометки тайтлов,
-- настройки сайта и журнал действий администраторов (server/admin_api.py). Idempotent — safe to run on top of pass-4.
--
-- Apply with:
--     mysql -u root AnimeSocial < sql/aviev-schema-pass5.sql

SET NAMES utf8mb4;
SET time_zone = '+00:00';

--
-- aviev_reports — жалобы из кнопки «Не работает» в плеере. Что было в плеере в момент жалобы:
-- тайтл (и его название тогда), серия, озвучка, источник (yummy_anime, animego…) и хост плеера
-- (kodikplayer.com…). status — open | fixed | rejected, note — ответ модератора.
--
CREATE TABLE IF NOT EXISTS `aviev_reports` (
    `id`          INT UNSIGNED      NOT NULL AUTO_INCREMENT,
    `user_id`     INT UNSIGNED      NOT NULL,
    `mal_id`      INT UNSIGNED      NOT NULL,
    `title`       VARCHAR(255)      NOT NULL DEFAULT '',
    `episode`     SMALLINT UNSIGNED NULL,
    `dub`         VARCHAR(128)      NOT NULL DEFAULT '',
    `source`      VARCHAR(32)       NOT NULL DEFAULT '',
    `player`      VARCHAR(64)       NOT NULL DEFAULT '',
    `kind`        VARCHAR(24)       NOT NULL,
    `text`        VARCHAR(1000)     NOT NULL DEFAULT '',
    `status`      VARCHAR(12)       NOT NULL DEFAULT 'open',
    `note`        VARCHAR(1000)     NOT NULL DEFAULT '',
    `created_at`  DATETIME          NOT NULL,
    `updated_at`  DATETIME          NOT NULL,
    `updated_by`  INT UNSIGNED      NULL,
    PRIMARY KEY (`id`),
    KEY `ix_reports_status` (`status`, `id`),
    KEY `ix_reports_user`   (`user_id`, `created_at`),
    KEY `ix_reports_title`  (`mal_id`, `episode`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_title_flags — решения модераторов по тайтлу: hidden — скрыт по жалобе правообладателя,
-- pinned — закреплён в карусели главной, adult — 18+. Строка есть только у тайтлов, которых касались.
--
CREATE TABLE IF NOT EXISTS `aviev_title_flags` (
    `mal_id`      INT UNSIGNED  NOT NULL,
    `hidden`      TINYINT(1)    NOT NULL DEFAULT 0,
    `pinned`      TINYINT(1)    NOT NULL DEFAULT 0,
    `adult`       TINYINT(1)    NOT NULL DEFAULT 0,
    `note`        VARCHAR(1000) NOT NULL DEFAULT '',
    `updated_by`  INT UNSIGNED  NULL,
    `updated_at`  DATETIME      NOT NULL,
    PRIMARY KEY (`mal_id`),
    KEY `ix_title_flags_pinned` (`pinned`, `updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_site_settings — настройки сайта из панели, JSON по имени: notice (плашка), features (функции),
-- sources (источники видео: включены ли и в каком порядке).
--
CREATE TABLE IF NOT EXISTS `aviev_site_settings` (
    `name`        VARCHAR(32)   NOT NULL,
    `value`       TEXT          NOT NULL,
    `updated_by`  INT UNSIGNED  NULL,
    `updated_at`  DATETIME      NOT NULL,
    PRIMARY KEY (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_admin_audit — журнал действий администраторов Anime Social в разделе «AsoPlay».
-- details — JSON: что именно поменяли.
--
CREATE TABLE IF NOT EXISTS `aviev_admin_audit` (
    `id`          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id`     INT UNSIGNED    NOT NULL,
    `action`      VARCHAR(48)     NOT NULL,
    `target`      VARCHAR(128)    NOT NULL DEFAULT '',
    `details`     VARCHAR(2000)   NOT NULL DEFAULT '{}',
    `at`          DATETIME        NOT NULL,
    PRIMARY KEY (`id`),
    KEY `ix_admin_audit_user` (`user_id`, `id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_watch_time — сколько секунд видео посмотрели вошедшие за каждый час (UTC), для «Обзора» в панели.
-- Считает server/user_lists.py по событиям плеера: сколько продвинулось видео между соседними событиями.
--
CREATE TABLE IF NOT EXISTS `aviev_watch_time` (
    `hour`        DATETIME        NOT NULL,
    `seconds`     BIGINT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (`hour`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_activity: индекс по дню — «Обзор» в панели считает серии за сутки и топ недели по всем людям.
--
SET @q := IF((SELECT COUNT(*) FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'aviev_activity' AND INDEX_NAME = 'ix_activity_day') = 0,
    'ALTER TABLE `aviev_activity` ADD KEY `ix_activity_day` (`day`, `kind`, `mal_id`)', 'DO 0');
PREPARE st FROM @q; EXECUTE st; DEALLOCATE PREPARE st;
