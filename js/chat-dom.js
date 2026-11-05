/*
 * Чат серии, мелочи разметки: узел с текстом, иконка Font Awesome, кнопка с действием, аватар-ссылка и текст
 * комментария с упоминаниями. Данные в разметку попадают только через textContent и атрибуты, никогда через innerHTML.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 */
import { authorName } from "./chat-logic.js?v=20261105-replay1";

const MENTION = /(^|[\s(>])@([A-Za-z0-9_]{3,20})\b/g;
const STATES = {
  empty: ["fas fa-comments", "Комментариев пока нет", "Напишите на любой минуте серии — комментарий встанет на эту минуту."],
  error: ["fas fa-triangle-exclamation", "Чат не загрузился", "Проверьте соединение и попробуйте ещё раз."],
};

/** Узел с классом и текстом. */
export function h(tag, cls = "", text = null) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== null) node.textContent = text;
  return node;
}

/** Иконка Font Awesome, скрытая от чтения с экрана. */
export function icon(cls) {
  const i = h("i", cls);
  i.setAttribute("aria-hidden", "true");
  return i;
}

/** Кнопка с действием data-act; label — подпись для чтения с экрана и подсказка, text — видимая подпись. */
export function actButton(cls, act, label = "", text = null) {
  const b = h("button", cls, text);
  b.type = "button";
  if (act) b.dataset.act = act;
  if (label) { b.setAttribute("aria-label", label); b.title = label; }
  return b;
}

/** Загрузка, пусто или ошибка с «Повторить» (data-act="retry") вместо списка. */
export function stateBox(kind) {
  const box = kind === "loading" ? h("div", "ed-loading") : h("div", "ed-empty chat-state");
  if (kind === "loading") {
    box.append(h("span", "ed-spinner"), document.createTextNode("Загружаем…"));
    return box;
  }
  const [cls, title, text] = STATES[kind], pic = h("div", "ed-empty__icon");
  pic.append(icon(cls));
  box.append(pic, h("div", "ed-empty__title", title), h("div", "ed-empty__text", text));
  if (kind === "error") {
    const act = h("div", "ed-empty__action");
    act.append(actButton("ed-btn ed-btn--secondary", "retry", "", "Повторить"));
    box.append(act);
  }
  return box;
}

/** Ссылка на страницу человека в соцсети (site — её адрес). */
export function profileUrl(site, author) {
  return `${site}/${encodeURIComponent(author?.alias || `profile${author?.id || ""}`)}`;
}

/** Аватар-ссылка: буква под фото, фото не загрузилось — видна буква; small — 24 px для ответов. */
export function avatar(site, author, small = false) {
  const box = h("a", `ed-ava comments__ava${small ? " comments__ava--sm" : ""}`);
  Object.assign(box, { href: profileUrl(site, author), target: "_blank", rel: "noopener", tabIndex: -1 });
  box.setAttribute("aria-hidden", "true");
  box.append(h("span", "ed-ava__initials", Array.from(authorName(author).trim())[0]?.toUpperCase() || "А"));
  const src = author?.avatar?.small || author?.avatar?.medium;
  if (src) {
    const img = h("img");
    Object.assign(img, { src, alt: "", loading: "lazy", referrerPolicy: "no-referrer" });
    img.addEventListener("error", () => img.remove());
    box.append(img);
  }
  return box;
}

/** Текст комментария: упоминания @адрес — ссылками на страницы соцсети, разбором, без вставки HTML. */
export function richText(site, text) {
  const box = h("span", "comment__text");
  const src = String(text || "");
  let at = 0;
  for (const m of src.matchAll(MENTION)) {
    const start = m.index + m[1].length;
    box.append(document.createTextNode(src.slice(at, start)));
    const a = h("a", "comment__mention", `@${m[2]}`);
    Object.assign(a, { href: `${site}/${m[2]}`, target: "_blank", rel: "noopener" });
    box.append(a);
    at = start + m[2].length + 1;
  }
  box.append(document.createTextNode(src.slice(at)));
  return box;
}
