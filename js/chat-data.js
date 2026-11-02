/*
 * Чат серии, данные: запросы к /comments своего сервера (он ходит в соцсеть, core/api/sections/comments.py),
 * корневые комментарии окнами по минуте серии, ответы веток, события живого соединения и число комментариев.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * Загружено всегда сплошным куском минут [lo, covered]: вперёд — страницами курсором, назад — окном целиком
 * (10 минут, а если там больше 1000 комментариев — минута). Каждое изменение — вызов onChange({type, …}),
 * по нему список и полоски обновляются: reset, before, after, insert, remove, update, thread, count.
 * После отказа окна не просятся до конца паузы (Backoff: 5 с, вдвое до 60 с, Retry-After важнее): соцсеть легла —
 * сотни открытых чатов не долбят сервер на каждом такте видео.
 */
import { Backoff, WINDOW, insertSorted, isRoot, windowAfter, windowAround, windowBefore } from "./chat-logic.js?v=20261102-chat1";

/** Отказ сервера: status, код из detail (as_link_required, rate_limited, own_content…), Retry-After. */
export class ChatApiError extends Error {
  /** Ошибка запроса к /comments. */
  constructor(status, code, retryAfter = 0) {
    super(`${status} ${code}`);
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

/** Запрос к своему серверу: JSON, cookie сессии; отказ — ChatApiError, обрыв сети — status 0. */
export async function request(backend, path, { method = "GET", body } = {}) {
  const init = { method, credentials: "include", headers: {} };
  if (body !== undefined) {
    init.headers["content-type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let r;
  try { r = await fetch(backend + path, init); } catch (_) { throw new ChatApiError(0, "network"); }
  if (!r.ok) {
    let code = "";
    try { const j = await r.json(); code = typeof j?.detail === "string" ? j.detail : ""; } catch (_) {}
    throw new ChatApiError(r.status, code, Number(r.headers.get("retry-after")) || 0);
  }
  return (r.headers.get("content-type") || "").includes("json") ? r.json() : null;
}

/** Комментарии одной серии: корневые с минутой окнами, ветки, события, число у значка. */
export class ChatStore {
  /** Хранилище для объекта asoplay:episode:{id}.{n}; onChange — что изменилось. */
  constructor(backend, object, onChange) {
    this.backend = backend;
    this.object = object;
    this.onChange = onChange;
    this.count = null;
    this.threads = new Map();
    this.alive = true;
    this.end = WINDOW.end;
    this.pauses = { around: new Backoff(), after: new Backoff(), before: new Backoff() };
    this._clear();
  }

  /** Запрос окна под паузой: успех сбрасывает её, отказ удлиняет (Retry-After из ответа важнее). */
  async _guarded(kind, run) {
    try {
      const out = await run();
      this.pauses[kind].ok();
      return out;
    } catch (err) {
      this.pauses[kind].fail(Date.now(), err?.retryAfter || 0);
      throw err;
    }
  }

  /** Конец серии (длительность видео): окна вперёд дальше него не просятся. */
  setEnd(duration) {
    const end = Math.min(WINDOW.end, Math.ceil(Number(duration) || 0));
    if (end <= 0 || end === this.end) return;
    this.end = end;
    if (this.covered !== null && !this.cursor && this.covered >= end) this.atEnd = true;
  }

  /** Известен ли конец серии — тогда список сам догружает следующее окно, пока короткий. */
  get endKnown() {
    return this.end < WINDOW.end;
  }

  /** Загруженного нет. */
  _clear() {
    this.roots = [];
    this.byId = new Map();
    this.lo = null;
    this.covered = null;
    this.windowHi = null;
    this.pageFrom = null;
    this.cursor = null;
    this.atStart = false;
    this.atEnd = false;
    this.threads.clear();
  }

  /** Строка запроса списка этой серии. */
  _query(params) {
    const q = new URLSearchParams({ object: this.object });
    for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined) q.set(k, String(v));
    return `/comments?${q}`;
  }

  /** Одна страница чата (sort=moment) на отрезке минут. */
  _page(from, to, cursor = null) {
    return request(this.backend, this._query({ sort: "moment", from, to, limit: WINDOW.page, cursor }));
  }

  /** Взять корневые с минутой; вернуть новые. */
  _take(items) {
    const fresh = [];
    for (const c of Array.isArray(items) ? items : []) {
      if (!c || !Number.isInteger(c.id) || c.moment === null || c.moment === undefined || !isRoot(c)) continue;
      if (insertSorted(this.roots, c)) { this.byId.set(c.id, c); fresh.push(c); }
    }
    return fresh;
  }

  /** Число комментариев серии у значка — один лёгкий запрос. */
  async loadCount() {
    const data = await request(this.backend, this._query({ sort: "moment", limit: 1 }));
    if (!this.alive) return;
    this.count = Number.isInteger(data?.comments_count) ? data.comments_count : null;
    this.onChange({ type: "count" });
  }

  /** Первое окно вокруг минуты t (и после обрыва связи — заново); force — по просьбе человека, мимо паузы. */
  async loadAround(t, force = false) {
    if (force) this.pauses.around.ok();
    else if (!this.pauses.around.ready(Date.now())) throw new ChatApiError(0, "paused");
    const [from, to] = windowAround(t);
    const data = await this._guarded("around", () => this._page(from, to));
    if (!this.alive) return;
    this._clear();
    this._take(data?.items);
    if (Number.isInteger(data?.comments_count)) this.count = data.comments_count;
    this.lo = from;
    this.pageFrom = from;
    this.windowHi = to;
    this._afterPage(data);
    this.atStart = from === 0;
    this.onChange({ type: "reset" });
  }

  /** После страницы вперёд: докуда загружено сплошь и есть ли продолжение. */
  _afterPage(data) {
    this.cursor = data?.next_cursor || null;
    const last = this.roots.length ? this.roots[this.roots.length - 1].moment : this.lo;
    this.covered = this.cursor ? Math.max(this.lo, last - 1) : this.windowHi;
    this.atEnd = !this.cursor && this.windowHi >= this.end;
  }

  /** Дальше по серии: следующая страница окна или следующее окно. */
  async loadAfter() {
    if (this.lo === null || this.atEnd || this._busyAfter || !this.pauses.after.ready(Date.now())) return;
    this._busyAfter = true;
    try {
      let data;
      if (this.cursor) data = await this._guarded("after", () => this._page(this.pageFrom, this.windowHi, this.cursor));
      else {
        if (this.covered >= this.end) { this.atEnd = true; return; }
        const [from, to] = windowAfter(this.covered);
        data = await this._guarded("after", () => this._page(from, Math.min(to, this.end)));
        [this.pageFrom, this.windowHi] = [from, Math.min(to, this.end)];
      }
      if (!this.alive) return;
      const fresh = this._take(data?.items);
      this._afterPage(data);
      this.onChange({ type: "after", items: fresh });
    } finally { this._busyAfter = false; }
  }

  /** Раньше по серии: окно целиком; больше 1000 комментариев за 10 минут — только последняя минута окна. */
  async loadBefore() {
    if (this.lo === null || this.atStart || this._busyBefore || !this.pauses.before.ready(Date.now())) return;
    this._busyBefore = true;
    try {
      for (const wide of [true, false]) {
        const [from, to] = windowBefore(this.lo, wide);
        const items = [];
        let cursor = null, pages = 0;
        do {
          const data = await this._guarded("before", () => this._page(from, to, cursor));
          if (!this.alive) return;
          items.push(...(data?.items || []));
          cursor = data?.next_cursor || null;
          pages += 1;
        } while (cursor && pages < WINDOW.maxPages);
        if (cursor && wide) continue;
        const fresh = this._take(items);
        this.lo = from;
        this.atStart = from === 0;
        this.onChange({ type: "before", items: fresh });
        return;
      }
    } finally { this._busyBefore = false; }
  }

  /** Ответы ветки по 20, старые сверху; more — следующая страница. */
  async loadThread(rootId, more = false) {
    const prev = this.threads.get(rootId);
    if (more && !prev?.cursor) return;
    const data = await request(this.backend, this._query({ thread: rootId, sort: "old", limit: 20,
      cursor: more ? prev.cursor : null }));
    if (!this.alive) return;
    const thread = more ? prev : { items: [], cursor: null };
    for (const c of data?.items || []) if (!thread.items.some(x => x.id === c.id)) thread.items.push(c);
    thread.cursor = data?.next_cursor || null;
    this.threads.set(rootId, thread);
    this.onChange({ type: "thread", rootId });
  }

  /** Свёрнутая ветка больше не держится в памяти. */
  dropThread(rootId) {
    this.threads.delete(rootId);
  }

  /** Комментарий по id среди корневых и открытых веток. */
  find(id) {
    if (this.byId.has(id)) return this.byId.get(id);
    for (const t of this.threads.values()) {
      const c = t.items.find(x => x.id === id);
      if (c) return c;
    }
    return null;
  }

  /** Свой новый комментарий или ответ из ответа сервера (и из события added). */
  add(c) {
    if (!c || !Number.isInteger(c.id) || this.find(c.id)) return;
    if (isRoot(c)) {
      const inside = c.moment !== null && c.moment !== undefined && this.lo !== null
        && c.moment >= this.lo && c.moment <= this.covered;
      if (inside && this._take([c]).length) this.onChange({ type: "insert", item: c });
    } else {
      const root = this.byId.get(c.thread);
      if (root) root.replies_count = (root.replies_count || 0) + 1;
      const thread = this.threads.get(c.thread);
      if (thread && !thread.items.some(x => x.id === c.id)) thread.items.push(c);
      if (root) this.onChange({ type: "thread", rootId: root.id });
    }
    if (this.count !== null) { this.count += 1; this.onChange({ type: "count" }); }
  }

  /** Удалённый комментарий: корневой уходит с веткой, ответ — из ветки; thread — корневой ответа (из события). */
  remove(id, thread = null) {
    const root = this.byId.get(id);
    if (root) {
      this.roots.splice(this.roots.indexOf(root), 1);
      this.byId.delete(id);
      this.threads.delete(id);
      if (this.count !== null) this.count = Math.max(0, this.count - 1 - (root.replies_count || 0));
      this.onChange({ type: "remove", id });
    } else {
      for (const [rootId, t] of this.threads) {
        const i = t.items.findIndex(x => x.id === id);
        if (i < 0) continue;
        t.items.splice(i, 1);
        const parent = this.byId.get(rootId);
        if (parent) parent.replies_count = Math.max(0, (parent.replies_count || 0) - 1);
        this.onChange({ type: "thread", rootId });
        thread = null;
        break;
      }
      const parent = thread && thread !== id ? this.byId.get(thread) : null;
      if (parent) {  // ответ в свёрнутой ветке: её нет в памяти, но число у корневого — меньше
        parent.replies_count = Math.max(0, (parent.replies_count || 0) - 1);
        this.onChange({ type: "thread", rootId: parent.id });
      }
      if (this.count !== null) this.count = Math.max(0, this.count - 1);
    }
    this.onChange({ type: "count" });
  }

  /** «Нравится» из события или своего нажатия. */
  setLikes(id, count, liked) {
    const c = this.find(id);
    if (!c) return;
    if (Number.isInteger(count)) c.likes_count = count;
    if (typeof liked === "boolean") c.liked = liked;
    this.onChange({ type: "update", item: c });
  }

  /** Кадр «comment» живого соединения; новый комментарий читается с сервера. */
  async event(frame) {
    if (!Number.isInteger(frame.comment_id) || frame.comment_id <= 0) return;
    if (frame.action === "liked") { this.setLikes(frame.comment_id, frame.likes_count); return; }
    if (frame.action === "deleted") { this.remove(frame.comment_id, Number.isInteger(frame.thread) ? frame.thread : null); return; }
    if (frame.action !== "added" || this.find(frame.comment_id)) return;
    try {
      const c = await request(this.backend, `/comments/item/${frame.comment_id}`);
      if (this.alive) this.add(c);
    } catch (_) { /* удалён, пока читали, или нет связи — после переподключения окно перечитается */ }
  }
}
