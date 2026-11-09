# Админ-API AsoPlay для раздела «AsoPlay» в /cpanel (этап 4, пункт 10) — контракт для движка

Готово на стороне AsoPlay 30.10.2026 (локально, `http://127.0.0.1:8787/api/admin/…`). Код — `core/admin/` (вход `router.py` и части `auth`, `reports`, `audit`, `settings`, `titles`, `summary`),
`server/site_settings.py`, `server/video_reports.py`, проверки — `tools/test-admin-api.py`.

## Подпись (отличие от задачи: id администратора входит в подпись)

```
X-AsoPlay-Admin-User: {id администратора Anime Social}
X-AsoPlay-Admin: "{ts}.{hex}"
hex = hash_hmac('sha256', ts . "\n" . METHOD . "\n" . path_with_query . "\n" . admin_user . "\n" . body, secret)
```

- `path_with_query` — ровно то, что уходит в запросе, уже закодированное (как отдаёт `http_build_query`);
  `body` — сырое тело запроса, у GET — пустая строка.
- `ts` — Unix-секунды, окно ±60 с. Повтор той же подписи у PATCH/PUT/POST отклоняется (`replayed`); GET повторять можно.
- Только с этой машины, напрямую на `127.0.0.1:8787`. Через публичный адрес (nginx добавляет X-Forwarded-For и
  X-Real-IP) API отвечает 404.
- Секрет — `AV_ADMIN_SECRET` в .env AsoPlay. Предложение: генерирует панель и показывает один раз, как секрет
  клиента; владелец передаёт его в .env AsoPlay. Пока его нет — 503 `not_configured`.

```php
$ts = (string) time();
$sig = hash_hmac('sha256', $ts."\n".$method."\n".$pathWithQuery."\n".$adminId."\n".$body, $secret);
// заголовки: X-AsoPlay-Admin: "$ts.$sig", X-AsoPlay-Admin-User: $adminId, Content-Type: application/json
```

## Ответы

`{ok: true, data}` или `{ok: false, error: {code, message}}`. Коды: `not_found` 404, `not_configured` 503,
`bad_signature` / `expired` / `replayed` 401, `bad_admin_user` 400, `validation_failed` 422 (message — поле),
`no_sign_in_left` 422, `unknown_source` 422, `title_not_found` 404; неизвестный метод — `not_found` 404 «Нет такого
метода» или `method_not_allowed` 405, сбой AsoPlay — `internal_error` 500. message — короткий, по-русски, без секретов
(панель показывает его под своим текстом). Время во всех полях — Unix-секунды.
`fail_rate_24h` — доля от 0 до 1, null — за сутки запросов не было. AsoPlay отвечает быстрее 8 секунд (столько ждёт
панель): каталог MyAnimeList спрашивается не дольше 5 секунд, дальше — без названия, запрос досчитывается в фоне.

## Методы (что сверх задачи — помечено «+»)

- `GET /summary` → version (сборка сайта, например `20261030-admin1`), started_at, health {ok, sources: [{id, name,
  enabled, ok, fail_rate_24h, latency_ms, + requests_24h}]}, users {total, active_1d, active_7d, active_30d, new_7d},
  watch {episodes_1d, minutes_1d}, top_titles_7d [{mal_id, title, viewers, + hidden}], + online {users, tabs},
  + reports_open. Люди и просмотры — вошедшие; «активен» — была открыта вкладка AsoPlay.
- `GET /reports?status=open|fixed|rejected&cursor=` → items [{id, mal_id, title, episode, dub, source, + source_name,
  + player (хост плеера), kind, + kind_title, text, user_id, created, + status, + note, + updated_by, + updated_at,
  + url}], next_cursor (id; null — конец), + counts {open, fixed, rejected}.
- `PATCH /reports/{id}` {status: open | fixed | rejected, note — до 1000 символов, можно пустую} → жалоба.
- Причины (kind, а подпись — kind_title в каждой жалобе): no_video «Видео не запускается», wrong_episode «Не та серия»,
  no_sound «Нет звука или он не совпадает с видео», bad_quality «Плохое качество», wrong_dub «Не та озвучка»,
  other «Другое». Публично — `GET /reports/kinds`. user_id жалобы всегда есть: жалуются только вошедшие.
- `GET /titles?q=&cursor=` → без q: тайтлы, которых касались модераторы, свежие первыми (курсор — смещение);
  с q: по названию (кеш AsoPlay и MyAnimeList), по номеру MAL или по ссылке на страницу AsoPlay. Элемент:
  {mal_id, title, hidden, pinned, adult, note, updated_by, updated_at, + url}.
- `PUT /titles/{mal_id}` {hidden?, pinned?, adult?, note?} — можно все четыре сразу (так шлёт панель) или часть;
  сохраняется и пишется в журнал только изменившееся. Нет в MAL → 404 `title_not_found`.
- `POST /titles/{mal_id}/purge` → {mal_id, removed}: кеш карточки и серий из каталога, Shikimori и страница тайтла.
- `GET /settings` → notice {text, until|null}, features {comments, password_login, as_login}, sources [{id, + name,
  enabled, order}] — все источники плеера, + available {as_login, password_login, comments}: что можно включить
  (комментариев на AsoPlay пока нет).
- `PUT /settings` {notice?, features?, sources?} — весь объект (так шлёт панель) или часть; сохраняется, видно на
  сайте и пишется в журнал только изменившееся. Оба входа выключить нельзя (`no_sign_in_left`); notice.text — до 500
  символов, until — Unix или null; sources — полный список, order с 1; источник не из списка → `unknown_source`.
- `GET /audit?cursor=` → items [{+ id, user_id (всегда администратор), action, target, + details, at}], next_cursor.
  action — как их показывает панель: report.fixed | report.rejected | report.open, title.update, title.purge,
  settings.notice | settings.features | settings.sources.
- Курсоры — строки из цифр (непрозрачные для панели); next_cursor: null — дальше нет.

## Что видит сайт

- hidden — тайтл пропадает из каталога, поиска, «Случайного» и карты сайта; страница остаётся с «Видео недоступно»
  (для поисковиков 451 и noindex), видео не ищется.
- pinned — первым в карусели главной с меткой «Рекомендуем».
- adult — метка «18+» и вопрос «Мне есть 18» перед видео (запоминается в браузере).
- Плашка, выключатели входа, порядок и включение источников — на открытых страницах сразу (живое соединение).
- Роли (admin — всё, chief_moderator и moderator — жалобы и тайтлы) проверяет панель.
