-- AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
-- All rights reserved. See /COPYRIGHT for full terms.
--
-- Где человек остановился в серии — для кнопки «Продолжить с m:ss» у «Эпизод N».
-- Idempotent — safe to run again, after the earlier migrations.
--
-- Apply with:
--     mysql -u root AnimeSocial < core/migrations/2026-11-05_progress_last_seconds.sql

SET NAMES utf8mb4;

--
-- aviev_episode_progress.last_seconds — последняя позиция, которую прислал плеер. seconds остаётся дальней точкой
--                                       серии: по ней «досмотрено». У строк, записанных до этой миграции, поле пустое,
--                                       и вместо него берётся seconds. Применять до нового кода: старый код его не знает.
--
SET @db := DATABASE();

SET @q := IF((SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = @db AND TABLE_NAME = 'aviev_episode_progress' AND COLUMN_NAME = 'last_seconds') = 0,
    'ALTER TABLE `aviev_episode_progress` ADD COLUMN `last_seconds` INT UNSIGNED NULL DEFAULT NULL AFTER `seconds`', 'DO 0');
PREPARE st FROM @q; EXECUTE st; DEALLOCATE PREPARE st;
