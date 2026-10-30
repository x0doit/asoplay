-- AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
-- All rights reserved. See /COPYRIGHT for full terms.
--
-- Вход через Anime Social. Idempotent — safe to run again, after the earlier migrations.
--
-- Apply with:
--     mysql -u root AnimeSocial < core/migrations/2026-10-30_as_sessions.sql

SET NAMES utf8mb4;
SET time_zone = '+00:00';

--
-- aviev_as_sessions — сессия устройства Anime Social, выданная при входе через соцсеть.
-- Одна строка на сессию AsoPlay (aviev_sessions.token): выход из AsoPlay завершает и её.
-- Токены зашифрованы (core/classes/token_box.py, ключ AV_TOKEN_KEY из .env), сроки — Unix-секунды.
--
CREATE TABLE IF NOT EXISTS `aviev_as_sessions` (
    `session_token`       CHAR(64)        NOT NULL,
    `user_id`             INT UNSIGNED    NOT NULL,
    `as_session_id`       INT UNSIGNED    NULL,
    `access_token`        VARBINARY(255)  NOT NULL,
    `access_expires_at`   INT UNSIGNED    NOT NULL,
    `refresh_token`       VARBINARY(255)  NOT NULL,
    `refresh_expires_at`  INT UNSIGNED    NOT NULL,
    `created_at`          DATETIME        NOT NULL,
    `updated_at`          DATETIME        NOT NULL,
    PRIMARY KEY (`session_token`),
    KEY `ix_as_sessions_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
