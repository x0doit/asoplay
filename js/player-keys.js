/*
 * Клавиши нашего плеера, когда фокус на странице: ← и → — на 5 с назад и вперёд, пробел — пауза и пуск. Страница
 * передаёт клавишу кадру (asoplay:key), а с фокусом в кадре он ловит её сам. Набор текста (поле ввода, textarea,
 * select, contenteditable — чат серии, поиск) и открытое окно клавиш не отдают; пробел на кнопке или ссылке нажимает
 * её, как обычно. Страница не листается от пробела, только когда он ушёл плееру.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 */
const KEYS = { ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", " ": "Space" };
const TYPING = "input, textarea, select, [contenteditable]:not([contenteditable='false'])";
const CONTROL = "a[href], button, summary, [role='button'], [role='link'], [role='menuitem'], [tabindex]";

/** Слушать клавиши страницы; frame() — окно нашего кадра, который принимает клавиши, или null. Ответ — отписка. */
export function bindPlayerKeys(frame) {
  const onKeydown = (e) => {
    const key = KEYS[e.key];
    if (!key || e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    const target = frame();
    if (!target) return;
    const el = document.activeElement;
    if (el?.closest?.(TYPING) || el?.isContentEditable || document.querySelector("[aria-modal='true']")) return;
    if (key === "Space" && el && el !== document.body && el.closest?.(CONTROL)) return;
    e.preventDefault();
    target.postMessage({ type: "asoplay:key", key }, "*");
  };
  document.addEventListener("keydown", onKeydown);
  return () => document.removeEventListener("keydown", onKeydown);
}
