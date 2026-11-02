/*
 * Чат серии, действия человека: написать на минуте серии, ответить в ветке, «Нравится», меню «Ещё» (удалить свой,
 * пожаловаться), подсказки гостю и сообщения об отказах.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * Минута нового комментария — та, на которой человек начал печатать (пока поле пустое, подсказка показывает текущую).
 * Ответу своей минуты на экране нет, но в запросе уходит минута корневого: по договору ответ на уже удалённый
 * комментарий публикуется корневым и так остаётся в чате на той же минуте. Гостю: «Ответить» нет, вместо поля —
 * «Войдите, чтобы написать», у сердца — «Войдите, чтобы отметить».
 */
import { request } from "./chat-data.js?v=20261102-chat1";
import { actButton, h, icon } from "./chat-dom.js?v=20261102-chat1";
import { authorName, fmtMoment, isRoot } from "./chat-logic.js?v=20261102-chat1";

const REFUSALS = {
  as_link_required: "Войдите заново через соцсеть, чтобы писать в чат.",
  blacklisted: "Этот человек ограничил вам ответы.",
  own_content: "Это ваш комментарий.",
  already_reported: "Вы уже пожаловались на этот комментарий.",
  not_found: "Комментарий уже удалён.",
  empty_text: "Напишите текст комментария.",
  comments_unavailable: "Чат сейчас недоступен. Попробуйте позже.",
  network: "Нет связи с сервером. Проверьте интернет.",
};
const REASONS = [["spam", "Спам"], ["insult", "Оскорбление"], ["adult", "Для взрослых"], ["violence", "Насилие"],
  ["fraud", "Мошенничество"], ["other", "Другое"]];

/** Текст отказа сервера для человека. */
export function refusalText(err) {
  if (err?.status === 429) {
    const wait = err.retryAfter > 0 ? ` через ${err.retryAfter} с` : " чуть позже";
    return `Слишком часто. Попробуйте${wait}.`;
  }
  return REFUSALS[err?.code] || "Не получилось. Попробуйте ещё раз.";
}

/** Поле с кнопкой отправки: Enter отправляет, Shift+Enter — новая строка, высота растёт до 120 px. */
function composer(placeholder, label, onSend) {
  const box = h("div", "chat-compose");
  const area = h("textarea", "ed-textarea");
  Object.assign(area, { rows: 1, maxLength: 5000, placeholder });
  area.setAttribute("aria-label", label);
  const send = actButton("ed-iconbtn ed-iconbtn--filled", "", "Отправить");
  send.append(icon("fas fa-paper-plane"));
  const fit = () => { area.style.height = "auto"; area.style.height = `${Math.min(120, area.scrollHeight)}px`; };
  area.addEventListener("input", fit);
  area.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); onSend(); }
  });
  send.addEventListener("click", onSend);
  box.append(area, send);
  return { box, area, send, fit };
}

/** Действия в чате; deps: backend, store(), object(), time(), guest(), gate(), confirm(opts), dialog(cls, html, opts),
 * posted(c), view(). */
export class ChatActions {
  /** Поле ввода и строка сообщений — в панели чата. */
  constructor(panel, deps) {
    this.deps = deps;
    this.notice = panel.querySelector(".chat-notice");
    this.moment = null;
    this.main = composer("", "Написать в чат серии", () => this.send());
    this.main.area.addEventListener("input", () => {
      if (!this.main.area.value.trim()) this.moment = null;
      else if (this.moment === null) this.moment = Math.floor(deps.time());
    });
    this.guestRow = h("div", "chat-guest");
    const login = actButton("ed-btn ed-btn--secondary ed-btn--sm", "", "", "Войти");
    login.addEventListener("click", () => deps.gate());
    this.guestRow.append(h("span", "", "Войдите, чтобы написать в чат"), login);
    this.menu = null;
    this._liking = new Set();
  }

  /** Поле или подсказка гостю — по тому, вошёл ли человек. */
  mountCompose(slot) {
    slot.replaceChildren(this.deps.guest() ? this.guestRow : this.main.box);
  }

  /** Подсказка «Написать на 09:41…» — минута, которая уйдёт с комментарием. */
  tickPlaceholder(t) {
    const at = this.moment ?? Math.floor(t);
    const text = `Написать на ${fmtMoment(at)}…`;
    if (this.main.area.placeholder !== text) this.main.area.placeholder = text;
  }

  /** Сообщение в панели на 5 секунд. */
  say(text, danger = true) {
    if (!this.notice) return;
    this.notice.className = `ed-note chat-notice${danger ? " ed-note--danger" : ""}`;
    this.notice.replaceChildren(icon(danger ? "fas fa-circle-exclamation" : "fas fa-check"), h("div", "", text));
    this.notice.hidden = false;
    clearTimeout(this._noticeTimer);
    this._noticeTimer = setTimeout(() => { this.notice.hidden = true; }, 5000);
  }

  /** Отказ: не вошёл — окно входа, иначе сообщение. */
  fail(err) {
    if (err?.status === 401 && err.code !== "as_link_required") this.deps.gate();
    else this.say(refusalText(err));
  }

  /** Новый корневой комментарий на минуте, где человек начал печатать. */
  async send() {
    const { area, send } = this.main;
    const text = area.value.trim();
    if (!text || send.disabled) return;
    send.disabled = true;
    try {
      const moment = this.moment ?? Math.floor(this.deps.time());
      const c = await request(this.deps.backend, "/comments", { method: "POST", body: { object: this.deps.object(), text, moment } });
      area.value = "";
      this.moment = null;
      this.main.fit();
      this.deps.posted(c);
    } catch (err) { this.fail(err); } finally { send.disabled = false; }
  }

  /** Поле ответа в ветке: «в ответ Имя ×», ответ уходит с минутой корневого. */
  openReply(slot, root, target) {
    const preview = h("div", "comment-reply-preview");
    const who = h("span", "", "в ответ ");
    who.append(h("b", "", authorName(target.author)));
    const cancel = actButton("ed-iconbtn", "", "Отменить ответ");
    cancel.append(icon("fas fa-xmark"));
    let reply;
    const submit = async () => {
      const text = reply.area.value.trim();
      if (!text || reply.send.disabled) return;
      reply.send.disabled = true;
      try {
        const body = { object: this.deps.object(), text, thread: root.id, reply_to: target.id, moment: root.moment };
        const c = await request(this.deps.backend, "/comments", { method: "POST", body });
        slot.replaceChildren();
        this.deps.posted(c);
        if (isRoot(c)) this.say("Комментарий, на который вы отвечали, удалён — ответ опубликован отдельно.", false);
      } catch (err) { this.fail(err); } finally { reply.send.disabled = false; }
    };
    reply = composer("Ваш ответ…", `Ответ ${authorName(target.author)}`, submit);
    reply.box.className = "chat-replybox";
    cancel.addEventListener("click", () => slot.replaceChildren());
    preview.append(who, cancel);
    slot.replaceChildren(preview, reply.box);
    reply.area.focus({ preventScroll: true });
  }

  /** «Нравится»: сразу на экране, число сверит событие liked; гостю — подсказка на 2,5 секунды. */
  async like(c, button) {
    if (this.deps.guest()) {
      const tip = button.closest(".ed-tip");
      tip?.classList.add("is-open");
      setTimeout(() => tip?.classList.remove("is-open"), 2500);
      return;
    }
    if (this._liking.has(c.id)) return;
    this._liking.add(c.id);
    const store = this.deps.store(), was = !!c.liked, count = c.likes_count || 0;
    store.setLikes(c.id, Math.max(0, count + (was ? -1 : 1)), !was);
    try {
      await request(this.deps.backend, `/comments/${c.id}/like`, { method: was ? "DELETE" : "PUT" });
    } catch (err) {
      store.setLikes(c.id, count, was);
      this.fail(err);
    } finally { this._liking.delete(c.id); }
  }

  /** Меню «Ещё» у комментария: удалить свой, пожаловаться. */
  openMenu(c, button, root) {
    this.closeMenu();
    const menu = h("div", "ed-menu chat-menu");
    menu.setAttribute("role", "menu");
    const item = (text, cls, run) => {
      const it = h("div", `ed-menu__item${cls}`);
      it.setAttribute("role", "menuitem");
      it.tabIndex = 0;
      it.append(icon(cls ? "fas fa-trash" : "fas fa-flag"), document.createTextNode(text));
      const go = () => { button.focus({ preventScroll: true }); this.closeMenu(); run(); };
      it.addEventListener("click", go);
      it.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); } });
      menu.append(it);
    };
    if (c.can?.delete) item("Удалить", " is-danger", () => this.remove(c, root));
    if (c.can?.report) item("Пожаловаться", "", () => this.report(c));
    const r = button.getBoundingClientRect();
    menu.style.top = `${Math.min(r.bottom + 4, innerHeight - 120)}px`;
    menu.style.right = `${Math.max(8, innerWidth - r.right)}px`;
    document.body.append(menu);
    button.setAttribute("aria-expanded", "true");
    const outside = e => { if (!menu.contains(e.target) && e.target !== button) this.closeMenu(); };
    const key = e => { if (e.key === "Escape") { this.closeMenu(); button.focus({ preventScroll: true }); } };
    setTimeout(() => document.addEventListener("pointerdown", outside, true), 0);
    document.addEventListener("keydown", key);
    this.menu = { menu, button, outside, key };
    menu.querySelector(".ed-menu__item")?.focus({ preventScroll: true });
  }

  /** Закрыть открытое меню. */
  closeMenu() {
    if (!this.menu) return;
    const { menu, button, outside, key } = this.menu;
    this.menu = null;
    document.removeEventListener("pointerdown", outside, true);
    document.removeEventListener("keydown", key);
    button.setAttribute("aria-expanded", "false");
    menu.remove();
  }

  /** Удалить свой: корневой — вместе с веткой. */
  remove(c, root) {
    this.deps.confirm({
      title: "Удалить комментарий?",
      body: root ? "Ответ исчезнет из ветки у всех." : "Комментарий и ответы в его ветке исчезнут у всех.",
      countdownSec: 2,
      yesLabel: "Удалить",
      onConfirm: async () => {
        try {
          await request(this.deps.backend, `/comments/${c.id}`, { method: "DELETE" });
          this.deps.store().remove(c.id);
        } catch (err) { this.fail(err); }
      },
    });
  }

  /** Жалоба: причина из списка соцсети и необязательная приписка до 300 знаков. */
  report(c) {
    const options = REASONS.map(([value, text], i) => `<label class="ed-check"><input type="radio" name="chatReason" value="${value}"${i ? "" : " checked"}><span class="ed-check__text">${text}</span></label>`).join("");
    const dialog = this.deps.dialog("chat-report", `
      <h2 class="ed-dialog__title" id="avChatReportTitle">Пожаловаться на комментарий</h2>
      <div class="chat-report__reasons" role="radiogroup" aria-labelledby="avChatReportTitle">${options}</div>
      <textarea class="ed-textarea chat-report__note" maxlength="300" rows="2" placeholder="Что не так (необязательно)" aria-label="Что не так"></textarea>
      <div class="ed-dialog__actions">
        <button type="button" class="ed-btn ed-btn--ghost" data-dialog-close>Отмена</button>
        <button type="button" class="ed-btn ed-btn--primary" data-send>Отправить</button>
      </div>`, { label: "avChatReportTitle" });
    const sendBtn = dialog.el.querySelector("[data-send]");
    sendBtn.addEventListener("click", async () => {
      sendBtn.disabled = true;
      const reason = dialog.el.querySelector("input[name=chatReason]:checked")?.value || "other";
      const comment = dialog.el.querySelector(".chat-report__note").value.trim();
      try {
        await request(this.deps.backend, `/comments/${c.id}/report`, { method: "POST", body: { reason, comment } });
        dialog.close();
        this.say("Жалоба отправлена модераторам.", false);
      } catch (err) {
        dialog.close();
        this.fail(err);
      }
    });
  }
}
