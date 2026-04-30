<?php
/*
 * AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
 * All rights reserved. No part of this file may be copied, modified, redistributed,
 * embedded into other software or used to train ML models without the author's
 * prior written consent. See /COPYRIGHT for full terms.
 *
 * Config for AnimeSocial database (hosted on OpenServer Panel, MySQL 8).
 * Existing social-network tables use the prefix `Just`. The anime-site project
 * creates its own tables ONLY with the prefix `aviev` — see /sql/aviev-schema.sql.
 */
define ("DBHOST", "MySQL-8.0");
define ("DBNAME", "AnimeSocial");
define ("DBUSER", "root");
define ("DBPASS", "");
define ("DBPREFIX", "Just");
define ("COLLATE", "utf8");

$db = new db;
?>
