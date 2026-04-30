-- AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
-- All rights reserved. See /COPYRIGHT for full terms.
--
-- Pass-2 migration. Idempotent — safe to run on top of pass-1 schema.
--
-- Apply with:
--     mysql -u root AnimeSocial < sql/aviev-schema-pass2.sql
-- or via the runnable helper:
--     python .claude/backups/_dump_aviev.py   (for a backup first, if you like)
--     python -m sql.apply_pass2              (if you wire one)

SET NAMES utf8mb4;
SET time_zone = '+00:00';

--
-- aviev_user_lists — единая таблица списков пользователя.
-- Одна строка = один тайтл у одного пользователя.
-- Статус — взаимоисключающий (один из watching/planned/completed/dropped/postponed).
-- Любимые — отдельный флаг is_favorite, живёт рядом со статусом.
--
CREATE TABLE IF NOT EXISTS `aviev_user_lists` (
    `user_id`        INT UNSIGNED NOT NULL,
    `mal_id`         INT UNSIGNED NOT NULL,
    `status`         ENUM('watching','planned','completed','dropped','postponed') NULL,
    `status_source`  ENUM('manual','auto') NOT NULL DEFAULT 'manual',
    `is_favorite`    TINYINT(1)   NOT NULL DEFAULT 0,
    `title`          VARCHAR(500) NOT NULL DEFAULT '',
    `poster_url`     VARCHAR(500) NOT NULL DEFAULT '',
    `added_at`       DATETIME     NOT NULL,
    `updated_at`     DATETIME     NOT NULL,
    PRIMARY KEY (`user_id`, `mal_id`),
    KEY `ix_lists_status`   (`user_id`, `status`),
    KEY `ix_lists_favorite` (`user_id`, `is_favorite`),
    KEY `ix_lists_updated`  (`user_id`, `updated_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_privacy — настройки приватности публичного профиля.
-- По умолчанию всё открыто; включением флага пользователь прячет блок.
--
CREATE TABLE IF NOT EXISTS `aviev_privacy` (
    `user_id`        INT UNSIGNED NOT NULL,
    `hide_lists`     TINYINT(1)   NOT NULL DEFAULT 0,
    `hide_activity`  TINYINT(1)   NOT NULL DEFAULT 0,
    `updated_at`     DATETIME     NOT NULL,
    PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- aviev_activity — журнал событий для contribution-графика.
-- `day` денормализован для быстрой агрегации GROUP BY по дням.
-- `kind` — тип действия: watch_start, watch_continue, list_add, list_move,
--         favorite, rate, complete, unfavorite.
--
CREATE TABLE IF NOT EXISTS `aviev_activity` (
    `id`           BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    `user_id`      INT UNSIGNED    NOT NULL,
    `day`          DATE            NOT NULL,
    `kind`         VARCHAR(24)     NOT NULL,
    `mal_id`       INT UNSIGNED    NULL,
    `meta`         VARCHAR(255)    NULL,
    `at`           DATETIME        NOT NULL,
    PRIMARY KEY (`id`),
    KEY `ix_activity_userday`  (`user_id`, `day`),
    KEY `ix_activity_recent`   (`user_id`, `at`),
    UNIQUE KEY `uq_act_dedup`  (`user_id`, `day`, `kind`, `mal_id`, `meta`(64))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

--
-- Keep pass-1 watch history compatible with the player/list auto-rules.
-- Older dev databases may have been created before this column existed.
--
SET @has_history_total := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name   = 'aviev_watch_history'
      AND column_name  = 'episodes_total'
);
SET @stmt := IF(
    @has_history_total = 0,
    'ALTER TABLE `aviev_watch_history` ADD COLUMN `episodes_total` SMALLINT UNSIGNED NOT NULL DEFAULT 0 AFTER `episode_duration`',
    'SELECT "episodes_total already present"'
);
PREPARE s FROM @stmt; EXECUTE s; DEALLOCATE PREPARE s;

--
-- Activity dedup is part of the runtime contract used by record_event().
-- If this ALTER fails on an old database, remove exact duplicate rows first.
--
SET @has_activity_dedup := (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name   = 'aviev_activity'
      AND index_name   = 'uq_act_dedup'
);
SET @stmt := IF(
    @has_activity_dedup = 0,
    'ALTER TABLE `aviev_activity` ADD UNIQUE KEY `uq_act_dedup` (`user_id`, `day`, `kind`, `mal_id`, `meta`(64))',
    'SELECT "uq_act_dedup already present"'
);
PREPARE s FROM @stmt; EXECUTE s; DEALLOCATE PREPARE s;

--
-- Расширяем aviev_account_settings новой колонкой auto_add_lists.
-- Не можем использовать ALTER TABLE IF EXISTS в чистом MySQL 8 —
-- делаем condition check на information_schema.
--
SET @has_auto_add := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name   = 'aviev_account_settings'
      AND column_name  = 'auto_add_lists'
);
SET @stmt := IF(
    @has_auto_add = 0,
    'ALTER TABLE `aviev_account_settings` ADD COLUMN `auto_add_lists` TINYINT(1) NOT NULL DEFAULT 1 AFTER `autonext`',
    'SELECT "auto_add_lists already present"'
);
PREPARE s FROM @stmt; EXECUTE s; DEALLOCATE PREPARE s;

--
-- ДАННЫЕ: миграция из старых таблиц в aviev_user_lists.
-- Обе INSERT IGNORE — при повторном запуске существующие записи не трогаем.
--

-- 1. aviev_favorites → is_favorite=1 в списках.
INSERT IGNORE INTO `aviev_user_lists`
    (user_id, mal_id, status, status_source, is_favorite, title, poster_url, added_at, updated_at)
SELECT
    f.user_id, f.mal_id,
    NULL,              -- статус вне «любимых» пусть проставляется пользователем/авто
    'manual',
    1,                 -- это флаг избранного
    f.title, f.poster_url,
    f.added_at, f.added_at
FROM `aviev_favorites` f;

-- Если title/poster_url пустой в lists, но есть в aviev_favorites — подтянем.
UPDATE `aviev_user_lists` l
JOIN `aviev_favorites` f ON f.user_id = l.user_id AND f.mal_id = l.mal_id
SET l.is_favorite = 1,
    l.title       = IF(l.title = '', f.title, l.title),
    l.poster_url  = IF(l.poster_url = '', f.poster_url, l.poster_url)
WHERE f.title IS NOT NULL;

-- 2. aviev_watch_history → авто-статус «watching» (если нет своего статуса).
INSERT IGNORE INTO `aviev_user_lists`
    (user_id, mal_id, status, status_source, is_favorite, title, poster_url, added_at, updated_at)
SELECT
    w.user_id, w.mal_id,
    'watching',
    'auto',
    0,
    w.title, w.poster_url,
    w.updated_at, w.updated_at
FROM `aviev_watch_history` w
WHERE w.episode_seconds >= 600         -- «смотрящий» = уже 10+ минут
   OR w.last_episode > 1;              -- или продвинулся дальше первой серии

-- Если у тайтла уже есть manual статус, оставляем его; если status=NULL — обновим на watching/auto.
UPDATE `aviev_user_lists` l
JOIN `aviev_watch_history` w ON w.user_id = l.user_id AND w.mal_id = l.mal_id
SET l.status         = 'watching',
    l.status_source  = 'auto',
    l.title          = IF(l.title = '', w.title, l.title),
    l.poster_url     = IF(l.poster_url = '', w.poster_url, l.poster_url),
    l.updated_at     = GREATEST(l.updated_at, w.updated_at)
WHERE l.status IS NULL
  AND (w.episode_seconds >= 600 OR w.last_episode > 1);

--
-- Заметка: aviev_favorites и aviev_watch_history мы НЕ дропаем — они остаются:
--   - watch_history продолжает хранить позицию последнего эпизода и метаданные
--     «Продолжить просмотр». Это источник прогресса, а не источник статуса.
--   - aviev_favorites остаётся для обратной совместимости с /account/favorites
--     API (endpoint проксируется в новый список is_favorite=1).
--
