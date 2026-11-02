-- AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
-- All rights reserved. See /COPYRIGHT for full terms.
--
-- Очередь событий просмотра для Anime Social (socialnetwork.local/docs/api/clients.md, раздел 14): серия впервые
-- досмотрена, тайтл досмотрен по последней серии, поставлена оценка. Событие пишется той же транзакцией, что
-- и само действие (core/functions/as_events.py), и лежит здесь, пока соцсеть его не подтвердила
-- (core/functions/as_events_sender.py). Применить до выкладки кода: без таблицы действия с событием не сохранятся.
-- Idempotent — safe to run again, after the earlier migrations.
--
-- Apply with:
--     mysql -u root AnimeSocial < core/migrations/2026-11-02_as_events.sql

SET NAMES utf8mb4;
SET time_zone = '+00:00';

--
-- aviev_as_events — одно событие на строку. episode — номер серии у type='episode', 0 у остальных; score — оценка
-- у type='rating'; at — когда это случилось (UTC). Уникальный ключ: серия и досмотр пишутся один раз, новая оценка
-- тайтла заменяет неотправленную.
--
CREATE TABLE IF NOT EXISTS `aviev_as_events` (
    `id`       BIGINT UNSIGNED                          NOT NULL AUTO_INCREMENT,
    `user_id`  INT UNSIGNED                             NOT NULL,
    `type`     ENUM('episode', 'completed', 'rating')   NOT NULL,
    `mal_id`   INT UNSIGNED                             NOT NULL,
    `episode`  INT UNSIGNED                             NOT NULL DEFAULT 0,
    `score`    TINYINT UNSIGNED                         NULL,
    `at`       DATETIME                                 NOT NULL,
    PRIMARY KEY (`id`),
    UNIQUE KEY `uq_as_events` (`user_id`, `type`, `mal_id`, `episode`),
    KEY `ix_as_events_at` (`at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
