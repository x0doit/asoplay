/*
 * Чат серии, список в панели: комментарий разметкой ленты соцсети (comments__item: аватар 32 px слева, имя, текст
 * без пузыря, низ с временем, «Ответить» и «Нравится»), ветки ответов внутри комментария, отметка «Сейчас», подгрузка
 * при прокрутке, состояния пусто, загрузка и ошибка, размытые спойлеры.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * Текст — только через textContent; упоминания @адрес становятся ссылками разбором, а не вставкой HTML. Нажатия
 * ловит один обработчик списка и отдаёт их делу (deps.act): seek, like, reply, menu, thread, more, expand, retry.
 * Спойлер — комментарий ниже «Сейчас» (у ответа — по минуте корневого): текст размыт, для экранных чтецов вместо
 * него «Спойлер, нажмите, чтобы показать»; нажатие открывает его одного, серия дошла до минуты — размытие снимается.
 */
import { actButton, avatar, h, icon, profileUrl, richText, stateBox } from "./chat-dom.js?v=20261105-replay1";
import { authorName, firstAfter, fmtMoment, fullDate, isSpoiler, plural } from "./chat-logic.js?v=20261105-replay1";

const REPLIES = ["ответ", "ответа", "ответов"];
const NEAR_EDGE = 160;
const SPOILER = "Спойлер, нажмите, чтобы показать";

/** Список комментариев чата: строит, обновляет по месту, держит «Сейчас» и знает, куда прокручен. */
export class ChatView {
  /** list — прокручиваемый блок; deps: site(), ago(unix), guest(), canSeek(), act(name, id, node), edge(dir), follow(on). */
  constructor(list, deps) {
    this.list = list;
    this.deps = deps;
    this.nodes = new Map();
    this.now = h("div", "ed-date chat-now");
    this.now.append(h("span"));
    this.nowAt = -1;
    this.following = true;
    this._ours = false;
    this.hide = true;
    this.t = 0;
    this.revealed = new Set();
    list.addEventListener("click", e => this._click(e));
    list.addEventListener("keydown", e => {
      if ((e.key === "Enter" || e.key === " ") && e.target.matches?.(".comment__text.is-spoiler")) { e.preventDefault(); this._reveal(e.target); }
    });
    list.addEventListener("scroll", () => this._scrolled(), { passive: true });
  }

  /** «Нравится»: сердце и число; гостю — подсказка «Войдите, чтобы отметить». */
  _like(c) {
    const wrap = h("span", "ed-tip comment__like-wrap");
    const b = actButton("comment__like", "like", "Нравится");
    b.setAttribute("aria-pressed", c.liked ? "true" : "false");
    b.append(icon(c.liked ? "fas fa-heart" : "far fa-heart"), h("span", "", c.likes_count ? String(c.likes_count) : ""));
    wrap.append(b);
    const tip = this.deps.guest() && h("span", "ed-tip__bubble", "Войдите, чтобы отметить");
    if (tip) { tip.setAttribute("role", "tooltip"); wrap.append(tip); }
    return wrap;
  }

  /** Минута серии «▶ 04:35»: кнопка перемотки, если плеер умеет, иначе неактивная. */
  _moment(c) {
    const b = actButton("comment__moment", "seek", `Перемотать на ${fmtMoment(c.moment)}`);
    b.append(icon("fas fa-play"), document.createTextNode(fmtMoment(c.moment)));
    b.disabled = !this.deps.canSeek();
    return b;
  }

  /** Размыть текст комментария (спойлер) или вернуть: размытый — кнопка с подписью для экранных чтецов. */
  _blur(text, on) {
    if (text.classList.contains("is-spoiler") === on) return;
    text.classList.toggle("is-spoiler", on);
    for (const a of text.querySelectorAll("a")) a.tabIndex = on ? -1 : 0;
    if (on) { text.setAttribute("role", "button"); text.tabIndex = 0; text.setAttribute("aria-label", SPOILER); }
    else { text.removeAttribute("role"); text.removeAttribute("tabindex"); text.removeAttribute("aria-label"); }
  }

  /** Спойлеры по времени серии t: ниже «Сейчас» размыто, выше — нет; открытые человеком остаются открытыми. */
  spoilers(t = this.t) {
    this.t = t;
    for (const item of this.list.querySelectorAll(".comments__item")) {
      const text = item.querySelector(":scope > .comment__info > .comment__text");
      if (text) this._blur(text, isSpoiler(item.dataset.moment, t, this.hide, this.revealed.has(Number(item.dataset.id))));
    }
  }

  /** Переключатель «Спойлеры»: hide — размывать ещё не просмотренное. */
  setHide(hide) {
    this.hide = hide;
    this.spoilers();
  }

  /** Человек открыл один спойлер. */
  _reveal(text) {
    this.revealed.add(Number(text.closest(".comments__item").dataset.id));
    this._blur(text, false);
  }

  /** Комментарий: корневой с минутой и веткой или ответ (аватар 24 px, «в ответ Имя», минута корневого). */
  comment(c, reply = false, rootMoment = null) {
    const item = h("div", "comments__item");
    item.dataset.id = String(c.id);
    item.dataset.moment = String((reply ? rootMoment : c.moment) ?? "");
    const info = h("div", "comment__info");
    const head = h("div", "comment__head");
    const name = h("a", "comment__autor", authorName(c.author));
    Object.assign(name, { href: profileUrl(this.deps.site(), c.author), target: "_blank", rel: "noopener" });
    head.append(name);
    if (!reply && c.moment !== null && c.moment !== undefined) head.append(this._moment(c));
    if (c.can?.delete || c.can?.report) {
      const more = actButton("ed-iconbtn comment__menu-btn", "menu", "Ещё");
      more.setAttribute("aria-haspopup", "menu");
      more.setAttribute("aria-expanded", "false");
      more.append(icon("fas fa-ellipsis-h"));
      head.append(more);
    }
    info.append(head);
    if (reply && c.reply_to?.author && c.reply_to.id !== c.thread) {
      const to = h("div", "comment__replyto", "в ответ ");
      to.append(h("span", "", authorName(c.reply_to.author)));
      info.append(to);
    }
    const text = richText(this.deps.site(), c.text);
    info.append(text);
    this._blur(text, isSpoiler(item.dataset.moment, this.t, this.hide, this.revealed.has(c.id)));
    if (String(c.text || "").length > 140 || (String(c.text).match(/\n/g) || []).length >= 4) {
      text.classList.add("is-collapsed");
      const expand = actButton("comment__expand", "expand", "", "Показать полностью");
      expand.hidden = true;
      info.append(expand);
      requestAnimationFrame(() => { expand.hidden = text.scrollHeight <= text.clientHeight + 2; });
    }
    const bottom = h("div", "comment__bottom");
    const date = h("time", "comment__date", this.deps.ago(c.date));
    date.dataset.ts = String(c.date || 0);
    date.title = fullDate(c.date);
    bottom.append(date);
    if (!this.deps.guest() && c.can?.reply !== false) bottom.append(actButton("comment__reply", "reply", "", "Ответить"));
    bottom.append(this._like(c));
    info.append(bottom);
    if (!reply) {
      const box = h("div", "comment__thread");
      this._fill(box, c, null);
      info.append(box);
    }
    item.append(avatar(this.deps.site(), c.author, reply), info);
    return item;
  }

  /** Ветка корневого по месту в списке; slot — место поля ответа (у свёрнутой ветки — null). */
  thread(root, thread) {
    const item = this.nodes.get(root.id) || this.list.querySelector(`[data-id="${root.id}"]`);
    const box = item?.querySelector(":scope > .comment__info > .comment__thread");
    return box ? this._fill(box, root, thread) : null;
  }

  /** Содержимое ветки: «N ответов» / «Скрыть ответы», ответы, «Ещё ответы» и место поля ответа. */
  _fill(box, root, thread) {
    box.replaceChildren();
    const open = !!thread;
    if (root.replies_count > 0 || open) {
      const toggle = actButton("comments__more", "thread");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
      toggle.append(icon(open ? "fas fa-chevron-up" : "fas fa-chevron-down"),
        document.createTextNode(open ? "Скрыть ответы" : `${root.replies_count} ${plural(root.replies_count, REPLIES)}`));
      if (root.replies_count > 0) box.append(toggle);
    }
    if (!open) return null;
    const replies = h("div", "comment__replies");
    for (const c of thread.items) replies.append(this.comment(c, true, root.moment));
    if (thread.cursor) replies.append(actButton("comments__more comments__more--replies", "more", "", "Ещё ответы"));
    const slot = h("div", "chat-replyslot");
    replies.append(slot);
    box.append(replies);
    return slot;
  }

  /** Обновить «Нравится» и меню комментария по месту. */
  patch(c) {
    for (const item of this.list.querySelectorAll(`.comments__item[data-id="${c.id}"]`)) {
      const wrap = item.querySelector(":scope > .comment__info > .comment__bottom > .comment__like-wrap");
      if (wrap) wrap.replaceWith(this._like(c));
    }
  }

  /** Перерисовать список по корневым: свои узлы остаются (открытые ветки и развёрнутый текст не сбрасываются). */
  render(roots, t) {
    const keep = new Set(roots.map(c => c.id));
    for (const [id, node] of this.nodes) if (!keep.has(id)) { node.remove(); this.nodes.delete(id); }
    for (const node of [...this.list.children]) if (!node.classList.contains("comments__item")) node.remove();
    let before = this.list.firstChild;
    for (const c of roots) {
      let node = this.nodes.get(c.id);
      if (!node) { node = this.comment(c); this.nodes.set(c.id, node); }
      if (node !== before) this.list.insertBefore(node, before);
      before = node.nextSibling;
    }
    this.nowAt = -1;
    this.placeNow(roots, t);
    requestAnimationFrame(() => this.checkEdges(true));
  }

  /** Пусто, загрузка или ошибка вместо списка. */
  state(kind) {
    this._setFollow(true);
    this.nodes.clear();
    this.nowAt = -1;
    this.list.replaceChildren(stateBox(kind));
  }

  /** Отметка «Сейчас m:ss» перед первым комментарием позже t; в режиме следования — в нижней трети списка. */
  placeNow(roots, t) {
    if (!roots.length) return;
    const at = firstAfter(roots, t);
    this.now.firstChild.textContent = `Сейчас ${fmtMoment(t)}`;
    if (at !== this.nowAt || !this.now.isConnected) {
      this.nowAt = at;
      this.list.insertBefore(this.now, at < roots.length ? this.nodes.get(roots[at].id) : null);
      this.spoilers(t);
      if (this.following) this.toNow();
    }
  }

  /** Своя прокрутка списка — её обработчик прокрутки не принимает за человека. */
  _scroll(top) {
    this._ours = true;
    this.list.scrollTop = top;
    requestAnimationFrame(() => { this._ours = false; });
  }

  /** Прокрутить к «Сейчас» (нижняя треть списка, верх — по границе комментария) и снова следовать за серией. */
  toNow() {
    if (!this.now.isConnected) return;
    const l = this.list, top = this.now.offsetTop - l.clientHeight * 0.7;
    const cut = [...l.children].find(n => n.offsetTop <= top && n.offsetTop + n.offsetHeight > top);
    const fits = cut && this.now.offsetTop + this.now.offsetHeight - cut.offsetTop <= l.clientHeight;
    const edge = !cut ? top : fits ? cut.offsetTop : cut.offsetTop + cut.offsetHeight;
    this._scroll(Math.max(0, edge));
    this._setFollow(true);
  }

  /** Показать узел внутри списка (поле ответа в ветке), не двигая страницу; низ — выше «К текущей минуте». */
  reveal(node) {
    const l = this.list, top = node.getBoundingClientRect().top - l.getBoundingClientRect().top + l.scrollTop;
    const room = parseFloat(getComputedStyle(l).paddingBottom) || 0;
    if (top + node.offsetHeight > l.scrollTop + l.clientHeight - room) this._scroll(top + node.offsetHeight - l.clientHeight + room + 8);
    else if (top < l.scrollTop) this._scroll(top - 8);
  }

  /** Держать место при добавлении сверху: прокрутка сдвигается на выросшую высоту. */
  keepPlace(change) {
    const before = this.list.scrollHeight;
    change();
    this._scroll(this.list.scrollTop + this.list.scrollHeight - before);
  }

  /** Перемотка доступна или нет — у всех минут сразу. */
  setSeek(can) {
    for (const b of this.list.querySelectorAll(".comment__moment")) b.disabled = !can;
  }

  /** «5 минут назад» стареет: раз в минуту время публикации пересчитывается. */
  refreshDates() {
    for (const t of this.list.querySelectorAll("time.comment__date")) t.textContent = this.deps.ago(Number(t.dataset.ts));
  }

  /** Следует ли список за серией; изменение — deps.follow (кнопка «К текущей минуте»). */
  _setFollow(on) {
    if (on !== this.following) { this.following = on; this.deps.follow(on); }
  }

  /** Край списка рядом — подгрузить; auto — после отрисовки, а не от прокрутки человеком. */
  checkEdges(auto = false) {
    const l = this.list;
    if (!l.isConnected || !l.clientHeight) return;
    if (l.scrollTop < NEAR_EDGE) this.deps.edge("before", auto);
    if (l.scrollHeight - l.scrollTop - l.clientHeight < NEAR_EDGE) this.deps.edge("after", auto);
  }

  /** Прокрутка человеком: «Сейчас» ушла из виду — не следовать; у края — подгрузить. */
  _scrolled() {
    const l = this.list;
    if (!this._ours && this.now.isConnected) {
      const top = this.now.offsetTop - l.scrollTop;
      this._setFollow(top >= 0 && top <= l.clientHeight - this.now.offsetHeight);
    }
    this.checkEdges();
  }

  /** Нажатие в списке — действию по data-act с id комментария. */
  _click(e) {
    const b = e.target.closest("[data-act]");
    const spoiler = e.target.closest(".comment__text.is-spoiler");
    if (spoiler && this.list.contains(spoiler)) { e.preventDefault(); this._reveal(spoiler); return; }
    if (!b || !this.list.contains(b)) return;
    const item = b.closest(".comments__item");
    if (b.dataset.act === "expand") {
      const text = item.querySelector(":scope > .comment__info > .comment__text");
      text.classList.toggle("is-collapsed");
      b.textContent = text.classList.contains("is-collapsed") ? "Показать полностью" : "Свернуть";
      return;
    }
    const root = b.closest(".comment__thread")?.closest(".comments__item") || null;
    this.deps.act(b.dataset.act, item ? Number(item.dataset.id) : 0, b, root ? Number(root.dataset.id) : 0);
  }
}
