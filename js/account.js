// -*- coding: utf-8 -*-
/*
 * AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
 * All rights reserved. See /COPYRIGHT for full terms.
 *
 * Account layer: login/logout/register redirect, personal sections (favorites,
 * history), guest auth-gates and the "store" abstraction that turns every
 * personal-data write into either a server call (authenticated) or a gate
 * prompt (guest). There is no localStorage fallback for new guest progress;
 * only a guest's per-title dub choice is kept in this browser.
 */

import { calendarMonth, dayDate, groupActivity } from "./profile-activity.js?v=20261027-profile6";
import { activeSection, applyTheme, getTheme, syncThemeLabels, themeLabel, SheetDrag } from "./navigation.js?v=20261029-eddas2";

let ctx = null;
let authConfig = {
  register_url: "https://animesocial.online/register",
  social: {
    site_name: "AnimeSocial",
    site_url: "https://animesocial.online",
    register_url: "https://animesocial.online/register",
    login_url: "https://animesocial.online/login",
    fallbacks: { avatar: "https://animesocial.online/img/noava.png" },
  },
};

// Bootstrap entry point called from app.js. `hooks` provides app-side
// utilities (nav, render, outlet, imgUrl, esc, backend URL, the shared state
// object). Everything else in this module reads through `ctx`.
export async function setup(hooks) {
  ctx = hooks;
  try {
    const cfg = await _fetchJson("/auth/config");
    if (cfg) {
      authConfig = { ...authConfig, ...cfg };
      if (cfg.social) authConfig.social = { ...authConfig.social, ...cfg.social };
    }
  } catch (_) { /* non-fatal; defaults stay */ }
  // Expose the social config on shared state so app.js can read it from
  // any view (e.g. the "supported by" banner on the anime page).
  ctx.state.social = authConfig.social;
  await refresh();
  maybeOfferAsAccount();
}

export function getSocialConfig() { return authConfig.social; }

function _historyQualifiedProgress(seconds, duration) {
  const sec = Number(seconds || 0);
  const dur = Number(duration || 0);
  return sec >= 300 || (dur > 0 && sec >= dur * 0.92);
}

// Ordered [malId, dubKey] pairs: object keys that look like numbers lose insertion
// order, and the oldest choices must be the ones dropped at the cap.
const GUEST_DUBS_KEY = "av_guest_dubs";
const GUEST_DUBS_LIMIT = 300;

function _guestDubPairs() {
  try {
    const pairs = JSON.parse(localStorage.getItem(GUEST_DUBS_KEY) || "[]");
    return Array.isArray(pairs) ? pairs.filter(p => Array.isArray(p) && p.length === 2) : [];
  } catch (_) {
    return [];
  }
}

function _loadGuestDubs() {
  return Object.fromEntries(_guestDubPairs());
}

function _rememberGuestDub(malId, key) {
  const pairs = _guestDubPairs().filter(([id]) => id !== malId);
  pairs.push([malId, key]);
  try { localStorage.setItem(GUEST_DUBS_KEY, JSON.stringify(pairs.slice(-GUEST_DUBS_LIMIT))); } catch (_) {}
}

export async function refresh() {
  try {
    const r = await _fetchJson("/auth/me", { credentials: "include" });
    ctx.state.user = r && r.authenticated ? r.user : null;
  } catch (_) {
    ctx.state.user = null;
  }
  if (ctx.state.user) {
    await _hydratePersonal();
    // If this browser still has old guest data in localStorage, offer a
    // one-shot import on the next tick.
    setTimeout(() => { maybeOfferLocalImport(); }, 600);
  } else {
    ctx.state.favorites = {};
    ctx.state.watch = {};
    ctx.state.ratings = {};
    ctx.state.dubPrefs = _loadGuestDubs();
    ctx.state.autoNext = true;
  }
  renderHeader();
  return ctx.state.user;
}

async function _hydratePersonal() {
  const out = await Promise.all([
    _fetchJson("/account/lists").catch(() => []),
    _fetchJson("/account/history").catch(() => []),
    _fetchJson("/account/ratings").catch(() => ({})),
    _fetchJson("/account/dub-prefs").catch(() => ({})),
    _fetchJson("/account/settings").catch(() => ({ autonext: true, auto_add_lists: true })),
    _fetchJson("/account/privacy").catch(() => ({ hide_lists: false, hide_activity: false })),
  ]);
  const [lists, history, ratings, dubPrefs, settings, privacy] = out;

  // state.lists — единая карта (mal_id → {status, is_favorite, ...}).
  ctx.state.lists = Object.fromEntries(
    (lists || []).map(l => [String(l.mal_id), {
      status: l.status,
      status_source: l.status_source,
      is_favorite: !!l.is_favorite,
      title: l.title || "",
      cover: l.poster_url || "",
      at: Date.parse(l.updated_at || l.added_at || "") || Date.now(),
    }])
  );
  // state.favorites — деривация от списков (для старого UI-кода).
  ctx.state.favorites = {};
  for (const [id, v] of Object.entries(ctx.state.lists)) {
    if (v.is_favorite) {
      ctx.state.favorites[id] = { at: v.at, title: v.title, cover: v.cover };
    }
  }
  // state.watch — прогресс (для экрана «Продолжить просмотр»).
  ctx.state.watch = Object.fromEntries(
    (history || []).map(h => [String(h.mal_id), {
      at: Date.parse(h.updated_at || "") || Date.now(),
      title: h.title || "",
      cover: h.poster_url || "",
      ep: h.last_episode || 1,
      time: h.episode_seconds || 0,
      duration: h.episode_duration || 0,
      total: h.episodes_total || 0,
    }])
  );
  ctx.state.ratings = ratings || {};
  ctx.state.dubPrefs = dubPrefs || {};
  ctx.state.autoNext = (settings && settings.autonext) !== false;
  ctx.state.autoAddLists = settings ? !!settings.auto_add_lists : true;
  ctx.state.privacy = privacy || { hide_lists: false, hide_activity: false };
}

function _fetchJson(path, opts = {}) {
  const url = /^https?:/.test(path) ? path : (ctx.backendUrl + path);
  const init = { credentials: "include", ...opts };
  if (init.body && typeof init.body === "object" && !(init.body instanceof FormData)) {
    init.headers = { "content-type": "application/json", ...(init.headers || {}) };
    init.body = JSON.stringify(init.body);
  }
  return fetch(url, init).then(async r => {
    if (!r.ok) {
      const text = await r.text().catch(() => "");
      const err = new Error(`${r.status} ${text.slice(0, 200)}`);
      err.status = r.status;
      err.retryAfter = Number(r.headers.get("retry-after")) || 0;
      throw err;
    }
    const ct = r.headers.get("content-type") || "";
    return ct.includes("json") ? r.json() : r.text();
  });
}

// ==== auth actions ====
export async function login(identity, password) {
  const r = await _fetchJson("/auth/login", {
    method: "POST",
    body: { login: identity, password },
  });
  ctx.state.user = r.user;
  await _hydratePersonal();
  renderHeader();
  setTimeout(() => { maybeOfferLocalImport(); }, 300);
  return r.user;
}

function _loginErrorMessage(exc) {
  if (exc?.status === 401 || exc?.status === 422) return "Неправильный логин или пароль.";
  if (exc?.status === 403) return "Аккаунт заблокирован.";
  if (exc?.status === 429) {
    const minutes = Math.max(1, Math.ceil((exc.retryAfter || 900) / 60));
    return `Слишком много попыток входа. Попробуйте снова через ${minutes} ${_plural(minutes, ["минуту", "минуты", "минут"])}.`;
  }
  return "Не удалось войти. Проверьте соединение и попробуйте снова.";
}

// ==== one-time localStorage migration ====
// Triggered after a successful login (and opportunistically when /my/favorites
// opens). Reads old av_favs / av_watch / av_ratings / av_dubs from this
// browser and offers to move them into the account. Per the playbook, this is
// a controlled one-shot — never the primary model for guests.
const LS_KEYS = ["av_favs", "av_watch", "av_ratings", "av_dubs", "av_autonext"];

export async function maybeOfferLocalImport() {
  if (!ctx.state.user) return;
  const flagKey = "av_import_offered_v1";
  if (localStorage.getItem(flagKey)) return;
  const blob = _collectLocalBlob();
  const total = blob.favorites.length + Object.keys(blob.watch).length
              + Object.keys(blob.ratings).length + Object.keys(blob.dub_prefs).length;
  if (total === 0) {
    localStorage.setItem(flagKey, "1");  // nothing to import, don't ask again
    return;
  }
  try {
    const marks = await _fetchJson("/account/import-marks");
    if (marks && (marks.favorites || marks.watch)) {
      localStorage.setItem(flagKey, "1");  // server already knows about this user's import
      return;
    }
  } catch (_) {}
  _showImportPrompt(blob, total, flagKey);
}

function _collectLocalBlob() {
  const parse = (key, fallback) => {
    try { return JSON.parse(localStorage.getItem(key) || "") ?? fallback; }
    catch { return fallback; }
  };
  const favsObj = parse("av_favs", {});
  const favorites = Object.entries(favsObj).map(([id, v]) => ({
    mal_id: Number(id),
    title: v?.title || "",
    cover: v?.cover || "",
  }));
  return {
    favorites,
    watch: parse("av_watch", {}) || {},
    ratings: parse("av_ratings", {}) || {},
    dub_prefs: parse("av_dubs", {}) || {},
    autonext: localStorage.getItem("av_autonext") === "1" ? true
            : localStorage.getItem("av_autonext") === "0" ? false : null,
  };
}

function _showImportPrompt(blob, total, flagKey) {
  const bar = document.createElement("div");
  bar.className = "import-bar";
  bar.setAttribute("role", "region");
  bar.setAttribute("aria-label", "Перенос данных из браузера");
  bar.innerHTML = `
    <div class="import-bar-inner">
      <div class="import-bar-text">
        В этом браузере есть записи без аккаунта: ${total}. Это избранное, история и оценки. Перенести их в аккаунт?
      </div>
      <div class="import-bar-actions">
        <button type="button" class="ed-btn ed-btn--ghost ed-btn--sm" id="avImportNo">Не переносить</button>
        <button type="button" class="ed-btn ed-btn--primary ed-btn--sm" id="avImportYes">Перенести</button>
      </div>
    </div>`;
  document.body.appendChild(bar);

  document.querySelector("#avImportNo").addEventListener("click", () => {
    localStorage.setItem(flagKey, "1");
    bar.remove();
  });
  document.querySelector("#avImportYes").addEventListener("click", async () => {
    const btn = document.querySelector("#avImportYes");
    btn.disabled = true;
    btn.textContent = "Переносим…";
    try {
      const r = await _fetchJson("/account/import-localstorage", {
        method: "POST",
        body: blob,
      });
      // Clean up only after a successful import — so user never loses data
      // on a network blip.
      for (const k of LS_KEYS) localStorage.removeItem(k);
      localStorage.setItem(flagKey, "1");
      await _hydratePersonal();
      btn.textContent = `Перенесено: ${Object.values(r.imported || {}).reduce((a, b) => a + b, 0)}`;
      setTimeout(() => bar.remove(), 2500);
    } catch (exc) {
      btn.disabled = false;
      btn.textContent = "Попробовать ещё раз";
      const msg = document.createElement("div");
      msg.className = "import-bar-error";
      msg.textContent = `Не удалось перенести записи (${exc.message}). Повторите попытку.`;
      bar.querySelector(".import-bar-inner").appendChild(msg);
    }
  });
}

export async function logout() {
  try { await _fetchJson("/auth/logout", { method: "POST" }); } catch (_) {}
  _clearProgressQueues();
  ctx.state.user = null;
  ctx.state.favorites = {};
  ctx.state.watch = {};
  ctx.state.ratings = {};
  ctx.state.dubPrefs = _loadGuestDubs();
  renderHeader();
}

export function registerUrl() {
  return authConfig.social?.register_url || authConfig.register_url;
}

export function socialSiteUrl() {
  return authConfig.social?.site_url || "";
}
export function socialSiteName() {
  return authConfig.social?.site_name || "AnimeSocial";
}

// ==== header slot ====
let cleanupHeader = () => {};
// EDDAS avatar: initials until the real picture has loaded, so a blocked or
// missing image never shows a broken-image icon.
function _avatarHtml(user, size = "") {
  const name = user.name || user.login || user.handle || "А";
  const letter = Array.from(name.trim())[0]?.toUpperCase() || "А";
  return `<span class="ed-ava${size ? ` ${size}` : ""}" data-account-avatar><span class="ed-ava__initials">${esc(letter)}</span></span>`;
}

function _upgradeAvatars(root, url) {
  if (!url) return;
  const probe = new Image();
  probe.referrerPolicy = "no-referrer";
  probe.onload = () => {
    root.querySelectorAll("[data-account-avatar]").forEach(box => {
      box.innerHTML = `<img src="${esc(url)}" alt="" referrerpolicy="no-referrer" />`;
    });
  };
  probe.src = url;
}

function _privacyGate(text, extraClass = "") {
  return `<div class="ed-note privacy-gate${extraClass ? ` ${extraClass}` : ""}"><i class="fas fa-lock" aria-hidden="true"></i><div>${esc(text)}</div></div>`;
}

function _profilePath(user) {
  return user.profile_path || ("/@" + (user.handle || ("profile" + user.id)));
}

export function renderHeader() {
  const slot = document.querySelector("#avAccountSlot");
  if (!slot) return;
  cleanupHeader();
  const user = ctx.state.user;
  const navPersonal = document.querySelector("#avNavPersonal");
  const sheetPersonal = document.querySelector("#avSheetPersonal");
  const tab = document.querySelector("#avTabAccount");
  document.querySelectorAll("[data-social-link]").forEach(a => { a.href = socialSiteUrl(); });

  if (!user) {
    slot.innerHTML = `
      <div class="app-header__actions app-header__actions--guest">
        <a class="ed-btn ed-btn--ghost" href="/login" data-nav="login">Войти</a>
        <a class="ed-btn ed-btn--primary app-guest-register" href="${esc(registerUrl())}" target="_blank" rel="noopener">
          <span class="app-guest-register__full">Создать аккаунт</span><span class="app-guest-register__short">Регистрация</span>
        </a>
      </div>`;
    if (navPersonal) { navPersonal.hidden = true; navPersonal.innerHTML = ""; }
    if (sheetPersonal) sheetPersonal.innerHTML = `
      <div class="ed-menu__divider" role="separator"></div>
      <a class="ed-menu__item" href="/login" data-nav="login"><i class="fas fa-right-to-bracket" aria-hidden="true"></i>Войти</a>
      <a class="ed-menu__item" href="${esc(registerUrl())}" target="_blank" rel="noopener"><i class="fas fa-user-plus" aria-hidden="true"></i>Создать аккаунт в ${esc(socialSiteName())}</a>`;
    if (tab) {
      tab.href = "/login";
      tab.dataset.nav = "login";
      tab.setAttribute("aria-label", "Войти");
      tab.innerHTML = `<i class="fas fa-right-to-bracket" aria-hidden="true"></i>`;
    }
    ctx.setActive(activeSection() || null);
    return;
  }

  const name = user.name || user.login || user.email || "Аккаунт";
  const profile = _profilePath(user);
  slot.innerHTML = `
    <div class="app-header__action">
      <button type="button" class="app-user" id="avAccountBtn" aria-haspopup="menu" aria-expanded="false" aria-controls="avAccountDrop" aria-label="Меню профиля">
        ${_avatarHtml(user)}
        <span class="app-user__name">${esc(name)}</span>
        <i class="fas fa-chevron-down" aria-hidden="true"></i>
      </button>
      <div class="app-popover app-popover--menu" id="avAccountDrop" role="menu" aria-label="Меню профиля">
        <a class="app-menu__head" href="${esc(profile)}" data-nav="profile">${_avatarHtml(user)}<span><b>${esc(name)}</b><span>Мой профиль</span></span></a>
        <div class="ed-menu__divider" role="separator"></div>
        <a class="ed-menu__item" role="menuitem" href="/my/continue"><i class="fas fa-clock-rotate-left" aria-hidden="true"></i>Продолжить просмотр</a>
        <a class="ed-menu__item" role="menuitem" href="/my/lists"><i class="fas fa-list" aria-hidden="true"></i>Списки</a>
        <a class="ed-menu__item" role="menuitem" href="/my/favorites"><i class="fas fa-star" aria-hidden="true"></i>Избранное</a>
        <a class="ed-menu__item" role="menuitem" href="/my/settings"><i class="fas fa-gear" aria-hidden="true"></i>Настройки</a>
        <button type="button" class="ed-menu__item" role="menuitem" data-theme-cycle><i class="fas fa-circle-half-stroke" aria-hidden="true"></i>Тема<span class="app-menu__value" data-theme-label></span></button>
        <div class="ed-menu__divider" role="separator"></div>
        <a class="ed-menu__item" role="menuitem" href="${esc(socialSiteUrl())}" target="_blank" rel="noopener"><i class="fas fa-users" aria-hidden="true"></i>${esc(socialSiteName())}</a>
        <div class="ed-menu__divider" role="separator"></div>
        <button type="button" class="ed-menu__item" role="menuitem" data-logout><i class="fas fa-right-from-bracket" aria-hidden="true"></i>Выйти</button>
      </div>
    </div>`;
  if (navPersonal) {
    navPersonal.hidden = false;
    navPersonal.innerHTML = `
      <div class="app-nav__sep" role="separator"></div>
      <a class="app-nav__item" data-nav="my-continue" href="/my/continue"><i class="fas fa-clock-rotate-left" aria-hidden="true"></i><span>Продолжить</span></a>
      <a class="app-nav__item" data-nav="my-lists" href="/my/lists"><i class="fas fa-list" aria-hidden="true"></i><span>Списки</span></a>
      <a class="app-nav__item" data-nav="my-favorites" href="/my/favorites"><i class="fas fa-star" aria-hidden="true"></i><span>Избранное</span></a>
      <a class="app-nav__item" data-nav="profile" href="${esc(profile)}"><i class="fas fa-user" aria-hidden="true"></i><span>Мой профиль</span></a>`;
  }
  if (sheetPersonal) sheetPersonal.innerHTML = `
    <div class="ed-menu__divider" role="separator"></div>
    <a class="ed-menu__item" data-nav="my-continue" href="/my/continue"><i class="fas fa-clock-rotate-left" aria-hidden="true"></i>Продолжить просмотр</a>
    <a class="ed-menu__item" data-nav="my-favorites" href="/my/favorites"><i class="fas fa-star" aria-hidden="true"></i>Избранное</a>
    <a class="ed-menu__item" data-nav="profile" href="${esc(profile)}"><i class="fas fa-user" aria-hidden="true"></i>Мой профиль</a>
    <a class="ed-menu__item" data-nav="my-settings" href="/my/settings"><i class="fas fa-gear" aria-hidden="true"></i>Настройки</a>
    <button type="button" class="ed-menu__item" data-logout><i class="fas fa-right-from-bracket" aria-hidden="true"></i>Выйти</button>`;
  if (tab) {
    tab.href = "/my/lists";
    tab.dataset.nav = "my-lists";
    tab.setAttribute("aria-label", "Списки");
    tab.innerHTML = `<i class="fas fa-list" aria-hidden="true"></i>`;
  }
  _upgradeAvatars(slot, user.avatar || "");
  syncThemeLabels();

  const btn = document.querySelector("#avAccountBtn");
  const drop = document.querySelector("#avAccountDrop");
  const setOpen = (open) => {
    drop.classList.toggle("is-open", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  };
  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(!drop.classList.contains("is-open"));
  });
  // A link or a menu command closes the menu; the theme item keeps it open to show the new value.
  drop.addEventListener("click", (e) => {
    if (e.target.closest("a[href], [data-logout]")) setOpen(false);
  });
  const closeOutside = (e) => {
    if (!btn.contains(e.target) && !drop.contains(e.target)) setOpen(false);
  };
  const closeEscape = (e) => {
    if (e.key === "Escape" && drop.classList.contains("is-open")) {
      setOpen(false);
      btn.focus();
    }
  };
  const onLogout = async (e) => {
    if (!e.target.closest("[data-logout]")) return;
    await logout();
    ctx.nav("/");
  };
  document.addEventListener("click", closeOutside);
  document.addEventListener("keydown", closeEscape);
  document.addEventListener("click", onLogout);
  cleanupHeader = () => {
    document.removeEventListener("click", closeOutside);
    document.removeEventListener("keydown", closeEscape);
    document.removeEventListener("click", onLogout);
  };
  ctx.setActive(activeSection() || null);
}

// ==== store: personal data writes ====
// All functions are async; most return the updated local-cache value.
// Guests get a guest-gate popup and no state change.
//
// Списки и «Любимые» теперь в одной таблице aviev_user_lists:
//   - getListStatus / setListStatus — основной статус (watching/planned/…)
//   - hasFavorite / toggleFavorite / setFavorite — отдельный флаг is_favorite
// Сохраняем совместимые методы, чтобы старый UI-код не сломался.
export const store = {
  getListEntry(malId) {
    return ctx.state.lists?.[String(malId)] || null;
  },
  getListStatus(malId) {
    return ctx.state.lists?.[String(malId)]?.status || null;
  },
  async setListStatus(malId, status, meta = {}) {
    if (!ctx.state.user) { openGate("lists"); return null; }
    const key = String(malId);
    const prev = ctx.state.lists?.[key] || null;
    const next = {
      status: status || null,
      status_source: "manual",
      is_favorite: prev?.is_favorite || false,
      title: meta.title || prev?.title || "",
      cover: meta.cover || prev?.cover || "",
      at: Date.now(),
    };
    ctx.state.lists = ctx.state.lists || {};
    ctx.state.lists[key] = next;
    try {
      const out = await _fetchJson(`/account/lists/${malId}/status`, {
        method: "PUT",
        body: {
          status: status || null,
          title: meta.title || "",
          poster_url: meta.cover || "",
        },
      });
      if (out && out.mal_id) {
        ctx.state.lists[key] = {
          status: out.status,
          status_source: out.status_source,
          is_favorite: !!out.is_favorite,
          title: out.title || next.title,
          cover: out.poster_url || next.cover,
          at: Date.parse(out.updated_at || "") || Date.now(),
        };
      }
      return ctx.state.lists[key];
    } catch (_) {
      if (prev) ctx.state.lists[key] = prev;
      else delete ctx.state.lists[key];
      return null;
    }
  },

  hasFavorite(malId) {
    return !!ctx.state.lists?.[String(malId)]?.is_favorite;
  },
  async toggleFavorite(malId, meta = {}) {
    const on = !this.hasFavorite(malId);
    return this.setFavorite(malId, on, meta);
  },
  async setFavorite(malId, on, meta = {}) {
    if (!ctx.state.user) { openGate("favorites"); return false; }
    const key = String(malId);
    const prev = ctx.state.lists?.[key] || null;
    ctx.state.lists = ctx.state.lists || {};
    ctx.state.lists[key] = {
      status: prev?.status || null,
      status_source: prev?.status_source || "manual",
      is_favorite: !!on,
      title: meta.title || prev?.title || "",
      cover: meta.cover || prev?.cover || "",
      at: Date.now(),
    };
    // Синхронизируем state.favorites (деривация для старого UI).
    if (on) ctx.state.favorites[key] = { at: Date.now(), title: meta.title || "", cover: meta.cover || "" };
    else delete ctx.state.favorites[key];
    try {
      await _fetchJson(`/account/lists/${malId}/favorite`, {
        method: "PUT",
        body: { is_favorite: !!on, title: meta.title || "", poster_url: meta.cover || "" },
      });
    } catch (_) {
      // откат
      if (prev) ctx.state.lists[key] = prev;
      else delete ctx.state.lists[key];
      if (on) delete ctx.state.favorites[key];
      else if (prev?.is_favorite) {
        ctx.state.favorites[key] = { at: prev.at, title: prev.title, cover: prev.cover };
      }
      return !on ? true : false;
    }
    return !!on;
  },

  async setAutoAddLists(on) {
    ctx.state.autoAddLists = !!on;
    if (!ctx.state.user) return;
    try {
      await _fetchJson("/account/settings", {
        method: "PUT",
        body: {
          autonext: !!ctx.state.autoNext,
          auto_add_lists: !!on,
        },
      });
    } catch (_) {}
  },

  async setPrivacy(patch) {
    if (!ctx.state.user) return;
    ctx.state.privacy = { ...ctx.state.privacy, ...patch };
    try {
      await _fetchJson("/account/privacy", {
        method: "PUT",
        body: { ...ctx.state.privacy },
      });
    } catch (_) {}
  },

  async sendProgressEvent(payload) {
    if (!ctx.state.user) return null;
    try {
      const r = await _fetchJson("/account/lists/progress-event", {
        method: "POST", body: payload,
      });
      if (r && r.entry && r.entry.mal_id) {
        const key = String(r.entry.mal_id);
        ctx.state.lists = ctx.state.lists || {};
        const prev = ctx.state.lists[key];
        ctx.state.lists[key] = {
          status: r.entry.status,
          status_source: r.entry.status_source,
          is_favorite: !!r.entry.is_favorite,
          title: r.entry.title || prev?.title || "",
          cover: r.entry.poster_url || prev?.cover || "",
          at: Date.parse(r.entry.updated_at || "") || Date.now(),
        };
        // Если сервер применил авто-правила (watching через 10 мин, completed
        // на последней серии, …) — бросаем событие, чтобы страница аниме могла
        // мгновенно перерисовать list-picker и отразить новый статус без
        // перезагрузки.
        const statusChanged = prev?.status !== r.entry.status;
        if ((r.applied && r.applied.length) || statusChanged) {
          window.dispatchEvent(new CustomEvent("av:list-updated", {
            detail: {
              mal_id: Number(r.entry.mal_id),
              applied: r.applied || [],
              entry: ctx.state.lists[key],
            },
          }));
        }
      }
      // Live-синхронизация state.watch: когда пользователь перевалил 5-мин
      // порог, сервер создаёт/обновляет aviev_watch_history — локальный state
      // должен увидеть это сразу, без рефреша. Раньше state.watch обновлялся
      // только при hydrate, и «Продолжить просмотр» показывал устаревшие данные.
      if (_historyQualifiedProgress(payload.seconds, payload.duration)) {
        ctx.state.watch = ctx.state.watch || {};
        const key = String(payload.mal_id);
        const prev = ctx.state.watch[key] || {};
        ctx.state.watch[key] = {
          ...prev,
          at: Date.now(),
          ep: payload.episode_num,
          time: payload.seconds,
          duration: payload.duration,
          total: payload.episodes_total || prev.total || 0,
          title: prev.title || payload.title || "",
          cover: prev.cover || payload.poster_url || "",
        };
      }
      return r;
    } catch (_) { return null; }
  },

  getRating(malId) { return Number(ctx.state.ratings[String(malId)] || 0); },
  async setRating(malId, score) {
    if (!ctx.state.user) { openGate("ratings"); return 0; }
    const key = String(malId);
    if (!score) {
      delete ctx.state.ratings[key];
      try { await _fetchJson(`/account/ratings/${malId}`, { method: "DELETE" }); } catch (_) {}
      return 0;
    }
    ctx.state.ratings[key] = score;
    try {
      await _fetchJson(`/account/ratings/${malId}`, { method: "PUT", body: { score } });
    } catch (_) { delete ctx.state.ratings[key]; }
    return score;
  },

  getDubPref(malId) { return ctx.state.dubPrefs[String(malId)] || null; },
  async setDubPref(malId, norm) {
    const id = String(malId);
    ctx.state.dubPrefs[id] = norm;
    if (!ctx.state.user) { _rememberGuestDub(id, norm); return; }
    try {
      await _fetchJson(`/account/dub-prefs/${malId}`, { method: "PUT", body: { dub_norm: norm } });
    } catch (_) {}
  },

  getAutoNext() { return !!ctx.state.autoNext; },
  async setAutoNext(on) {
    ctx.state.autoNext = !!on;
    if (!ctx.state.user) return;
    try { await _fetchJson("/account/settings", { method: "PUT", body: { autonext: !!on } }); }
    catch (_) {}
  },

  getWatch(malId) { return ctx.state.watch[String(malId)] || null; },
  getProgress(malId, ep) {
    const w = ctx.state.watch[String(malId)];
    return w && w.ep === ep ? w : null;
  },
  async ensureWatchEntry(malId, title, cover, ep, total = 0) {
    if (!ctx.state.user) return; // guests don't get account history
    const key = String(malId);
    const existing = ctx.state.watch[key];
    const ent = {
      at: Date.now(),
      ep: ep || (existing && existing.ep) || 1,
      title: (existing && existing.title) || title || "",
      cover: (existing && existing.cover) || cover || "",
      time: (existing && existing.time) || 0,
      duration: (existing && existing.duration) || 0,
      total: (existing && existing.total) || total || 0,
    };
    ctx.state.watch[key] = ent;
    return ent;
  },
  async saveProgress(malId, ep, time, dur) {
    if (!ctx.state.user) return;
    const key = String(malId);
    const w = ctx.state.watch[key];
    if (!w) return; // no entry = never confirmed as "watching" (10-sec guard elsewhere)
    const updated = { ...w, ep, time, duration: dur, at: Date.now() };
    ctx.state.watch[key] = updated;
    // Debounced: we only hit the network every ~8 s per title to keep the
    // server light, but we always carry the latest value in memory.
    _debouncedProgress(malId, ep, time, dur, updated);
  },
  async removeWatch(malId) {
    if (!ctx.state.user) return;
    delete ctx.state.watch[String(malId)];
    try { await _fetchJson(`/account/history/${malId}`, { method: "DELETE" }); } catch (_) {}
  },
  async markEpisodeUnwatched(malId, ep) {
    if (!ctx.state.user) return null;
    const key = String(malId);
    const queued = _progressQueues.get(key);
    if (queued) {
      clearTimeout(queued.timer);
      _progressQueues.delete(key);
    }

    const r = await _fetchJson(`/account/progress/${malId}/${ep}`, { method: "DELETE" });
    if (r?.watch) {
      ctx.state.watch[key] = {
        at: Date.parse(r.watch.updated_at || "") || Date.now(),
        title: r.watch.title || "",
        cover: r.watch.poster_url || "",
        ep: r.watch.last_episode || 1,
        time: r.watch.episode_seconds || 0,
        duration: r.watch.episode_duration || 0,
        total: r.watch.episodes_total || 0,
      };
    } else {
      delete ctx.state.watch[key];
    }

    ctx.state.lists = ctx.state.lists || {};
    ctx.state.favorites = ctx.state.favorites || {};
    if (r?.entry) {
      const prev = ctx.state.lists[key] || {};
      ctx.state.lists[key] = {
        status: r.entry.status,
        status_source: r.entry.status_source,
        is_favorite: !!r.entry.is_favorite,
        title: r.entry.title || prev.title || "",
        cover: r.entry.poster_url || prev.cover || "",
        at: Date.parse(r.entry.updated_at || r.entry.added_at || "") || Date.now(),
      };
      if (r.entry.is_favorite) {
        ctx.state.favorites[key] = {
          at: ctx.state.lists[key].at,
          title: ctx.state.lists[key].title,
          cover: ctx.state.lists[key].cover,
        };
      } else {
        delete ctx.state.favorites[key];
      }
    } else {
      delete ctx.state.lists[key];
      delete ctx.state.favorites[key];
    }

    window.dispatchEvent(new CustomEvent("av:list-updated", {
      detail: { mal_id: Number(malId), action: "episode-unwatched", entry: r?.entry || null },
    }));
    return r;
  },
  async clearAllHistory() {
    if (!ctx.state.user) return { ok: false };
    try {
      const r = await _fetchJson(`/account/history`, { method: "DELETE" });
      ctx.state.watch = {};
      return r || { ok: true };
    } catch (_) { return { ok: false }; }
  },
};

const _progressQueues = new Map();

function _clearProgressQueues() {
  for (const q of _progressQueues.values()) clearTimeout(q.timer);
  _progressQueues.clear();
}

function _debouncedProgress(malId, ep, time, dur, w) {
  const key = String(malId);
  const prev = _progressQueues.get(key);
  if (prev) clearTimeout(prev.timer);
  const flush = async () => {
    _progressQueues.delete(key);
    try {
      await _fetchJson(`/account/progress/${malId}`, {
        method: "PUT",
        body: {
          episode_num: ep,
          seconds: time,
          duration: dur,
          title: w.title || "",
          poster_url: w.cover || "",
          episodes_total: w.total || 0,
        },
      });
    } catch (_) { /* network hiccup — we'll retry on next timeupdate */ }
  };
  _progressQueues.set(key, { timer: setTimeout(flush, 8000) });
}

// Flush on tab close so the last 0-8 s of progress doesn't vanish.
window.addEventListener("beforeunload", () => {
  for (const [key, q] of _progressQueues.entries()) {
    clearTimeout(q.timer);
    // We can't await in beforeunload — fire-and-forget via keepalive.
    try {
      const w = ctx?.state?.watch?.[key];
      if (!w) continue;
      fetch(`${ctx.backendUrl}/account/progress/${key}`, {
        method: "PUT",
        credentials: "include",
        keepalive: true,
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          episode_num: w.ep,
          seconds: w.time,
          duration: w.duration,
          title: w.title || "",
          poster_url: w.cover || "",
          episodes_total: w.total || 0,
        }),
      });
    } catch (_) {}
  }
});

// ==== guest gate ====
let _gateEl = null;

// EDDAS dialog: centred on wide screens, a bottom sheet on phones that the finger can swipe away.
function _openDialog(className, inner, { onClose, label } = {}) {
  const wrap = document.createElement("div");
  wrap.className = `av-dialog ${className}`;
  wrap.innerHTML = `
    <div class="av-dialog__scrim" data-dialog-close></div>
    <div class="ed-dialog av-dialog__panel" role="dialog" aria-modal="true" ${label ? `aria-labelledby="${label}"` : ""}>
      <div class="app-sheet__grip av-dialog__grip" aria-hidden="true"></div>
      ${inner}
    </div>`;
  const opener = document.activeElement;
  const previousOverflow = document.body.style.overflow;
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    document.removeEventListener("keydown", onKey);
    document.body.style.overflow = previousOverflow;
    wrap.remove();
    if (opener?.isConnected) opener.focus({ preventScroll: true });
    onClose?.();
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  wrap.addEventListener("click", (e) => { if (e.target.closest("[data-dialog-close]")) close(); });
  document.addEventListener("keydown", onKey);
  document.body.appendChild(wrap);
  document.body.style.overflow = "hidden";
  const panel = wrap.querySelector(".av-dialog__panel");
  SheetDrag.bind(panel, {
    scrim: wrap.querySelector(".av-dialog__scrim"),
    active: () => window.matchMedia("(max-width: 767px)").matches,
    close,
  });
  (panel.querySelector("[autofocus]") || panel.querySelector(".ed-btn, button"))?.focus({ preventScroll: true });
  return { el: wrap, close };
}

// Вход через Anime Social — полный переход на сервер (/auth/as/start), а не SPA-маршрут:
// абсолютный адрес перехватчик ссылок пропускает
function _asLoginUrl(next) {
  const target = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
  return new URL(`${ctx.backendUrl || ""}/auth/as/start?next=${encodeURIComponent(target)}`, location.href).href;
}

// Кто вошёл в Anime Social в этом браузере: соцсеть отвечает по своей cookie входа, а читать ответ
// (CORS) можно только сайтам-клиентам. Пользователь соцсети, false — не вошёл, null — неизвестно.
let _asHintPromise = null;
function _asHint() {
  if (!authConfig.as_login || !authConfig.as_api_url) return Promise.resolve(null);
  if (!_asHintPromise) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 3000);
    _asHintPromise = fetch(`${authConfig.as_api_url}/auth/web/session`, { credentials: "include", signal: ctrl.signal })
      .then(r => (r.ok ? r.json() : null))
      .then(j => {
        if (!j?.ok || !j.data) return null;
        return j.data.logged_in && j.data.user?.id ? j.data.user : false;
      })
      .catch(() => null)
      .finally(() => clearTimeout(timer));
  }
  return _asHintPromise;
}

function _asUserName(user) {
  return `${user.first_name || ""} ${user.last_name || ""}`.trim() || user.alias || "Пользователь";
}

function _asUserAvatar(user) {
  return user.avatar?.medium || user.avatar?.small || user.avatar?.original || "";
}

// «Имя Фамилия, это вы?» — гостю, который вошёл в Anime Social. «Нет» не спрашивает этот аккаунт 30 дней
const AS_HINT_DISMISS_KEY = "av_as_hint_dismissed";
const AS_HINT_DISMISS_DAYS = 30;

function _asHintDismissed(uid) {
  try {
    const saved = JSON.parse(localStorage.getItem(AS_HINT_DISMISS_KEY) || "null");
    return !!saved && saved.uid === uid && saved.until > Date.now();
  } catch (_) { return false; }
}

function _dismissAsHint(uid) {
  try { localStorage.setItem(AS_HINT_DISMISS_KEY, JSON.stringify({ uid, until: Date.now() + AS_HINT_DISMISS_DAYS * 864e5 })); } catch (_) {}
}

async function maybeOfferAsAccount() {
  if (ctx.state.user || !authConfig.as_login || location.pathname.startsWith("/login")) return;
  const user = await _asHint();
  if (!user || ctx.state.user || _asHintDismissed(user.id) || location.pathname.startsWith("/login")) return;
  if (document.querySelector(".as-hint")) return;
  const name = _asUserName(user);
  const site = socialSiteName();
  const card = document.createElement("section");
  card.className = "as-hint";
  card.setAttribute("aria-label", `Вход через ${site}`);
  card.innerHTML = `
    ${_avatarHtml({ name })}
    <div class="as-hint__body">
      <p class="as-hint__title">${esc(name)}, это вы?</p>
      <p class="as-hint__text">Продолжить под этим аккаунтом ${esc(site)}?</p>
      <div class="as-hint__actions">
        <a class="ed-btn ed-btn--primary ed-btn--sm" href="${esc(_asLoginUrl(location.pathname + location.search))}" data-hint-yes>Продолжить</a>
        <button type="button" class="ed-btn ed-btn--ghost ed-btn--sm" data-hint-no>Нет</button>
      </div>
    </div>`;
  document.body.appendChild(card);
  _upgradeAvatars(card, _asUserAvatar(user));
  // Возврат — на страницу, открытую в момент нажатия: по сайту могли перейти после показа
  card.querySelector("[data-hint-yes]").addEventListener("click", e => {
    e.currentTarget.href = _asLoginUrl(location.pathname + location.search);
  });
  card.querySelector("[data-hint-no]").addEventListener("click", () => {
    _dismissAsHint(user.id);
    card.remove();
  });
}

const AS_LOGIN_ERRORS = {
  denied: site => `Вход отменён на странице ${site}.`,
  expired: site => `Время на вход истекло. Нажмите «Войти через ${site}» ещё раз.`,
  banned: site => `Аккаунт заблокирован в ${site}.`,
  error: site => `Не удалось войти через ${site}. Попробуйте ещё раз или войдите по логину и паролю.`,
};

export function openGate(kind) {
  const copy = _gateCopy(kind);
  closeGate();
  const here = location.pathname + location.search;
  const loginAction = authConfig.as_login
    ? `<a class="ed-btn ed-btn--primary" href="${esc(_asLoginUrl(here))}" autofocus>Войти через ${esc(socialSiteName())}</a>`
    : `<a class="ed-btn ed-btn--primary" href="/login" data-dialog-close autofocus>Войти</a>`;
  const dialog = _openDialog("gate-overlay", `
    <button type="button" class="ed-iconbtn av-dialog__close gate-close" aria-label="Закрыть" title="Закрыть" data-dialog-close><i class="fas fa-xmark" aria-hidden="true"></i></button>
    <h2 class="ed-dialog__title" id="avGateTitle">${esc(copy.title)}</h2>
    <p class="ed-dialog__text">${copy.body}</p>
    <ul class="gate-points">
      ${copy.points.map(p => `<li><i class="fas fa-check" aria-hidden="true"></i>${esc(p)}</li>`).join("")}
    </ul>
    <div class="ed-dialog__actions${authConfig.as_login ? " gate-actions--stack" : ""}">
      <a class="ed-btn ed-btn--secondary" href="${esc(registerUrl())}" target="_blank" rel="noopener">Создать аккаунт</a>
      ${loginAction}
    </div>`, { label: "avGateTitle", onClose: () => { _gateEl = null; } });
  _gateEl = dialog;
}

export function closeGate() {
  if (_gateEl) { const gate = _gateEl; _gateEl = null; gate.close(); }
}

function _gateCopy(kind) {
  const presets = {
    favorites: {
      title: "Избранное — только для вошедших",
      body: "После входа тайтлы, которые вы отметили звёздочкой, сохранятся в вашем аккаунте и будут доступны с любого устройства.",
      points: [
        "Синхронизация между устройствами",
        "Личный список, виден только вам",
        "Быстрый переход с любой страницы",
      ],
    },
    lists: {
      title: "Списки — только для вошедших",
      body: "Чтобы размечать тайтлы статусами «Смотрю», «В Планах», «Просмотрено» и т.д., нужна авторизация.",
      points: [
        "Статус в один клик со страницы аниме",
        "Авто-попадание в «Смотрю» после 10 минут",
        "Отдельный раздел «Списки» в аккаунте",
      ],
    },
    settings: {
      title: "Настройки — только для вошедших",
      body: "Настройки (приватность, автодобавление) хранятся в аккаунте — войдите, чтобы их менять.",
      points: ["Сохранение на сервере", "Общие для всех устройств"],
    },
    ratings: {
      title: "Оценки — для вошедших",
      body: "Чтобы ваша оценка сохранялась и показывалась рядом с публичным рейтингом MyAnimeList, нужна авторизация.",
      points: ["Сохраняется в вашем аккаунте", "Можно менять в любой момент"],
    },
    history: {
      title: "История просмотра — для вошедших",
      body: "После входа сайт запомнит серию и позицию, на которой вы остановились, и продолжит с того же места на любом устройстве.",
      points: [
        "Продолжение с той же серии и минуты",
        "Отдельная страница «Смотрю»",
        "Работает даже после перезагрузки",
      ],
    },
    watching: {
      title: "Список «Смотрю» — для вошедших",
      body: "Чтобы отслеживать, что вы сейчас смотрите, и не терять место в серии — авторизуйтесь. Вся персональная история живёт в аккаунте.",
      points: ["Актуальный список сериалов", "Кнопка «Продолжить смотреть»"],
    },
    progress: {
      title: "Сохранение позиции — для вошедших",
      body: "Мы не храним позицию в серии для гостей — это было бы обманом: на другом устройстве она бы всё равно не работала. Войдите, чтобы сохранение было настоящим.",
      points: ["Позиция и выбранная серия", "Синхронизация между устройствами"],
    },
  };
  return presets[kind] || presets.favorites;
}

// ==== views (rendered by the app router) ====
export async function viewLogin(params) {
  ctx.setActive("login");
  document.querySelector(".as-hint")?.remove();
  const next = params.get("next") || "/";
  const site = esc(socialSiteName());
  const asLogin = !!authConfig.as_login;
  const asError = AS_LOGIN_ERRORS[params.get("as")]?.(socialSiteName()) || "";
  // Вошедшему в Anime Social — кнопка с его именем, остальным — сразу почта. Соцсеть отвечает быстро,
  // но ждать её дольше 0,7 секунды не будем: без ответа показываем кнопку без имени
  const asUser = asLogin ? await Promise.race([_asHint(), new Promise(r => setTimeout(() => r(null), 700))]) : null;
  if (!location.pathname.startsWith("/login")) return;  // за время ожидания ушли на другую страницу
  const firstView = !asLogin || (asUser === false && !asError) ? "mail" : "as";
  ctx.outlet.innerHTML = `
    <div class="auth">
      <aside class="auth__hero">
        <span class="auth__wordmark">AsoPlay</span>
        <div class="auth__hero-body">
          <h1 class="auth__headline">Аниме онлайн в русской озвучке</h1>
          <p class="auth__lead">Войдите аккаунтом ${site}: списки, история просмотра и оценки будут на всех ваших устройствах.</p>
        </div>
        <nav class="auth__links" aria-label="Ссылки">
          <a href="${esc(socialSiteUrl())}" target="_blank" rel="noopener">${site}</a>
          <a href="/">Каталог без входа</a>
        </nav>
      </aside>
      <div class="auth__main">
        <div class="auth__box">
          <div class="auth__brand">
            <span class="auth__wordmark">AsoPlay</span>
            <p class="auth__brand-lead">Аниме онлайн в русской озвучке</p>
          </div>
          <h2 class="auth__title">Вход</h2>
          ${asLogin ? `
          <div class="auth-view" data-auth-view="as" ${firstView === "as" ? "" : "hidden"}>
            <p class="auth__sub">${asUser ? `Вы уже вошли в ${site}: продолжите под этим аккаунтом.` : `Если вы уже вошли в ${site}, вход займёт одно нажатие.`}</p>
            <a class="ed-btn ed-btn--primary ed-btn--lg ed-btn--block auth-as" href="${esc(_asLoginUrl(next))}">${asUser
              ? `${_avatarHtml({ name: _asUserName(asUser) }, "auth-as__ava")}<span class="auth-as__label">Продолжить как ${esc(_asUserName(asUser))}</span>`
              : `<i class="fas fa-right-to-bracket" aria-hidden="true"></i><span class="auth-as__label">Войти через ${site}</span>`}</a>
            ${asError ? `<div class="ed-note ed-note--danger auth-as-error" role="alert"><i class="fas fa-circle-exclamation" aria-hidden="true"></i><div>${esc(asError)}</div></div>` : ""}
            <button type="button" class="auth-switch" data-auth-switch="mail">Войти через почту</button>
          </div>` : ""}
          <div class="auth-view" data-auth-view="mail" ${firstView === "mail" ? "" : "hidden"}>
            <p class="auth__sub">Логин и пароль те же, что в ${site}. Регистрация проходит на сайте соцсети.</p>
            <form class="ed-form auth-form" id="avLoginForm" autocomplete="on" novalidate>
              <div class="ed-field">
                <label class="ed-field__label" for="avLoginInput">Логин или электронная почта</label>
                <div class="ed-field__wrap"><input class="ed-input" type="text" name="login" id="avLoginInput" autocomplete="username" autocapitalize="off" spellcheck="false" required ${firstView === "mail" ? "autofocus" : ""} /></div>
              </div>
              <div class="ed-field">
                <label class="ed-field__label" for="avLoginPass">Пароль</label>
                <div class="ed-field__wrap">
                  <input class="ed-input ed-input--action" type="password" name="password" id="avLoginPass" autocomplete="current-password" required />
                  <button type="button" class="ed-field__action" id="avPassToggle" aria-controls="avLoginPass" aria-pressed="false">Показать</button>
                </div>
              </div>
              <div class="ed-note ed-note--danger auth-error" id="avLoginErr" role="alert" ${asError && !asLogin ? "" : "hidden"}><i class="fas fa-circle-exclamation" aria-hidden="true"></i><div id="avLoginErrText">${asLogin ? "" : esc(asError)}</div></div>
              <button type="submit" class="ed-btn ed-btn--primary ed-btn--lg ed-btn--block auth-submit">Войти</button>
            </form>
            ${asLogin ? `<button type="button" class="auth-switch" data-auth-switch="as">Войти через ${site}</button>` : ""}
          </div>
          <p class="auth__switch">Нет аккаунта? <a class="auth-link" href="${esc(registerUrl())}" target="_blank" rel="noopener">Создать в ${site}</a></p>
        </div>
      </div>
    </div>`;
  if (asUser) _upgradeAvatars(ctx.outlet, _asUserAvatar(asUser));
  ctx.outlet.querySelectorAll("[data-auth-switch]").forEach(btn => btn.addEventListener("click", () => {
    const view = btn.dataset.authSwitch;
    ctx.outlet.querySelectorAll("[data-auth-view]").forEach(box => { box.hidden = box.dataset.authView !== view; });
    (view === "mail" ? document.querySelector("#avLoginInput") : document.querySelector(".auth-as"))?.focus();
  }));
  const pass = document.querySelector("#avLoginPass");
  document.querySelector("#avPassToggle")?.addEventListener("click", (e) => {
    const show = pass.type === "password";
    pass.type = show ? "text" : "password";
    e.currentTarget.textContent = show ? "Скрыть" : "Показать";
    e.currentTarget.setAttribute("aria-pressed", String(show));
  });
  document.querySelector("#avLoginForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.querySelector("#avLoginErr");
    const submit = e.target.querySelector(".auth-submit");
    err.hidden = true;
    const fd = new FormData(e.target);
    submit.classList.add("is-loading");
    try {
      await login(fd.get("login").toString().trim(), fd.get("password").toString());
      ctx.nav(next && next.startsWith("/") ? next : "/");
    } catch (exc) {
      err.hidden = false;
      document.querySelector("#avLoginErrText").textContent = _loginErrorMessage(exc);
    } finally {
      submit.classList.remove("is-loading");
    }
  });
}

export const LIST_LABELS = {
  watching:  { one: "Смотрю",        many: "Смотрю" },
  planned:   { one: "В планах",      many: "В планах" },
  completed: { one: "Просмотрено",   many: "Просмотрено" },
  dropped:   { one: "Брошено",       many: "Брошено" },
  postponed: { one: "Отложено",      many: "Отложено" },
  favorite:  { one: "Избранное",     many: "Избранное" },
};
const LIST_ICONS = {
  watching: "fa-eye", planned: "fa-bookmark", completed: "fa-circle-check",
  postponed: "fa-pause", dropped: "fa-ban", favorite: "fa-star",
};
const LIST_ORDER = ["watching", "planned", "completed", "postponed", "dropped", "favorite"];
// «Избранное» (favorite) живёт отдельно — /my/favorites. В /my/lists
// показываем только взаимоисключающие статусы.
const LIST_ORDER_MYLISTS = ["watching", "planned", "completed", "postponed", "dropped"];

function _plural(n, forms) {
  const a = Math.abs(n) % 100, b = a % 10;
  return forms[a > 10 && a < 20 ? 2 : b === 1 ? 0 : b >= 2 && b <= 4 ? 1 : 2];
}
const TITLE_FORMS = ["тайтл", "тайтла", "тайтлов"];

function _pageHead(title, sub = "", actions = "") {
  return `
    <div class="page-head">
      <div class="page-head__text">
        <h1 class="page-head__title">${esc(title)}</h1>
        ${sub ? `<p class="page-head__sub">${esc(sub)}</p>` : ""}
      </div>
      ${actions ? `<div class="page-head__actions">${actions}</div>` : ""}
    </div>`;
}

function _listCards(items) {
  if (!items.length) return ctx.errorPage({
    title: "В избранном пока пусто",
    message: "Отмечайте тайтлы звёздочкой на их странице, и они появятся здесь.",
    action: { href: "/top", label: "Открыть топ" },
    icon: "far fa-star",
  });
  const g = document.createElement("div");
  g.className = "ed-grid av-grid";
  items.forEach(v => g.appendChild(ctx.makeCard({
    mal_id: v.mal_id, title: v.title || "", _ru: v.title || "",
    images: { jpg: { large_image_url: v.poster_url || v.cover } },
  })));
  return g.outerHTML;
}

async function _listsFetch(status) {
  try {
    const params = status ? `?status=${encodeURIComponent(status)}` : "";
    return await _fetchJson(`/account/lists${params}`);
  } catch (_) { return []; }
}

export async function viewMyFavorites() {
  ctx.setActive("my-favorites");
  if (!ctx.state.user) { openGate("favorites"); ctx.nav("/login?next=/my/favorites"); return; }
  ctx.outlet.innerHTML = `<div class="page-stack">${_pageHead("Избранное")}<div>${ctx.loadingBlock()}</div></div>`;
  const list = await _listsFetch("favorite");
  ctx.outlet.innerHTML = `
    <div class="page-stack">
      ${_pageHead("Избранное", list.length ? `${list.length} ${_plural(list.length, TITLE_FORMS)}` : "")}
      <section class="ed-panel"><div class="ed-panel__body" id="avFavGrid">${_listCards(list)}</div></section>
    </div>`;
  // После рендера добавим русские названия и добьём обложки из Shikimori.
  const grid = document.querySelector("#avFavGrid .av-grid");
  if (grid && ctx.enrichWithShiki) ctx.enrichWithShiki(grid, list);
}

export async function viewMyContinue() {
  ctx.setActive("my-continue");
  if (!ctx.state.user) { openGate("watching"); ctx.nav("/login?next=/my/continue"); return; }

  // Всегда тянем свежий /account/history перед рендером. Локальный state.watch
  // заполняется только при бутстрапе страницы, и если пользователь в той же
  // сессии посмотрел что-то и вернулся сюда SPA-переходом — локальные данные
  // устарели. Бэкенд отдаёт только реальные точки продолжения: 5+ минут
  // просмотра или завершённые короткие серии, поэтому повторно фильтровать
  // порог здесь не нужно.
  try {
    const fresh = await _fetchJson("/account/history");
    ctx.state.watch = Object.fromEntries(
      (fresh || []).map(h => [String(h.mal_id), {
        at: Date.parse(h.updated_at || "") || Date.now(),
        title: h.title || "",
        cover: h.poster_url || "",
        ep: h.last_episode || 1,
        time: h.episode_seconds || 0,
        duration: h.episode_duration || 0,
        total: h.episodes_total || 0,
      }])
    );
  } catch (_) { /* используем то что есть в state.watch, если сеть легла */ }

  const isEpFinished = (v) => v.duration > 0
    && (v.time >= v.duration * 0.92 || v.time >= v.duration - 90);
  const nextEp = (v) => isEpFinished(v) ? v.ep + 1 : v.ep;

  // Убираем тайтлы, все серии которых уже досмотрены (total>0 и nextEp за пределами).
  const entries = Object.entries(ctx.state.watch)
    .map(([id, v]) => ({ mal_id: Number(id), ...v }))
    .filter(v => !(v.total > 0 && nextEp(v) > v.total))
    .sort((a, b) => (b.at || 0) - (a.at || 0));
  if (!entries.length) {
    ctx.outlet.innerHTML = `<div class="page-stack">${_pageHead("Продолжить просмотр")}<section class="ed-panel">` + ctx.errorPage({
      title: "Пока нечего продолжать",
      message: "Посмотрите аниме хотя бы 5 минут или досмотрите короткую серию, и тайтл появится здесь с того же места.",
      action: { href: "/", label: "Выбрать аниме" },
      icon: "fas fa-clock-rotate-left",
    }) + `</section></div>`;
    return;
  }
  ctx.outlet.innerHTML = `
    <div class="page-stack">
      ${_pageHead("Продолжить просмотр", `${entries.length} ${_plural(entries.length, TITLE_FORMS)}`,
        `<button type="button" class="ed-btn ed-btn--ghost continue-clear" id="avContinueClear" aria-label="Очистить список" title="Очистить список"><i class="fas fa-trash-can" aria-hidden="true"></i><span>Очистить список</span></button>`)}
      <section class="ed-panel"><div class="ed-panel__body"><div class="ed-grid av-grid" id="g"></div></div></section>
    </div>`;
  const g = document.querySelector("#g");
  entries.forEach(v => {
    const displayEp = nextEp(v);
    const finished = isEpFinished(v);
    const wrap = document.createElement("div");
    wrap.className = "av-card-wrap";
    wrap.dataset.mal = String(v.mal_id);
    const src = v.cover ? ctx.imgUrl(v.cover) : "";
    // The poster already names the episode; the caption says what is left of it.
    const leftMin = v.duration > 0 ? Math.max(1, Math.round((v.duration - v.time) / 60)) : 0;
    const metaText = finished ? "Следующая серия" : leftMin ? `Осталось ${leftMin} мин` : "Продолжить просмотр";
    const pct = !finished && v.duration > 0 ? Math.min(100, Math.round(v.time / v.duration * 100)) : 0;
    wrap.innerHTML = `
      <a class="ed-tile av-card" href="/anime/${v.mal_id}/" data-mal="${v.mal_id}">
        <span class="ed-tile__media av-card-img">
          <i class="fas fa-film" aria-hidden="true"></i>
          <img src="${esc(src)}" alt="${esc(v.title || "")}"
               onerror="this.dataset.broken='1';this.removeAttribute('src');">
          <span class="tile-flag">${displayEp}-я серия</span>
          ${pct ? `<span class="tile-progress" aria-hidden="true"><i style="width:${pct}%"></i></span>` : ""}
        </span>
        <span class="ed-tile__title av-card-title" title="${esc(v.title || "")}">${esc(v.title || "")}</span>
        <span class="ed-tile__meta av-card-meta">${esc(metaText)}</span>
      </a>
      <button type="button" class="ed-iconbtn tile-action card-remove" title="Убрать из списка"
              data-mal="${v.mal_id}" aria-label="Убрать «${esc(v.title || "")}» из списка">
        <i class="fas fa-xmark" aria-hidden="true"></i>
      </button>`;
    g.appendChild(wrap);
  });
  if (ctx.enrichWithShiki) ctx.enrichWithShiki(g, entries);

  // Per-title remove
  g.addEventListener("click", async (e) => {
    const btn = e.target.closest(".card-remove");
    if (!btn) return;
    e.preventDefault();
    e.stopPropagation();
    const id = Number(btn.dataset.mal);
    btn.disabled = true;
    await store.removeWatch(id);
    const wrap = btn.closest(".av-card-wrap");
    if (wrap) wrap.remove();
    // если стало пусто — перерисуем страницу (empty-state)
    if (!Object.keys(ctx.state.watch).filter(k => (ctx.state.watch[k].time || 0) >= 300).length) {
      viewMyContinue();
    }
  });

  // Clear-all with countdown-confirm
  document.querySelector("#avContinueClear")?.addEventListener("click", () => {
    confirmWithCountdown({
      title: "Очистить список «Продолжить просмотр»?",
      body: "Прогресс просмотра удалится с сервера на всех устройствах. Вернуть его нельзя.",
      countdownSec: 5,
      yesLabel: "Очистить список",
      noLabel: "Отмена",
      onConfirm: async () => {
        const r = await store.clearAllHistory();
        viewMyContinue();
        return r;
      },
    });
  });
}

// ==== confirm dialog with countdown ====
//
// Показывает модалку «Вы уверены?». Кнопка «Да» становится доступной только
// после обратного отсчёта (по ТЗ — 5 сек). Полезно для разрушительных
// операций: пользователь успевает одуматься, чтобы не кликнуть случайно.
export function confirmWithCountdown({
  title = "Подтверждение",
  body  = "",
  countdownSec = 5,
  yesLabel = "Да",
  noLabel = "Отмена",
  onConfirm,
}) {
  let tick = 0;
  const dialog = _openDialog("confirm-overlay", `
    <h2 class="ed-dialog__title" id="avConfirmTitle">${esc(title)}</h2>
    <p class="ed-dialog__text">${esc(body)}</p>
    <div class="ed-dialog__actions">
      <button type="button" class="ed-btn ed-btn--ghost" id="avConfirmNo" data-dialog-close autofocus>${esc(noLabel)}</button>
      <button type="button" class="ed-btn ed-btn--danger confirm-yes" id="avConfirmYes" disabled>
        <span class="confirm-yes-label">${esc(yesLabel)}</span>
        <span class="confirm-countdown" id="avConfirmTimer">(${countdownSec})</span>
      </button>
    </div>`, { label: "avConfirmTitle", onClose: () => clearInterval(tick) });
  const wrap = dialog.el;

  let remaining = countdownSec;
  const timerEl = wrap.querySelector("#avConfirmTimer");
  const yesBtn = wrap.querySelector("#avConfirmYes");
  const labelEl = wrap.querySelector(".confirm-yes-label");

  tick = setInterval(() => {
    remaining -= 1;
    if (remaining <= 0) {
      clearInterval(tick);
      timerEl.textContent = "";
      yesBtn.disabled = false;
      yesBtn.classList.add("ready");
      labelEl.textContent = yesLabel;
    } else {
      timerEl.textContent = `(${remaining})`;
    }
  }, 1000);

  yesBtn.addEventListener("click", async () => {
    if (yesBtn.disabled) return;
    yesBtn.classList.add("is-loading");
    try { if (onConfirm) await onConfirm(); } finally { dialog.close(); }
  });
}

// Старые имена на случай прямого вызова из app.js legacy-путей.
export const viewMyWatching = viewMyContinue;
export const viewMyHistory  = viewMyContinue;

// Основной раздел «Списки» — вкладки по статусам, плюс отдельный таб «Любимые».
export async function viewMyLists(activeStatus) {
  ctx.setActive("my-lists");
  if (!ctx.state.user) { openGate("lists"); ctx.nav("/login?next=/my/lists"); return; }
  const allowed = new Set(LIST_ORDER_MYLISTS);
  const status = allowed.has(activeStatus) ? activeStatus : "watching";
  ctx.outlet.innerHTML = `<div class="page-stack">${_pageHead("Списки")}${ctx.loadingBlock()}</div>`;
  let counts = { watching: 0, planned: 0, completed: 0, dropped: 0, postponed: 0, favorite: 0 };
  try { counts = await _fetchJson("/account/lists/counts"); } catch (_) {}
  const items = await _listsFetch(status);

  const tabs = LIST_ORDER_MYLISTS.map(s => {
    const label = LIST_LABELS[s].one;
    const c = counts[s] || 0;
    const active = s === status;
    return `<a class="ed-tabs__item${active ? " is-active" : ""}" href="/my/lists/${s}" data-status="${s}"${active ? ' aria-current="page"' : ""}>
      ${esc(label)}${c ? `<span class="ed-tabs__count">${c}</span>` : ""}
    </a>`;
  }).join("");

  const cardsHtml = items.length
    ? `<div class="ed-grid av-grid">${items.map(v => _listCardWithActions(v, status)).join("")}</div>`
    : _listEmptyState(status);

  ctx.outlet.innerHTML = `
    <div class="page-stack">
      ${_pageHead("Списки")}
      <section class="ed-panel list-panel">
        <div class="ed-panel__body">
          <nav class="ed-tabs tabs-wrap" id="avListTabs" aria-label="Статус в списке">${tabs}</nav>
          <div id="avListGrid">${cardsHtml}</div>
        </div>
      </section>
    </div>`;
  const grid = document.querySelector("#avListGrid .av-grid");
  if (grid) {
    if (ctx.enrichWithShiki) ctx.enrichWithShiki(grid, items);
    _bindListCardActions(grid, status);
  }
}

function _listEmptyState(status) {
  return ctx.errorPage({
    title: `В списке «${LIST_LABELS[status].one}» пока пусто`,
    message: "Откройте страницу аниме и выберите статус в меню «В список».",
    action: { href: "/top", label: "Открыть топ" },
    icon: "fas fa-list",
  });
}

function _bumpListCount(status, delta) {
  const tab = document.querySelector(`#avListTabs [data-status="${status}"]`);
  if (!tab) return;
  let badge = tab.querySelector(".ed-tabs__count");
  const next = Math.max(0, Number(badge?.textContent || 0) + delta);
  if (!next) { badge?.remove(); return; }
  if (!badge) {
    badge = document.createElement("span");
    badge.className = "ed-tabs__count";
    tab.appendChild(badge);
  }
  badge.textContent = String(next);
}

// Карточка со всплывающим меню «переместить/удалить». Рендерится как строка
// HTML, обработчики вешаются через делегирование в _bindListCardActions.
function _listCardWithActions(v, currentStatus) {
  const mal = Number(v.mal_id);
  const src = v.poster_url ? ctx.imgUrl(v.poster_url) : "";
  const title = v.title || `#${mal}`;
  const autoBadge = v.status_source === "auto"
    ? `<span class="tile-flag" title="Статус выставлен автоматически">Автостатус</span>`
    : "";
  // Опции перемещения = все статусы КРОМЕ текущего. Плюс «Убрать из списков».
  const moveOptions = LIST_ORDER_MYLISTS.filter(s => s !== currentStatus).map(s =>
    `<button type="button" class="ed-menu__item lp-option" role="menuitem" data-action="move" data-status="${s}"><i class="fas ${LIST_ICONS[s]}" aria-hidden="true"></i>${esc(LIST_LABELS[s].one)}</button>`
  ).join("");
  return `
    <div class="av-card-wrap" data-mal="${mal}">
      <a class="ed-tile av-card" href="/anime/${mal}/" data-mal="${mal}">
        <span class="ed-tile__media av-card-img">
          <i class="fas fa-film" aria-hidden="true"></i>
          <img src="${esc(src)}" alt="${esc(title)}"
               onerror="this.dataset.broken='1';this.removeAttribute('src');">
          ${autoBadge}
        </span>
        <span class="ed-tile__title av-card-title" title="${esc(title)}">${esc(title)}</span>
        <span class="ed-tile__meta av-card-meta">${esc(LIST_LABELS[currentStatus]?.one || "")}</span>
      </a>
      <button type="button" class="ed-iconbtn tile-action card-action-menu" title="Действия"
              data-mal="${mal}" aria-haspopup="menu" aria-expanded="false" aria-label="Действия с «${esc(title)}»">
        <i class="fas fa-ellipsis" aria-hidden="true"></i>
      </button>
      <div class="ed-menu card-action-pop" role="menu" hidden>
        <div class="card-action-title">Перенести в список</div>
        ${moveOptions}
        <div class="ed-menu__divider" role="separator"></div>
        <button type="button" class="ed-menu__item is-danger lp-option lp-danger" role="menuitem" data-action="remove"><i class="fas fa-xmark" aria-hidden="true"></i>Убрать из списков</button>
      </div>
    </div>`;
}

// A menu anchored to a narrow tile can stick out of the screen: move it back inside, like FitMenu() in AnimeSocial.
function _fitMenu(pop) {
  pop.style.left = pop.style.right = "";
  const gap = 8;
  const r = pop.getBoundingClientRect();
  // Read the offset before touching the styles: right: auto alone sends the menu to its static position.
  const left = pop.offsetLeft;
  if (r.left < gap) {
    pop.style.right = "auto";
    pop.style.left = `${left + gap - r.left}px`;
  } else if (r.right > window.innerWidth - gap) {
    pop.style.right = "auto";
    pop.style.left = `${left - (r.right - window.innerWidth + gap)}px`;
  }
}

function _syncCardMenus(grid) {
  grid.querySelectorAll(".av-card-wrap").forEach(wrap => {
    const pop = wrap.querySelector(".card-action-pop");
    wrap.classList.toggle("is-menu-open", !!pop && !pop.hidden);
    wrap.querySelector(".card-action-menu")?.setAttribute("aria-expanded", String(!!pop && !pop.hidden));
  });
}

function _bindListCardActions(grid, currentStatus) {
  grid.addEventListener("click", async (e) => {
    const menuBtn = e.target.closest(".card-action-menu");
    if (menuBtn) {
      e.preventDefault();
      e.stopPropagation();
      const wrap = menuBtn.closest(".av-card-wrap");
      const pop = wrap.querySelector(".card-action-pop");
      const opening = pop.hidden;
      // Закроем все другие открытые меню
      grid.querySelectorAll(".card-action-pop").forEach(p => { if (p !== pop) p.hidden = true; });
      pop.hidden = !opening;
      _syncCardMenus(grid);
      if (opening) {
        _fitMenu(pop);
        pop.querySelector(".ed-menu__item")?.focus({ preventScroll: true });
      }
      return;
    }
    const opt = e.target.closest(".card-action-pop .lp-option");
    if (!opt) return;
    e.preventDefault();
    e.stopPropagation();
    const wrap = opt.closest(".av-card-wrap");
    const pop = wrap.querySelector(".card-action-pop");
    const mal = Number(wrap.dataset.mal);
    const action = opt.dataset.action;
    pop.hidden = true;
    _syncCardMenus(grid);
    const newStatus = action === "move" ? opt.dataset.status : null;
    const entry = ctx.state.lists?.[String(mal)] || {};
    const res = await store.setListStatus(mal, newStatus, action === "move" ? { title: entry.title, cover: entry.cover } : {});
    if (!res) return;
    // Тайтл больше не в текущем списке: карточка уходит, счётчики вкладок меняются, пустой список получает пустое состояние.
    wrap.classList.add("slide-out");
    setTimeout(() => {
      wrap.remove();
      _bumpListCount(currentStatus, -1);
      if (newStatus) _bumpListCount(newStatus, 1);
      const box = document.querySelector("#avListGrid");
      if (box && !box.querySelector(".av-card-wrap")) box.innerHTML = _listEmptyState(currentStatus);
    }, 180);
  });
  // Клик мимо — закрыть открытые меню. Рендер списков может повторяться, поэтому
  // держим ровно один document-listener и снимаем предыдущий перед новым.
  if (window._avListCardCloser) {
    document.removeEventListener("click", window._avListCardCloser);
  }
  window._avListCardCloser = (e) => {
    if (!document.contains(grid)) {
      document.removeEventListener("click", window._avListCardCloser);
      window._avListCardCloser = null;
      return;
    }
    if (e.target.closest(".card-action-menu")) return;
    if (e.target.closest(".card-action-pop")) return;
    grid.querySelectorAll(".card-action-pop").forEach(p => p.hidden = true);
    _syncCardMenus(grid);
  };
  document.addEventListener("click", window._avListCardCloser);
  // Escape closes an open card menu and returns focus to its «⋯» button.
  grid.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    const open = grid.querySelector(".card-action-pop:not([hidden])");
    if (!open) return;
    open.hidden = true;
    _syncCardMenus(grid);
    open.closest(".av-card-wrap")?.querySelector(".card-action-menu")?.focus();
  });
}

export async function viewMySettings(tab) {
  ctx.setActive("my-settings");
  if (!ctx.state.user) { openGate("settings"); ctx.nav("/login?next=/my/settings"); return; }
  const s = ctx.state.user;
  const privacy = ctx.state.privacy || { hide_lists: false, hide_activity: false };
  const autoLists = ctx.state.autoAddLists !== false;
  const activeTab = tab === "privacy" ? "privacy" : "general";
  const tabLink = (key, href, label) => `<a class="ed-tabs__item${activeTab === key ? " is-active" : ""}" href="${href}"${activeTab === key ? ' aria-current="page"' : ""}>${label}</a>`;
  const theme = getTheme();
  const themeOption = (value) => `<button type="button" class="ed-tabs__item${theme === value ? " is-active" : ""}" role="radio" aria-checked="${theme === value}" data-theme-value="${value}">${esc(themeLabel(value))}</button>`;

  ctx.outlet.innerHTML = `
    <div class="page-stack settings-page">
      ${_pageHead("Настройки")}
      <nav class="ed-tabs ed-tabs--line" aria-label="Разделы настроек">
        ${tabLink("general", "/my/settings", "Общие")}${tabLink("privacy", "/my/settings/privacy", "Конфиденциальность")}
      </nav>
      ${activeTab === "general" ? `
        <section class="ed-panel" aria-labelledby="avSetAccount">
          <div class="ed-panel__head"><span id="avSetAccount">Аккаунт</span></div>
          <div class="ed-panel__body settings-body">
            <dl class="ed-kv">
              <dt>Имя</dt><dd>${esc(s.name || s.login || s.email || "")}</dd>
              ${s.handle ? `<dt>Профиль</dt><dd><a href="${esc(s.profile_path || "/@" + s.handle)}">@${esc(s.handle)}</a></dd>` : ""}
              ${s.email ? `<dt>Электронная почта</dt><dd>${esc(s.email)}</dd>` : ""}
            </dl>
            <div class="ed-note"><i class="fas fa-circle-info" aria-hidden="true"></i><div>Имя и пароль меняются в <a href="${esc(socialSiteUrl())}" target="_blank" rel="noopener">кабинете ${esc(socialSiteName())}</a>.</div></div>
          </div>
        </section>
        <section class="ed-panel" aria-labelledby="avSetWatch">
          <div class="ed-panel__head"><span id="avSetWatch">Просмотр</span></div>
          <div class="ed-panel__body settings-body">
            <div class="settings-option">
              <label class="ed-switch"><span>Добавлять в «Смотрю» и «Просмотрено» автоматически</span><input type="checkbox" role="switch" id="avAutoAddToggle" ${autoLists ? "checked" : ""} /></label>
              <p class="settings-help">Вместе с этим отключается правило «30 дней без просмотра, тайтл переходит в „Брошено“». Автопереключение серий включается под плеером.</p>
            </div>
          </div>
        </section>
        <section class="ed-panel" aria-labelledby="avSetLook">
          <div class="ed-panel__head"><span id="avSetLook">Оформление</span></div>
          <div class="ed-panel__body settings-body">
            <div class="ed-field">
              <span class="ed-field__label" id="avThemeLabel">Тема</span>
              <div class="tabs-scroll"><div class="ed-tabs theme-tabs" role="radiogroup" aria-labelledby="avThemeLabel">
                ${themeOption("system")}${themeOption("light")}${themeOption("dark")}
              </div></div>
              <div class="ed-field__note">Выбор хранится в этом браузере.</div>
            </div>
          </div>
        </section>
      ` : `
        <section class="ed-panel" aria-labelledby="avSetPrivacy">
          <div class="ed-panel__head"><span id="avSetPrivacy">Публичный профиль</span></div>
          <div class="ed-panel__body settings-body">
            <div class="settings-option">
              <label class="ed-switch"><span>Скрыть мои списки</span><input type="checkbox" role="switch" id="avHideListsToggle" ${privacy.hide_lists ? "checked" : ""} /></label>
              <p class="settings-help">Другие пользователи увидят надпись «Списки скрыты настройками приватности». Вы сами видите списки как обычно.</p>
            </div>
            <div class="settings-option">
              <label class="ed-switch"><span>Скрыть активность</span><input type="checkbox" role="switch" id="avHideActivityToggle" ${privacy.hide_activity ? "checked" : ""} /></label>
              <p class="settings-help">Скрытая активность не передаётся посторонним ни на странице, ни через API.</p>
            </div>
          </div>
        </section>
      `}
    </div>`;

  if (activeTab === "general") {
    document.querySelector("#avAutoAddToggle")?.addEventListener("change", e => store.setAutoAddLists(e.target.checked));
    const themeTabs = document.querySelector(".theme-tabs");
    // The header and the sheet can change the theme while this page is open.
    const paintTheme = () => {
      if (!themeTabs?.isConnected) { document.removeEventListener("av:theme", paintTheme); return; }
      themeTabs.querySelectorAll("[data-theme-value]").forEach(b => {
        const on = b.dataset.themeValue === getTheme();
        b.classList.toggle("is-active", on);
        b.setAttribute("aria-checked", String(on));
      });
    };
    themeTabs?.addEventListener("click", (e) => {
      const b = e.target.closest("[data-theme-value]");
      if (b) applyTheme(b.dataset.themeValue);
    });
    document.addEventListener("av:theme", paintTheme);
  } else {
    document.querySelector("#avHideListsToggle")?.addEventListener("change", e => store.setPrivacy({ hide_lists: e.target.checked }));
    document.querySelector("#avHideActivityToggle")?.addEventListener("change", e => store.setPrivacy({ hide_activity: e.target.checked }));
  }
}

// ==== public profile ====
export async function viewProfile(handle) {
  ctx.setActive(null);
  let data;
  try {
    const period = window.matchMedia("(max-width: 720px)").matches ? 30 : 365;
    data = await _fetchJson(`/profile/${encodeURIComponent(handle)}/summary?days=${period}`);
  } catch (exc) {
    ctx.outlet.innerHTML = ctx.errorPage({
      code: "404",
      title: "Профиль не найден",
      message: `Пользователь @${handle} не существует или был удалён.`,
      action: { href: "/", label: "На главную" },
      variant: "confused",
    });
    return;
  }
  const { user, is_owner, privacy, counts, activity } = data;
  const listsHidden = privacy.hide_lists && !is_owner;
  const activityHidden = privacy.hide_activity && !is_owner;
  ctx.setActive(is_owner ? "profile" : null);

  const tabs = counts ? LIST_ORDER.map(s => {
    const label = LIST_LABELS[s].one;
    const c = counts[s] || 0;
    return `<button type="button" class="ed-tabs__item seg-item" data-status="${s}" aria-pressed="false">
      ${esc(label)}${c ? `<span class="ed-tabs__count">${c}</span>` : ""}
    </button>`;
  }).join("") : "";
  const inLists = counts ? LIST_ORDER_MYLISTS.reduce((n, s) => n + (counts[s] || 0), 0) : 0;
  const stats = !listsHidden && counts
    ? [`${inLists} в списках`, `${counts.completed || 0} просмотрено`, `${counts.favorite || 0} в избранном`].join(", ")
    : "";

  ctx.outlet.innerHTML = `
    <div class="page-stack profile-page">
      <section class="ed-panel profile-hero" aria-labelledby="avProfileName">
        ${_avatarHtml(user, "ed-ava--lg profile-ava")}
        <div class="profile-info">
          <h1 class="profile-name" id="avProfileName">${esc(user.name || ("@" + user.handle))}</h1>
          <div class="profile-handle">@${esc(user.handle)}</div>
          ${stats ? `<p class="profile-stats">${esc(stats)}</p>` : ""}
        </div>
        ${is_owner ? `<a class="ed-btn ed-btn--secondary profile-privacy" href="/my/settings/privacy"><i class="fas fa-lock" aria-hidden="true"></i>Приватность</a>` : ""}
      </section>

      <div class="ed-tabs ed-tabs--line profile-nav" role="group" aria-label="Раздел профиля">
        <button type="button" class="ed-tabs__item is-active" data-profile-mode="overview" aria-pressed="true">Обзор</button>
        <button type="button" class="ed-tabs__item" data-profile-mode="diary" aria-pressed="false">Дневник активности</button>
      </div>
      <div class="profile-content" data-mode="overview">
        <section class="ed-panel profile-library" aria-labelledby="avLibraryTitle">
          <div class="ed-panel__head"><h2 id="avLibraryTitle">Списки</h2></div>
          <div class="ed-panel__body">
            ${listsHidden
              ? _privacyGate("Списки скрыты настройками приватности.")
              : `<div class="ed-tabs tabs-wrap profile-seg" id="avProfileTabs">${tabs}</div>
                 <div id="avProfileList" class="profile-list-stage" aria-live="polite"></div>`}
          </div>
        </section>

        ${activityHidden
          ? _privacyGate("Активность скрыта настройками приватности.", "profile-activity-gate")
          : `<section id="avProfileActivity" class="ed-panel activity-box" aria-label="Активность"></section>`}
      </div>
    </div>`;
  _upgradeAvatars(ctx.outlet, user.avatar || "");

  const profile = ctx.outlet.querySelector(".profile-page");
  const content = profile.querySelector(".profile-content");
  const setMode = (mode) => {
    content.dataset.mode = mode;
    profile.querySelectorAll("[data-profile-mode]").forEach(button => {
      const selected = button.dataset.profileMode === mode;
      button.classList.toggle("is-active", selected);
      button.setAttribute("aria-pressed", String(selected));
    });
  };
  profile.querySelectorAll("[data-profile-mode]").forEach(button => {
    button.addEventListener("click", () => {
      const box = profile.querySelector("#avProfileActivity");
      if (box?.querySelector(".gh-view-btn")) _switchActivityMode(box, button.dataset.profileMode);
      else setMode(button.dataset.profileMode);
    });
  });

  if (!listsHidden && counts) {
    const listCache = new Map();
    let listSeq = 0;

    const buildListHtml = (r) => {
      if (r.hidden) {
        return {
          html: _privacyGate("Списки скрыты настройками приватности."),
          items: [],
        };
      }
      if (!r.items.length) {
        return {
          html: ctx.errorPage({
            title: "В этом списке пока пусто",
            message: is_owner ? "Выберите статус на странице аниме, и тайтл появится здесь." : "Пользователь ещё не добавил сюда тайтлы.",
            icon: "fas fa-list",
          }),
          items: [],
        };
      }
      const grid = document.createElement("div");
      grid.className = "ed-grid av-grid";
      r.items.forEach(v => grid.appendChild(ctx.makeCard({
        mal_id: v.mal_id, title: v.title || "", _ru: v.title || "",
        images: { jpg: { large_image_url: v.poster_url } },
      })));
      return { html: grid.outerHTML, items: r.items };
    };

    const paintList = (status, html, items = [], seq = listSeq) => {
      const list = profile.querySelector("#avProfileList");
      if (!list) return;
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
      const hadRealContent = !!list.querySelector(".profile-list-panel:not(.profile-list-loading)");
      const h = list.offsetHeight;
      if (h > 0) list.style.minHeight = `${h}px`;

      const swap = () => {
        if (seq !== listSeq || !profile.isConnected) return;
        list.dataset.status = status;
        list.innerHTML = `<div class="profile-list-panel">${html}</div>`;
        const grid = list.querySelector(".av-grid");
        if (grid && items.length && ctx.enrichWithShiki) ctx.enrichWithShiki(grid, items);
        requestAnimationFrame(() => {
          list.classList.remove("is-switching", "is-loading");
          list.removeAttribute("aria-busy");
          list.style.minHeight = "";
        });
      };

      if (reduce || !hadRealContent) {
        swap();
        return;
      }
      list.classList.add("is-switching");
      window.setTimeout(swap, 130);
    };

    const loadTab = async (status) => {
      const list = profile.querySelector("#avProfileList");
      if (!list) return;
      const seq = ++listSeq;

      const cached = listCache.get(status);
      if (cached) {
        paintList(status, cached.html, cached.items);
        return;
      }

      list.setAttribute("aria-busy", "true");
      if (list.innerHTML.trim()) {
        list.classList.add("is-loading");
      } else {
        list.innerHTML = `<div class="profile-list-panel profile-list-loading">${ctx.loadingBlock()}</div>`;
      }

      try {
        const r = await _fetchJson(`/profile/${encodeURIComponent(handle)}/lists?status=${status}`);
        if (seq !== listSeq) return;
        const next = buildListHtml(r);
        listCache.set(status, next);
        paintList(status, next.html, next.items);
      } catch (_) {
        if (seq !== listSeq) return;
        paintList(status, ctx.errorPage({
          title: "Не удалось загрузить список",
          message: "Сервер не ответил. Обновите страницу.",
          variant: "sad",
        }));
      }
    };
    const tabsEl = profile.querySelector("#avProfileTabs");
    const initial = LIST_ORDER_MYLISTS.find(s => (counts[s] || 0) > 0) || "watching";
    [...tabsEl.querySelectorAll(".seg-item")].forEach(el => {
      el.classList.toggle("is-active", el.dataset.status === initial);
      el.setAttribute("aria-pressed", String(el.dataset.status === initial));
      el.addEventListener("click", (e) => {
        e.preventDefault();
        [...tabsEl.querySelectorAll(".seg-item")].forEach(x => {
          x.classList.toggle("is-active", x === el);
          x.setAttribute("aria-pressed", String(x === el));
        });
        loadTab(el.dataset.status);
      });
    });
    loadTab(initial);
  }

  if (!activityHidden) {
    const box = profile.querySelector("#avProfileActivity");
    if (box) {
      box._paintProfileMode = setMode;
      box._openDiary = (chooseMonth = false) => {
        _switchActivityMode(box, "diary", { preservePeriod: !chooseMonth });
      };
      const fetchFn = async (days) => {
        try {
          return await _fetchJson(`/profile/${encodeURIComponent(handle)}/activity?days=${days}`);
        } catch (_) { return null; }
      };
      // Пагинированный recent для «Показать ещё» и фильтра — отдельный
      // эндпоинт, респектит приватность через is_owner check на сервере.
      box._fetchRecent = async ({ group, offset, limit, date }) => {
        try {
          let u = `/profile/${encodeURIComponent(handle)}/activity/recent`
                + `?offset=${offset}&limit=${limit}&group=${encodeURIComponent(group)}`;
          if (date) u += `&date=${encodeURIComponent(date)}`;
          return await _fetchJson(u);
        } catch (_) { return null; }
      };
      renderActivityGraph(box, activity, fetchFn);
    }
  }
}

const ACT_PERIODS = [30, 90, 180, 365];

const KIND_RU = {
  watch_start: "Начал просмотр",
  watch_continue: "Продолжил просмотр",
  list_add: "Добавил в список",
  list_move: "Перенёс между списками",
  list_remove: "Убрал из списка",
  favorite: "Добавил в избранное",
  unfavorite: "Убрал из избранного",
  rate: "Оценил аниме",
  complete: "Завершил",
};

const ACT_FILTER_GROUPS = [
  { key: "all",        label: "Все" },
  { key: "watch",      label: "Просмотр" },
  { key: "lists",      label: "Списки" },
  { key: "favorites",  label: "Избранное" },
  { key: "rate",       label: "Оценки" },
];

function _renderRecentGroups(items) {
  let previousDay = null;
  return groupActivity(items).map(group => {
    const heading = group.day !== previousDay && group.day
      ? `<div class="act-day-heading">${esc(_ruDateLong(group.day))}</div>` : "";
    previousDay = group.day;
    const title = group.title || (group.mal_id ? `Аниме #${group.mal_id}` : "Активность");
    const href = Number(group.mal_id) > 0 ? `/anime/${Number(group.mal_id)}/` : "";
    const poster = /^(https?:\/\/|\/(?!\/))/i.test(group.poster_url || "") ? group.poster_url : "";
    const episode = group.episodes.length === 1 ? group.episodes[0] : null;
    const art = `<span class="act-poster" aria-hidden="true"><span>${esc(Array.from(title)[0] || "")}</span>${poster ? `<img src="${esc(poster)}" alt="" loading="lazy" referrerpolicy="no-referrer" />` : ""}</span>`;
    return `<li class="act-entry">${heading}<article class="act-session">
      ${href ? `<a href="${href}" tabindex="-1" aria-hidden="true">${art}</a>` : art}
      <div class="act-session-body"><h3>${href ? `<a href="${href}">${esc(title)}</a>` : esc(title)}</h3>
        ${episode ? `<div class="act-episode">Серия ${esc(episode)}</div>` : ""}
        <ol class="act-events">${group.events.map(event => `<li>
          <time datetime="${esc(event.at || "")}" title="Время UTC">${esc((event.at || "").slice(11, 16))}</time>
          <span>${esc(KIND_RU[event.kind] || event.kind)}${!episode && event.episode_num ? `, серия ${esc(event.episode_num)}` : ""}</span></li>`).join("")}</ol>
      </div></article></li>`;
  }).join("");
}

// Абсолютные пороги (как у GitHub) — интенсивность не пляшет по относительной
// шкале от максимума в периоде. 10+ действий за день = всегда самый насыщенный.
function _bucketAbsolute(n) {
  if (!n) return 0;
  if (n >= 10) return 4;
  if (n >= 7)  return 3;
  if (n >= 4)  return 2;
  return 1;
}

const RU_MONTHS_SHORT = [
  "Янв", "Фев", "Мар", "Апр", "Май", "Июн",
  "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек",
];
const RU_WEEKDAYS_SUN_FIRST = ["Вс", "Пн", "Вт", "Ср", "Чт", "Пт", "Сб"];

function _ruDateLong(isoDate) {
  return dayDate(isoDate).toLocaleDateString("ru-RU", {
    day: "numeric", month: "long", year: "numeric", timeZone: "UTC",
  });
}
function _ruActionsWord(n) {
  const abs = Math.abs(n);
  const mod10 = abs % 10, mod100 = abs % 100;
  if (mod10 === 1 && mod100 !== 11) return "действие";
  if ([2, 3, 4].includes(mod10) && ![12, 13, 14].includes(mod100)) return "действия";
  return "действий";
}

function _renderActPeriodTabs(current) {
  return ACT_PERIODS.map(d => {
    const cls = d === current ? "ed-tabs__item act-period is-active" : "ed-tabs__item act-period";
    return `<button type="button" class="${cls}" data-days="${d}" aria-pressed="${d === current}">${d === 365 ? "Год" : `${d} дней`}</button>`;
  }).join("");
}

// GitHub contribution graph. Вс сверху, 7 подписей слева, месяцы сверху,
// адаптивный размер клеток через CSS grid + aspect-ratio.
function _buildGhWeeks(days) {
  if (!days || !days.length) return { weeks: [], firstDayShift: 0 };
  const firstDay = dayDate(days[0].d);
  // Вс=0, Пн=1, … — GitHub показывает Вс сверху.
  const shift = firstDay.getUTCDay();
  const padded = Array(shift).fill(null).concat(days);
  while (padded.length % 7) padded.push(null);
  const weeks = [];
  for (let i = 0; i < padded.length; i += 7) weeks.push(padded.slice(i, i + 7));
  return { weeks, firstDayShift: shift };
}

function _renderGhMonths(weeks) {
  // Над какой колонкой показать подпись месяца: первая колонка, в которую
  // попал хотя бы один день нового месяца (отличного от предыдущей колонки).
  let lastMonth = -1;
  const cells = weeks.map((w, col) => {
    // Находим первый реальный день в этой неделе
    const anyDay = w.find(d => d);
    if (!anyDay) return `<span class="gh-month"></span>`;
    const m = dayDate(anyDay.d).getUTCMonth();
    // Показываем только если месяц сменился и эта колонка не первая
    // (на первой можем подписать, если в ней ≥3 дня нового месяца — иначе
    // подпись наедет на пустые клетки; GitHub-эвристика).
    const isFirstCol = col === 0;
    const daysOfThisMonthInWeek = w.filter(d => d && dayDate(d.d).getUTCMonth() === m).length;
    const nextMonthSoon = isFirstCol && weeks.slice(1, 3).some(week => {
      const nextDay = week.find(Boolean);
      return nextDay && dayDate(nextDay.d).getUTCMonth() !== m;
    });
    const showLabel = m !== lastMonth && (!isFirstCol || (daysOfThisMonthInWeek >= 4 && !nextMonthSoon));
    if (showLabel) {
      lastMonth = m;
      return `<span class="gh-month">${RU_MONTHS_SHORT[m]}</span>`;
    }
    if (lastMonth === -1) lastMonth = m;
    return `<span class="gh-month"></span>`;
  }).join("");
  return cells;
}

function _renderGhCells(weeks) {
  // grid-auto-flow: column — clicks flow column by column (по неделям).
  // Каждая клетка — один день. Пустые (до первого дня периода) — "gh-cell gh-pad".
  return weeks.map((w, col) =>
    w.map((d, row) => {
      if (!d) return `<span class="gh-cell gh-pad" aria-hidden="true"></span>`;
      const b = _bucketAbsolute(d.n);
      const label = `${_ruDateLong(d.d)}, ${d.n} ${_ruActionsWord(d.n)}`;
      return `<button type="button" class="gh-cell gh-b${b}"
        tabindex="-1"
        data-date="${d.d}" data-count="${d.n}" data-col="${col}" data-row="${row}"
        aria-pressed="false" aria-label="${esc(label)}"></button>`;
    }).join("")
  ).join("");
}

function _renderGhLegend() {
  return `
    <div class="gh-legend" aria-hidden="true">
      <span class="gh-legend-txt">меньше</span>
      <span class="gh-cell gh-b0"></span>
      <span class="gh-cell gh-b1"></span>
      <span class="gh-cell gh-b2"></span>
      <span class="gh-cell gh-b3"></span>
      <span class="gh-cell gh-b4"></span>
      <span class="gh-legend-txt">больше</span>
    </div>`;
}

function _renderGhStreaks(totals) {
  const cur = totals?.streak_current || 0;
  const best = totals?.streak_best || 0;
  const dayWord = n => n % 10 === 1 && n % 100 !== 11 ? "день" :
    ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? "дня" : "дней");
  const curWord = dayWord(cur), bestWord = dayWord(best);
  return `
    <div class="gh-streaks">
      <div class="gh-streak">
        <i class="fas fa-fire" aria-hidden="true"></i>
        <span>Сейчас подряд: <b>${cur} ${curWord}</b></span>
      </div>
      <div class="gh-streak">
        <i class="fas fa-trophy" aria-hidden="true"></i>
        <span>Лучшая серия за период: <b>${best} ${bestWord}</b></span>
      </div>
    </div>`;
}

function _renderGhView(totals, days, periodDays) {
  const periodLabel = { 30: "30 дней", 90: "90 дней", 180: "180 дней", 365: "год" }[periodDays] || `${periodDays}д`;
  const wordActions = _ruActionsWord(totals?.events || 0);
  const { weeks } = _buildGhWeeks(days);
  const cols = weeks.length;
  const monthsHtml = _renderGhMonths(weeks);
  const cellsHtml = _renderGhCells(weeks);
  const weekdaysHtml = RU_WEEKDAYS_SUN_FIRST.map(w => `<span>${w}</span>`).join("");

  return `
    <div class="gh-title">
      <b>${totals?.events || 0}</b> ${wordActions} за ${esc(periodLabel)}
    </div>
    <div class="gh-scroll">
      <div class="gh-grid" style="--cols:${cols}">
        <div class="gh-months" style="--cols:${cols}">${monthsHtml}</div>
        <div class="gh-weekdays">${weekdaysHtml}</div>
        <div class="gh-cells" role="group" aria-label="Активность по дням"
             style="--cols:${cols}">${cellsHtml}</div>
        ${_renderGhLegend()}
      </div>
    </div>
    ${_renderGhStreaks(totals)}
    <div class="gh-tip" id="avGhTip" role="tooltip" hidden></div>
  `;
}

function _renderCalendar(activity, month) {
  const { cells, previous, next } = calendarMonth(activity.days, month);
  const first = dayDate(`${month}-01`);
  // "октябрь 2026 г." from toLocaleDateString would read as "Октябрь 2026 Г." once capitalised.
  const monthName = first.toLocaleDateString("ru-RU", { month: "long", timeZone: "UTC" });
  const title = `${monthName.charAt(0).toUpperCase()}${monthName.slice(1)} ${first.getUTCFullYear()}`;
  const arrow = (dir, target, label) => `<button type="button" class="ed-iconbtn act-month-arrow" data-month="${target || ""}" ${target ? "" : "disabled"} aria-label="${label}" title="${label}"><i class="fas fa-chevron-${dir}" aria-hidden="true"></i></button>`;
  return `<div class="act-calendar">
    <div class="act-month-head">${arrow("left", previous, "Предыдущий месяц")}<h3>${esc(title)}</h3>${arrow("right", next, "Следующий месяц")}</div>
    <div class="act-calendar-weekdays" aria-hidden="true">${["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map(d => `<span>${d}</span>`).join("")}</div>
    <div class="act-calendar-days" role="group" aria-label="Календарь активности">
      ${cells.map(({ day, number, col, row }) => day
        ? `<button type="button" class="gh-cell gh-b${_bucketAbsolute(day.n)}" tabindex="-1" data-date="${day.d}" data-count="${day.n}" data-col="${col}" data-row="${row}" aria-pressed="false" aria-label="${esc(`${_ruDateLong(day.d)}, ${day.n} ${_ruActionsWord(day.n)}`)}">${number}${day.n ? '<i aria-hidden="true"></i>' : ""}</button>`
        : `<span class="act-calendar-outside" aria-hidden="true">${number}</span>`).join("")}
    </div>${_renderGhLegend()}
  </div><div class="act-calendar-summary"><strong>${activity.totals?.events || 0}</strong><span>${_ruActionsWord(activity.totals?.events || 0)} за 30 дней</span>
    <span>Активных дней: <b>${activity.totals?.active_days || 0}</b></span>${_renderGhStreaks(activity.totals)}</div>
    <div class="gh-tip" id="avGhTip" role="tooltip" hidden></div>`;
}

function _renderViewToggle(view) {
  return `
    <div class="ed-tabs gh-view-toggle" role="group" aria-label="Вид активности">
      <button type="button" class="ed-tabs__item gh-view-btn${view === "grid" ? " is-active" : ""}"
              data-view="grid" aria-pressed="${view === "grid"}">График</button>
      <button type="button" class="ed-tabs__item gh-view-btn${view === "list" ? " is-active" : ""}"
              data-view="list" aria-pressed="${view === "list"}">Лента</button>
    </div>`;
}

function _setActivityView(container, view) {
  container._view = view;
  try { sessionStorage.setItem("av_activity_view", view); } catch (_) {}
  // A feed is always the full diary, never the overview's four-entry preview.
  if (view === "list") container._openDiary?.();
  _paintActivity(container, container._activity);
}

function _switchActivityMode(container, mode, { preservePeriod = false } = {}) {
  if (container._profileMode === mode) return;
  // Invalidate the previous tab's pending period request even on a cache hit.
  container._periodSeq = (container._periodSeq || 0) + 1;
  container._profileMode = mode;
  container._paintProfileMode?.(mode);
  if (preservePeriod) {
    container._periods[mode] = container._activity?.period_days || container._periods[mode];
    const status = container.querySelector(".act-status");
    if (status) status.textContent = "";
    return;
  }
  if (mode === "overview") _setActivityView(container, "grid");
  return _loadActivityPeriod(container, container._periods[mode]);
}

async function _loadActivityPeriod(container, days) {
  const mode = container._profileMode;
  const seq = container._periodSeq = (container._periodSeq || 0) + 1;
  const status = container.querySelector(".act-status");
  if (status) status.textContent = "Загружаем…";
  const fresh = container._cache.get(days) || await container._fetch(days).catch(() => null);
  if (seq !== container._periodSeq || mode !== container._profileMode || !container.isConnected) return;
  if (fresh?.hidden) { _hideActivity(container); return; }
  if (fresh?.period_days === days && Array.isArray(fresh.days)) {
    container._cache.set(days, fresh);
    container._periods[mode] = days;
    const changed = container._activity?.period_days !== days;
    const hadDate = changed && !!container._dayFilter;
    if (changed) container._month = null;
    if (hadDate) container._dayFilter = null;
    _paintActivity(container, fresh);
    if (hadDate) _reloadRecent(container);
  } else {
    const currentStatus = container.querySelector(".act-status");
    if (currentStatus) currentStatus.textContent = "Не удалось загрузить период. Попробуйте ещё раз.";
  }
}

// Главная точка входа. fetchFn(days) → новые данные графа.
function renderActivityGraph(container, activity, fetchFn) {
  container._fetch = fetchFn || container._fetch;
  container._view = "grid";
  try { container._view = sessionStorage.getItem("av_activity_view") === "list" ? "list" : "grid"; } catch (_) {}
  container._cache = new Map();
  container._profileMode = "overview";
  container._periods = { overview: activity?.period_days || 365, diary: 30 };
  container._activity = activity;
  container._recentGroup = "all";
  container._recentItems = activity?.recent || [];
  container._recentHasMore = container._recentItems.length >= 10;
  if (activity) container._cache.set(activity.period_days, activity);
  if (container._view === "list") container._openDiary?.();
  _paintActivity(container, activity);

  container.addEventListener("click", async (e) => {
    const periodBtn = e.target.closest(".act-period");
    if (periodBtn && container._fetch) {
      await _loadActivityPeriod(container, Number(periodBtn.dataset.days));
      return;
    }
    const viewBtn = e.target.closest(".gh-view-btn");
    if (viewBtn) {
      const v = viewBtn.dataset.view;
      _setActivityView(container, v);
      return;
    }
    const monthBtn = e.target.closest("[data-month]");
    if (monthBtn?.dataset.month) {
      container._month = monthBtn.dataset.month;
      _paintActivity(container, container._activity);
      container.querySelector(`[data-month]:not(:disabled)`)?.focus();
    }
    if (e.target.closest("[data-open-diary]")) container._openDiary?.(true);
  });
  _reloadRecent(container);
}

function _hideActivity(container) {
  container._recentSeq = (container._recentSeq || 0) + 1;
  container._periodSeq = (container._periodSeq || 0) + 1;
  container._cache.clear();
  container._recentItems = [];
  container._activity = null;
  container.innerHTML = `<div class="ed-panel__body">${_privacyGate("Активность скрыта настройками приватности.", "profile-activity-gate")}</div>`;
}

function _paintActivity(container, activity) {
  container._activity = activity;
  const { days = [], totals = {}, period_days = 365 } = activity || {};
  const view = container._view || "grid";
  container.dataset.period = period_days;
  container.dataset.view = view;
  if (!container._month || !days.some(d => d.d.startsWith(container._month))) {
    container._month = days[days.length - 1]?.d.slice(0, 7);
  }

  const filterTabs = ACT_FILTER_GROUPS.map(g =>
    `<button type="button" class="ed-chip act-filter-btn${g.key === container._recentGroup ? " is-active" : ""}" data-group="${g.key}" aria-pressed="${g.key === container._recentGroup}">${esc(g.label)}</button>`
  ).join("");

  // .act-top holds the head and the graph: in the overview it is a card above the lists and the history.
  container.innerHTML = `
    <div class="act-top">
    <div class="act-head">
      <h2>Активность</h2>
      <div class="act-head__tools">
        <div class="tabs-scroll"><div class="ed-tabs act-period-tabs" role="group" aria-label="Период">${_renderActPeriodTabs(period_days || 30)}</div></div>
        ${_renderViewToggle(view)}
      </div>
      <div class="act-status" role="status"></div>
    </div>
    ${view === "grid" ? `<section class="act-visual${period_days === 30 ? " is-calendar" : ""}" aria-label="График активности">${!days.length
      ? `<div class="ed-empty"><div class="ed-empty__icon"><i class="fas fa-chart-simple" aria-hidden="true"></i></div><div class="ed-empty__title">Активности пока нет</div><div class="ed-empty__text">За выбранный период ничего не произошло.</div></div>`
      : period_days === 30 ? _renderCalendar(activity, container._month) : _renderGhView(totals, days, period_days)}</section>` : ""}
    </div>
    <section class="act-diary" aria-label="История действий">
    <div class="act-recent-head">
      <h2 class="act-recent-title">История действий</h2>
      <div class="act-filter" role="group" aria-label="Тип действий">${filterTabs}</div>
    </div>
    <div class="ed-note gh-date-filter" id="avDateFilter" ${container._dayFilter ? "" : "hidden"}>
      <i class="fas fa-calendar-day" aria-hidden="true"></i>
      <span>Показано: <b id="avDateFilterText">${container._dayFilter ? esc(_ruDateLong(container._dayFilter)) : ""}</b></span>
      <button type="button" class="ed-iconbtn gh-date-clear" id="avDateClear" aria-label="Снять фильтр по дате" title="Снять фильтр по дате"><i class="fas fa-xmark" aria-hidden="true"></i></button>
    </div>
    <ol class="act-recent" id="avActRecent"></ol>
    <div class="ed-empty act-recent-empty" id="avActRecentEmpty" hidden>
      <div class="ed-empty__icon"><i class="fas fa-clock-rotate-left" aria-hidden="true"></i></div>
      <div class="ed-empty__title">Действий пока нет</div>
      <div class="ed-empty__text">Здесь появятся просмотры, списки и оценки.</div>
    </div>
    <div class="act-recent-status" role="status"></div>
    <button type="button" class="ed-btn ed-btn--secondary act-more" id="avActMore" hidden>Показать ещё 10</button>
    <div class="act-end" id="avActEnd" hidden>Это вся активность</div>
    <button type="button" class="ed-btn ed-btn--ghost act-open-diary" data-open-diary>Вся активность<i class="fas fa-chevron-right" aria-hidden="true"></i></button>
    </section>`;

  _bindGhInteractions(container);
  // When the year does not fit, open the scrolled graph at its latest weeks.
  const graph = container.querySelector(".gh-scroll");
  if (graph) graph.scrollLeft = graph.scrollWidth;
  _bindRecentList(container);
  _paintRecent(container);
}

// ==== grid interaction: tooltip + click-day filter + keyboard ====
function _bindGhInteractions(container) {
  const tip = container.querySelector("#avGhTip");
  const cells = container.querySelectorAll(".gh-cell[data-date]");
  if (!cells.length || !tip) return;

  const showTip = (cell) => {
    const label = cell.getAttribute("aria-label") || "";
    tip.textContent = label;
    tip.hidden = false;
    // Позиционируем над клеткой
    const r = cell.getBoundingClientRect();
    // Сначала показываем чтобы измерить ширину
    tip.style.visibility = "hidden";
    tip.style.top = "0px";
    tip.style.left = "0px";
    requestAnimationFrame(() => {
      const tr = tip.getBoundingClientRect();
      if (tip.hidden || !tip.isConnected) return;
      let top = r.top - tr.height - 8;
      let left = r.left + r.width / 2 - tr.width / 2;
      // Не даём вылезать за viewport
      left = Math.max(8, Math.min(window.innerWidth - tr.width - 8, left));
      if (top < 8) top = r.bottom + 8;
      tip.style.top = `${top}px`;
      tip.style.left = `${left}px`;
      tip.style.visibility = "";
    });
  };
  const hideTip = () => { if (tip) tip.hidden = true; };

  cells.forEach(c => {
    c.classList.toggle("active", c.dataset.date === container._dayFilter);
    c.setAttribute("aria-pressed", String(c.dataset.date === container._dayFilter));
    c.addEventListener("mouseenter", () => showTip(c));
    c.addEventListener("mouseleave", hideTip);
    c.addEventListener("focus", () => showTip(c));
    c.addEventListener("blur", hideTip);
    c.addEventListener("click", () => _activateDateFilter(container, c));
    c.addEventListener("keydown", (e) => _handleGhKey(container, e));
  });
  // Первая клетка — единственная в tab-order'е
  const first = [...cells].find(c => c.dataset.date === container._dayFilter) || cells[0];
  if (first) first.setAttribute("tabindex", "0");
}

function _handleGhKey(container, e) {
  const el = e.target;
  if (!el.classList?.contains("gh-cell")) return;
  const col = Number(el.dataset.col);
  const row = Number(el.dataset.row);
  let nc = col, nr = row;
  if (e.key === "ArrowLeft")  nc--;
  else if (e.key === "ArrowRight") nc++;
  else if (e.key === "ArrowUp")   nr--;
  else if (e.key === "ArrowDown") nr++;
  else if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    _activateDateFilter(container, el);
    return;
  } else if (e.key === "Escape") {
    _clearDateFilter(container);
    return;
  } else {
    return;
  }
  e.preventDefault();
  const target = container.querySelector(
    `.gh-cell[data-col="${nc}"][data-row="${nr}"][data-date]`,
  );
  if (!target) return;
  container.querySelectorAll(".gh-cell").forEach(c => c.setAttribute("tabindex", "-1"));
  target.setAttribute("tabindex", "0");
  target.focus();
}

function _activateDateFilter(container, cell) {
  const date = cell.dataset.date;
  container.querySelectorAll(".gh-cell[data-date]").forEach(c => {
    c.classList.remove("active");
    c.setAttribute("aria-pressed", String(c === cell));
    c.setAttribute("tabindex", c === cell ? "0" : "-1");
  });
  cell.classList.add("active");
  const filterBar = container.querySelector("#avDateFilter");
  const text = container.querySelector("#avDateFilterText");
  if (filterBar && text) {
    text.textContent = _ruDateLong(date);
    filterBar.hidden = false;
  }
  container._dayFilter = date;
  container.querySelector("#avGhTip").hidden = true;
  container._openDiary?.();
  _reloadRecent(container);
}

function _clearDateFilter(container) {
  container.querySelectorAll(".gh-cell.active").forEach(c => {
    c.classList.remove("active");
    c.setAttribute("aria-pressed", "false");
  });
  const filterBar = container.querySelector("#avDateFilter");
  if (filterBar) filterBar.hidden = true;
  container._dayFilter = null;
  _reloadRecent(container);
}

// ==== recent list: filter pills + pagination + date-clear ====
function _bindRecentList(container) {
  container.querySelectorAll(".act-filter-btn").forEach(b => {
    b.addEventListener("click", () => {
      if (b.classList.contains("is-active")) return;
      container.querySelectorAll(".act-filter-btn").forEach(x => {
        x.classList.toggle("is-active", x === b);
        x.setAttribute("aria-pressed", String(x === b));
      });
      container._recentGroup = b.dataset.group;
      _reloadRecent(container);
    });
  });

  container.querySelector("#avActMore")?.addEventListener("click", () => _reloadRecent(container, true));
  container.querySelector("#avDateClear")?.addEventListener("click", () => {
    _clearDateFilter(container);
  });
}

function _paintRecent(container) {
  const recentBox = container.querySelector("#avActRecent");
  if (!recentBox) return;
  const items = container._recentItems || [];
  recentBox.innerHTML = _renderRecentGroups(items);
  recentBox.hidden = !items.length;
  recentBox.classList.toggle("is-loading", !!container._recentBusy);
  recentBox.setAttribute("aria-busy", String(!!container._recentBusy));
  recentBox.querySelectorAll("img").forEach(img => {
    img.addEventListener("error", () => img.remove(), { once: true });
  });
  container.querySelector("#avActRecentEmpty").hidden = !!items.length || !!container._recentBusy || !!container._recentError;
  container.querySelector("#avActEnd").hidden = !items.length || container._recentHasMore || !!container._recentError;
  container.querySelector(".act-recent-status").innerHTML = container._recentError
    ? 'Не удалось загрузить действия. <button type="button" class="ed-btn ed-btn--ghost ed-btn--sm act-retry">Повторить</button>' : "";
  container.querySelector(".act-retry")?.addEventListener("click", () => _reloadRecent(container, container._failedAppend));
  const moreBtn = container.querySelector("#avActMore");
  moreBtn.hidden = !items.length || !container._recentHasMore || !!container._recentError;
  moreBtn.disabled = !!container._recentBusy;
  moreBtn.textContent = container._recentBusy ? "Загружаем…" : "Показать ещё 10";
}

async function _reloadRecent(container, append = false) {
  if (!container._fetchRecent || (append && container._recentBusy)) return;
  const seq = container._recentSeq = (container._recentSeq || 0) + 1;
  container._recentBusy = true;
  container._recentError = false;
  _paintRecent(container);
  const r = await container._fetchRecent({
    group: container._recentGroup,
    offset: append ? (container._recentItems || []).length : 0, limit: 10,
    date: container._dayFilter,
  }).catch(() => null);
  if (seq !== container._recentSeq || !container.isConnected) return;
  if (r?.hidden) { _hideActivity(container); return; }
  if (r && Array.isArray(r.items)) {
    container._recentItems = append ? [...container._recentItems, ...r.items] : r.items;
    container._recentHasMore = !!r.has_more;
  } else {
    container._recentError = true;
    container._failedAppend = append;
  }
  container._recentBusy = false;
  _paintRecent(container);
}

// ==== helpers ====
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
