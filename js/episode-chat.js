/*
 * Чат серии на странице тайтла: значок у «Эпизод N» меняет панель «Эпизоды» на чат этой серии; комментарий встаёт на
 * минуту серии, список идёт по минутам, корневые комментарии пролетают полосками поверх видео, пока чат включён.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * Плеер (app.js) сообщает время видео (time), умеет ли он перематывать (setSeek) и смену серии (setEpisode). Данные —
 * js/chat-data.js, список — js/chat-view.js, действия — js/chat-actions.js, полоски — js/chat-strips.js, разметка
 * значка и панели — js/chat-panel.js. Открытие и закрытие объявляются событием av:chat на document: по нему
 * кнопка «Наверх» (app.js) не ложится поверх чата.
 * Раскладка: от 1024 px чат стоит в колонке справа от плеера (на 1024–1311 колонка встаёт туда только с чатом),
 * уже — лист снизу под видео, без затемнения; страница под ним не прокручивается, поле держится над клавиатурой.
 * Число у значка живёт по живому соединению; после обрыва связи открытое окно перечитывается. Переключатель
 * «Спойлеры» в голове панели (выключен — ещё не просмотренное размыто) запоминается в браузере зрителя.
 */
import { ChatActions } from "./chat-actions.js?v=20261102-chat1";
import { ChatStore } from "./chat-data.js?v=20261102-chat1";
import { buildChat, spoilersShown } from "./chat-panel.js?v=20261102-chat1";
import { ChatStrips } from "./chat-strips.js?v=20261102-chat1";
import { ChatView } from "./chat-view.js?v=20261102-chat1";

const SHEET = "(max-width: 1023.98px)";
const LOOKAHEAD = 120;

/** Чат серии одного тайтла; deps: malId, backend, user(), seek(t), ago(unix), site(), gate(), confirm(o),
 * dialog(cls, html, o), watch(objects), sheetDrag. */
export class EpisodeChat {
  /** Значок и панель встают в разметку страницы тайтла. */
  constructor(deps) {
    this.deps = deps;
    this.open = false;
    this.t = 0;
    this.canSeek = false;
    this.store = null;
    this.object = "";
    this.layout = document.querySelector(".title-layout");
    this.sidebar = document.querySelector(".ep-sidebar");
    this.card = document.getElementById("avPlayerCard");
    this.video = this.card?.querySelector(".sp-video") || null;
    Object.assign(this, buildChat(this.sidebar, {
      toggle: () => (this.open ? this.close() : this.show()), close: () => this.close(),
      toNow: () => this.view.toNow(), spoilers: shown => this.view.setHide(!shown),
    }));
    deps.sheetDrag?.bind(this.panel, { close: () => this.close(), active: () => this.sheet,
      scroll: ".chat-list", skip: "textarea, .chat-compose, .chat-replybox" });
    this.view = new ChatView(this.list, {
      site: () => deps.site(), ago: unix => deps.ago(unix), guest: () => !deps.user(), canSeek: () => this.canSeek,
      act: (name, id, button, rootId) => this._act(name, id, button, rootId),
      edge: (dir, auto) => this._edge(dir, auto),
      follow: on => { this.toNow.hidden = on; },
    });
    this.view.setHide(!spoilersShown());
    this.actions = new ChatActions(this.panel, {
      backend: deps.backend, store: () => this.store, object: () => this.object, time: () => this.t,
      guest: () => !deps.user(), gate: deps.gate, confirm: deps.confirm, dialog: deps.dialog,
      posted: c => this._posted(c),
    });
    this.strips = new ChatStrips(this.video);
    this._onComment = e => { if (e.detail?.object === this.object) this.store?.event(e.detail); };
    this._onLink = e => this._link(e.detail);
    document.addEventListener("av:comment", this._onComment);
    document.addEventListener("av:link", this._onLink);
    this._onResize = () => { if (this.open) this._place(); };
    window.addEventListener("resize", this._onResize);
    window.visualViewport?.addEventListener("resize", this._onResize);
    this._cardObserver = typeof ResizeObserver === "function" ? new ResizeObserver(() => this._fitHeight()) : null;
  }

  /** Серия сменилась: своя ветка, подписка на её события, число у значка; открытый чат перечитывается. */
  setEpisode(num) {
    const object = `asoplay:episode:${this.deps.malId}.${num}`;
    if (object === this.object) return;
    if (this.store) this.store.alive = false;
    this.object = object;
    this.store = new ChatStore(this.deps.backend, object, change => this._changed(change));
    this.deps.watch([object]);
    this.strips.stop();
    this.store.loadCount().catch(() => {});
    if (this.open) { this.strips.start(); this._load(); }
  }

  /** Время видео (и длительность, если плеер её знает): полоски, отметка «Сейчас», подсказка поля, подгрузка вперёд. */
  time(t, duration = 0) {
    if (!Number.isFinite(t) || t < 0) return;
    this.t = t;
    if (duration > 30) this.store?.setEnd(duration);
    if (!this.open || !this.store) return;
    this.strips.tick(t);
    if (Math.floor(t) !== this._shownSecond) {
      this._shownSecond = Math.floor(t);
      if (this.store.roots.length) this.view.placeNow(this.store.roots, t);
      this.actions.tickPlaceholder(t);
    }
    const s = this.store;
    if (s.covered !== null && !s.atEnd && t + LOOKAHEAD > s.covered) s.loadAfter().catch(() => {});
  }

  /** Умеет ли текущий плеер перематывать (наш кадр сообщил, Kodik): минуты — кнопки или текст. */
  setSeek(can) {
    this.canSeek = !!can;
    this.view.setSeek(this.canSeek);
  }

  /** Открыть чат: вместо «Эпизодов» (или листом снизу), полоски поверх видео. */
  show() {
    if (this.open || !this.store) return;
    this.open = true;
    this.button.setAttribute("aria-pressed", "true");
    this.button.classList.add("ed-iconbtn--tonal");
    this.panel.hidden = false;
    this.sidebar?.classList.add("is-chat");
    this.layout?.classList.add("is-chat");
    this.actions.mountCompose(this.composeSlot);
    this.actions.tickPlaceholder(this.t);
    this._place();
    this._cardObserver?.observe(this.card);
    if (this.card?.parentElement) this._cardObserver?.observe(this.card.parentElement);
    this.strips.start();
    this._load();
    this._dates = setInterval(() => this.view.refreshDates(), 60_000);
    document.dispatchEvent(new CustomEvent("av:chat", { detail: { open: true } }));
  }

  /** Закрыть: «Эпизоды» на месте, поверх видео ничего нет. */
  close() {
    if (!this.open) return;
    this.open = false;
    this.button.setAttribute("aria-pressed", "false");
    this.button.classList.remove("ed-iconbtn--tonal");
    this.panel.hidden = true;
    this.panel.classList.remove("is-sheet");
    this.panel.style.top = this.panel.style.bottom = this.panel.style.height = "";
    this.sidebar?.classList.remove("is-chat");
    this.layout?.classList.remove("is-chat");
    document.documentElement.classList.remove("chat-sheet-open");
    this._cardObserver?.disconnect();
    this.actions.closeMenu();
    this.strips.stop();
    clearInterval(this._dates);
    document.dispatchEvent(new CustomEvent("av:chat", { detail: { open: false } }));
  }

  /** Окно вокруг текущей минуты: загрузка, потом список или пусто; отказ — ошибка с «Повторить». force — по нажатию
   * человека (открыл, «Повторить», другая серия), мимо паузы после отказа; после переподключения — с паузой. */
  async _load(force = true) {
    const store = this.store;
    this.view.state("loading");
    try { await store.loadAround(this.t, force); } catch (_) { if (store === this.store && this.open) this.view.state("error"); }
  }

  /** Лист снизу на узком экране или колонка справа; высота колонки — как у карточки плеера. */
  _place() {
    this.sheet = window.matchMedia(SHEET).matches;
    this.panel.classList.toggle("is-sheet", this.sheet);
    document.documentElement.classList.toggle("chat-sheet-open", this.sheet);
    if (!this.sheet) { this.panel.style.top = this.panel.style.bottom = ""; this._fitHeight(); return; }
    this.panel.style.height = "";
    const header = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--header-h")) || 56;
    const box = this.video?.getBoundingClientRect();
    if (box && Math.abs(box.top - header) > 1) window.scrollBy({ top: box.top - header, behavior: "instant" });
    const bottom = this.video?.getBoundingClientRect().bottom || header;
    this.panel.style.top = `${Math.round(Math.min(bottom, innerHeight * 0.35))}px`;
    const vv = window.visualViewport;
    this.panel.style.bottom = vv ? `${Math.max(0, Math.round(innerHeight - vv.height - vv.offsetTop))}px` : "0px";
  }

  /** Колонка чата вровень с колонкой плеера: верх — как у вкладок сезонов, низ — как у карточки (не ниже 420 px). */
  _fitHeight() {
    if (!this.open || this.sheet || !this.card) return;
    const top = (this.card.parentElement || this.card).getBoundingClientRect().top;
    this.panel.style.height = `${Math.max(420, Math.round(this.card.getBoundingClientRect().bottom - top))}px`;
  }

  /** Край списка: раньше — всегда; позже сам, без прокрутки человека, — только если известен конец серии. */
  _edge(dir, auto) {
    const s = this.store;
    if (!s || (dir === "after" && auto && !s.endKnown)) return;
    (dir === "before" ? s.loadBefore() : s.loadAfter()).catch(() => {});
  }

  /** Связь пропала и вернулась: события за обрыв потеряны — окно и число перечитываются. Подписку повторяет сам
   * js/realtime.js; перечитывание — вразброс в пределах 3 с: после перезапуска сервера переподключаются все разом. */
  _link(detail) {
    if (!detail?.up) { this._wasDown = true; return; }
    if (!this._wasDown || !this.store) return;
    this._wasDown = false;
    const store = this.store;
    setTimeout(() => {
      if (store !== this.store || !store.alive) return;
      store.loadCount().catch(() => {});
      if (this.open) this._load(false);
    }, Math.random() * 3000);
  }

  /** Изменение данных — список, полоски, число. */
  _changed(change) {
    const s = this.store;
    if (change.type === "count") {
      const n = s.count || 0;
      for (const b of [this.badge, this.count]) { b.textContent = String(n); b.hidden = !n; }
      return;
    }
    if (change.type === "update") { this.view.patch(change.item); return; }
    if (change.type === "thread") { this._thread(change.rootId); return; }
    if (!this.open) return;
    if (!s.roots.length) { if (change.type === "reset" || change.type === "remove") this.view.state("empty"); return; }
    if (change.type === "before") this.view.keepPlace(() => this.view.render(s.roots, this.t));
    else this.view.render(s.roots, this.t);
    this.strips.setRoots(s.roots);
    if (change.type === "insert" && Math.abs(change.item.moment - this.t) <= 2) this.strips.push(change.item, this.t);
  }

  /** Перерисовать ветку корневого; открытое поле ответа возвращается на место. */
  _thread(rootId) {
    const root = this.store.find(rootId);
    if (!root) return;
    const slot = this.view.thread(root, this.store.threads.get(rootId) || null);
    const reply = this._reply;
    if (!slot || reply?.rootId !== rootId) return;
    this.actions.openReply(slot, root, this.store.find(reply.targetId) || root);
    this.view.reveal(slot);
  }

  /** Свой комментарий: в список и сразу полоской; ответ — в ветку. */
  _posted(c) {
    this._reply = null;
    this.store.add(c);
    if (c.moment !== null && c.moment !== undefined && (!c.thread || c.thread === c.id)) this.strips.push(c, this.t);
  }

  /** Нажатие в списке. */
  async _act(name, id, button, rootId) {
    const s = this.store;
    if (name === "retry") { this._load(); return; }
    const c = s?.find(id);
    if (!c) return;
    const root = rootId ? s.find(rootId) : c;
    try {
      if (name === "seek" && this.canSeek) this.deps.seek(c.moment);
      else if (name === "like") await this.actions.like(c, button);
      else if (name === "menu") this.actions.openMenu(c, button, !!rootId);
      else if (name === "more") await s.loadThread(root.id, true);
      else if (name === "thread") {
        if (s.threads.has(root.id)) { s.dropThread(root.id); this._reply = null; this._thread(root.id); }
        else await s.loadThread(root.id);
      } else if (name === "reply") {
        this._reply = { rootId: root.id, targetId: c.id };
        if (!s.threads.has(root.id)) await s.loadThread(root.id);
        else this._thread(root.id);
      }
    } catch (err) { this.actions.fail(err); }
  }

  /** Страница уходит: слушатели, слой полосок, подписка. */
  destroy() {
    this.close();
    if (this.store) this.store.alive = false;
    this.deps.watch([]);
    document.removeEventListener("av:comment", this._onComment);
    document.removeEventListener("av:link", this._onLink);
    window.removeEventListener("resize", this._onResize);
    window.visualViewport?.removeEventListener("resize", this._onResize);
    this.toggle.remove();
    this.panel.remove();
  }
}
