-- AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
-- All rights reserved. See /COPYRIGHT for full terms.
--
-- Присутствие на AsoPlay — «онлайн на AsoPlay» в Anime Social и друзья.
-- Idempotent — safe to run again, after the earlier migrations.
--
-- Apply with:
--     mysql -u root AnimeSocial < core/migrations/2026-10-30_presence.sql

SET NAMES utf8mb4;

--
-- aviev_privacy.hide_presence — никому не показывать, что человек на AsoPlay: ни друзьям здесь,
--                               ни в Anime Social (его id не уходит в POST /clients/presence).
-- aviev_privacy.show_watching — показывать друзьям, что он сейчас смотрит. По умолчанию выключено.
--
SET @db := DATABASE();

SET @q := IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'aviev_privacy' AND COLUMN_NAME = 'hide_presence') = 0,
    'ALTER TABLE `aviev_privacy` ADD COLUMN `hide_presence` TINYINT(1) NOT NULL DEFAULT 0 AFTER `hide_activity`', 'DO 0');
PREPARE st FROM @q; EXECUTE st; DEALLOCATE PREPARE st;

SET @q := IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'aviev_privacy' AND COLUMN_NAME = 'show_watching') = 0,
    'ALTER TABLE `aviev_privacy` ADD COLUMN `show_watching` TINYINT(1) NOT NULL DEFAULT 0 AFTER `hide_presence`', 'DO 0');
PREPARE st FROM @q; EXECUTE st; DEALLOCATE PREPARE st;
