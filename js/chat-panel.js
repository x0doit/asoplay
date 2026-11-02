/*
 * Чат серии, разметка на странице тайтла: значок у «Эпизод N» с числом и панель чата рядом с «Эпизодами» — голова
 * с «Чат серии», числом, переключателем «Спойлеры» и крестиком листа, список с «К текущей минуте» поверх его низа,
 * строка сообщений и место поля ввода.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * Переключатель «Спойлеры» включён — комментарии к ещё не просмотренной части серии видны без размытия; выбор
 * запоминается в этом браузере (localStorage, удобство зрителя), без него — размыты.
 */
import { actButton, h, icon } from "./chat-dom.js?v=20261102-chat1";

const SPOILERS_KEY = "av_chat_spoilers";

/** Показывать ли спойлеры без размытия (выбор зрителя в этом браузере). */
export function spoilersShown() {
  try { return localStorage.getItem(SPOILERS_KEY) === "show"; } catch (_) { return false; }
}

/** Значок и панель; on: toggle(), close(), toNow(), spoilers(shown). Значок встаёт после #avEpInfo, панель — в sidebar. */
export function buildChat(sidebar, on) {
  const toggle = h("span", "sp-chat");
  const button = actButton("ed-iconbtn sp-chat__btn", "", "Чат серии");
  button.setAttribute("aria-pressed", "false");
  button.append(icon("fas fa-comments"));
  const badge = h("span", "ed-badge ed-badge--muted sp-chat__count");
  badge.hidden = true;
  toggle.append(button, badge);
  button.addEventListener("click", () => on.toggle());
  document.getElementById("avEpInfo")?.after(toggle);

  const panel = h("section", "ed-panel chat-panel");
  panel.setAttribute("aria-label", "Чат серии");
  panel.hidden = true;
  const grip = h("div", "app-sheet__grip chat-grip");
  grip.setAttribute("aria-hidden", "true");
  const head = h("div", "ed-panel__head");
  const count = h("span", "ed-badge ed-badge--muted chat-count");
  count.hidden = true;
  const spoilers = h("label", "ed-switch chat-spoilers");
  spoilers.title = "Показывать комментарии к ещё не просмотренной части серии без размытия";
  const input = h("input");
  Object.assign(input, { type: "checkbox", checked: spoilersShown() });
  input.setAttribute("role", "switch");
  input.addEventListener("change", () => {
    try { localStorage.setItem(SPOILERS_KEY, input.checked ? "show" : "hide"); } catch (_) {}
    on.spoilers(input.checked);
  });
  spoilers.append(h("span", "", "Спойлеры"), input);
  const close = actButton("ed-iconbtn chat-close", "", "Закрыть чат");
  close.append(icon("fas fa-xmark"));
  close.addEventListener("click", () => on.close());
  head.append(h("span", "chat-title", "Чат серии"), count, spoilers, close);

  const body = h("div", "ed-panel__body");
  const list = h("div", "chat-list");
  list.setAttribute("aria-label", "Комментарии серии по минутам");
  const toNow = actButton("ed-btn ed-btn--secondary ed-btn--sm chat-tonow", "", "", "К текущей минуте");
  toNow.prepend(icon("fas fa-arrow-down"));
  toNow.hidden = true;
  toNow.addEventListener("click", () => on.toNow());
  const notice = h("div", "ed-note chat-notice");
  notice.setAttribute("role", "status");
  notice.hidden = true;
  const composeSlot = h("div", "chat-compose-slot");
  const scroll = h("div", "chat-scroll");
  scroll.append(list, toNow);
  body.append(scroll, notice, composeSlot);
  panel.append(grip, head, body);
  sidebar?.append(panel);
  return { toggle, button, badge, panel, count, list, toNow, composeSlot };
}
