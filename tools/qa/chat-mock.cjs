/*
 * QA чата серии: подменённые ответы /comments своего сервера (core/api/sections/comments.py) — список по минутам
 * серии с окнами from–to и курсором, ответы веток, один комментарий, написать, удалить, «Нравится», жалоба.
 * Соцсеть не трогается: всё живёт в памяти сценария, вызовы копятся в db.calls.
 *
 * Автор: Чепела Даниил Максимович.
 *
 * Серия — «Фрирен», 1-я (asoplay:episode:52991.1). Минуты как на макете v3: обычные комментарии вокруг 09:41,
 * плотная минута 12:36–12:40 (самые отмеченные «Нравится» — Рин, Кэндзи, Сора, Акира), длинные тексты, ветки.
 */
const OBJECT = "asoplay:episode:52991.1";
const now = Math.floor(Date.now() / 1000);
const who = (id, first, last = "") => ({ id, alias: `qa_${id}`, first_name: first, last_name: last, avatar: null });
const LONG = "Лучшая сцена серии, пересматриваю второй раз. Как они показали воспоминания Химмеля — через мелочи, "
  + "через кольцо, через цветы у дороги. И всё без лишних слов: только музыка и взгляд Фрирен. Вот это и есть "
  + "режиссура, за которую я люблю это аниме.";
const KENJI = "Я ждал эту сцену с первой серии, и она лучше, чем я представлял. Анимация, музыка, тишина перед ударом — "
  + "всё на месте. Студия выложилась по полной, это видно в каждом кадре.";
// [id, автор, минута в секундах, минут назад, текст, «Нравится», ответов]
const ROOTS = [
  [1, who(9101, "Сакура"), 72, 120, "Музыка в начале — мурашки", 4, 0],
  [2, who(9102, "Наруто"), 275, 60, "Вот это поворот!", 12, 2],
  [3, who(9103, "Хината", "с очень длинным именем, которое не помещается"), 362, 40, "Фрирен такая милая", 1, 0],
  [5, who(9105, "Саске"), 577, 10, LONG, 27, 0],
  [6, who(9106, "Мику"), 579, 6, "Химмель лучший, без вариантов", 8, 0],
  [7, who(9107, "Итачи"), 580, 2, "Этот взгляд Фрирен в конце сцены", 0, 0],
  [8, who(9108, "Ино"), 605, 1, "Сейчас будет моя любимая часть", 2, 1],
  [9, who(9109, "Лина"), 740, 240, "Готовьтесь, сейчас начнётся", 9, 0],
  [10, who(9110, "Рин"), 756, 180, "ВОТ ОНО", 54, 0],
  [11, who(9111, "Кэндзи"), 757, 130, KENJI, 41, 0],
  [12, who(9112, "Юи"), 757, 125, "мурашки по коже", 2, 0],
  [13, who(9113, "Сора"), 758, 70, "Штарк красавчик", 33, 0],
  [14, who(9114, "Мэй"), 759, 50, "кто тоже на паузе?", 0, 0],
  [15, who(9115, "Акира"), 760, 20, "Ферн просто огонь в этой сцене", 20, 0],
  [16, who(9116, "Нана"), 760, 5, "+++", 1, 0],
  [17, who(9117, "Кира"), 782, 60, "Эта музыка теперь у меня в плейлисте", 7, 0],
  [18, who(9118, "Гаара"), 1263, 1440, "Финал серии — шедевр", 15, 0],
];
const REPLIES = {
  2: [[21, who(9101, "Сакура"), 50, "Согласна, никто не ожидал", 2, null],
    [22, who(9103, "Хината", "с очень длинным именем, которое не помещается"), 45, "А я догадывалась ещё с первой серии", 0, 21]],
  8: [[81, who(9105, "Саске"), 1, "Это где они у моря?", 0, null]],
};

/** База чата: корневые и ответы; me — кто вошёл (USER сценария) или null для гостя. */
function chatDb({ me = null, empty = false } = {}) {
  const can = c => me ? { delete: c.author.id === me.id, reply: true, report: c.author.id !== me.id }
    : { delete: false, reply: false, report: false };
  const make = (id, author, moment, ago, text, likes, replies, thread = null, replyTo = null) => ({
    id, object: OBJECT, author, text, date: now - ago * 60, thread, moment, reply_to: replyTo,
    replies_count: replies, likes_count: likes, liked: false });
  const db = { calls: [], posted: [], fail: false, me, nextId: 500 };
  db.roots = empty ? [] : ROOTS.map(r => make(...r));
  db.replies = {};
  if (!empty) {
    for (const [rootId, list] of Object.entries(REPLIES)) {
      db.replies[rootId] = list.map(([id, author, ago, text, likes, toId]) => {
        const to = toId ? list.find(x => x[0] === toId) : null;
        return make(id, author, null, ago, text, likes, 0, Number(rootId), to ? { id: to[0], author: to[1] } : { id: Number(rootId), author: db.roots.find(r => r.id === Number(rootId)).author });
      });
    }
    if (me) db.roots.find(r => r.id === 5).liked = true;
  }
  db.all = () => [...db.roots, ...Object.values(db.replies).flat()];
  db.find = id => db.all().find(c => c.id === id) || null;
  db.view = c => ({ ...c, can: can(c) });
  return db;
}

/** Обработчик маршрута /comments для page.route(CHAT_ROUTE, …). */
function chatRoute(db) {
  return async route => {
    const req = route.request();
    const url = new URL(req.url());
    const method = req.method();
    const json = (body, status = 200, headers = {}) => route.fulfill({ status, contentType: "application/json", headers, body: JSON.stringify(body) });
    db.calls.push(`${method} ${url.pathname}${url.search}`);
    if (db.fail) return json({ detail: "comments_unavailable" }, 503, db.retryAfter ? { "Retry-After": String(db.retryAfter) } : {});
    const id = Number(url.pathname.split("/")[2] || 0);
    const count = db.all().length;
    if (method === "GET" && url.pathname === "/comments") {
      const thread = Number(url.searchParams.get("thread") || 0);
      if (thread) {
        const items = (db.replies[thread] || []).map(db.view);
        return json({ items, next_cursor: null, total: items.length, comments_count: count });
      }
      const from = Number(url.searchParams.get("from") ?? 0), to = Number(url.searchParams.get("to") ?? 86400);
      const limit = Number(url.searchParams.get("limit") || 20), at = Number(url.searchParams.get("cursor") || 0);
      const inside = db.roots.filter(c => c.moment >= from && c.moment <= to).sort((a, b) => (a.moment - b.moment) || (a.id - b.id));
      return json({ items: inside.slice(at, at + limit).map(db.view), next_cursor: at + limit < inside.length ? String(at + limit) : null,
        total: inside.length, comments_count: count });
    }
    if (method === "GET" && url.pathname.startsWith("/comments/item/")) {
      const c = db.find(Number(url.pathname.split("/").pop()));
      return c ? json(db.view(c)) : json({ detail: "not_found" }, 404);
    }
    if (!db.me) return json({ detail: "Not authenticated" }, 401);
    if (method === "POST" && url.pathname === "/comments") {
      const body = JSON.parse(req.postData() || "{}");
      db.posted.push(body);
      const root = body.thread ? db.find(body.thread) : null;
      const target = body.reply_to ? db.find(body.reply_to) : null;
      const c = { id: ++db.nextId, object: body.object, author: { id: db.me.id, alias: db.me.alias, first_name: "Тестовый", last_name: "зритель", avatar: null },
        text: body.text, date: Math.floor(Date.now() / 1000), thread: root ? root.id : null, moment: root ? null : body.moment ?? null,
        reply_to: target ? { id: target.id, author: target.author } : null, replies_count: 0, likes_count: 0, liked: false };
      if (root) { (db.replies[root.id] = db.replies[root.id] || []).push(c); root.replies_count += 1; } else db.roots.push(c);
      return json(db.view(c));
    }
    if (method === "DELETE" && /^\/comments\/\d+$/.test(url.pathname)) {
      db.roots = db.roots.filter(c => c.id !== id);
      for (const list of Object.values(db.replies)) { const i = list.findIndex(c => c.id === id); if (i >= 0) list.splice(i, 1); }
      return json({ ok: true });
    }
    if (url.pathname.endsWith("/like")) {
      const c = db.find(id);
      if (!c) return json({ detail: "not_found" }, 404);
      const liked = method === "PUT";
      if (liked !== c.liked) c.likes_count += liked ? 1 : -1;
      c.liked = liked;
      return json({ liked, likes_count: c.likes_count });
    }
    if (url.pathname.endsWith("/report")) return json({ ok: true });
    return json({ detail: "not_found" }, 404);
  };
}

// Только API своего сервера: стили чата лежат в /assets/eddas/css/chat/ и мимо подмены
const CHAT_ROUTE = /^https?:\/\/[^/]+\/comments(\?|\/|$)/;

module.exports = { OBJECT, chatDb, chatRoute, CHAT_ROUTE };
