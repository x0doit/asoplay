// -*- coding: utf-8 -*-
/*
 * AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
 * All rights reserved. See /COPYRIGHT for full terms.
 *
 * Account layer: login/logout/register redirect, personal sections (favorites,
 * history), guest auth-gates and the "store" abstraction that turns every
 * personal-data write into either a server call (authenticated) or a gate
 * prompt (guest). There is no localStorage fallback for new guest progress.
 */

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
}

export function getSocialConfig() { return authConfig.social; }

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
    ctx.state.dubPrefs = {};
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
  bar.innerHTML = `
    <div class="import-bar-inner">
      <div class="import-bar-text">
        Нашли <b>${total}</b> записей в этом браузере (избранное / история / оценки).
        Перенести в ваш аккаунт?
      </div>
      <div class="import-bar-actions">
        <button type="button" class="btn btn-primary" id="avImportYes">Перенести</button>
        <button type="button" class="btn btn-ghost" id="avImportNo">Нет, спасибо</button>
      </div>
    </div>`;
  document.body.appendChild(bar);
  requestAnimationFrame(() => bar.classList.add("show"));

  document.querySelector("#avImportNo").addEventListener("click", () => {
    localStorage.setItem(flagKey, "1");
    bar.remove();
  });
  document.querySelector("#avImportYes").addEventListener("click", async () => {
    const btn = document.querySelector("#avImportYes");
    btn.disabled = true;
    btn.textContent = "Переношу…";
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
      msg.textContent = `Не удалось перенести: ${exc.message}`;
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
  ctx.state.dubPrefs = {};
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
export function renderHeader() {
  const slot = document.querySelector("#avAccountSlot");
  if (!slot) return;
  const user = ctx.state.user;
  if (!user) {
    slot.innerHTML = `
      <a href="/login" data-view="login" class="menu-icon menu-login" title="Войти">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M10 17l1.4-1.4L8.8 13H21v-2H8.8l2.6-2.6L10 7l-5 5 5 5zM4 5h8V3H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h8v-2H4V5z"/></svg>
        <span>Авторизация</span>
      </a>`;
    return;
  }
  const name = user.name || user.login || user.email || "Аккаунт";
  const letter = (name || "A").trim().slice(0, 1).toUpperCase();
  const avatar = user.avatar || "";
  slot.innerHTML = `
    <button type="button" class="menu-account-btn" id="avAccountBtn" title="${esc(name)}">
      <span class="menu-avatar menu-avatar-fallback" id="avAccountAva">${esc(letter)}</span>
      <span class="menu-account-name">${esc(name)}</span>
      <svg class="menu-account-chev" viewBox="0 0 24 24" width="11" height="11" fill="currentColor"><path d="M7 10l5 5 5-5z"/></svg>
    </button>
    <ul class="menu-account-drop" id="avAccountDrop" hidden>
      <li><a href="${esc(user.profile_path || ("/@" + (user.handle || ("profile" + user.id))))}" data-view="profile">Профиль</a></li>
      <li><a href="/my/continue" data-view="my-continue">Продолжить просмотр</a></li>
      <li><a href="/my/lists" data-view="my-lists">Списки</a></li>
      <li><a href="/my/favorites" data-view="my-favorites">Избранное</a></li>
      <li><a href="/my/settings" data-view="my-settings">Настройки</a></li>
      <li class="menu-account-sep"></li>
      <li><button type="button" class="menu-logout" id="avLogoutBtn">Выйти</button></li>
    </ul>`;
  // Try to upgrade the letter-fallback with a real avatar. Preload with a
  // throwaway Image so CORS/404 failures don't show a broken-image icon
  // in the header — the letter stays visible until the real image is ready.
  const avaBox = document.querySelector("#avAccountAva");
  if (avatar && avaBox) {
    const probe = new Image();
    probe.referrerPolicy = "no-referrer";
    probe.onload = () => {
      avaBox.outerHTML = `<img class="menu-avatar" src="${esc(avatar)}" alt="" referrerpolicy="no-referrer" />`;
    };
    probe.onerror = () => { /* keep letter fallback */ };
    probe.src = avatar;
  }
  const btn = document.querySelector("#avAccountBtn");
  const drop = document.querySelector("#avAccountDrop");
  const setOpen = (open) => {
    drop.hidden = !open;
    btn.classList.toggle("open", open);
    btn.setAttribute("aria-expanded", open ? "true" : "false");
  };
  btn?.addEventListener("click", (e) => {
    e.stopPropagation();
    setOpen(drop.hidden);
  });
  // Global click handler: close on outside click. Replaces previous listener
  // on every re-render so we don't stack up.
  if (window._avDropCloser) document.removeEventListener("click", window._avDropCloser);
  window._avDropCloser = (e) => {
    const d = document.querySelector("#avAccountDrop");
    const b = document.querySelector("#avAccountBtn");
    if (!d || d.hidden) return;
    if (!d.contains(e.target) && !b?.contains(e.target)) {
      d.hidden = true;
      b?.classList.remove("open");
      b?.setAttribute("aria-expanded", "false");
    }
  };
  document.addEventListener("click", window._avDropCloser);
  // Close on Escape for keyboard users.
  if (window._avDropEsc) document.removeEventListener("keydown", window._avDropEsc);
  window._avDropEsc = (e) => {
    if (e.key === "Escape" && !drop.hidden) setOpen(false);
  };
  document.addEventListener("keydown", window._avDropEsc);
  document.querySelector("#avLogoutBtn")?.addEventListener("click", async () => {
    await logout();
    ctx.nav("/");
  });
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
      if (payload.seconds >= 300) {
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
    if (!ctx.state.user) return; // guests just silently skip — no gate for this
    ctx.state.dubPrefs[String(malId)] = norm;
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

export function openGate(kind) {
  const copy = _gateCopy(kind);
  closeGate();
  const wrap = document.createElement("div");
  wrap.className = "gate-overlay";
  wrap.innerHTML = `
    <div class="gate-card">
      <button class="gate-close" aria-label="Закрыть">&times;</button>
      <div class="gate-icon">
        <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <rect x="3" y="11" width="18" height="10" rx="2"/>
          <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
        </svg>
      </div>
      <h2 class="gate-title">${esc(copy.title)}</h2>
      <p class="gate-body">${copy.body}</p>
      <ul class="gate-points">
        ${copy.points.map(p => `<li>${esc(p)}</li>`).join("")}
      </ul>
      <div class="gate-actions">
        <a class="btn btn-primary" href="/login">Войти</a>
        <a class="btn btn-ghost" href="${esc(registerUrl())}" target="_blank" rel="noopener">Регистрация в ${esc(socialSiteName())}</a>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  requestAnimationFrame(() => wrap.classList.add("show"));
  wrap.addEventListener("click", e => {
    if (e.target === wrap || e.target.closest(".gate-close")) closeGate();
  });
  _gateEl = wrap;
}

export function closeGate() {
  if (_gateEl) { _gateEl.remove(); _gateEl = null; }
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
  const next = params.get("next") || "/";
  ctx.outlet.innerHTML = `
    <div class="auth-shell">
      <div class="auth-card">
        <h1 class="auth-title">Вход в AnimeViev</h1>
        <p class="auth-sub">Регистрация отдельная — ведётся на сайте социальной сети. Здесь только вход.</p>
        <form class="auth-form" id="avLoginForm" autocomplete="on" novalidate>
          <label class="auth-field">
            <span>Логин или e-mail</span>
            <input type="text" name="login" id="avLoginInput" autocomplete="username" required autofocus />
          </label>
          <label class="auth-field">
            <span>Пароль</span>
            <input type="password" name="password" id="avLoginPass" autocomplete="current-password" required />
          </label>
          <div class="auth-error" id="avLoginErr" hidden></div>
          <button type="submit" class="btn btn-primary auth-submit">Войти</button>
        </form>
        <div class="auth-foot">
          Ещё нет аккаунта?
          <a href="${esc(registerUrl())}" target="_blank" rel="noopener">Зарегистрироваться в ${esc(socialSiteName())}&nbsp;↗</a>
        </div>
      </div>
    </div>`;
  document.querySelector("#avLoginForm")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = document.querySelector("#avLoginErr");
    err.hidden = true;
    const fd = new FormData(e.target);
    try {
      await login(fd.get("login").toString().trim(), fd.get("password").toString());
      ctx.nav(next && next.startsWith("/") ? next : "/");
    } catch (exc) {
      err.hidden = false;
      err.textContent = _loginErrorMessage(exc);
    }
  });
}

export const LIST_LABELS = {
  watching:  { one: "Смотрю",        many: "Смотрю" },
  planned:   { one: "В Планах",      many: "В Планах" },
  completed: { one: "Просмотрено",   many: "Просмотрено" },
  dropped:   { one: "Брошено",       many: "Брошено" },
  postponed: { one: "Отложено",      many: "Отложено" },
  favorite:  { one: "Избранное",     many: "Избранное" },
};
const LIST_ORDER = ["watching", "planned", "completed", "postponed", "dropped", "favorite"];
// «Избранное» (favorite) живёт отдельно — /my/favorites. В /my/lists
// показываем только взаимоисключающие статусы.
const LIST_ORDER_MYLISTS = ["watching", "planned", "completed", "postponed", "dropped"];

function _listCards(items) {
  if (!items.length) return ctx.errorPage({
    title: "Здесь пока пусто",
    message: "Добавляйте аниме в списки со страницы тайтла — оно появится тут.",
    action: { href: "/top", label: "Посмотреть топ" },
    variant: "empty",
  });
  const g = document.createElement("div");
  g.className = "av-grid";
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
  ctx.setActive(null);
  if (!ctx.state.user) { openGate("favorites"); ctx.nav("/login?next=/my/favorites"); return; }
  const list = await _listsFetch("favorite");
  ctx.outlet.innerHTML = `
    <div class="block-header">Избранное <span class="block-header-link">${list.length} тайтлов</span></div>
    <div id="avFavGrid">${_listCards(list)}</div>`;
  // После рендера добавим русские названия и добьём обложки из Shikimori.
  const grid = document.querySelector("#avFavGrid .av-grid");
  if (grid && ctx.enrichWithShiki) ctx.enrichWithShiki(grid, list);
}

export async function viewMyContinue() {
  ctx.setActive(null);
  if (!ctx.state.user) { openGate("watching"); ctx.nav("/login?next=/my/continue"); return; }

  // Всегда тянем свежий /account/history перед рендером. Локальный state.watch
  // заполняется только при бутстрапе страницы, и если пользователь в той же
  // сессии посмотрел что-то и вернулся сюда SPA-переходом — локальные данные
  // устарели. Бэкенд отдаёт ТОЛЬКО записи с episode_seconds >= 300, поэтому
  // фильтровать 5-минутный порог ещё раз не нужно.
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
    ctx.outlet.innerHTML = `<div class="block-header">Продолжить просмотр</div>` + ctx.errorPage({
      title: "Пока пусто",
      message: "Как только вы посмотрите хотя бы 5 минут любого аниме, оно появится здесь — продолжить с того же места.",
      action: { href: "/", label: "Найти что-нибудь" },
      variant: "empty",
    });
    return;
  }
  ctx.outlet.innerHTML = `
    <div class="block-header continue-head">
      <span class="continue-title">Продолжить просмотр <span class="block-header-link">${entries.length}</span></span>
      <button type="button" class="btn btn-ghost continue-clear" id="avContinueClear">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z"/></svg>
        Очистить весь список
      </button>
    </div>
    <div class="av-grid" id="g"></div>`;
  const g = document.querySelector("#g");
  entries.forEach(v => {
    const displayEp = nextEp(v);
    const finished = isEpFinished(v);
    const wrap = document.createElement("div");
    wrap.className = "av-card-wrap";
    wrap.dataset.mal = String(v.mal_id);
    const src = v.cover ? ctx.imgUrl(v.cover) : "";
    const metaText = finished
      ? `Следующая серия: ${displayEp}`
      : `Остановились на ${displayEp} серии`;
    wrap.innerHTML = `
      <a class="av-card" href="/anime/${v.mal_id}/" data-mal="${v.mal_id}">
        <div class="av-card-img">
          <img src="${src}" alt="${esc(v.title || "")}"
               onerror="this.dataset.broken='1';this.removeAttribute('src');">
          <span class="av-badge-type show">${displayEp} серия</span>
        </div>
        <div class="av-card-body">
          <div class="av-card-title" title="${esc(v.title || "")}">${esc(v.title || "")}</div>
          <div class="av-card-meta">${esc(metaText)}</div>
        </div>
      </a>
      <button type="button" class="card-remove" title="Убрать из списка"
              data-mal="${v.mal_id}" aria-label="Убрать из списка">
        <svg viewBox="0 0 24 24" width="13" height="13" fill="currentColor"><path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>
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
      body: "Это действие невозможно отменить. Весь прогресс просмотра будет удалён с сервера, на всех устройствах.",
      countdownSec: 5,
      yesLabel: "Да",
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
  const wrap = document.createElement("div");
  wrap.className = "confirm-overlay";
  wrap.innerHTML = `
    <div class="confirm-card">
      <div class="confirm-icon">
        <svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 3 1 21h22L12 3z"/>
          <line x1="12" y1="9" x2="12" y2="14"/>
          <line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg>
      </div>
      <h2 class="confirm-title">${esc(title)}</h2>
      <p class="confirm-body">${esc(body)}</p>
      <div class="confirm-actions">
        <button type="button" class="btn btn-ghost" id="avConfirmNo">${esc(noLabel)}</button>
        <button type="button" class="btn btn-primary confirm-yes" id="avConfirmYes" disabled>
          <span class="confirm-yes-label">${esc(yesLabel)}</span>
          <span class="confirm-countdown" id="avConfirmTimer">(${countdownSec})</span>
        </button>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  requestAnimationFrame(() => wrap.classList.add("show"));

  let remaining = countdownSec;
  const timerEl = wrap.querySelector("#avConfirmTimer");
  const yesBtn = wrap.querySelector("#avConfirmYes");
  const noBtn = wrap.querySelector("#avConfirmNo");
  const labelEl = wrap.querySelector(".confirm-yes-label");

  const tick = setInterval(() => {
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

  const close = () => {
    clearInterval(tick);
    wrap.classList.remove("show");
    document.removeEventListener("keydown", onKey);
    setTimeout(() => wrap.remove(), 180);
  };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  document.addEventListener("keydown", onKey);

  noBtn.addEventListener("click", close);
  wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
  yesBtn.addEventListener("click", async () => {
    if (yesBtn.disabled) return;
    yesBtn.disabled = true;
    labelEl.textContent = "Готово…";
    try { if (onConfirm) await onConfirm(); } finally { close(); }
  });
}

// Старые имена на случай прямого вызова из app.js legacy-путей.
export const viewMyWatching = viewMyContinue;
export const viewMyHistory  = viewMyContinue;

// Основной раздел «Списки» — вкладки по статусам, плюс отдельный таб «Любимые».
export async function viewMyLists(activeStatus) {
  ctx.setActive(null);
  if (!ctx.state.user) { openGate("lists"); ctx.nav("/login?next=/my/lists"); return; }
  const allowed = new Set(LIST_ORDER_MYLISTS);
  const status = allowed.has(activeStatus) ? activeStatus : "watching";
  let counts = { watching: 0, planned: 0, completed: 0, dropped: 0, postponed: 0, favorite: 0 };
  try { counts = await _fetchJson("/account/lists/counts"); } catch (_) {}
  const items = await _listsFetch(status);

  const tabs = LIST_ORDER_MYLISTS.map(s => {
    const label = LIST_LABELS[s].one;
    const c = counts[s] || 0;
    const cls = s === status ? "seg-item active" : "seg-item";
    return `<a class="${cls}" href="/my/lists/${s}" data-status="${s}">
      <span>${esc(label)}</span><span class="seg-count">${c}</span>
    </a>`;
  }).join("");

  const cardsHtml = items.length
    ? `<div class="av-grid">${items.map(v => _listCardWithActions(v, status)).join("")}</div>`
    : ctx.errorPage({
        title: "В этом списке пока пусто",
        message: "Откройте страницу любого аниме и выберите подходящий статус — тайтл появится здесь.",
        action: { href: "/top", label: "Посмотреть топ" },
        variant: "empty",
      });

  ctx.outlet.innerHTML = `
    <div class="block-header">Списки</div>
    <div class="seg-tabs" id="avListTabs">${tabs}</div>
    <div id="avListGrid">${cardsHtml}</div>`;
  const grid = document.querySelector("#avListGrid .av-grid");
  if (grid) {
    if (ctx.enrichWithShiki) ctx.enrichWithShiki(grid, items);
    _bindListCardActions(grid, status);
  }
}

// Карточка со всплывающим меню «переместить/удалить». Рендерится как строка
// HTML, обработчики вешаются через делегирование в _bindListCardActions.
function _listCardWithActions(v, currentStatus) {
  const mal = Number(v.mal_id);
  const src = v.poster_url ? ctx.imgUrl(v.poster_url) : "";
  const title = v.title || `#${mal}`;
  const autoBadge = v.status_source === "auto"
    ? `<span class="card-auto-badge" title="Статус выставлен автоматически">авто</span>`
    : "";
  // Опции перемещения = все статусы КРОМЕ текущего. Плюс «Убрать из списков».
  const moveOptions = LIST_ORDER_MYLISTS.filter(s => s !== currentStatus).map(s =>
    `<button type="button" class="lp-option" data-action="move" data-status="${s}">${esc(LIST_LABELS[s].one)}</button>`
  ).join("");
  return `
    <div class="av-card-wrap" data-mal="${mal}">
      <a class="av-card" href="/anime/${mal}/" data-mal="${mal}">
        <div class="av-card-img">
          <img src="${src}" alt="${esc(title)}"
               onerror="this.dataset.broken='1';this.removeAttribute('src');">
          ${autoBadge}
        </div>
        <div class="av-card-body">
          <div class="av-card-title" title="${esc(title)}">${esc(title)}</div>
          <div class="av-card-meta">${esc(LIST_LABELS[currentStatus]?.one || "")}</div>
        </div>
      </a>
      <button type="button" class="card-action card-action-menu" title="Действия"
              data-mal="${mal}" aria-haspopup="menu">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><circle cx="12" cy="5" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="12" cy="19" r="1.7"/></svg>
      </button>
      <div class="card-action-pop" role="menu" hidden>
        <div class="lp-sec-title">Перенести в:</div>
        ${moveOptions}
        <div class="lp-sep"></div>
        <button type="button" class="lp-option lp-danger" data-action="remove">Убрать из списков</button>
      </div>
    </div>`;
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
    if (action === "move") {
      const newStatus = opt.dataset.status;
      const entry = ctx.state.lists?.[String(mal)] || {};
      const res = await store.setListStatus(mal, newStatus, {
        title: entry.title, cover: entry.cover,
      });
      if (res) {
        // Тайтл больше не в текущем списке — убираем карточку с экрана.
        wrap.classList.add("slide-out");
        setTimeout(() => wrap.remove(), 180);
      }
    } else if (action === "remove") {
      const res = await store.setListStatus(mal, null, {});
      if (res) {
        wrap.classList.add("slide-out");
        setTimeout(() => wrap.remove(), 180);
      }
    }
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
  };
  document.addEventListener("click", window._avListCardCloser);
}

export async function viewMySettings(tab) {
  ctx.setActive(null);
  if (!ctx.state.user) { openGate("settings"); ctx.nav("/login?next=/my/settings"); return; }
  const s = ctx.state.user;
  const privacy = ctx.state.privacy || { hide_lists: false, hide_activity: false };
  const autoLists = ctx.state.autoAddLists !== false;
  const autoNext = !!ctx.state.autoNext;
  const activeTab = tab === "privacy" ? "privacy" : "general";

  ctx.outlet.innerHTML = `
    <div class="block-header">Настройки аккаунта</div>
    <div class="seg-tabs">
      <a class="seg-item ${activeTab === "general" ? "active" : ""}" href="/my/settings">Общие</a>
      <a class="seg-item ${activeTab === "privacy" ? "active" : ""}" href="/my/settings/privacy">Конфиденциальность</a>
    </div>
    ${activeTab === "general" ? `
      <div class="acct-settings">
        <div class="acct-info">
          <div class="acct-row"><span>Имя:</span> <b>${esc(s.name || s.login || s.email || "")}</b></div>
          ${s.handle ? `<div class="acct-row"><span>Профиль:</span> <b><a href="${esc(s.profile_path || "/@" + s.handle)}">/@${esc(s.handle)}</a></b></div>` : ""}
          ${s.email ? `<div class="acct-row"><span>E-mail:</span> <b>${esc(s.email)}</b></div>` : ""}
        </div>
        <label class="acct-toggle">
          <input type="checkbox" id="avAutoAddToggle" ${autoLists ? "checked" : ""} />
          <span>Автоматическое добавление в списки «Смотрю» и «Просмотрено»</span>
        </label>
        <p class="acct-help">Правило «30 дней простоя → Брошено» тоже отключается вместе с этой галкой. Автопереключение серий настраивается прямо на странице аниме под плеером.</p>
        <p class="acct-help">Чтобы изменить имя или пароль, перейдите в
          <a href="${esc(socialSiteUrl())}" target="_blank" rel="noopener">кабинет ${esc(socialSiteName())}&nbsp;↗</a>.</p>
      </div>
    ` : `
      <div class="acct-settings">
        <label class="acct-toggle">
          <input type="checkbox" id="avHideListsToggle" ${privacy.hide_lists ? "checked" : ""} />
          <span>Скрыть мои списки в публичном профиле</span>
        </label>
        <p class="acct-help">Посторонние увидят плашку: «Списки пользователя ограничены настройками приватности». Вы сами видите списки как обычно.</p>
        <label class="acct-toggle">
          <input type="checkbox" id="avHideActivityToggle" ${privacy.hide_activity ? "checked" : ""} />
          <span>Скрыть блок активности в профиле</span>
        </label>
        <p class="acct-help">Скрытая активность не отдаётся публично ни в HTML, ни в JSON — никаких CSS-фокусов.</p>
      </div>
    `}`;

  if (activeTab === "general") {
    document.querySelector("#avAutoAddToggle")?.addEventListener("change", e => store.setAutoAddLists(e.target.checked));
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
    data = await _fetchJson(`/profile/${encodeURIComponent(handle)}/summary`);
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
  const letter = (user.name || user.handle).trim().slice(0, 1).toUpperCase();
  const listsHidden = privacy.hide_lists && !is_owner;
  const activityHidden = privacy.hide_activity && !is_owner;

  const tabs = counts ? LIST_ORDER_MYLISTS.map(s => {
    const label = LIST_LABELS[s].one;
    const c = counts[s] || 0;
    return `<a class="seg-item" href="#lists-${s}" data-status="${s}">
      <span>${esc(label)}</span><span class="seg-count">${c}</span>
    </a>`;
  }).join("") : "";

  ctx.outlet.innerHTML = `
    <div class="profile-page">
      <div class="profile-hero">
        <div class="profile-ava">
          ${user.avatar ? `<img src="${esc(user.avatar)}" alt="" referrerpolicy="no-referrer" />`
                         : `<span class="profile-ava-fallback">${esc(letter)}</span>`}
        </div>
        <div class="profile-info">
          <h1 class="profile-name">${esc(user.name || ("@" + user.handle))}</h1>
          <div class="profile-handle">@${esc(user.handle)}</div>
          ${is_owner ? `<div class="profile-owner-hint">Это ваш профиль · <a href="/my/settings/privacy">настроить приватность</a></div>` : ""}
        </div>
      </div>

      <div class="tabs-block">
        <div class="block-header">Списки</div>
        ${listsHidden
          ? `<div class="privacy-gate">Списки пользователя ограничены настройками приватности</div>`
          : `<div class="seg-tabs profile-seg" id="avProfileTabs">${tabs}</div>
             <div id="avProfileList"></div>`}
      </div>

      <div class="tabs-block">
        <div class="block-header">Активность</div>
        ${activityHidden
          ? `<div class="privacy-gate">Активность скрыта настройками приватности</div>`
          : `<div id="avProfileActivity" class="activity-box"></div>`}
      </div>
    </div>`;

  if (!listsHidden && counts) {
    const loadTab = async (status) => {
      const list = document.querySelector("#avProfileList");
      list.innerHTML = `<div class="av-empty" style="padding:40px 0">Загружаю…</div>`;
      try {
        const r = await _fetchJson(`/profile/${encodeURIComponent(handle)}/lists?status=${status}`);
        if (r.hidden) {
          list.innerHTML = `<div class="privacy-gate">Списки пользователя ограничены настройками приватности</div>`;
          return;
        }
        if (!r.items.length) {
          list.innerHTML = ctx.errorPage({
            title: "В этом списке пока пусто",
            message: "Пользователь ещё не добавил сюда тайтлы.",
            variant: "empty",
          });
          return;
        }
        list.innerHTML = `<div class="av-grid"></div>`;
        const grid = list.querySelector(".av-grid");
        r.items.forEach(v => grid.appendChild(ctx.makeCard({
          mal_id: v.mal_id, title: v.title || "", _ru: v.title || "",
          images: { jpg: { large_image_url: v.poster_url } },
        })));
        if (ctx.enrichWithShiki) ctx.enrichWithShiki(grid, r.items);
      } catch (_) {
        list.innerHTML = ctx.errorPage({
          title: "Не удалось загрузить список",
          message: "Попробуйте обновить страницу — возможно, временный сбой сети.",
          variant: "sad",
        });
      }
    };
    const tabsEl = document.querySelector("#avProfileTabs");
    const initial = LIST_ORDER_MYLISTS.find(s => (counts[s] || 0) > 0) || "watching";
    [...tabsEl.querySelectorAll(".seg-item")].forEach(el => {
      el.classList.toggle("active", el.dataset.status === initial);
      el.addEventListener("click", (e) => {
        e.preventDefault();
        [...tabsEl.querySelectorAll(".seg-item")].forEach(x => x.classList.remove("active"));
        el.classList.add("active");
        loadTab(el.dataset.status);
      });
    });
    loadTab(initial);
  }

  if (!activityHidden && activity) {
    const box = document.querySelector("#avProfileActivity");
    if (box) {
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

function _renderRecentItem(r) {
  const t = r.at ? new Date(r.at).toLocaleString("ru-RU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
  const label = KIND_RU[r.kind] || r.kind;
  const titleText = r.title || (r.mal_id ? `#${r.mal_id}` : "");
  const titleLink = r.mal_id && titleText
    ? `<a href="/anime/${r.mal_id}/">${esc(titleText)}</a>`
    : esc(titleText);
  const epSuffix = (r.kind === "watch_continue" || r.kind === "watch_start") && r.episode_num
    ? `, серия ${r.episode_num}` : "";
  const body = titleText
    ? `<span class="act-label">${esc(label)} <span class="act-title">${titleLink}</span>${esc(epSuffix)}</span>`
    : `<span class="act-label">${esc(label)}</span>`;
  return `<li><span class="act-time">${esc(t)}</span>${body}</li>`;
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
  return new Date(isoDate).toLocaleDateString("ru-RU", {
    day: "numeric", month: "long", year: "numeric",
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
    const cls = d === current ? "act-period active" : "act-period";
    return `<button type="button" class="${cls}" data-days="${d}">${d}д</button>`;
  }).join("");
}

// GitHub contribution graph. Вс сверху, 7 подписей слева, месяцы сверху,
// адаптивный размер клеток через CSS grid + aspect-ratio.
function _buildGhWeeks(days) {
  if (!days || !days.length) return { weeks: [], firstDayShift: 0 };
  const firstDay = new Date(days[0].d);
  // Вс=0, Пн=1, … — GitHub показывает Вс сверху.
  const shift = firstDay.getDay();
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
    const m = new Date(anyDay.d).getMonth();
    // Показываем только если месяц сменился и эта колонка не первая
    // (на первой можем подписать, если в ней ≥3 дня нового месяца — иначе
    // подпись наедет на пустые клетки; GitHub-эвристика).
    const isFirstCol = col === 0;
    const daysOfThisMonthInWeek = w.filter(d => d && new Date(d.d).getMonth() === m).length;
    const showLabel = m !== lastMonth && (!isFirstCol || daysOfThisMonthInWeek >= 4);
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
      const label = `${_ruDateLong(d.d)} · ${d.n} ${_ruActionsWord(d.n)}`;
      return `<button type="button" class="gh-cell gh-b${b}" role="gridcell"
        tabindex="-1"
        data-date="${d.d}" data-count="${d.n}" data-col="${col}" data-row="${row}"
        aria-label="${esc(label)}"></button>`;
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
  const curWord = cur === 1 ? "день" : (cur >= 2 && cur <= 4 ? "дня" : "дней");
  const bestWord = best === 1 ? "день" : (best >= 2 && best <= 4 ? "дня" : "дней");
  return `
    <div class="gh-streaks">
      <div class="gh-streak">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M12 2s2 3 2 6-2 4-2 4 4 0 4 4-3 6-6 6-7-2-7-7c0-5 4-6 4-10 3 1 5 3 5 5z"/>
        </svg>
        <span>Текущая серия: <b>${cur} ${curWord}</b></span>
      </div>
      <div class="gh-streak">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true">
          <path d="M6 3h12v3h3v4a4 4 0 0 1-4 4h-.25A6 6 0 0 1 13 16.93V19h3v2H8v-2h3v-2.07A6 6 0 0 1 7.25 14H7a4 4 0 0 1-4-4V6h3V3zm0 5H5v2a2 2 0 0 0 2 2V8zm12 0v4a2 2 0 0 0 2-2V8h-2z"/>
        </svg>
        <span>Лучшая серия: <b>${best} ${bestWord}</b></span>
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
        <div class="gh-cells" role="grid" aria-label="Активность по дням"
             style="--cols:${cols}">${cellsHtml}</div>
        ${_renderGhLegend()}
      </div>
    </div>
    ${_renderGhStreaks(totals)}
    <div class="gh-tip" id="avGhTip" role="tooltip" hidden></div>
  `;
}

function _renderViewToggle(view) {
  return `
    <div class="gh-view-toggle" role="group" aria-label="Вид активности">
      <button type="button" class="gh-view-btn${view === "grid" ? " active" : ""}"
              data-view="grid">График</button>
      <button type="button" class="gh-view-btn${view === "list" ? " active" : ""}"
              data-view="list">Лента</button>
    </div>`;
}

// Главная точка входа. fetchFn(days) → новые данные графа.
function renderActivityGraph(container, activity, fetchFn) {
  container._fetch = fetchFn || container._fetch;
  // Вид ('grid' | 'list') — хранится сессионно per-device.
  container._view = sessionStorage.getItem("av_activity_view") === "list" ? "list" : "grid";
  _paintActivity(container, activity);

  // Делегирование клика: период-табы.
  container.addEventListener("click", async (e) => {
    const periodBtn = e.target.closest(".act-period");
    if (periodBtn && container._fetch) {
      const days = Number(periodBtn.dataset.days);
      const fresh = await container._fetch(days);
      if (fresh && !fresh.hidden) _paintActivity(container, fresh);
      return;
    }
    const viewBtn = e.target.closest(".gh-view-btn");
    if (viewBtn) {
      const v = viewBtn.dataset.view;
      if (container._view !== v) {
        container._view = v;
        try { sessionStorage.setItem("av_activity_view", v); } catch (_) {}
        // Перерисуем с текущими данными. Активность у нас в контейнере не
        // хранится — просим пере-fetch текущего периода.
        const currentDays = Number(container.querySelector(".act-period.active")?.dataset.days) || 30;
        if (container._fetch) {
          const fresh = await container._fetch(currentDays);
          if (fresh && !fresh.hidden) _paintActivity(container, fresh);
        }
      }
      return;
    }
  });
}

function _paintActivity(container, activity) {
  if (!activity || !Array.isArray(activity.days) || !activity.days.length) {
    container.innerHTML = `
      <div class="act-head">
        <div class="act-period-tabs">${_renderActPeriodTabs(activity?.period_days || 30)}</div>
        ${_renderViewToggle(container._view || "grid")}
      </div>
      <div class="av-empty">Пока нет активности за выбранный период</div>`;
    return;
  }
  const { days, totals, recent, period_days } = activity;
  const view = container._view || "grid";
  const initialRecent = (recent || []).slice(0, 10);
  const initialHasMore = (recent || []).length >= 10;

  const filterTabs = ACT_FILTER_GROUPS.map(g =>
    `<button type="button" class="act-filter-btn${g.key === "all" ? " active" : ""}" data-group="${g.key}">${esc(g.label)}</button>`
  ).join("");

  container.innerHTML = `
    <div class="act-head">
      <div class="act-period-tabs">${_renderActPeriodTabs(period_days || 30)}</div>
      ${_renderViewToggle(view)}
    </div>
    ${view === "grid" ? _renderGhView(totals, days, period_days || 30) : ""}
    <div class="act-recent-head">
      <h4 class="act-recent-title">Недавние действия</h4>
      <div class="act-filter">${filterTabs}</div>
    </div>
    <div class="gh-date-filter" id="avDateFilter" hidden>
      <span>Показано: <b id="avDateFilterText"></b></span>
      <button type="button" class="gh-date-clear" id="avDateClear" aria-label="Снять фильтр по дате">×</button>
    </div>
    <ol class="act-recent" id="avActRecent">${initialRecent.map(_renderRecentItem).join("")}</ol>
    <div class="act-recent-empty" id="avActRecentEmpty" ${initialRecent.length ? "hidden" : ""}>В этом разделе пока нет действий.</div>
    <button type="button" class="act-more" id="avActMore" ${initialHasMore ? "" : "hidden"}>Показать ещё 10</button>
    <div class="act-end" id="avActEnd" ${!initialRecent.length || initialHasMore ? "hidden" : ""}>
      <span class="act-end-dot"></span>
      Это вся активность — дальше ничего нет
      <span class="act-end-dot"></span>
    </div>`;

  _bindGhInteractions(container);
  _bindRecentList(container);
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
      let top = r.top - tr.height - 8 + window.scrollY;
      let left = r.left + r.width / 2 - tr.width / 2 + window.scrollX;
      // Не даём вылезать за viewport
      left = Math.max(8, Math.min(window.innerWidth - tr.width - 8, left));
      if (top < window.scrollY + 8) top = r.bottom + 8 + window.scrollY;
      tip.style.top = `${top}px`;
      tip.style.left = `${left}px`;
      tip.style.visibility = "";
    });
  };
  const hideTip = () => { if (tip) tip.hidden = true; };

  cells.forEach(c => {
    c.addEventListener("mouseenter", () => showTip(c));
    c.addEventListener("mouseleave", hideTip);
    c.addEventListener("focus", () => showTip(c));
    c.addEventListener("blur", hideTip);
    c.addEventListener("click", () => _activateDateFilter(container, c));
    c.addEventListener("keydown", (e) => _handleGhKey(container, e));
  });
  // Первая клетка — единственная в tab-order'е
  const first = cells[0];
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
  container.querySelectorAll(".gh-cell.active").forEach(c => c.classList.remove("active"));
  cell.classList.add("active");
  const filterBar = container.querySelector("#avDateFilter");
  const text = container.querySelector("#avDateFilterText");
  if (filterBar && text) {
    text.textContent = _ruDateLong(date);
    filterBar.hidden = false;
  }
  container._dayFilter = date;
  _reloadRecent(container);
}

function _clearDateFilter(container) {
  container.querySelectorAll(".gh-cell.active").forEach(c => c.classList.remove("active"));
  const filterBar = container.querySelector("#avDateFilter");
  if (filterBar) filterBar.hidden = true;
  container._dayFilter = null;
  _reloadRecent(container);
}

// ==== recent list: filter pills + pagination + date-clear ====
function _bindRecentList(container) {
  const recentBox = container.querySelector("#avActRecent");
  const moreBtn = container.querySelector("#avActMore");
  const emptyBox = container.querySelector("#avActRecentEmpty");
  const endBox = container.querySelector("#avActEnd");
  container._recentGroup = "all";
  container._recentOffset = recentBox?.children.length || 0;
  container._recentBusy = false;

  const setEmpty = (empty) => {
    if (emptyBox) emptyBox.hidden = !empty;
    if (recentBox) recentBox.hidden = !!empty;
  };
  const setTail = (hasMore) => {
    const hasItems = !!recentBox && recentBox.children.length > 0;
    if (moreBtn) moreBtn.hidden = !(hasMore && hasItems);
    if (endBox)  endBox.hidden  = !(!hasMore && hasItems);
  };
  container._setEmpty = setEmpty;
  container._setTail = setTail;

  container.querySelectorAll(".act-filter-btn").forEach(b => {
    b.addEventListener("click", () => {
      if (b.classList.contains("active")) return;
      container.querySelectorAll(".act-filter-btn").forEach(x => x.classList.remove("active"));
      b.classList.add("active");
      container._recentGroup = b.dataset.group;
      _reloadRecent(container);
    });
  });

  moreBtn?.addEventListener("click", async () => {
    if (container._recentBusy || !container._fetchRecent) return;
    container._recentBusy = true;
    moreBtn.disabled = true;
    moreBtn.textContent = "Загружаю…";
    const r = await container._fetchRecent({
      group: container._recentGroup,
      offset: container._recentOffset,
      limit: 10,
      date: container._dayFilter,
    }).catch(() => null);
    if (r && Array.isArray(r.items)) {
      r.items.forEach(item => recentBox.insertAdjacentHTML("beforeend", _renderRecentItem(item)));
      container._recentOffset += r.items.length;
      setTail(!!r.has_more);
    }
    moreBtn.disabled = false;
    moreBtn.textContent = "Показать ещё 10";
    container._recentBusy = false;
  });

  container.querySelector("#avDateClear")?.addEventListener("click", () => {
    _clearDateFilter(container);
  });

  // Начальный хвост: проверяем has_more, основываясь на стартовых данных.
  // recent приходит из ответа /activity, там мы берём первые 10.
  setTail(recentBox?.children.length >= 10);
}

async function _reloadRecent(container) {
  const recentBox = container.querySelector("#avActRecent");
  const moreBtn = container.querySelector("#avActMore");
  const emptyBox = container.querySelector("#avActRecentEmpty");
  if (!container._fetchRecent || !recentBox) return;
  container._recentBusy = true;
  // «Stale-while-revalidate»: старые элементы остаются в DOM, просто
  // приглушаются, пока не приедет ответ. Иначе блок схлопывается в 0 →
  // расширяется обратно = визуальный прыжок.
  recentBox.classList.add("is-loading");
  if (moreBtn) moreBtn.disabled = true;
  const r = await container._fetchRecent({
    group: container._recentGroup,
    offset: 0, limit: 10,
    date: container._dayFilter,
  }).catch(() => null);
  recentBox.classList.remove("is-loading");
  container._recentOffset = 0;
  if (r && Array.isArray(r.items)) {
    if (!r.items.length) {
      recentBox.innerHTML = "";
      if (emptyBox) emptyBox.hidden = false;
      recentBox.hidden = true;
      container._setTail?.(false);
    } else {
      recentBox.innerHTML = r.items.map(_renderRecentItem).join("");
      if (emptyBox) emptyBox.hidden = true;
      recentBox.hidden = false;
      container._recentOffset = r.items.length;
      container._setTail?.(!!r.has_more);
    }
  }
  if (moreBtn) { moreBtn.disabled = false; moreBtn.textContent = "Показать ещё 10"; }
  container._recentBusy = false;
}

// ==== helpers ====
function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}
