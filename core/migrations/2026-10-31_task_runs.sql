-- AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
-- All rights reserved. See /COPYRIGHT for full terms.
--
-- Отметки фоновых задач AsoPlay. Первая задача — правило 30 дней (core/functions/lists_sweep.py): по отметке
-- оно идёт раз в сутки, а после простоя сервера догоняет пропущенный проход. Idempotent — safe to run again,
-- after the earlier migrations.
--
-- Apply with:
--     mysql -u root AnimeSocial < core/migrations/2026-10-31_task_runs.sql

SET NAMES utf8mb4;
SET time_zone = '+00:00';

--
-- aviev_task_runs — последний проход задачи: одна строка на задачу. finished_at = NULL — проход идёт или оборвался
-- (тогда его повторят); processed — сколько строк он сделал.
--
CREATE TABLE IF NOT EXISTS `aviev_task_runs` (
    `name`         VARCHAR(64)  NOT NULL,
    `started_at`   DATETIME     NOT NULL,
    `finished_at`  DATETIME     NULL,
    `processed`    INT UNSIGNED NOT NULL DEFAULT 0,
    PRIMARY KEY (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
