"""
Присутствие друзьям по живому соединению: друзья тех, кто на сайте, и события «зашёл, вышел, смотрит».

Автор: Чепела Даниил Максимович.
AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).

Человек на AsoPlay, пока хоть одна его вкладка открыта и на экране (core/functions/presence.py). Событие о нём
уходит его друзьям из Anime Social, которые сейчас на сайте, и самой соцсети — с учётом его настроек (hide_presence,
show_watching). HubPresence — миксин Hub (core/functions/realtime.py): своего состояния нет.
"""
from __future__ import annotations

import asyncio
import logging

from core.classes import as_sessions
from core.config.config import SITE_URL
from core.functions import friends as friend_data, presence, privacy as privacy_rules

log = logging.getLogger("asoplay.realtime")

PRIVACY_TTL = 60


class HubPresence:
    """Присутствие друзьям: кто из друзей на сайте и что им сообщать — зашёл, вышел, начал или перестал смотреть.

    Миксин Hub (core/functions/realtime.py): своего состояния нет. Берёт у Hub:
    - атрибуты из Hub.__init__: _clock, _loop, _privacy, _titles, briefs, by_user, conns, followers,
      friends, published, social;
    - методы Hub: send."""
    async def load_friends(self, conn: Conn) -> None:
        """Друзья из соцсети — по токену сессии соцсети этого человека; без неё друзей нет."""
        ua = conn.ws.headers.get("user-agent") or ""
        try:
            token = await asyncio.to_thread(as_sessions.access_token, conn.session, conn.ip, ua) if as_sessions.enabled() else None
            if token:
                # friend_data.friend_ids сам сообщит id через friend_listeners
                await asyncio.to_thread(friend_data.friend_ids, conn.user_id, token, conn.ip, ua)
        except Exception as exc:  # noqa: BLE001 — без друзей соединение всё равно работает
            log.info("Could not load friends for realtime: %s", exc)

    def set_friends(self, user_id: int, ids: frozenset[int]) -> None:
        """Запомнить друзей человека и обратный указатель «кому о ком сообщать»; пустой набор — забыть."""
        if ids and user_id not in self.by_user:
            return
        old = self.friends.get(user_id, frozenset())
        for fid in old - ids:
            watchers = self.followers.get(fid)
            if watchers:
                watchers.discard(user_id)
                if not watchers:
                    self.followers.pop(fid, None)
        for fid in ids - old:
            self.followers.setdefault(fid, set()).add(user_id)
        if ids:
            self.friends[user_id] = ids
        else:
            self.friends.pop(user_id, None)

    def friends_from_thread(self, user_id: int, ids: list[int]) -> None:
        """set_friends из потока (friend_listeners) — в цикл событий хаба."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.set_friends, user_id, frozenset(ids))

    # ----- события друзьям -----
    async def _rules(self, user_id: int) -> privacy_rules.Privacy:
        """Настройки присутствия человека с кешем на PRIVACY_TTL секунд; база недоступна — никому не показывать."""
        hit = self._privacy.get(user_id)
        if hit and hit[0] > self._clock():
            return hit[1]
        try:
            rules = (await asyncio.to_thread(privacy_rules.for_users, [user_id])).get(user_id, privacy_rules.Privacy())
        except Exception:  # noqa: BLE001 — база недоступна: никому не показываем
            return privacy_rules.Privacy(hide_presence=True)
        self._privacy[user_id] = (self._clock() + PRIVACY_TTL, rules)
        return rules

    async def _title(self, mal_id: int, fallback: str) -> tuple[str, str]:
        """Название и постер тайтла из кеша страниц тайтлов; нет строки — название из вкладки."""
        if mal_id not in self._titles:
            try:
                found = (await asyncio.to_thread(presence.title_cards, [mal_id])).get(mal_id)
            except Exception:  # noqa: BLE001
                found = None
            if len(self._titles) > 5000:
                self._titles.clear()
            if found:
                self._titles[mal_id] = found
            title, poster = found or ("", "")
            return title or fallback, poster
        title, poster = self._titles[mal_id]
        return title or fallback, poster

    async def publish(self, user_id: int) -> None:
        """Сообщить друзьям, если для них что-то изменилось: зашёл, вышел, начал или перестал смотреть."""
        mark = presence.presence.current().get(user_id)
        rules = await self._rules(user_id) if mark else privacy_rules.Privacy()
        here = mark is not None and not rules.hide_presence
        watching = None
        if here and rules.show_watching and mark.mal_id:
            title, poster = await self._title(mark.mal_id, mark.title)
            if title:
                watching = {"mal_id": mark.mal_id, "title": title, "poster": poster, "path": f"/anime/{mark.mal_id}"}
        state = (here, (watching["mal_id"], watching["title"]) if watching else None)
        if self.published.get(user_id, (False, None)) == state:
            return
        self.social.presence(user_id, here, {"title": watching["title"], "url": SITE_URL + watching["path"]} if watching else None)
        brief = self.briefs.get(user_id)
        if here:
            self.published[user_id] = state
        else:
            self.published.pop(user_id, None)
            if user_id not in self.by_user:
                self.briefs.pop(user_id, None)
        if not brief:
            return
        event = {"type": "presence", "user": brief, "on_asoplay": here, "watching": watching}
        for viewer in list(self.followers.get(user_id, ())):
            for conn_id in list(self.by_user.get(viewer, ())):
                conn = self.conns.get(conn_id)
                if conn:
                    await self.send(conn, event)

    async def privacy_changed(self, user_id: int) -> None:
        """Человек поменял настройки: сбросить кеш и сообщить друзьям, если для них что-то изменилось."""
        self._privacy.pop(user_id, None)
        await self.publish(user_id)

    def privacy_changed_from_thread(self, user_id: int) -> None:
        """privacy_changed из обычного обработчика (поток)."""
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self.privacy_changed(user_id), self._loop)
