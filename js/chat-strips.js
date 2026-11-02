/*
 * Чат серии, полоски поверх видео: небольшие полоски с текстом корневых комментариев справа в кадре, пока включён
 * чат и идёт видео. Когда и какие — решает StripScheduler (js/chat-logic.js), здесь — только разметка и дорожки.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * Полоски живут в нашей странице поверх iframe плеера: собственный полноэкранный режим чужого плеера (Kodik) их не
 * показывает, наш кадр раскрывает на весь экран контейнер .sp-video вместе с ними. Для чтения с экрана полоски
 * скрыты — те же комментарии есть в списке.
 */
import { h } from "./chat-dom.js?v=20261102-chat1";
import { STRIP, StripScheduler, cutStrip, lanesFor } from "./chat-logic.js?v=20261102-chat1";

const PHONE = "(max-width: 767px)";
const TOP = 64;
const FADE_MS = 220;
const SAMPLE = "Лучшая сцена серии";

/** Полоски в контейнере видео .sp-video. */
export class ChatStrips {
  /** video — контейнер плеера, в который кладётся слой полосок. */
  constructor(video) {
    this.video = video;
    this.box = h("div", "chat-strips");
    this.box.setAttribute("aria-hidden", "true");
    this.nodes = new Map();
    this.sched = new StripScheduler();
    this.step = 39;
    this.on = false;
    this._observer = typeof ResizeObserver === "function" ? new ResizeObserver(() => this.measure()) : null;
  }

  /** Включить слой: чат открыт. */
  start() {
    if (this.on || !this.video) return;
    this.on = true;
    this.video.append(this.box);
    this._observer?.observe(this.video);
    this.measure();
  }

  /** Выключить слой: чат закрыт — поверх видео ничего нет. */
  stop() {
    this.on = false;
    this._observer?.disconnect();
    this.box.replaceChildren();
    this.box.remove();
    this.nodes.clear();
    this.sched.reset(null);
  }

  /** Телефон или нет и сколько дорожек влезает в кадр (полный экран, поворот, ширина окна). */
  measure() {
    if (!this.on) return;
    const phone = window.matchMedia(PHONE).matches;
    const probe = h("div", "chat-strip", SAMPLE);
    probe.style.maxWidth = "none";
    this.box.append(probe);
    this.step = (probe.offsetHeight || 31) + 8;
    // Ширина под текст полоски на этой ширине видео: длинный текст режет наше жирное «…», а не многоточие браузера
    const range = document.createRange();
    range.selectNodeContents(probe);
    this.font = getComputedStyle(probe).font;
    this.room = this.video.clientWidth * (phone ? 0.85 : 0.75) - (probe.offsetWidth - range.getBoundingClientRect().width) - 6;
    probe.remove();
    const mode = phone ? STRIP.phone : STRIP.desktop;
    this.sched.configure({ phone, lanes: lanesFor(this.video.clientHeight, this.step, mode.lanes) });
    this.chars = mode.chars;
    for (const [id, node] of this.nodes) {
      const s = this.sched.active.find(a => a.id === id);
      if (s) node.style.top = `${TOP + s.lane * this.step}px`;
    }
  }

  /** Текст полоски: до 80 знаков (телефон — 40) по границе слова и короче, если столько не влезает в ширину. */
  _fit(text) {
    const ctx = (this._ctx ||= document.createElement("canvas").getContext("2d"));
    ctx.font = this.font || "500 14px sans-serif";
    let max = this.chars || STRIP.desktop.chars, [part, cut] = cutStrip(text, max);
    while (max > 20 && this.room > 0 && ctx.measureText(cut ? `${part}…` : part).width > this.room) [part, cut] = cutStrip(text, max -= 4);
    return [part, cut];
  }

  /** Корневые комментарии чата по порядку минут. */
  setRoots(roots) {
    this.sched.setRoots(roots);
  }

  /** Живой комментарий у текущей минуты — сразу в очередь. */
  push(c, t) {
    if (this.on) this.sched.push(c, t);
  }

  /** Время видео: показать то, что расписание сочло видимым. */
  tick(t) {
    if (!this.on) return;
    const active = this.sched.tick(t);
    const keep = new Set(active.map(s => s.id));
    for (const [id, node] of this.nodes) {
      if (keep.has(id)) continue;
      this.nodes.delete(id);
      node.classList.remove("is-in");
      setTimeout(() => node.remove(), FADE_MS);
    }
    for (const s of active) {
      if (this.nodes.has(s.id)) continue;
      const [text, cut] = this._fit(s.comment.text);
      const node = h("div", "chat-strip", text);
      if (cut) node.append(h("b", "", "…"));
      node.style.top = `${TOP + s.lane * this.step}px`;
      this.box.append(node);
      this.nodes.set(s.id, node);
      requestAnimationFrame(() => node.classList.add("is-in"));
    }
  }
}
