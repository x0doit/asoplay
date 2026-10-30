// AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
// All rights reserved. See /COPYRIGHT for full terms.
//
// Решения из раздела «AsoPlay» панели Anime Social для всего сайта (server/site_settings.py): плашка-объявление,
// тайтлы, скрытые по жалобе правообладателя, тайтлы 18+, закреплённые в карусели главной, способы входа и
// порядок источников видео. Приходят вместе с состоянием сервера — при загрузке (/health) и по живому
// соединению (av:status), — так что правка из панели видна сразу. Изменение — событие av:site {changed}.

const state = {
  notice: null,
  hidden: new Set(),
  adult: new Set(),
  pinned: [],
  login: { social: true, password: true },
  sources: [],
};

const NOTICE_KEY = "av_notice_dismissed";
const ADULT_KEY = "av_adult_ok";
let noticeTimer = null;

const ids = list => (Array.isArray(list) ? list.filter(Number.isInteger) : []);
const sameSet = (a, b) => a.size === b.size && [...a].every(x => b.has(x));

export function apply(site) {
  if (!site || typeof site !== "object") return;
  const changed = new Set();
  if ("notice" in site) {
    const notice = site.notice?.text ? { text: String(site.notice.text), until: site.notice.until || null } : null;
    if (JSON.stringify(notice) !== JSON.stringify(state.notice)) { state.notice = notice; changed.add("notice"); }
  }
  for (const key of ["hidden", "adult"]) {
    if (!Array.isArray(site[key])) continue;
    const next = new Set(ids(site[key]));
    if (!sameSet(next, state[key])) { state[key] = next; changed.add(key); }
  }
  if (Array.isArray(site.pinned) && ids(site.pinned).join() !== state.pinned.join()) {
    state.pinned = ids(site.pinned);
    changed.add("pinned");
  }
  if (site.login && typeof site.login === "object") {
    const login = { social: site.login.social !== false, password: site.login.password !== false };
    if (login.social !== state.login.social || login.password !== state.login.password) { state.login = login; changed.add("login"); }
  }
  if (Array.isArray(site.sources)) {
    const sources = site.sources.filter(s => typeof s === "string");
    if (sources.join() !== state.sources.join()) { state.sources = sources; changed.add("sources"); }
  }
  if (!changed.size) return;
  if (changed.has("notice")) renderNotice();
  document.dispatchEvent(new CustomEvent("av:site", { detail: { changed } }));
}

export const isHidden = id => state.hidden.has(Number(id));
export const isAdult = id => state.adult.has(Number(id));
// Закреплённые для карусели: скрытые и 18+ туда не попадают
export const pinned = () => state.pinned.filter(id => !state.hidden.has(id) && !state.adult.has(id));
export const login = () => ({ ...state.login });
export const sourceOrder = () => [...state.sources];

export function visible(list, key = "mal_id") {
  return state.hidden.size ? (list || []).filter(x => !state.hidden.has(Number(x?.[key]))) : list;
}

// ---------- 18+ ----------
export function adultConfirmed() {
  try { return localStorage.getItem(ADULT_KEY) === "1"; } catch { return false; }
}

export function confirmAdult() {
  try { localStorage.setItem(ADULT_KEY, "1"); } catch { /* приватный режим — спросим ещё раз */ }
}

// ---------- плашка ----------
const noticeKey = n => `${n.text}|${n.until || ""}`;

function noticeDismissed(n) {
  try { return localStorage.getItem(NOTICE_KEY) === noticeKey(n); } catch { return false; }
}

export function renderNotice() {
  const box = document.getElementById("avNotice");
  if (!box) return;
  clearTimeout(noticeTimer);
  const n = state.notice;
  if (!n || (n.until && n.until * 1000 <= Date.now()) || noticeDismissed(n)) {
    box.hidden = true;
    box.replaceChildren();
    return;
  }
  box.innerHTML = `
    <div class="ed-note site-notice__note" role="status">
      <i class="fas fa-bullhorn" aria-hidden="true"></i>
      <div class="site-notice__text"></div>
      <button type="button" class="ed-iconbtn site-notice__close" aria-label="Скрыть объявление" title="Скрыть"><i class="fas fa-xmark" aria-hidden="true"></i></button>
    </div>`;
  box.querySelector(".site-notice__text").textContent = n.text;
  box.querySelector(".site-notice__close").addEventListener("click", () => {
    try { localStorage.setItem(NOTICE_KEY, noticeKey(n)); } catch { /* скроем до перезагрузки */ }
    box.hidden = true;
  });
  box.hidden = false;
  if (n.until) noticeTimer = setTimeout(renderNotice, Math.min(n.until * 1000 - Date.now() + 500, 2 ** 31 - 1));
}
