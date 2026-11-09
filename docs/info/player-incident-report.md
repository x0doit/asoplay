# AsoPlay player incident report

Дата работ: 2026-05-03.

## Контекст

Проблема проявлялась на телевизоре с Yandex Browser / Android WebView:

```text
Mozilla/5.0 (Linux; Android 9; Build/PPR1.180610.011; wv)
AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0
Chrome/118.0.0.0 YaBrowser/24.12.0.453 (lite) TV Safari/537.36
```

Пользователь пытался смотреть anime `51553` в озвучке `Dream Cast`, 1 серия.

## Что ломалось

Сначала iframe Alloha через наш `/player/frame` на телевизоре показывал черный экран.
В клиентских логах и systemd-журнале были последовательные ошибки:

```text
ReferenceError: fileList is not defined
Cannot read properties of undefined (reading 'active')
POST https://alloha.yani.tv/bnsi/movies/1378659 -> 404 Not Found
```

После исправления загрузки JS Alloha плеер стал отрисовываться, но вместо видео показывал:

```text
Упс. Ошибка!
```

Причина этой финальной ошибки была уже не в CSS или sandbox iframe. Alloha сам отдавал `404`
на файл Dream Cast:

```text
/bnsi/movies/1378659
```

## Почему так произошло

У OldYummy для одной и той же озвучки `Dream Cast` было несколько iframe:

```text
//alloha.yani.tv/?token_movie=...&translation=215&season=1&episode=1...
//kodikplayer.com/season/...
//ru.yummyani.me/iframeCVH.html?dubbing_code=Dream+Cast&anime_id=51553&episode=1...
```

Серверная дедупликация озвучек брала первый iframe с таким названием. Первым оказался Alloha.
Именно этот Alloha-вариант для 1 серии Dream Cast отдавал `404`.

CVH-вариант той же озвучки оказался рабочим. Он отдает прямой video source через:

```text
https://plapi.cdnvideohub.com/api/v1/player/sv/playlist
https://plapi.cdnvideohub.com/api/v1/player/sv/video/<vkId>
```

## Что исправили

### 1. Alloha bootstrap

Файл:

```text
server/player_proxy.py
```

Для Alloha были добавлены совместимости:

- `fileList` и `config` теперь поднимаются до загрузки `app.*.js`;
- `fileList` кешируется по base URL;
- `fileList.active.typeList` создается, если его нет;
- для сериалов добавляются ожидаемые алиасы структуры;
- рекламные SDK-запросы продолжают блокироваться через adblock/proxy.

Это убрало черный экран и ошибки `fileList is not defined`.

### 2. Выбор рабочего iframe для одинаковой озвучки

Файл:

```text
server/main.py
```

В `_native_dubs()` добавлен scoring iframe:

```text
iframeCVH.html   -> 30
kodikplayer.com  -> 20
alloha.yani.tv   -> 10
other            -> 0
```

Если у одной озвучки несколько iframe, сервер теперь выбирает более надежный вариант.
Например в нашем тесте для Dream Cast это переключило источник с битого Alloha на рабочий CVH.

### 3. Clean CVH player

Файл:

```text
server/player_proxy.py
```

Для `ru.yummyani.me/iframeCVH.html` сделан отдельный server-side clean player:

- сервер запрашивает CVH playlist;
- находит нужную серию и озвучку;
- запрашивает video metadata;
- выбирает доступные качества;
- отдает собственный HTML с `<video>` и проксированными media URL.

Текущая версия прокси после работ:

```text
20260503-player44
```

## Текущая логика нового плеера

Новый clean-плеер:

- не использует нативные `video controls`, чтобы элементы не накладывались друг на друга на TV;
- имеет единую нижнюю панель;
- содержит play/pause, seek bar, время, выбор качества, fullscreen;
- клик по самому видео переключает play/pause;
- при смене качества сохраняет текущую позицию;
- через 10 секунд после запуска скрывает интерфейс, если видео играет;
- при паузе интерфейс возвращается;
- при клике/движении/фокусе интерфейс снова показывается;
- отправляет события времени в родительский AsoPlay, чтобы продолжал работать прогресс просмотра.

## Проверки

Проверка backend:

```bash
systemctl status asoplay --no-pager
curl http://127.0.0.1:8787/health
```

Ожидаемое состояние:

```json
{
  "ok": true,
  "vpn": true,
  "db": { "ok": true },
  "adblock": { "ready": true }
}
```

Проверка логов:

```bash
journalctl -u asoplay -f --no-pager
```

В рабочих логах для TV после исправления видно:

```text
GET /player/frame?url=https%3A%2F%2Fru.yummyani.me%2FiframeCVH.html...
GET https://plapi.cdnvideohub.com/api/v1/player/sv/playlist?...id=51553 -> 200 OK
GET https://plapi.cdnvideohub.com/api/v1/player/sv/video/12960243931888 -> 200 OK
GET /player/proxy?...ok6-2.vkuser.net...&pv=20260503-player44 -> 200 OK
```

## Где исходники

Рабочий код находится в:

```text
server/player_proxy.py
server/main.py
```

В этой папке `info` дополнительно сохранены референсные исходники нового clean-плеера:

```text
info/cvh-clean-player.html
info/cvh-clean-player.css
info/cvh-clean-player.js
```

Эти файлы не подключаются приложением напрямую. Они нужны как документация и удобная копия логики,
которая сейчас встроена на VDS в `_clean_video_frame()` внутри `server/player_proxy.py`.
