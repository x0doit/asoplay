-- AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
-- All rights reserved. See /COPYRIGHT for full terms.
--
-- Любимая озвучка в настройках аккаунта и реестр имён озвучек, из которого её выбирают.
-- Idempotent — safe to run again, after the earlier migrations.
--
-- Apply with:
--     mysql -u root AnimeSocial < core/migrations/2026-11-10_dub_names.sql

SET NAMES utf8mb4;

--
-- aviev_dub_names — озвучки, которые встречались в плеере (core/functions/dub_names.py): ключ — имя для сравнения
--                   (source_filters.norm_dub_name), name — как его прислал источник, seen — сколько раз встречалось.
--                   Только озвучки, без субтитров; не больше 5000 строк — лишние, самые редкие, убираются.
--
CREATE TABLE IF NOT EXISTS `aviev_dub_names` (
    `norm`      VARCHAR(128) NOT NULL,
    `name`      VARCHAR(200) NOT NULL,
    `seen`      INT UNSIGNED NOT NULL DEFAULT 0,
    `last_seen` DATETIME     NOT NULL,
    PRIMARY KEY (`norm`),
    KEY `idx_seen` (`seen`, `last_seen`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_account_settings.favorite_dub — любимая озвучка человека, как её показывает список; пусто — не выбрана.
--                                       Применять до нового кода: старый код поля не знает и не трогает его.
--
SET @db := DATABASE();

SET @q := IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'aviev_account_settings' AND COLUMN_NAME = 'favorite_dub') = 0,
    'ALTER TABLE `aviev_account_settings` ADD COLUMN `favorite_dub` VARCHAR(200) NOT NULL DEFAULT '''' AFTER `auto_add_lists`', 'DO 0');
PREPARE st FROM @q; EXECUTE st; DEALLOCATE PREPARE st;
