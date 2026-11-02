-- AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
-- All rights reserved. See /COPYRIGHT for full terms.
--
-- Кеш страниц тайтлов пишет только сервер, из своего каталога (core/modules/title_cache.py). До этого любой
-- браузер мог прислать POST /title-pages/record и переписать название, описание, постер и жанры страницы
-- /anime/{id}/, которую читают поисковики и превью ссылок. Всё, что успели записать так, считается устаревшим:
-- первый заход обновит запись из каталога, а подменённый адрес уйдёт 301 на настоящий.
-- aviev_title_refresh_queue — остаток недоделанной очереди обновления: в неё никто не писал, store_title только
-- очищал её. Сервер обновляет устаревшие страницы сам, в фоне, без таблицы.
-- Применять при остановленном сервере, до старта нового кода: старый код ещё очищает эту таблицу.
-- Idempotent — safe to run again, after the earlier migrations.
--
-- Apply with:
--     mysql -u root AnimeSocial < core/migrations/2026-11-02_title_pages_server.sql

SET NAMES utf8mb4;
SET time_zone = '+00:00';

UPDATE `aviev_title_pages` SET `fresh_until` = UTC_TIMESTAMP();

DROP TABLE IF EXISTS `aviev_title_refresh_queue`;
