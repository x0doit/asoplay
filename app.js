// -*- coding: utf-8 -*-
/*
 * AnimeViev — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
 * All rights reserved. See /COPYRIGHT for full terms.
 *
 * SPA entry point, router and public views. Personal sections (login,
 * favorites, history, watching, settings) live in /js/account.js and own
 * the server-backed store. Guests cannot read or write personal data;
 * any attempt to do so surfaces a guest-gate.
 */

import * as account from "./js/account.js?v=20260430-7";

// ---------- backend URL ----------
// When the page is served by FastAPI (same-origin, port 8787), fetches use
// relative paths. When running under Live Server on :5500 / :3000 / :8000,
// we point at localhost:8787 explicitly. A global override wins over both.
const BACKEND = (() => {
  if (typeof window.AV_BACKEND === "string") return window.AV_BACKEND.replace(/\/+$/, "");
  const p = location.port;
  if (location.protocol === "file:") return "http://localhost:8787";
  if (p === "5500" || p === "3000" || p === "8000") return "http://localhost:8787";
  return "";  // same-origin
})();

const JIKAN_DIRECT = "https://api.jikan.moe/v4";
const ANILIST_DIRECT = "https://graphql.anilist.co";
const JIKAN = () => state.useProxy ? `${BACKEND}/proxy/jikan` : JIKAN_DIRECT;
const ANILIST = () => state.useProxy ? `${BACKEND}/proxy/anilist` : ANILIST_DIRECT;
const imgUrl = (u) => (u && state.useProxy ? `${BACKEND}/proxy/img?url=${encodeURIComponent(u)}` : (u || ""));

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s = "") => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uniq = a => [...new Set(a)];

function errorPage(opts = {}) {
  const { code, title, message, action, secondary, variant = "sad" } = opts;
  const faces = {
    sad: `<svg viewBox="0 0 120 120" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="60" cy="60" r="48"/>
      <path d="M42 52 L52 62 M52 52 L42 62"/>
      <path d="M68 52 L78 62 M78 52 L68 62"/>
      <path d="M45 80 q7 -8 15 0 q8 8 15 0"/>
      <path d="M90 30 q3 -9 6 0 q-3 5 -6 0 z" fill="currentColor" stroke="none"/>
    </svg>`,
    empty: `<svg viewBox="0 0 120 120" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="60" cy="60" r="48"/>
      <path d="M44 54 h10 M66 54 h10"/>
      <path d="M45 82 h30"/>
    </svg>`,
    confused: `<svg viewBox="0 0 120 120" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round">
      <circle cx="60" cy="60" r="48"/>
      <circle cx="47" cy="55" r="3" fill="currentColor"/>
      <circle cx="73" cy="55" r="3" fill="currentColor"/>
      <path d="M45 82 q6 4 15 0 q9 -4 15 0"/>
      <path d="M92 38 q6 -7 10 0 q-2 6 -5 6 v4"/>
      <circle cx="97" cy="55" r="1.5" fill="currentColor" stroke="none"/>
    </svg>`,
  };
  const art = faces[variant] || faces.sad;
  return `
    <div class="err-page">
      ${code ? `<div class="err-code">${esc(String(code))}</div>` : ""}
      <div class="err-art">${art}</div>
      <h1 class="err-title">${esc(title || "")}</h1>
      ${message ? `<p class="err-msg">${typeof message === "string" && message.includes("<") ? message : esc(message)}</p>` : ""}
      ${(action || secondary) ? `
        <div class="err-actions">
          ${action ? `<a class="err-btn err-btn-primary" href="${esc(action.href)}">${esc(action.label)}</a>` : ""}
          ${secondary ? `<a class="err-btn err-btn-ghost" href="${esc(secondary.href)}">${esc(secondary.label)}</a>` : ""}
        </div>` : ""}
    </div>`;
}

// ---------- translation tables (Jikan EN → RU) ----------
const STATUS_RU = {
  "Finished Airing": "Вышло",
  "Currently Airing": "Онгоинг",
  "Not yet aired": "Анонс",
  "On Hiatus": "На паузе",
  "Discontinued": "Прекращён",
};
const TYPE_RU = {
  "TV": "ТВ-сериал", "Movie": "Фильм", "OVA": "OVA", "ONA": "ONA",
  "Special": "Спешл", "Music": "Клип", "TV Special": "ТВ-спешл",
  "PV": "Промо", "CM": "Реклама",
};
const GENRE_RU = {
  "Action": "Экшен", "Adventure": "Приключения", "Cars": "Гонки",
  "Comedy": "Комедия", "Dementia": "Безумие", "Demons": "Демоны",
  "Drama": "Драма", "Ecchi": "Этти", "Fantasy": "Фэнтези",
  "Game": "Игры", "Harem": "Гарем", "Historical": "Исторический",
  "Horror": "Ужасы", "Josei": "Дзёсэй", "Kids": "Детское",
  "Magic": "Магия", "Martial Arts": "Боевые искусства", "Mecha": "Меха",
  "Military": "Военная тематика", "Music": "Музыка", "Mystery": "Детектив",
  "Parody": "Пародия", "Police": "Полицейские", "Psychological": "Психология",
  "Romance": "Романтика", "Samurai": "Самураи", "School": "Школьная жизнь",
  "Sci-Fi": "Фантастика", "Seinen": "Сэйнэн", "Shoujo": "Сёдзё",
  "Shounen": "Сёнэн", "Slice of Life": "Повседневность",
  "Space": "Космос", "Sports": "Спорт", "Super Power": "Суперспособности",
  "Supernatural": "Сверхъестественное", "Thriller": "Триллер",
  "Vampire": "Вампиры", "Award Winning": "Награда",
  "Gourmet": "Кулинария", "Suspense": "Саспенс", "Avant Garde": "Авангард",
  "Boys Love": "Сёнэн-ай", "Girls Love": "Юри",
  "Isekai": "Исэкай", "Mythology": "Мифология", "Time Travel": "Путешествия во времени",
  "Reincarnation": "Реинкарнация", "Gag Humor": "Гэг-юмор",
  "Survival": "Выживание", "Detective": "Детектив",
  "Workplace": "Работа", "Video Game": "Видеоигры", "Adult Cast": "Взрослый каст",
  "Idols (Female)": "Айдолы",
  "Idols (Male)": "Айдолы (муж.)", "Love Polygon": "Любовный многоугольник",
  "Medical": "Медицина", "Otaku Culture": "Отаку-культура",
  "Performing Arts": "Искусства", "Pets": "Питомцы",
  "Racing": "Гонки", "Reverse Harem": "Обратный гарем",
  "Romantic Subtext": "Романтика", "Team Sports": "Командный спорт",
  "Visual Arts": "Изобразительные искусства",
};
const tr = (m, v) => m[v] || v;
const trGenre = v => GENRE_RU[v] || v;
const hasCyrillic = s => /[\u0400-\u04FF]/.test(s || "");

function fitHeroTitle(el) {
  if (!el) return;
  const len = (el.textContent || "").length;
  let fs = "";
  if (len > 180) fs = "clamp(14px, 1.4vw, 18px)";
  else if (len > 140) fs = "clamp(16px, 1.6vw, 22px)";
  else if (len > 100) fs = "clamp(18px, 1.9vw, 26px)";
  else if (len > 70)  fs = "clamp(22px, 2.4vw, 32px)";
  else if (len > 45)  fs = "clamp(26px, 2.8vw, 38px)";
  el.style.fontSize = fs;
}

function ruEpisodeTitle(title, num) {
  const fallback = `\u042d\u043f\u0438\u0437\u043e\u0434 ${num}`;
  if (!title) return fallback;
  const t = String(title).trim();
  if (/^(episode|ep)\s*\.?\s*\d*\s*$/i.test(t)) return fallback;
  if (/^(\u044d\u043f\u0438\u0437\u043e\u0434|\u0441\u0435\u0440\u0438\u044f)\s*\.?\s*\d*\s*$/i.test(t)) return fallback;
  const m = t.match(/^(episode|ep)\s*\.?\s*\d*\s*[:\-–—]\s*(.+)$/i);
  if (m) return `${fallback}: ${m[2]}`;
  if (/^\d+$/.test(t)) return fallback;
  if (hasCyrillic(t)) return t;
  return t;
}

function isGenericEpisodeTitle(title) {
  const t = String(title || "").trim();
  if (!t || /^\d+$/.test(t)) return true;
  return /^(episode|ep)\s*\.?\s*\d*\s*$/i.test(t)
      || /^(\u044d\u043f\u0438\u0437\u043e\u0434|\u0441\u0435\u0440\u0438\u044f)\s*\.?\s*\d*\s*$/i.test(t);
}

async function translateToRu(text) {
  if (!text) return "";
  const max = 4500;
  const raw = text.length > max ? text.slice(0, max) : text;
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 4000);
  try {
    const url = state.useProxy
      ? `${BACKEND}/proxy/translate?q=${encodeURIComponent(raw)}&sl=auto&tl=ru`
      : `https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ru&dt=t&q=${encodeURIComponent(raw)}`;
    const r = await fetch(url, { signal: ctrl.signal });
    if (!r.ok) return "";
    const data = await r.json();
    return (data?.[0] || []).map(x => x?.[0] || "").join("").trim();
  } catch { return ""; }
  finally { clearTimeout(t); }
}

function parseEpDurationMin(s) {
  if (!s) return null;
  const str = String(s);
  const hr = (str.match(/(\d+)\s*hr/i) || [,0])[1];
  const min = (str.match(/(\d+)\s*min/i) || [,0])[1];
  const total = Number(hr) * 60 + Number(min);
  return total > 0 ? total : null;
}

// ---------- title matching ----------
const normTitle = s => (s || "").toLowerCase()
  .replace(/[\u0000-\u001f]/g, "")
  .replace(/[:!?.,'"`()\[\]{}\-–—_\/\\]+/g, " ")
  .replace(/\s+(season|part|cour|tv|ova|ona|movie|sp|special)\s*\d*$/i, "")
  .replace(/\s+\d+$/, "")
  .replace(/\s+/g, " ").trim();

function titleSimilarity(a, b) {
  const A = normTitle(a), B = normTitle(b);
  if (!A || !B) return 0;
  if (A === B) return 1;
  if (A.includes(B) || B.includes(A)) {
    const s = Math.min(A.length, B.length), l = Math.max(A.length, B.length);
    return 0.6 + (s / l) * 0.4;
  }
  const tA = new Set(A.split(" ").filter(t => t.length > 2));
  const tB = new Set(B.split(" ").filter(t => t.length > 2));
  if (!tA.size || !tB.size) return 0;
  const inter = [...tA].filter(t => tB.has(t)).length;
  if (inter <= 1) {
    const denom = Math.max(tA.size, tB.size);
    return (inter / denom) * 0.5;
  }
  return inter / Math.max(tA.size, tB.size);
}

// ---------- state ----------
const state = {
  watch: {},          // populated from /account/history on login
  favorites: {},      // populated from /account/favorites on login
  ratings: {},        // populated from /account/ratings on login
  dubPrefs: {},       // populated from /account/dub-prefs on login
  autoNext: true,     // guests default ON; authed loaded from /account/settings
  backendAvailable: false,
  useProxy: false,
  backendSources: [],
  user: null,         // set by account.setup/refresh
  current: null,      // currently-mounted Player
  social: {           // AnimeSocial config — refilled by account.setup() from /auth/config
    site_name: "AnimeSocial",
    site_url: "https://animesocial.online",
    register_url: "https://animesocial.online/register",
  },
};

// ---------- personal store wrappers (delegate to account module) ----------
// Keeps the existing view code simple: favs.has(id), rating.set(id, v), etc.
// The heavy lifting — network round-trips, cache hydration, guest gates — all
// lives in /js/account.js. Here we keep a synchronous read surface over the
// local cache that account.js fills.
const favs = {
  has: id => account.store.hasFavorite(id),
  toggle: (id, meta) => account.store.toggleFavorite(id, meta),
  list: () => Object.entries(state.favorites)
    .sort(([, a], [, b]) => (b.at || 0) - (a.at || 0))
    .map(([id, v]) => ({ ...v, mal_id: Number(id) })),
};
const rating = {
  get: id => account.store.getRating(id),
  set: (id, value) => account.store.setRating(id, value),
};
const prefs = {
  getDub: id => account.store.getDubPref(id),
  setDub: (id, norm) => account.store.setDubPref(id, norm),
  setAutoNext: on => account.store.setAutoNext(on),
};
function saveProgress(id, ep, time, dur) { account.store.saveProgress(id, ep, time, dur); }
function saveWatchMeta(id, title, cover) {
  // Kept as a no-op stub for Shiki sync — progress/title sync goes through
  // ensureWatchEntry + saveProgress now, so we don't need a separate writer.
  void id; void title; void cover;
}
function ensureWatchEntry(id, title, cover, ep, total) { account.store.ensureWatchEntry(id, title, cover, ep, total); }
function getWatch(id) { return account.store.getWatch(id); }
function getProgress(id, ep) { return account.store.getProgress(id, ep); }

// ---------- lightbox ----------
const lightbox = {
  open(src) {
    this.close();
    const wrap = document.createElement("div");
    wrap.className = "lb-overlay";
    wrap.innerHTML = `
      <div class="lb-close"><svg viewBox="0 0 24 24" width="18" height="18"><path fill="currentColor" d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg></div>
      <img class="lb-img" src="${imgUrl(src)}" alt="" />
    `;
    document.body.appendChild(wrap);
    wrap.addEventListener("click", e => {
      if (!e.target.closest(".lb-img") || e.target.closest(".lb-close")) this.close();
    });
    document.addEventListener("keydown", this._esc = e => { if (e.key === "Escape") this.close(); });
    requestAnimationFrame(() => wrap.classList.add("show"));
    this._el = wrap;
  },
  close() {
    if (this._el) { this._el.remove(); this._el = null; }
    if (this._esc) { document.removeEventListener("keydown", this._esc); this._esc = null; }
  },
};

// ---------- fetch helpers ----------
async function fetchJson(url, opts = {}) {
  const { timeout = 9000, ...rest } = opts;
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeout);
  try {
    const r = await fetch(url, { ...rest, signal: rest.signal || ctl.signal });
    if (!r.ok) {
      const err = new Error(`${r.status}`);
      err.status = r.status;
      try { err.body = await r.text(); } catch (_) {}
      throw err;
    }
    return await r.json();
  } finally { clearTimeout(t); }
}
const debounce = (fn, ms = 400) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };
async function retry429(fn, n = 5, delay = 500) {
  try { return await fn(); }
  catch (e) {
    const msg = String(e);
    if (n && /429|502|503|504/.test(msg)) {
      await new Promise(r => setTimeout(r, delay));
      return retry429(fn, n - 1, Math.min(delay * 1.8, 5000));
    }
    throw e;
  }
}

const season = () => { const m = new Date().getMonth() + 1; return m <= 3 ? "winter" : m <= 6 ? "spring" : m <= 9 ? "summer" : "fall"; };
const SEASON_RU = { winter: "Зима", spring: "Весна", summer: "Лето", fall: "Осень" };
const year = () => new Date().getFullYear();

// ---------- API ----------
const api = {
  search: (q, lim = 24) => retry429(() => fetchJson(`${JIKAN()}/anime?q=${encodeURIComponent(q)}&limit=${lim}&sfw=true&order_by=popularity`)).then(d => d.data || []),
  top: (lim = 18) => retry429(() => fetchJson(`${JIKAN()}/top/anime?limit=${lim}&filter=bypopularity`)).then(d => d.data || []),
  season: (lim = 18) => retry429(() => fetchJson(`${JIKAN()}/seasons/${year()}/${season()}?limit=${lim}&sfw=true`)).then(d => (d.data || []).slice(0, lim)),
  details: id => retry429(() => fetchJson(`${JIKAN()}/anime/${id}/full`)).then(d => d.data),
  characters: id => retry429(() => fetchJson(`${JIKAN()}/anime/${id}/characters`)).then(d => d.data || []).catch(() => []),
  recommendations: id => retry429(() => fetchJson(`${JIKAN()}/anime/${id}/recommendations`)).then(d => d.data || []).catch(() => []),
  byGenre: (g, l = 24) => retry429(() => fetchJson(`${JIKAN()}/anime?genres=${g}&limit=${l}&order_by=popularity&sfw=true`)).then(d => d.data || []),
  genres: () => retry429(() => fetchJson(`${JIKAN()}/genres/anime`)).then(d => (d.data || []).filter(g => !/Hentai|Erotica/i.test(g.name))),
  movies: (lim = 24, page = 1) => retry429(() => fetchJson(`${JIKAN()}/anime?type=movie&order_by=popularity&sfw=true&limit=${lim}&page=${page}`)).then(d => d.data || []),
  random: () => retry429(() => fetchJson(`${JIKAN()}/random/anime?sfw=true&_=${Date.now()}`, { timeout: 15000 })).then(d => d.data),
  catalog: (params) => {
    const usp = new URLSearchParams({ sfw: "true", limit: "24" });
    for (const [k, v] of Object.entries(params)) if (v !== "" && v != null) usp.set(k, v);
    return retry429(() => fetchJson(`${JIKAN()}/anime?${usp.toString()}`)).then(d => ({ data: d.data || [], pagination: d.pagination || {} }));
  },
  seasonNow: (params) => {
    const usp = new URLSearchParams({ sfw: "true", limit: "24" });
    for (const [k, v] of Object.entries(params)) if (v !== "" && v != null) usp.set(k, v);
    return retry429(() => fetchJson(`${JIKAN()}/seasons/now?${usp.toString()}`)).then(d => ({ data: d.data || [], pagination: d.pagination || {} }));
  },
  topPaged: (page = 1) =>
    retry429(() => fetchJson(`${JIKAN()}/top/anime?limit=24&filter=bypopularity&page=${page}`))
      .then(d => ({ data: d.data || [], pagination: d.pagination || {} })),

  trendingRich: async (n = 8) => {
    try {
      const j = await fetchJson(ANILIST(), {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          query: `query ($n:Int){ Page(perPage:$n){ media(type:ANIME, sort:TRENDING_DESC, status_in:[RELEASING,FINISHED]){
            idMal title{romaji english native} coverImage{large extraLarge} bannerImage averageScore episodes format seasonYear genres description(asHtml:false)
          }}}`, variables: { n },
        }),
      });
      const seen = new Set();
      return (j.data?.Page?.media || [])
        .filter(m => m.idMal && m.bannerImage && !seen.has(m.idMal) && (seen.add(m.idMal), true))
        .map(m => ({
          mal_id: m.idMal,
          title: m.title.english || m.title.romaji,
          cover: m.coverImage.extraLarge || m.coverImage.large,
          banner: m.bannerImage,
          score: m.averageScore ? (m.averageScore / 10).toFixed(1) : null,
          episodes: m.episodes, type: m.format, year: m.seasonYear,
          genres: (m.genres || []).slice(0, 4),
          description: (m.description || "").replace(/<[^>]+>/g, "").replace(/&[a-z]+;/g, " ").trim(),
        }));
    } catch { return []; }
  },
  trending: async (lim = 18) => {
    try {
      const j = await fetchJson(ANILIST(), {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          query: `query ($n:Int){ Page(perPage:$n){ media(type:ANIME, sort:TRENDING_DESC){
            idMal title{romaji english} coverImage{large} averageScore episodes format seasonYear genres
          }}}`, variables: { n: lim },
        }),
      });
      const seen = new Set();
      return (j.data?.Page?.media || [])
        .filter(m => m.idMal && !seen.has(m.idMal) && (seen.add(m.idMal), true))
        .map(m => ({
          mal_id: m.idMal, title: m.title.english || m.title.romaji,
          images: { jpg: { large_image_url: m.coverImage.large, image_url: m.coverImage.large } },
          score: m.averageScore ? (m.averageScore / 10).toFixed(1) : null,
          episodes: m.episodes, type: m.format, year: m.seasonYear,
          genres: (m.genres || []).map(g => ({ name: g })),
        }));
    } catch { return []; }
  },

  shiki: async id => { try { return await fetchJson(`${BACKEND}/shiki/anime/${id}`, { timeout: 6000 }); } catch { return null; } },
  shikiBatch: async ids => {
    const list = ids.filter(Boolean).slice(0, 50);
    if (!list.length) return {};
    try { return await fetchJson(`${BACKEND}/shiki/batch?ids=${list.join(",")}`, { timeout: 6000 }); } catch { return {}; }
  },
  shikiSearch: async (q, lim = 24, page = 1) => {
    try {
      return await fetchJson(
        `${BACKEND}/shiki/search?q=${encodeURIComponent(q)}&limit=${lim}&page=${page}`,
        { timeout: 8000 }
      );
    } catch { return []; }
  },
  anilistByMal: async id => {
    try {
      const j = await fetchJson(ANILIST(), {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          query: `query($id:Int){Media(idMal:$id,type:ANIME){bannerImage coverImage{extraLarge large} description(asHtml:false) tags{name}}}`,
          variables: { id },
        }),
        timeout: 6000,
      });
      return j.data?.Media || null;
    } catch { return null; }
  },
  async pingBackend() {
    try {
      const j = await fetchJson(`${BACKEND}/health`, { timeout: 1500 });
      return { ok: !!j.ok, sources: j.sources || [], vpn: !!j.vpn };
    } catch { return { ok: false, sources: [], vpn: false }; }
  },
  backendSearch: (q, s, yr, mal) => fetchJson(`${BACKEND}/src/search?q=${encodeURIComponent(q)}&source=${s}${yr ? `&year=${yr}` : ""}${mal ? `&mal_id=${mal}` : ""}`, { timeout: 12000 }),
  backendEpisodes: (k, s) => fetchJson(`${BACKEND}/src/episodes?key=${encodeURIComponent(k)}&source=${s}`, { timeout: 12000 }),
  backendSources: (k, s) => fetchJson(`${BACKEND}/src/dubs?key=${encodeURIComponent(k)}&source=${s}`, { timeout: 15000 }),
  backendVideos: (k, s) => fetchJson(`${BACKEND}/src/videos?key=${encodeURIComponent(k)}&source=${s}`, { timeout: 20000 }),

  recordTitlePage: (payload) => fetch(`${BACKEND}/title-pages/record`, {
    method: "POST",
    credentials: "include",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  }).catch(() => null),
};

// ---------- cards + grid ----------
function makeCard(a) {
  const link = document.createElement("a");
  link.className = "av-card";
  link.href = `/anime/${a.mal_id}/`;
  link.dataset.mal = a.mal_id;

  const poster = a.images?.jpg?.large_image_url || a.images?.jpg?.image_url || a.cover || "";
  const title = a._ru || a.title_english || a.title || "—";
  const statusRu = tr(STATUS_RU, a.status || "");
  const statusCls = statusRu === "Онгоинг" ? "ongoing" : statusRu === "Вышло" ? "released" : statusRu === "Анонс" ? "announce" : "";
  const meta = [];
  if (a.episodes) meta.push(`${a.episodes} эп.`);
  if (a.year || a.aired?.prop?.from?.year) meta.push(a.year || a.aired.prop.from.year);
  const typeRu = a.type ? tr(TYPE_RU, a.type) : "";
  const imgSrc = poster ? imgUrl(poster) : "";
  link.innerHTML = `
    <div class="av-card-img">
      <img src="${imgSrc}" loading="lazy" alt="${esc(title)}"
           onerror="this.dataset.broken='1';this.removeAttribute('src');" />
      ${a.score ? `<span class="av-badge-score show">★ ${esc(String(a.score))}</span>` : ""}
      ${typeRu ? `<span class="av-badge-type show">${esc(typeRu)}</span>` : ""}
      ${statusRu ? `<span class="av-badge-status ${statusCls} show">${esc(statusRu)}</span>` : ""}
      <div class="av-card-overlay">
        <div class="av-card-play">
          <svg viewBox="0 0 24 24" width="22" height="22"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
        </div>
      </div>
    </div>
    <div class="av-card-body">
      <div class="av-card-title" title="${esc(title)}">${esc(title)}</div>
      <div class="av-card-meta">${meta.join(" · ")}</div>
    </div>`;
  let prefetched = false;
  const warm = () => {
    if (prefetched) return;
    prefetched = true;
    fetch(`${JIKAN()}/anime/${a.mal_id}/full`).catch(() => {});
  };
  link.addEventListener("mouseenter", warm, { once: true });
  link.addEventListener("touchstart", warm, { once: true, passive: true });
  return link;
}

async function enrichWithShiki(container, list, limit) {
  if (!state.backendAvailable) return;
  const ids = list.map(a => a.mal_id).filter(Boolean);
  if (!ids.length) return;
  const map = await api.shikiBatch(ids);
  $$(".av-card", container).forEach(c => {
    const id = c.dataset.mal;
    const entry = map?.[id];
    if (!entry) return;
    if (entry.russian) {
      const t = $(".av-card-title", c);
      if (t) { t.textContent = entry.russian; t.title = entry.russian; }
      const img = $(".av-card-img img", c);
      if (img) img.alt = entry.russian;
    }
    // Доливаем обложку, если её не было или она битая (пустой src / крестик
    // на сломанном домене). Shikimori image шлёт поле вида
    //   { original: "/system/animes/original/XXXX.jpg", preview: "...", "x96": "..." }
    // Превращаем в абсолютный URL + проксируем через /proxy/img если VPN.
    const img = $(".av-card-img img", c);
    if (img) {
      const shikiImg = entry.image;
      const shikiPath = shikiImg?.preview || shikiImg?.original || shikiImg?.x96 || "";
      const shikiUrl = shikiPath
        ? (shikiPath.startsWith("http") ? shikiPath : "https://shikimori.one" + shikiPath)
        : "";
      const needReplace = !img.getAttribute("src")
                        || img.getAttribute("src") === ""
                        || img.dataset.broken === "1";
      if (shikiUrl && needReplace) {
        img.src = imgUrl(shikiUrl);
        img.dataset.broken = "";
      }
    }
  });
  const seenTitle = new Set();
  $$(".av-card", container).forEach(c => {
    const t = $(".av-card-title", c);
    const key = normTitle(t?.textContent || c.dataset.mal);
    if (!key) return;
    if (seenTitle.has(key)) c.remove();
    else seenTitle.add(key);
  });
  if (limit) {
    const cards = $$(".av-card", container);
    for (let i = limit; i < cards.length; i++) cards[i].remove();
  }
}

const NON_ANIME_TYPES = new Set(["Music", "CM", "PV"]);
const MIN_EPISODE_DURATION = 8;
const MIN_MOVIE_DURATION = 40;

function _isRealAnime(a, ctx = "") {
  if (NON_ANIME_TYPES.has(a.type)) return false;
  const dur = parseEpDurationMin(a.duration);
  if (dur != null) {
    if (a.type === "Movie" || ctx === "movies") {
      if (dur < MIN_MOVIE_DURATION) return false;
    } else if (dur < MIN_EPISODE_DURATION) return false;
  }
  return true;
}

function renderGrid(el, list, limit, ctx = "") {
  el.innerHTML = "";
  const renderEmpty = () => {
    el.innerHTML = errorPage(ctx === "search" ? {
      title: "По вашему запросу ничего не найдено",
      message: "Попробуйте изменить название, убрать часть фильтров или поискать по другому критерию.",
      action: { href: "/catalog", label: "Сбросить фильтры" },
      secondary: { href: "/", label: "На главную" },
      variant: "confused",
    } : {
      title: "Здесь пока пусто",
      message: "Возможно, источник временно недоступен или под фильтры ничего не подошло.",
      action: { href: "/", label: "На главную" },
      variant: "empty",
    });
  };
  if (!list?.length) { renderEmpty(); return; }
  const seen = new Set();
  const unique = list.filter(a => {
    if (!_isRealAnime(a, ctx)) return false;
    if (!a.mal_id) return false;
    if (seen.has(a.mal_id)) return false;
    seen.add(a.mal_id);
    return true;
  });
  if (!unique.length) { renderEmpty(); return; }
  const sliced = limit ? unique.slice(0, limit) : unique;
  const frag = document.createDocumentFragment();
  sliced.forEach(a => frag.appendChild(makeCard(a)));
  el.appendChild(frag);
  enrichWithShiki(el, sliced, limit);
}
function renderSkeletons(el, n = 12) {
  el.innerHTML = "";
  for (let i = 0; i < n; i++) { const d = document.createElement("div"); d.className = "av-skeleton"; el.appendChild(d); }
}

// ---------- hero carousel ----------
let heroTimer = null;
async function renderHeroCarousel(container) {
  container.innerHTML = `
    <div class="hero-carousel" id="avHero">
      <div id="avHeroSlides"></div>
      <div class="hero-ctrl">
        <button class="hero-nav prev" aria-label="Предыдущий">
          <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M15.5 4 7.5 12l8 8 1.4-1.4L10.3 12l6.6-6.6z"/></svg>
        </button>
        <div class="hero-dots"></div>
        <button class="hero-nav next" aria-label="Следующий">
          <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M8.5 4 7.1 5.4 13.7 12l-6.6 6.6L8.5 20l8-8z"/></svg>
        </button>
      </div>
    </div>`;

  const slides = await api.trendingRich(8).catch(() => []);
  if (!slides.length) { container.innerHTML = ""; return; }
  if (state.backendAvailable) {
    const map = await api.shikiBatch(slides.map(s => s.mal_id));
    slides.forEach(s => { const ru = map?.[s.mal_id]?.russian; if (ru) s.title = ru; });
  }
  {
    const seen = new Set();
    for (let i = slides.length - 1; i >= 0; i--) {
      const k = normTitle(slides[i].title);
      if (seen.has(k)) slides.splice(i, 1);
      else seen.add(k);
    }
  }

  const sw = $("#avHeroSlides", container);
  const dots = $(".hero-dots", container);
  slides.forEach((s, i) => {
    const el = document.createElement("div");
    el.className = "hero-slide" + (i === 0 ? " active" : "");
    el.innerHTML = `
      <div class="hero-bg" style="background-image:url('${imgUrl(s.banner)}')"></div>
      <div class="hero-content">
        <span class="hero-badge">#${i + 1} В тренде</span>
        <h1 class="hero-title">${esc(s.title)}</h1>
        <div class="hero-stats">
          ${s.score ? `<span>★ <b>${s.score}</b></span>` : ""}
          ${s.episodes ? `<span>${s.episodes} эп.</span>` : ""}
          ${s.type ? `<span>${tr(TYPE_RU, s.type)}</span>` : ""}
          ${s.year ? `<span>${s.year}</span>` : ""}
          ${s.genres?.length ? `<span>${s.genres.map(trGenre).join(", ")}</span>` : ""}
        </div>
        <p class="hero-desc">${esc(s.description).slice(0, 280)}</p>
        <div class="hero-actions">
          <a class="btn btn-primary" href="/anime/${s.mal_id}/">
            <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
            Смотреть
          </a>
        </div>
      </div>`;
    sw.appendChild(el);
    const dot = document.createElement("button");
    if (i === 0) dot.classList.add("active");
    dot.addEventListener("click", () => goTo(i));
    dots.appendChild(dot);
  });

  let idx = 0;
  const go = n => {
    idx = (n + slides.length) % slides.length;
    $$(".hero-slide", container).forEach((el, i) => el.classList.toggle("active", i === idx));
    $$(".hero-dots button", container).forEach((el, i) => el.classList.toggle("active", i === idx));
  };
  function goTo(n) { clearInterval(heroTimer); go(n); heroTimer = setInterval(() => go(idx + 1), 6500); }
  $(".hero-nav.prev", container).addEventListener("click", () => goTo(idx - 1));
  $(".hero-nav.next", container).addEventListener("click", () => goTo(idx + 1));
  heroTimer = setInterval(() => go(idx + 1), 6500);

  slides.forEach(async (s, i) => {
    if (!s.description || hasCyrillic(s.description)) return;
    let ru = "";
    try {
      const shiki = await api.shiki(s.mal_id);
      const cleaned = cleanBBCode(shiki?.description || "");
      if (cleaned && hasCyrillic(cleaned)) ru = cleaned;
    } catch {}
    if (!ru) ru = await translateToRu(s.description);
    if (!ru) return;
    const slideEl = $$(".hero-slide", container)[i];
    const p = slideEl?.querySelector(".hero-desc");
    if (p) p.textContent = ru.slice(0, 280);
  });
}

// ---------- views: catalog family ----------
function setActiveNav(view) {
  $$("#topMenu [data-view]").forEach(el => {
    const li = el.closest("li");
    if (!li) return;
    li.classList.toggle("active", el.dataset.view === view);
  });
}

async function viewHome() {
  setActiveNav("home");
  clearInterval(heroTimer);
  $("#outlet").innerHTML = `
    <div id="avCarousel"></div>
    <div class="tabs-block">
      <div class="block-header">Топ по популярности <a href="/top" class="block-header-link">Все →</a></div>
      <div class="av-grid" id="topGrid"></div>
    </div>
    <div class="tabs-block">
      <div class="block-header">Текущий сезон <a href="/season" class="block-header-link">Весь сезон →</a></div>
      <div class="av-grid" id="seasonGrid"></div>
    </div>
    <div class="tabs-block">
      <div class="block-header">Сейчас смотрят <a href="/trending" class="block-header-link">Все →</a></div>
      <div class="av-grid" id="trendGrid"></div>
    </div>`;
  renderHeroCarousel($("#avCarousel"));
  ["topGrid", "seasonGrid", "trendGrid"].forEach(id => renderSkeletons($(`#${id}`), 12));
  try {
    const [t, s, tr] = await Promise.all([api.top(18), api.season(18), api.trending(18).catch(() => [])]);
    renderGrid($("#topGrid"), t, 12);
    renderGrid($("#seasonGrid"), s, 12);
    renderGrid($("#trendGrid"), tr, 12);
  } catch (e) {
    $("#topGrid").innerHTML = `<div class="av-empty">Ошибка: ${esc(e.message)}</div>`;
  }
}

let _genresCache = null;
async function getGenres() {
  if (_genresCache) return _genresCache;
  try { _genresCache = await api.genres(); } catch { _genresCache = []; }
  return _genresCache;
}

const CATALOG_TITLES = {
  top: "Топ по популярности",
  season: null,
  trending: "Сейчас в тренде",
  movies: "Полнометражные аниме",
  search: "Поиск",
};

// ====== Shikimori → Jikan-shape ======
// Русскоязычный поиск идёт через Shikimori /api/animes. Его ответ не совместим
// с тем, что принимает makeCard()/_isRealAnime(), поэтому здесь мы нормализуем
// каждый объект под ожидаемую Jikan-структуру. Ничего больше — никакой бизнес-
// логики. Это чистый формат-адаптер.
const SHIKI_KIND_TO_JIKAN = {
  tv: "TV", movie: "Movie", ova: "OVA", ona: "ONA",
  special: "Special", tv_special: "TV Special",
  music: "Music", pv: "PV", cm: "CM",
};
function _shikiToJikan(a) {
  if (!a || !a.id) return null;
  const raw = a.image?.original || a.image?.preview || a.image?.x96 || "";
  const image_url = raw
    ? (raw.startsWith("http") ? raw : "https://shikimori.one" + raw)
    : "";
  const ru = (a.russian || "").trim();
  const en = (a.name || "").trim();
  const year = a.aired_on && /^\d{4}/.test(a.aired_on)
    ? Number(a.aired_on.slice(0, 4)) : null;
  const score = a.score ? Number(a.score) : null;
  return {
    mal_id: Number(a.id),
    title: ru || en,
    title_english: en,
    _ru: ru || undefined,
    type: SHIKI_KIND_TO_JIKAN[a.kind] || a.kind || "",
    episodes: a.episodes || null,
    year,
    score: score && !Number.isNaN(score) && score > 0 ? score : null,
    images: { jpg: { large_image_url: image_url, image_url } },
  };
}

function _catalogDefaults(kind) {
  if (kind === "top") return { order_by: "score", sort: "desc", min_score: "7" };
  if (kind === "season") {
    const ranges = { winter: ["01-01", "03-31"], spring: ["04-01", "06-30"], summer: ["07-01", "09-30"], fall: ["10-01", "12-31"] };
    const [f, t] = ranges[season()];
    return { start_date: `${year()}-${f}`, end_date: `${year()}-${t}`, order_by: "popularity" };
  }
  if (kind === "trending") return { order_by: "popularity", status: "airing", min_score: "6.5" };
  if (kind === "movies") return { type: "movie", order_by: "popularity", min_score: "6.5" };
  return { order_by: "popularity" };
}

async function viewCatalog(kind, queryStr) {
  setActiveNav(kind);
  const p = new URLSearchParams(queryStr || "");
  const page = Math.max(1, Number(p.get("page") || 1));
  const q = p.get("q") || "";
  const type = p.get("type") || "";
  const status = p.get("status") || "";
  const fromYear = p.get("from_year") || "";
  const toYear = p.get("to_year") || "";
  const genres = (p.get("genres") || "").split(",").filter(Boolean);
  const genresAll = p.get("genres_all") === "1";
  const order = p.get("order_by") || "";
  const sort = p.get("sort") || "";

  const isSearch = kind === "search";
  const title = kind === "season" ? `Сезон ${tr(SEASON_RU, season())} ${year()}` : CATALOG_TITLES[kind];
  const typeFixed = kind === "movies";

  $("#outlet").innerHTML = `
    <div class="block-header catalog-head">
      <span class="catalog-title">${esc(title)}</span>
      <div class="block-header-tools">
        <button type="button" class="filter-btn" id="filterToggle">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M10 18h4v-2h-4v2zM3 6v2h18V6H3zm3 7h12v-2H6v2z"/></svg>
          Фильтр<span class="filter-count" id="filterCount"></span>
        </button>
      </div>
    </div>
    ${isSearch ? `
      <form class="search-form" id="searchForm" onsubmit="return false" style="margin-bottom: 14px;">
        <div class="search-form-row main">
          <input type="text" class="search-form-input" id="sfQ" placeholder="Название аниме…" value="${esc(q)}" autocomplete="off" />
          <button type="submit" class="btn btn-primary">Найти</button>
        </div>
      </form>` : ""}
    <div class="catalog-filter" id="catalogFilter" hidden>
      <div class="cf-row">
        ${!typeFixed ? `
        <div class="cf-field">
          <label>Тип</label>
          <select id="cfType">
            <option value="">Любой</option>
            <option value="tv" ${type === "tv" ? "selected" : ""}>Сериал</option>
            <option value="movie" ${type === "movie" ? "selected" : ""}>Фильм</option>
            <option value="ova" ${type === "ova" ? "selected" : ""}>OVA</option>
            <option value="ona" ${type === "ona" ? "selected" : ""}>ONA</option>
            <option value="special" ${type === "special" ? "selected" : ""}>Спешл</option>
            <option value="music" ${type === "music" ? "selected" : ""}>Клип</option>
          </select>
        </div>` : ""}
        <div class="cf-field">
          <label>Статус</label>
          <select id="cfStatus">
            <option value="">Любой</option>
            <option value="airing" ${status === "airing" ? "selected" : ""}>Онгоинг</option>
            <option value="complete" ${status === "complete" ? "selected" : ""}>Вышло</option>
            <option value="upcoming" ${status === "upcoming" ? "selected" : ""}>Анонс</option>
          </select>
        </div>
        <div class="cf-field">
          <label>Год (от / до)</label>
          <div class="cf-year">
            <input type="number" id="cfFrom" placeholder="от" min="1960" max="${year() + 1}" value="${esc(fromYear)}" />
            <input type="number" id="cfTo" placeholder="до" min="1960" max="${year() + 1}" value="${esc(toYear)}" />
          </div>
        </div>
        <div class="cf-field">
          <label>Сортировка</label>
          <select id="cfOrder">
            <option value="">По умолчанию</option>
            <option value="popularity" ${order === "popularity" ? "selected" : ""}>По популярности</option>
            <option value="score" ${order === "score" ? "selected" : ""}>По рейтингу</option>
            <option value="start_date" ${order === "start_date" ? "selected" : ""}>По дате выхода</option>
            <option value="title" ${order === "title" ? "selected" : ""}>По названию</option>
            <option value="episodes" ${order === "episodes" ? "selected" : ""}>По количеству серий</option>
          </select>
          <label class="cf-sort-toggle">
            <input type="checkbox" id="cfSortAsc" ${sort === "asc" ? "checked" : ""} />
            <span>по возрастанию</span>
          </label>
        </div>
      </div>
      <div class="cf-row">
        <div class="cf-field wide">
          <label>Жанры (можно несколько)</label>
          <div class="cf-genres" id="cfGenres"><span class="muted">Загрузка…</span></div>
          <label class="cf-sort-toggle" style="margin-top: 10px;">
            <input type="checkbox" id="cfGenresAll" ${genresAll ? "checked" : ""} />
            <span>Аниме должно содержать каждый из выбранных жанров</span>
          </label>
        </div>
      </div>
      <div class="cf-actions">
        <button type="button" class="btn btn-primary" id="cfApply">Применить</button>
        <button type="button" class="btn btn-ghost" id="cfReset">Сбросить</button>
      </div>
    </div>
    <div class="av-grid" id="g"></div>
    <div class="pager" id="pager"></div>`;

  const activeCount = [q, type, status, fromYear, toYear, order, sort, genres.length ? "y" : ""].filter(Boolean).length;
  if (activeCount) $("#filterCount").textContent = ` · ${activeCount}`;

  getGenres().then(list => {
    const box = $("#cfGenres");
    if (!box) return;
    box.innerHTML = "";
    list.forEach(g => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "cf-chip" + (genres.includes(String(g.mal_id)) ? " active" : "");
      chip.dataset.id = g.mal_id;
      chip.textContent = trGenre(g.name);
      chip.addEventListener("click", () => chip.classList.toggle("active"));
      box.appendChild(chip);
    });
  });

  const applyFilters = () => {
    const params = new URLSearchParams();
    if (isSearch) {
      const qv = $("#sfQ")?.value.trim();
      if (qv) params.set("q", qv);
    } else if (q) params.set("q", q);
    if (!typeFixed) {
      const t = $("#cfType")?.value; if (t) params.set("type", t);
    }
    const st = $("#cfStatus")?.value; if (st) params.set("status", st);
    const fy = $("#cfFrom")?.value.trim(); if (fy) params.set("from_year", fy);
    const ty = $("#cfTo")?.value.trim(); if (ty) params.set("to_year", ty);
    const od = $("#cfOrder")?.value; if (od) params.set("order_by", od);
    if ($("#cfSortAsc")?.checked) params.set("sort", "asc");
    const active = [...$$("#cfGenres .cf-chip.active")].map(b => b.dataset.id).join(",");
    if (active) params.set("genres", active);
    if ($("#cfGenresAll")?.checked && active.includes(",")) params.set("genres_all", "1");
    const qs = params.toString();
    nav(qs ? `/${catalogPathFor(kind)}?${qs}` : `/${catalogPathFor(kind)}`);
  };

  $("#filterToggle").addEventListener("click", () => {
    const f = $("#catalogFilter");
    f.hidden = !f.hidden;
  });
  if (activeCount) $("#catalogFilter").hidden = false;
  $("#cfApply").addEventListener("click", applyFilters);
  $("#cfReset").addEventListener("click", () => nav(`/${catalogPathFor(kind)}`));
  $("#searchForm")?.addEventListener("submit", e => { e.preventDefault(); applyFilters(); });

  const g = $("#g");
  renderSkeletons(g, 24);
  try {
    let data, pagination;
    const seasonSimple = kind === "season" && !genres.length && !fromYear && !toYear && !q;
    // Поиск на русском — MAL/Jikan не индексирует русские названия, поэтому при
    // кириллическом запросе мы идём через Shikimori и нормализуем его ответ
    // в Jikan-формат, чтобы остальной рендер не изменился.
    const russianSearch = isSearch && q && hasCyrillic(q);

    if (seasonSimple) {
      const p = { page: String(page) };
      if (type) p.filter = type;
      ({ data, pagination } = await api.seasonNow(p));
    } else if (russianSearch) {
      const raw = await api.shikiSearch(q, 24, page);
      const list = Array.isArray(raw) ? raw : (raw?.data || []);
      data = list.map(_shikiToJikan).filter(Boolean);
      // Shikimori не отдаёт pagination.last_visible_page. Чтобы у пользователя
      // был пэйджер, эвристика: если на странице полный 24-row блок, считаем,
      // что может быть ещё одна. Jikan-совместимая форма.
      pagination = {
        last_visible_page: list.length >= 24 ? page + 1 : page,
        has_next_page: list.length >= 24,
      };
    } else {
      const defaults = _catalogDefaults(kind);
      const baseParams = { ...defaults, page: String(page) };
      if (q) baseParams.q = q;
      if (!typeFixed && type) baseParams.type = type;
      else if (typeFixed) baseParams.type = defaults.type;
      if (status) baseParams.status = status;
      if (fromYear) baseParams.start_date = `${fromYear}-01-01`;
      if (toYear) baseParams.end_date = `${toYear}-12-31`;
      if (order) baseParams.order_by = order;
      if (sort) baseParams.sort = sort;

      if (genres.length <= 1 || genresAll) {
        const params = { ...baseParams };
        if (genres.length) params.genres = genres.join(",");
        ({ data, pagination } = await api.catalog(params));
      } else {
        const results = await Promise.all(
          genres.map(gid => api.catalog({ ...baseParams, genres: gid }).catch(() => ({ data: [], pagination: {} })))
        );
        const seen = new Set();
        const merged = [];
        let maxPage = 0;
        for (const r of results) {
          for (const item of r.data || []) {
            if (!item?.mal_id || seen.has(item.mal_id)) continue;
            seen.add(item.mal_id);
            merged.push(item);
          }
          const lp = r.pagination?.last_visible_page || 0;
          if (lp > maxPage) maxPage = lp;
        }
        data = merged;
        pagination = { last_visible_page: maxPage, has_next_page: maxPage > page };
      }
    }
    renderGrid(g, data, 24, kind);
    renderPager($("#pager"), kind, queryStr || "", page, pagination);
  } catch (e) { g.innerHTML = `<div class="av-empty">${esc(e.message)}</div>`; }
}

function catalogPathFor(kind) { return kind === "search" ? "catalog" : kind; }

function renderPager(el, kind, queryStr, page, pag) {
  el.innerHTML = "";
  const last = pag?.last_visible_page || (pag?.has_next_page ? page + 1 : page);
  if (last <= 1) return;
  const frag = document.createDocumentFragment();
  const mkBtn = (label, targetPage, opts = {}) => {
    const b = document.createElement(opts.disabled ? "span" : "a");
    b.className = "pager-btn" + (opts.current ? " current" : "") + (opts.disabled ? " disabled" : "");
    b.textContent = label;
    if (!opts.disabled) {
      const p = new URLSearchParams(queryStr || "");
      if (targetPage <= 1) p.delete("page");
      else p.set("page", targetPage);
      b.href = `/${catalogPathFor(kind)}${p.toString() ? "?" + p.toString() : ""}`;
    }
    return b;
  };
  frag.appendChild(mkBtn("←", page - 1, { disabled: page <= 1 }));
  const pages = new Set([1, last, page, page - 1, page + 1, page - 2, page + 2]);
  const sorted = [...pages].filter(n => n >= 1 && n <= last).sort((a, b) => a - b);
  let prev = 0;
  for (const n of sorted) {
    if (n - prev > 1) {
      const dot = document.createElement("span");
      dot.className = "pager-dots";
      dot.textContent = "…";
      frag.appendChild(dot);
    }
    frag.appendChild(mkBtn(String(n), n, { current: n === page }));
    prev = n;
  }
  frag.appendChild(mkBtn("→", page + 1, { disabled: page >= last }));
  el.appendChild(frag);
}

async function viewRandom() {
  setActiveNav("random");
  const outlet = $("#outlet");
  outlet.innerHTML = `
    <div class="random-loader">
      <div class="random-spinner"></div>
      <div class="random-title">Ищу что-нибудь интересное…</div>
      <div class="random-status" id="avRandStatus">Выбираю случайное аниме из каталога…</div>
      <div class="random-progress" id="avRandProgress"></div>
    </div>`;
  const setStatus = (txt) => { const el = $("#avRandStatus"); if (el) el.textContent = txt; };
  const setProgress = (i, max) => { const el = $("#avRandProgress"); if (el) el.textContent = max ? `Попытка ${i} из ${max}` : ""; };

  for (let i = 0; i < 3; i++) {
    setProgress(i + 1, 3);
    try {
      const r = await fetchJson(`${BACKEND}/random/watchable?_=${Date.now()}`, { timeout: 25000 });
      if (r?.mal_id) {
        setStatus(`Открываю: ${r.title || 'MAL #' + r.mal_id}`);
        nav(`/anime/${r.mal_id}/`);
        return;
      }
    } catch { /* retry */ }
    await new Promise(r => setTimeout(r, 400));
  }
  outlet.innerHTML = errorPage({
    title: "Каталог временно недоступен",
    message: "Не удалось получить случайное аниме. Проблема обычно на стороне yummy/Jikan и быстро проходит.",
    action: { href: "/random", label: "Попробовать ещё раз" },
    secondary: { href: "/", label: "На главную" },
  });
}

async function viewGenre(id, name) {
  setActiveNav(null);
  $("#outlet").innerHTML = `<div class="block-header">Жанр: ${esc(name)}</div><div class="av-grid" id="g"></div>`;
  const g = $("#g"); renderSkeletons(g, 18);
  try { renderGrid(g, await api.byGenre(id, 24)); }
  catch (e) { g.innerHTML = `<div class="av-empty">${esc(e.message)}</div>`; }
}

// ---------- anime detail ----------
function cleanBBCode(s) {
  return (s || "")
    .replace(/\[\/?(b|i|u|s|url[^\]]*|character[^\]]*|anime[^\]]*|manga[^\]]*|ranobe[^\]]*|person[^\]]*|studio[^\]]*|spoiler[^\]]*)\]/gi, "")
    .replace(/\[br\]/gi, "\n").trim();
}

async function viewAnime(malId) {
  setActiveNav(null);
  $("#outlet").innerHTML = `<div class="av-empty" style="padding:80px 0">Загружаю…</div>`;

  const preload = _consumeSSRPreload(malId);
  const jikanP = api.details(malId);
  const shikiP = api.shiki(malId).catch(() => null);
  const anilistP = api.anilistByMal(malId).catch(() => null);
  const charsP = api.characters(malId).catch(() => []);
  const recsP = api.recommendations(malId).catch(() => []);

  let a;
  try { a = await jikanP; }
  catch (e) {
    const msg = String(e.message || "");
    if (msg.includes("404")) {
      // MAL ID может быть удалён (например, /anime/2 — обычный тестовый id,
      // у которого в MyAnimeList давно ничего нет). Если в URL был slug, пробуем
      // поиском подобрать реальный тайтл и предлагаем перейти на него.
      const slugMatch = /^\/anime\/(\d+)(?:-([a-z0-9-]+))?/.exec(location.pathname);
      const slug = slugMatch?.[2] || "";
      let suggest = null;
      if (slug) {
        const q = slug.replace(/-/g, " ").trim();
        if (q.length >= 2) {
          try {
            const rs = await api.search(q, 3);
            const best = (rs || []).find(x => x?.mal_id && x.mal_id !== malId);
            if (best) suggest = best;
          } catch (_) {}
        }
      }
      const ru = suggest ? (await api.shikiBatch([suggest.mal_id]).catch(() => ({})))?.[suggest.mal_id]?.russian : null;
      const suggestedTitle = ru || suggest?.title_english || suggest?.title || "";
      $("#outlet").innerHTML = errorPage({
        code: "404",
        title: "Этот идентификатор удалён в MyAnimeList",
        message: suggest
          ? `Похоже, вы искали «${esc(suggestedTitle)}». Запись #${malId} в MyAnimeList больше не существует.`
          : `Запись с идентификатором ${malId} отсутствует в MyAnimeList — возможно, она была удалена или перенесена. Попробуйте поиск по названию.`,
        action: suggest
          ? { href: `/anime/${suggest.mal_id}/`, label: `Открыть «${suggestedTitle}»` }
          : { href: "/catalog", label: "Поиск по названию" },
        secondary: { href: "/", label: "На главную" },
        variant: "confused",
      });
    } else {
      const is5xx = /\b(5\d\d)\b/.test(msg);
      $("#outlet").innerHTML = errorPage({
        code: is5xx ? (msg.match(/\b(5\d\d)\b/) || [])[1] : "",
        title: "Не удалось загрузить аниме",
        message: is5xx
          ? "Внешний источник временно недоступен. Обычно это самоустраняется за минуту."
          : `Что-то пошло не так: ${msg}.`,
        action: { href: `/anime/${malId}/`, label: "Повторить" },
        secondary: { href: "/", label: "На главную" },
      });
    }
    return;
  }

  const poster = a.images?.jpg?.large_image_url || a.images?.jpg?.image_url || "";
  const titleEn = a.title_english || a.title;

  const shellCleanup = renderAnimeShell({ a, banner: poster, poster, titleMain: titleEn, titleOrig: "", synopsis: (a.synopsis || "").replace(/\[.*?\]/g, "").trim() });

  const initialTitles = uniq([
    titleEn, a.title, a.title_english, a.title_japanese,
    ...(a.title_synonyms || []),
  ]).filter(Boolean);
  const player = new Player({ malId, titles: initialTitles, anime: a });
  player._shellCleanup = shellCleanup;
  state.current = player;
  player.init();

  shikiP.then(shiki => {
    if (!shiki || player.destroyed) return;
    applyShiki(malId, a, shiki);
    const newTitles = [shiki.russian, ...(shiki.synonyms || [])].filter(Boolean);
    if (newTitles.length) player.addTitles(newTitles);
    _recordTitlePage(malId, a, shiki);
  });

  anilistP
    .then(al => { if (al) applyAnilist(al); })
    .finally(() => setTimeout(ensureRussianDescription, 400));
  charsP.then(renderCharacters);
  recsP.then(renderRecommendations);

  // If Shikimori is slow, still record the English-only snapshot so the
  // canonical page gets cached quickly for crawlers.
  setTimeout(() => _recordTitlePage(malId, a, null), 2500);
}

function _consumeSSRPreload(expectedMalId) {
  const el = document.querySelector("#av-preload");
  if (!el) return null;
  try {
    const data = JSON.parse(el.textContent || "null");
    el.remove();
    if (data && Number(data.mal_id) === Number(expectedMalId)) return data;
  } catch { /* ignore */ }
  return null;
}

function _recordTitlePage(malId, a, shiki) {
  if (_recordedTitles.has(malId)) return;
  _recordedTitles.add(malId);
  api.recordTitlePage({
    mal_id: malId,
    title_ru: shiki?.russian || "",
    title_en: a.title_english || a.title || "",
    title_jp: a.title_japanese || "",
    synopsis: (a.synopsis || "").replace(/\[.*?\]/g, "").trim().slice(0, 5000),
    poster_url: a.images?.jpg?.large_image_url || a.images?.jpg?.image_url || "",
    banner_url: a.images?.jpg?.large_image_url || "",
    year: a.year || null,
    kind: a.type || "",
    airing_status: a.status || "",
    episodes_total: a.episodes || null,
    score: a.score || null,
    genres: (a.genres || []).map(g => g.name).filter(Boolean),
    studios: (a.studios || []).map(s => s.name).filter(Boolean).join(", "),
  });
}
const _recordedTitles = new Set();

function renderAnimeShell({ a, banner, poster, titleMain, titleOrig, synopsis }) {
  const cleanup = [];
  const shortDesc = synopsis.slice(0, 260) + (synopsis.length > 260 ? "…" : "");
  const genres = (a.genres || []).concat(a.themes || []).map(g => esc(trGenre(g.name))).join(", ");
  const trailerId = a.trailer?.youtube_id;
  const seasons = (a.relations || []).filter(r =>
    /Sequel|Prequel|Side story|Parent story|Alternative version|Summary/i.test(r.relation)
  ).flatMap(r => r.entry.filter(e => e.type === "anime").map(e => ({ ...e, relation: r.relation })));

  const statusRu = tr(STATUS_RU, a.status || "");
  const typeRu = tr(TYPE_RU, a.type || "");
  const badgeCls = statusRu === "Онгоинг" ? "badge attention" : "badge";

  $("#outlet").innerHTML = `
    <div class="cinema-hero" id="avHero" style="background-image:url('${imgUrl(banner)}')">
      <div class="cinema-overlay"></div>
      <div class="cinema-content">
        <div class="cinema-poster"><img src="${imgUrl(poster)}" alt=""></div>
        <div class="cinema-info">
          <div class="cinema-badges">
            ${a.type ? `<span class="badge neutral">${esc(typeRu)}</span>` : ""}
            ${a.year ? `<span class="badge neutral">${a.year}</span>` : ""}
            ${statusRu ? `<span class="${badgeCls}">${esc(statusRu)}</span>` : ""}
          </div>
          <h1 class="cinema-title" id="avHeroTitle">${esc(titleMain)}</h1>
          <div class="cinema-sub" id="avHeroSub">${titleOrig ? esc(titleOrig) + (a.title_japanese ? " · " + esc(a.title_japanese) : "") : (a.title_japanese ? esc(a.title_japanese) : "")}</div>
          <div class="cinema-stats">
            ${a.score ? `<span class="cinema-score">★ ${a.score}</span>` : ""}
            ${a.episodes ? `<span>${a.episodes} эп.</span>` : ""}
            ${a.duration ? `<span>${esc(a.duration.replace("per ep", "/эп.").replace("min", "мин"))}</span>` : ""}
            ${genres ? `<span>${genres.slice(0, 80)}${genres.length > 80 ? "…" : ""}</span>` : ""}
          </div>
          <p class="cinema-desc" id="avHeroDesc">${esc(shortDesc)}</p>
          <div class="cinema-actions">
            <button class="btn btn-primary" id="avCtaPlay">
              <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M8 5v14l11-7z"/></svg>
              Смотреть
            </button>
            <div class="list-picker" id="avListPicker">
              <button class="btn btn-ghost list-picker-trigger" id="avListPickerBtn" type="button" aria-haspopup="menu" aria-expanded="false">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M4 6h16v2H4zM4 11h16v2H4zM4 16h10v2H4z"/></svg>
                <span class="list-picker-label">В список</span>
                <svg class="list-picker-chev" viewBox="0 0 24 24" width="11" height="11" fill="currentColor"><path d="M7 10l5 5 5-5z"/></svg>
              </button>
              <div class="list-picker-pop" id="avListPickerPop" role="menu" hidden>
                <button type="button" class="lp-option" data-status="watching"  role="menuitemradio">Смотрю</button>
                <button type="button" class="lp-option" data-status="planned"   role="menuitemradio">В Планах</button>
                <button type="button" class="lp-option" data-status="completed" role="menuitemradio">Просмотрено</button>
                <button type="button" class="lp-option" data-status="postponed" role="menuitemradio">Отложено</button>
                <button type="button" class="lp-option" data-status="dropped"   role="menuitemradio">Брошено</button>
                <div class="lp-sep"></div>
                <button type="button" class="lp-option lp-clear" data-status="">Убрать из списков</button>
              </div>
            </div>
            <button class="btn btn-ghost" id="avFavBtn" aria-pressed="false" title="Добавить в избранное">
              <svg viewBox="0 0 24 24" width="16" height="16"><path fill="currentColor" d="M12 2l3 7h7l-5.5 4.5L18 21l-6-4-6 4 1.5-7.5L2 9h7z"/></svg>
              <span class="fav-label">В избранное</span>
            </button>
            <a href="${a.url || '#'}" target="_blank" rel="noopener" class="btn btn-ghost">MyAnimeList ↗</a>
            <a href="https://shikimori.one/animes?search=${encodeURIComponent(titleMain)}" target="_blank" rel="noopener" class="btn btn-ghost">Shikimori ↗</a>
            <a href="https://anilist.co/search/anime?search=${encodeURIComponent(titleMain)}" target="_blank" rel="noopener" class="btn btn-ghost">AniList ↗</a>
          </div>
        </div>
      </div>
    </div>

    <aside class="av-support" role="note">
      <span class="av-support-bar" aria-hidden="true"></span>
      <span class="av-support-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M12 21s-7.5-4.6-9.6-9.1C.9 8.6 2.7 5 6.2 5c2 0 3.4 1 4.3 2.3h1c.9-1.3 2.3-2.3 4.3-2.3 3.5 0 5.3 3.6 3.8 6.9C19.5 16.4 12 21 12 21z"/></svg>
      </span>
      <div class="av-support-text">
        <span class="av-support-kicker">При поддержке партнёра</span>
        <span class="av-support-msg">Портал работает при поддержке первой социальной сети для анимешников <a class="av-support-link" href="${esc((state.social && state.social.site_url) || "#")}" target="_blank" rel="noopener">${esc((state.social && state.social.site_name) || "AnimeSocial")}<svg viewBox="0 0 24 24" width="12" height="12" fill="currentColor" aria-hidden="true"><path d="M14 3h7v7h-2V6.4l-9.3 9.3-1.4-1.4L17.6 5H14V3zM5 5h5v2H7v10h10v-3h2v5H5V5z"/></svg></a></span>
      </div>
    </aside>

    <div class="detail-grid">
      <div class="watch-main">
        <div class="seasons-bar" id="avSeasonsBar" hidden></div>

        <div class="sp-player" id="avPlayerCard">
          <div class="sp-head">
            <div class="sp-ep" id="avEpInfo"></div>
            <button class="sp-dub-trigger" id="avDubTrigger" type="button">
              <svg viewBox="0 0 24 24" width="14" height="14" fill="currentColor"><path d="M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zm-1 5h2v5l4 2-1 2-5-2.5V8z" opacity=".9"/></svg>
              <span class="sp-dub-label">—</span>
              <svg class="sp-dub-chev" viewBox="0 0 24 24" width="11" height="11" fill="currentColor"><path d="M7 10l5 5 5-5z"/></svg>
            </button>
          </div>

          <div class="sp-video">
            <div id="artplayer" style="width:100%;height:100%"></div>
            <div class="player-overlay" id="avPlayerOverlay">
              <div class="spinner"></div>
              <div id="avPlayerStatus">Ищу источники…</div>
            </div>
          </div>

          <div class="sp-foot">
            <div class="sp-prev-next">
              <button class="sp-nav" id="avPrevEp" type="button" aria-label="Предыдущая">
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M6 6h2v12H6zm12 0v12l-8-6z"/></svg>
                <span>Пред.</span>
              </button>
              <button class="sp-nav" id="avNextEp" type="button" aria-label="Следующая">
                <span>След.</span>
                <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M6 6v12l8-6zm10 0h2v12h-2z"/></svg>
              </button>
            </div>
            <label class="sp-auto">
              <input type="checkbox" id="avAutoNext" />
              <span class="sp-auto-box"></span>
              <span class="sp-auto-txt">Автопереключение серий</span>
            </label>
          </div>

          <div class="sp-dub-pop" id="avDubPop" hidden>
            <div class="sp-dub-pop-inner" id="avDubTabs"></div>
          </div>
        </div>
      </div>

      <aside class="ep-sidebar">
        <div class="ep-side-head">
          <h3>Эпизоды</h3><span id="avEpCount" class="muted" style="font-size:12px"></span>
        </div>
        <input type="text" class="ep-search" id="avEpSearch" placeholder="Поиск эпизода…" hidden />
        <div class="episodes-list" id="avEpisodesList"><div class="av-empty" style="padding:30px 0">Ищу источник…</div></div>
      </aside>

      <div class="detail-main">
        <div class="tabs-block">
          <div class="block-header">Описание</div>
          <div class="info-body">
            <div class="synopsis" id="avSynopsis">${synopsis ? esc(synopsis) : "<span class='muted'>Описание не найдено</span>"}</div>

            <ul class="content-main-info">
              ${a.status ? `<li><span>Статус:</span> <span class="${badgeCls}">${esc(statusRu)}</span></li>` : ""}
              ${a.type ? `<li><span>Тип:</span> <span>${esc(typeRu)}</span></li>` : ""}
              ${a.year ? `<li><span>Год:</span> <span>${a.year}</span></li>` : ""}
              ${a.episodes ? `<li><span>Кол-во серий:</span> <span>${a.episodes}</span></li>` : ""}
              ${a.duration ? `<li><span>Длительность:</span> <span>${esc(a.duration.replace("per ep", "/эп.").replace("min", "мин"))}</span></li>` : ""}
              ${a.rating ? `<li><span>Возрастной рейтинг:</span> <span>${esc(a.rating)}</span></li>` : ""}
              ${a.studios?.length ? `<li><span>Студии:</span> <span>${a.studios.map(s => esc(s.name)).join(", ")}</span></li>` : ""}
              ${genres ? `<li class="categories-list"><span>Жанры:</span> <span>${genres}</span></li>` : ""}
              ${a.score ? `<li><span>Оценка:</span> <span><span class="main-rating">${a.score}</span><span class="main-rating-info">Голосов: ${a.scored_by || "—"}</span></span></li>` : ""}
              <li><span>Ваша оценка:</span> <span class="rate-block" id="avRate"></span></li>
            </ul>
          </div>
        </div>

        ${trailerId ? `
        <div class="tabs-block">
          <div class="block-header">Трейлер</div>
          <div class="trailer">
            <iframe src="https://www.youtube-nocookie.com/embed/${trailerId}" allow="accelerometer; encrypted-media; gyroscope; picture-in-picture; fullscreen" loading="lazy"></iframe>
          </div>
        </div>` : ""}

        <div class="tabs-block">
          <div class="block-header">Персонажи</div>
          <div class="chars-grid" id="avChars">
            ${Array.from({ length: 6 }).map(() => `<div class="char"><div class="char-img av-skeleton" style="aspect-ratio:auto"></div><div class="char-info"><div class="av-skeleton-line" style="width:80%;height:12px;margin-bottom:6px"></div><div class="av-skeleton-line" style="width:50%;height:10px"></div></div></div>`).join("")}
          </div>
        </div>

        <div class="tabs-block">
          <div class="block-header">Похожее</div>
          <div class="recs-wrap"><div class="av-grid" id="avRecs"><div class="av-empty">Загружаю…</div></div></div>
        </div>
      </div>
    </div>`;

  fitHeroTitle($("#avHeroTitle"));

  $("#avCtaPlay")?.addEventListener("click", () => $("#avPlayerCard").scrollIntoView({ behavior: "smooth", block: "start" }));

  const pstr = $(".cinema-poster");
  if (pstr) pstr.addEventListener("click", () => lightbox.open(poster));

  const malId = a.mal_id;
  const pickerLabel = { watching: "Смотрю", planned: "В Планах", completed: "Просмотрено", postponed: "Отложено", dropped: "Брошено" };

  const favBtn = $("#avFavBtn");
  const picker = $("#avListPicker");
  const pickerBtn = $("#avListPickerBtn");
  const pickerPop = $("#avListPickerPop");

  const paintFav = () => {
    if (!favBtn) return;
    const isFav = favs.has(malId);
    favBtn.classList.toggle("is-fav", isFav);
    favBtn.setAttribute("aria-pressed", isFav ? "true" : "false");
    favBtn.querySelector(".fav-label").textContent = isFav ? "В избранном" : "В избранное";
  };
  const paintPicker = () => {
    if (!picker) return;
    const entry = account.store.getListEntry(malId);
    const status = entry?.status || null;
    const auto = entry?.status_source === "auto";
    const label = $(".list-picker-label", picker);
    if (label) label.textContent = status ? pickerLabel[status] : "В список";
    picker.classList.toggle("has-status", !!status);
    picker.classList.toggle("auto", !!auto);
    [...pickerPop.querySelectorAll(".lp-option")].forEach(el => {
      el.classList.toggle("active", el.dataset.status === (status || ""));
    });
  };
  paintFav();
  paintPicker();

  // Авто-правила на сервере (watching через 10 мин, completed на последней
  // серии, dropped через 30 дней) меняют статус без действий пользователя.
  // sendProgressEvent рассылает `av:list-updated` — ловим и перекрашиваем
  // picker/favBtn, чтобы видимое состояние «Смотрю → Просмотрено» менялось
  // без перезагрузки. Cleanup возвращается наружу и вызывается из Player.destroy().
  const onListUpdated = (e) => {
    const detail = e.detail || {};
    if (Number(detail.mal_id) !== Number(malId)) return;
    paintPicker();
    paintFav();
  };
  window.addEventListener("av:list-updated", onListUpdated);
  cleanup.push(() => window.removeEventListener("av:list-updated", onListUpdated));

  favBtn?.addEventListener("click", async () => {
    const curTitle = ($("#avHeroTitle")?.textContent || titleMain || "").trim();
    await favs.toggle(malId, { title: curTitle, cover: poster });
    paintFav();
  });

  // list picker — open/close + status selection.
  // Дропдаун обязан жить во `fixed`-позиции: родитель .cinema-hero
  // имеет overflow:hidden для клиппинга баннера, и absolute-меню в нём
  // обрезается следующим блоком. Поэтому считаем координаты от триггера.
  if (picker && pickerBtn && pickerPop) {
    const position = () => {
      const r = pickerBtn.getBoundingClientRect();
      pickerPop.style.position = "fixed";
      pickerPop.style.top = `${r.bottom + 6}px`;
      pickerPop.style.left = `${r.left}px`;
      pickerPop.style.minWidth = `${Math.max(r.width, 220)}px`;
    };
    const setOpen = (on) => {
      pickerPop.hidden = !on;
      pickerBtn.classList.toggle("open", on);
      pickerBtn.setAttribute("aria-expanded", on ? "true" : "false");
      if (on) position();
    };
    pickerBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      setOpen(pickerPop.hidden);
    });
    const outsideClose = (e) => {
      if (pickerPop.hidden) return;
      if (!picker.contains(e.target) && !pickerPop.contains(e.target)) setOpen(false);
    };
    const onScroll = () => { if (!pickerPop.hidden) position(); };
    const onResize = () => { if (!pickerPop.hidden) position(); };
    const onKeydown = (e) => { if (e.key === "Escape" && !pickerPop.hidden) setOpen(false); };
    document.addEventListener("click", outsideClose);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
    document.addEventListener("keydown", onKeydown);
    cleanup.push(() => document.removeEventListener("click", outsideClose));
    cleanup.push(() => window.removeEventListener("scroll", onScroll, true));
    cleanup.push(() => window.removeEventListener("resize", onResize));
    cleanup.push(() => document.removeEventListener("keydown", onKeydown));
    [...pickerPop.querySelectorAll(".lp-option")].forEach(el => {
      el.addEventListener("click", async () => {
        const status = el.dataset.status || null;
        const curTitle = ($("#avHeroTitle")?.textContent || titleMain || "").trim();
        await account.store.setListStatus(malId, status, { title: curTitle, cover: poster });
        paintPicker();
        setOpen(false);
      });
    });
  }

  renderRatingStars(malId);

  renderSeasonsBar(a, seasons);
  return () => {
    for (const dispose of cleanup.splice(0)) {
      try { dispose(); } catch (_) {}
    }
  };
}

function renderRatingStars(malId) {
  const box = $("#avRate");
  if (!box) return;
  const current = rating.get(malId);
  box.innerHTML = "";
  for (let i = 1; i <= 10; i++) {
    const s = document.createElement("span");
    s.className = "rate-star" + (i <= current ? " filled" : "");
    s.innerHTML = "★";
    s.title = `Оценить на ${i}`;
    s.addEventListener("mouseenter", () => {
      $$(".rate-star", box).forEach((el, idx) => el.classList.toggle("filled", idx < i));
    });
    s.addEventListener("mouseleave", () => {
      const c = rating.get(malId);
      $$(".rate-star", box).forEach((el, idx) => el.classList.toggle("filled", idx < c));
    });
    s.addEventListener("click", async () => {
      const v = i === current ? 0 : i;
      await rating.set(malId, v);
      renderRatingStars(malId);
    });
    box.appendChild(s);
  }
  const lbl = document.createElement("span");
  lbl.className = "rate-label";
  lbl.textContent = current ? `Ваша оценка: ${current}/10` : (state.user ? "Оцените" : "Оцените (войдите)");
  box.appendChild(lbl);
}

function renderSeasonsBar(currentAnime, relations) {
  const bar = $("#avSeasonsBar");
  if (!bar) return;
  if (!relations?.length) { bar.hidden = true; return; }
  const LABELS = {
    "Side story": "Побочная", "Summary": "Саммари",
    "Alternative version": "Альт. версия", "Parent story": "Оригинал",
    "Alternative setting": "Альт. сеттинг", "Full story": "Полная версия",
    "Spin-off": "Спин-офф",
  };
  const preqs = relations.filter(r => r.relation === "Prequel");
  const seqs = relations.filter(r => r.relation === "Sequel");
  const others = relations.filter(r => !["Prequel", "Sequel"].includes(r.relation));
  const chain = [
    ...preqs.map(r => ({ mal_id: r.mal_id, name: r.name })),
    { mal_id: currentAnime.mal_id, name: currentAnime.title_english || currentAnime.title, current: true },
    ...seqs.map(r => ({ mal_id: r.mal_id, name: r.name })),
  ];
  bar.hidden = false;
  bar.innerHTML = "";
  chain.forEach((it, i) => {
    const el = document.createElement(it.current ? "div" : "a");
    el.className = "season-tab" + (it.current ? " active" : "");
    if (!it.current) el.href = `/anime/${it.mal_id}/`;
    el.innerHTML = `<span class="season-lbl">${i + 1} сезон</span><span class="season-name">${esc(it.name)}</span>`;
    bar.appendChild(el);
  });
  others.forEach(r => {
    const el = document.createElement("a");
    el.className = "season-tab";
    el.href = `/anime/${r.mal_id}/`;
    el.innerHTML = `<span class="season-lbl">${esc(LABELS[r.relation] || r.relation)}</span><span class="season-name">${esc(r.name)}</span>`;
    bar.appendChild(el);
  });
}

function applyShiki(malId, a, shiki) {
  if (!shiki) return;
  if (shiki.russian) {
    const tEl = $("#avHeroTitle"); if (tEl) { tEl.textContent = shiki.russian; fitHeroTitle(tEl); }
    const sEl = $("#avHeroSub");
    const orig = a.title_english || a.title;
    if (sEl) sEl.textContent = [orig, a.title_japanese].filter(Boolean).join(" · ");
  }
  const desc = cleanBBCode(shiki.description);
  if (desc) {
    const syn = $("#avSynopsis"); if (syn) syn.textContent = desc;
    const hd = $("#avHeroDesc"); if (hd) hd.textContent = desc.slice(0, 260) + (desc.length > 260 ? "…" : "");
  }
  if (state.current?.addTitles) {
    state.current.addTitles([shiki.russian, ...(shiki.synonyms || [])].filter(Boolean));
  }
}
function applyAnilist(al) {
  if (!al) return;
  if (al.bannerImage) {
    const h = $("#avHero"); if (h) h.style.backgroundImage = `url('${imgUrl(al.bannerImage)}')`;
  }
  if (al.description) {
    const syn = $("#avSynopsis");
    const heroDesc = $("#avHeroDesc");
    const cur = syn ? syn.textContent.trim() : "";
    if (hasCyrillic(cur)) return;
    if (!cur || cur === "Описание не найдено" || cur.length < 30) {
      const clean = al.description.replace(/<br\s*\/?>/gi, "\n").replace(/<[^>]+>/g, "").replace(/&[a-z]+;/g, " ").trim();
      if (syn) syn.textContent = clean;
      if (heroDesc) heroDesc.textContent = clean.slice(0, 260) + (clean.length > 260 ? "…" : "");
    }
  }
}

async function ensureRussianDescription() {
  const syn = $("#avSynopsis");
  if (!syn) return;
  const cur = syn.textContent.trim();
  if (!cur || cur === "Описание не найдено" || hasCyrillic(cur)) return;
  const ru = await translateToRu(cur);
  if (!ru) return;
  const synNow = $("#avSynopsis");
  if (!synNow || hasCyrillic(synNow.textContent)) return;
  synNow.textContent = ru;
  const hd = $("#avHeroDesc");
  if (hd) hd.textContent = ru.slice(0, 260) + (ru.length > 260 ? "…" : "");
}

function renderCharacters(chars) {
  const box = $("#avChars"); if (!box) return;
  if (!chars?.length) { box.innerHTML = `<div class="av-empty">Нет данных</div>`; return; }
  box.innerHTML = "";
  chars.slice(0, 12).forEach(c => {
    const el = document.createElement("div");
    el.className = "char";
    const va = (c.voice_actors || []).find(v => v.language === "Japanese") || c.voice_actors?.[0];
    const img = c.character?.images?.jpg?.image_url || "";
    el.innerHTML = `
      <div class="char-img"><img src="${imgUrl(img)}" loading="lazy" alt=""></div>
      <div class="char-info">
        <div class="char-name">${esc(c.character?.name || '')}</div>
        <div class="char-role">${esc(c.role || '')}</div>
        ${va ? `<div class="char-va">${esc(va.person?.name || '')}</div>` : ""}
      </div>`;
    if (img) $(".char-img", el).addEventListener("click", () => lightbox.open(img));
    box.appendChild(el);
  });
}
function renderRecommendations(recs) {
  const box = $("#avRecs"); if (!box) return;
  if (!recs?.length) { box.innerHTML = `<div class="av-empty">Нет рекомендаций</div>`; return; }
  box.innerHTML = "";
  const frag = document.createDocumentFragment();
  recs.slice(0, 12).forEach(r => {
    const entry = r.entry;
    if (!entry?.mal_id) return;
    frag.appendChild(makeCard({
      mal_id: entry.mal_id,
      title: entry.title,
      images: { jpg: { large_image_url: entry.images?.jpg?.large_image_url || entry.images?.jpg?.image_url } },
    }));
  });
  box.appendChild(frag);
  enrichWithShiki(box, recs.slice(0, 12).map(r => ({ mal_id: r.entry.mal_id })));
}

// ---------- player ----------
class Player {
  constructor({ malId, titles, anime }) {
    this.malId = malId;
    this.titles = titles;
    this.anime = anime;
    this.sources = {};
    this.episodes = [];
    this.currentEp = 0;
    this.art = null;
    this.destroyed = false;
    this.currentDubs = [];
    this.currentDubIdx = 0;
    this._dubsCache = new Map();
    this._reqId = 0;
    this._activeSource = null;
    this._iframeEl = null;
  }

  _tabEpisodeKey() {
    return `av:tab-episode:${this.malId}`;
  }

  _readTabEpisodeNum() {
    try {
      const raw = sessionStorage.getItem(this._tabEpisodeKey());
      if (!raw) return 0;
      const data = JSON.parse(raw);
      const ep = Number(data?.ep || 0);
      const at = Number(data?.at || 0);
      if (!ep || (at && Date.now() - at > 12 * 60 * 60 * 1000)) {
        sessionStorage.removeItem(this._tabEpisodeKey());
        return 0;
      }
      return ep;
    } catch (_) {
      return 0;
    }
  }

  _rememberTabEpisode(epNum) {
    try {
      const ep = Number(epNum || 0);
      if (!ep) return;
      sessionStorage.setItem(this._tabEpisodeKey(), JSON.stringify({ ep, at: Date.now() }));
    } catch (_) {}
  }

  _normDubName(name) {
    let s = (name || "").toLowerCase().trim();
    s = s.replace(/[\(\[]([^)\]]*)[\)\]]/g, " ");
    const prefixes = ["озвучка ", "субтитры ", "оригинал ", "original ", "voice ", "sub ", "дубляж "];
    for (const p of prefixes) if (s.startsWith(p)) s = s.slice(p.length);
    s = s.replace(/\b(ru|en|jp|ja|рус|русский|англ\w*|яп\w*|дубляж|дубл|sub|subs|ost)\b/g, " ");
    s = s.replace(/[^\w\sа-яё]+/gi, " ");
    s = s.replace(/\s+/g, " ").trim();
    return s;
  }

  async addTitles(newTitles) {
    const before = this.titles.length;
    this.titles = uniq([...this.titles, ...newTitles]).filter(Boolean);
    if (this.titles.length === before) return;
    if (Object.keys(this.sources).length) {
      if (this._activeSource && this._episodeTitlesNeedRefresh()) {
        const reqId = this._reqId;
        this._refreshActiveSourceKeys(reqId).catch(() => null);
      }
      return;
    }
    this.rediscover();
  }

  _episodeTitlesNeedRefresh() {
    return (this.episodes || []).some(e => isGenericEpisodeTitle(e.name));
  }

  _syncCurrentEpisodeTitle() {
    const ep = this.episodes?.[this.currentEp];
    const el = $("#avEpInfo .sp-ep-title");
    if (ep && el) el.textContent = ep.name;
  }

  async rediscover() {
    if (this._red) return; this._red = true;
    try {
      await this.discoverWithFallback();
      if (this.destroyed) return;
      this._dubsCache.clear();
      this._renderFoundSource();
    } finally { this._red = false; }
  }
  async init() {
    this.status("Ищу аниме…");
    if (!state.backendAvailable) { this.renderUnavailable("Бэкенд не запущен"); return; }
    if (this._isNonAnime()) { this.renderUnavailable(); return; }
    const releaseAt = this._nextAiringDate();
    if (releaseAt === "unknown" || (releaseAt instanceof Date && releaseAt > new Date())) {
      this.renderComingSoon(releaseAt);
      return;
    }
    await this.discoverWithFallback();
    if (this.destroyed) return;
    if (this._adoptAnyReadySource() || this._renderFoundSource()) return;

    for (let i = 0; i < 25; i++) {
      await new Promise(r => setTimeout(r, 200));
      if (this.destroyed) return;
      if (this._adoptAnyReadySource() || this._renderFoundSource()) return;
    }
    while (this._red) {
      await new Promise(r => setTimeout(r, 200));
      if (this.destroyed) return;
      if (this._adoptAnyReadySource() || this._renderFoundSource()) return;
    }
    await this.discoverWithFallback();
    if (this.destroyed) return;
    if (this._adoptAnyReadySource() || this._renderFoundSource()) return;
    if (this._matchedNoEps) { this.renderComingSoon("unknown"); return; }
    this.renderUnavailable();
  }

  _renderFoundSource() {
    const srcData = this._activeSource ? this.sources[`backend:${this._activeSource}`] : null;
    if (!srcData?.episodes?.length) return false;
    if (this._rendered) return true;
    this._rendered = true;
    this.episodes = this._buildEpisodeList(srcData.episodes);

    $("#avEpCount").textContent = `${this.episodes.length} эп.`;
    this._bindPlayerControls();
    this._setupAutoNext();
    // Перед первым рендером списка эпизодов подтягиваем серверный per-episode
    // прогресс — чтобы досмотренные серии подсветились корректно. Без этого
    // мы видим только последний эпизод как «watched», а предыдущие — нет.
    this._fetchEpisodeProgress().then(() => {
      if (this.destroyed) return;
      this.renderEpisodes();
      this.playEpisode(this._resumeIndex());
    });
    return true;
  }

  _buildEpisodeList(rawEpisodes = []) {
    const seenNums = new Set();
    return rawEpisodes
      .map(e => {
        const num = Number(e.num) || 0;
        return {
          num,
          name: ruEpisodeTitle(e.name || e.title, num),
          preview: e.preview,
          key: e.key,
          yummyKey: e.key,
        };
      })
      .filter(e => {
        if (!e.num || seenNums.has(e.num)) return false;
        seenNums.add(e.num);
        return true;
      })
      .sort((a, b) => a.num - b.num);
  }

  _isStaleBackendKeyError(err) {
    return Number(err?.status) === 410 || String(err?.message || "") === "410";
  }

  async _refreshActiveSourceKeys(reqId) {
    const src = this._activeSource;
    if (!src) return false;
    const currentNum = this.episodes[this.currentEp]?.num;
    try {
      await this.discoverBackend(src);
    } catch (_) {
      return false;
    }
    if (reqId !== this._reqId || this.destroyed) return false;
    const fresh = this.sources[`backend:${src}`]?.episodes;
    if (!fresh?.length) return false;

    const nextEpisodes = this._buildEpisodeList(fresh);
    const currentIdx = nextEpisodes.findIndex(e => e.num === currentNum);
    this.episodes = nextEpisodes;
    if (currentIdx >= 0) this.currentEp = currentIdx;
    this._dubsCache.clear();
    this.renderEpisodes();
    $$("#avEpisodesList .ep-row").forEach((b, idx) => b.classList.toggle("active", idx === this.currentEp));
    this._syncCurrentEpisodeTitle();
    return true;
  }

  async _loadEpisodeDubs(ep, reqId, { retryStale = true } = {}) {
    const src = this._activeSource;
    const epKey = ep?.yummyKey || ep?.key;
    if (!epKey || !src) return [];
    try {
      const list = await api.backendSources(epKey, src);
      if (reqId !== this._reqId) return [];
      return (list || []).map(s => ({ label: s.name, _sourceKey: s.key, _src: src }));
    } catch (err) {
      if (retryStale && this._isStaleBackendKeyError(err)) {
        const refreshed = await this._refreshActiveSourceKeys(reqId);
        if (!refreshed || reqId !== this._reqId) return [];
        const freshEp = this.episodes[this.currentEp];
        return this._loadEpisodeDubs(freshEp, reqId, { retryStale: false });
      }
      throw err;
    }
  }

  async _reloadCurrentEpisodeDubs(reqId) {
    const ep = this.episodes[this.currentEp];
    const dubs = await this._loadEpisodeDubs(ep, reqId, { retryStale: true });
    if (reqId !== this._reqId || !dubs.length) return null;
    this._dubsCache.set(ep.num, dubs);
    this.currentDubs = dubs;
    return dubs;
  }

  async _fetchEpisodeProgress() {
    this._epProgress = new Map();  // num → { seconds, duration }
    if (!state.user) return;
    try {
      const list = await fetchJson(`${BACKEND}/account/progress/${this.malId}`,
                                   { timeout: 4000, credentials: "include" });
      for (const row of (list || [])) {
        this._epProgress.set(Number(row.episode_num), {
          seconds: Number(row.seconds || 0),
          duration: Number(row.duration || 0),
        });
      }
    } catch (_) { /* не критично — покраска просто не применится */ }
  }

  // Выбор стартовой серии: если последняя просмотренная серия досмотрена
  // (>=92% или финишный хвост), стартуем со следующей. Иначе — с той же,
  // чтобы пользователь мог продолжить с места паузы.
  _resumeIndex() {
    const tabEp = this._readTabEpisodeNum();
    if (tabEp) {
      const tabIdx = this.episodes.findIndex(e => Number(e.num) === tabEp);
      if (tabIdx >= 0) return tabIdx;
    }

    const w = state.watch[String(this.malId)];
    if (!w || !w.ep) return 0;
    const finished = w.duration > 0
      && (w.time >= w.duration * 0.92 || w.time >= w.duration - 90);
    const targetNum = finished ? (w.ep + 1) : w.ep;
    let idx = this.episodes.findIndex(e => e.num === targetNum);
    if (idx < 0) idx = this.episodes.findIndex(e => e.num === w.ep);
    return idx >= 0 ? idx : 0;
  }

  _progressDone(entry) {
    const seconds = Number(entry?.seconds ?? entry?.time ?? 0);
    const duration = Number(entry?.duration || 0);
    return duration > 0
      && (seconds >= duration * 0.92 || seconds >= Math.max(0, duration - 90));
  }

  _episodeProgressPct(entry) {
    const seconds = Number(entry?.seconds ?? entry?.time ?? 0);
    const duration = Number(entry?.duration || 0);
    return duration > 0 ? Math.min(100, (seconds / duration) * 100) : 0;
  }

  _completedThroughEpisodeNum() {
    let completed = 0;
    for (const e of this.episodes || []) {
      const epNum = Number(e.num);
      const prog = this._epProgress?.get(epNum) || getProgress(this.malId, epNum);
      if (this._progressDone(prog)) completed = Math.max(completed, epNum);
    }

    const watch = getWatch(this.malId);
    const watchEp = Number(watch?.ep || 0);
    const watchSeconds = Number(watch?.seconds ?? watch?.time ?? 0);
    if (watchEp > 0) {
      if (this._progressDone(watch)) completed = Math.max(completed, watchEp);
      else if (watchSeconds > 0) completed = Math.max(completed, watchEp - 1);
    }

    return Math.max(0, completed);
  }

  _lastProgressEpisodeNum() {
    let latestTouched = 0;
    for (const e of this.episodes || []) {
      const epNum = Number(e.num);
      const prog = this._epProgress?.get(epNum) || getProgress(this.malId, epNum);
      const seconds = Number(prog?.seconds ?? prog?.time ?? 0);
      if (seconds > 0) latestTouched = Math.max(latestTouched, epNum);
    }
    return latestTouched;
  }

  _setEpisodeProgressRows(rows = []) {
    this._epProgress = new Map();
    for (const row of rows || []) {
      const epNum = Number(row.episode_num);
      if (!epNum) continue;
      this._epProgress.set(epNum, {
        seconds: Number(row.seconds || 0),
        duration: Number(row.duration || 0),
      });
    }
  }

  _updateEpisodeRowProgress(epNum) {
    const list = $("#avEpisodesList");
    if (!list) return false;
    const row = $$(".ep-row", list).find(r => Number(r.dataset.num) === Number(epNum));
    if (!row) return false;
    const prog = this._epProgress?.get(Number(epNum)) || getProgress(this.malId, epNum);
    const pct = this._episodeProgressPct(prog);
    const completedThrough = this._completedThroughEpisodeNum();
    row.classList.toggle("watched", pct > 90 || Number(epNum) <= completedThrough);

    let bar = $(".ep-progress", row);
    if (pct > 0 && pct < 95) {
      if (!bar) {
        bar = document.createElement("div");
        bar.className = "ep-progress";
        bar.innerHTML = "<span></span>";
        row.appendChild(bar);
      }
      const fill = $("span", bar);
      if (fill) fill.style.width = `${pct}%`;
    } else if (bar) {
      bar.remove();
    }
    return true;
  }

  _applyEpisodeProgress(epNum, seconds, duration, { renderOnAnyChange = false } = {}) {
    epNum = Number(epNum || 0);
    if (!epNum) return false;
    this._epProgress ??= new Map();
    const prevLastProgressEp = this._lastProgressEpisodeNum();
    const prevEntry = this._epProgress.get(epNum);
    const wasDone = this._progressDone(prevEntry);
    const nextEntry = {
      seconds: Math.max(Math.floor(Number(seconds) || 0), Number(prevEntry?.seconds || 0)),
      duration: Math.max(Math.floor(Number(duration) || 0), Number(prevEntry?.duration || 0)),
    };
    const changed = !prevEntry
      || nextEntry.seconds !== Number(prevEntry.seconds || 0)
      || nextEntry.duration !== Number(prevEntry.duration || 0);
    this._epProgress.set(epNum, nextEntry);

    const isDone = this._progressDone(nextEntry);
    let needsRender = !!(renderOnAnyChange && changed);
    if (isDone && !wasDone) needsRender = true;
    const dirtyRows = new Set();
    if (changed || isDone !== wasDone) dirtyRows.add(epNum);

    // Realtime mirror of the server rule: finishing episode N means earlier
    // episodes are treated as watched too, so skipping credits does not leave
    // the previous episode half-filled until the next page reload.
    if (isDone && epNum > 1) {
      const stampDuration = Math.max(nextEntry.duration, 1);
      for (let prev = 1; prev < epNum; prev++) {
        const pe = this._epProgress.get(prev);
        if (!this._progressDone(pe)) {
          this._epProgress.set(prev, { seconds: stampDuration, duration: stampDuration });
          dirtyRows.add(prev);
          needsRender = true;
        }
      }
    }

    const nextLastProgressEp = this._lastProgressEpisodeNum();
    if (state.user && nextLastProgressEp !== prevLastProgressEp) needsRender = true;
    if (needsRender) {
      this.closeEpisodeUnwatchConfirm();
      this.renderEpisodes();
      return true;
    }

    let updatedAllRows = true;
    for (const rowEp of dirtyRows) {
      if (!this._updateEpisodeRowProgress(rowEp)) updatedAllRows = false;
    }
    if (needsRender && !updatedAllRows) this.renderEpisodes();
    return needsRender;
  }

  _recordProgressPoint(epNum, seconds, duration, { renderOnAnyChange = false, forceEvent = false } = {}) {
    if (!epNum || seconds <= 0) return;
    const title = this.anime._ru || this.anime.title_english || this.anime.title || "";
    const poster = this.anime.images?.jpg?.large_image_url || this.anime.images?.jpg?.image_url || "";
    const total = this.anime?.episodes || this.episodes.length || 0;
    if (state.user && !getWatch(this.malId)) {
      ensureWatchEntry(this.malId, title, poster, epNum, total);
    }
    saveProgress(this.malId, epNum, seconds, duration);
    this._applyEpisodeProgress(epNum, seconds, duration, { renderOnAnyChange });

    const now = Date.now();
    const lastSent = this._lastProgressSent || 0;
    if (state.user && (forceEvent || now - lastSent > 30000)) {
      this._lastProgressSent = now;
      account.store.sendProgressEvent({
        mal_id: this.malId,
        episode_num: epNum,
        seconds,
        duration,
        episodes_total: total,
        title,
        poster_url: poster,
      });
    }
  }

  _recordCurrentEpisodeComplete({ allowEndedSignal = false } = {}) {
    const epNum = this.episodes[this.currentEp]?.num;
    if (!epNum) return false;
    const knownDur = Math.floor(Number(this._duration || 0));
    const knownTime = Math.floor(Number(this._lastTime || 0));
    let dur = knownDur;
    let secs = knownTime;

    if (knownDur > 30 && knownTime > 0) {
      const nearEnd = knownTime >= knownDur * 0.92 || knownTime >= Math.max(0, knownDur - 90);
      if (!nearEnd) return false;
      secs = Math.max(knownTime, knownDur);
    } else if (allowEndedSignal && this._sawPlaybackSignal) {
      const fallbackDur = Math.floor((parseEpDurationMin(this.anime?.duration) || 0) * 60);
      if (knownDur > 30) dur = knownDur;
      else if (knownTime > 0 && fallbackDur > 30) dur = fallbackDur;
      else return false;
      secs = dur;
    } else {
      return false;
    }

    if (this._lastCompleteSignalEp === epNum) return true;
    this._lastCompleteSignalEp = epNum;
    this._recordProgressPoint(epNum, secs, dur, {
      renderOnAnyChange: true,
      forceEvent: true,
    });
    return true;
  }

  // Player iframes from yummyani/kodik/aksor post timeupdate/ended messages.
  // We validate that the message comes from our own iframe — not any other
  // window — before trusting it. Without this check a hostile page could
  // trigger auto-next and manipulate local progress.
  _setupAutoNext() {
    if (this._msgBound) return;
    this._msgBound = true;
    this._lastTime = 0;
    this._duration = 0;
    this._autoFired = false;
    this._playStartedAt = 0;
    this._sawPlaybackSignal = false;
    this._onMessage = (e) => {
      if (this.destroyed) return;
      const iframe = this._iframeEl;
      if (iframe) {
        const fromDirectFrame = e.source === iframe.contentWindow;
        let fromFrameOrigin = false;
        try {
          fromFrameOrigin = !!e.origin && new URL(iframe.src).origin === e.origin;
        } catch (_) {}
        if (!fromDirectFrame && !fromFrameOrigin) return;
      }
      this._dubSawEvent = true;
      const since = Date.now() - (this._playStartedAt || 0);
      if (since < 5000) return;
      const d = e.data;
      let key = "", val = null;
      if (typeof d === "string") {
        try { const p = JSON.parse(d); key = p.key || p.event || p.type || ""; val = p.value ?? p.time ?? p.data; }
        catch { key = d; }
      } else if (d && typeof d === "object") {
        key = d.key || d.event || d.type || "";
        val = d.value ?? d.time ?? d.data;
      }
      if (!key) return;
      const k = String(key).toLowerCase();
      const vObj = val && typeof val === "object" ? val : null;
      const n = typeof val === "number" ? val
              : (vObj && typeof vObj.current_time === "number") ? vObj.current_time
              : (vObj && typeof vObj.time === "number") ? vObj.time
              : null;
      const vState = (vObj && (vObj.state || vObj.status)) ? String(vObj.state || vObj.status).toLowerCase() : "";
      if (vObj && typeof vObj.duration === "number" && vObj.duration > 30) this._duration = vObj.duration;
      if (k.includes("time_update") || k === "timeupdate") {
        if (n != null) {
          this._lastTime = n;
          if (n > 0) this._sawPlaybackSignal = true;
          const epNum = this.episodes[this.currentEp]?.num;
          const secs = Math.floor(n);
          const dur = Math.floor(this._duration || 0);
          this._recordProgressPoint(epNum, secs, dur);
        }
      }
      else if (k.includes("duration")) { if (n != null && n > 30) this._duration = n; }
      else if (k === "play" || k === "playing" || k.includes("player_play")) {
        this._sawPlaybackSignal = true;
      }
      else if (k === "end" || k === "ended" || k === "finish" || k === "finished" || k === "complete" || k.includes("video_end") || k.includes("playback_finished")) {
        if (this._recordCurrentEpisodeComplete({ allowEndedSignal: true })) this._maybeAutoNext("ended");
      }
      if (vState === "end" || vState === "ended" || vState === "finished" || vState === "complete") {
        if (this._recordCurrentEpisodeComplete({ allowEndedSignal: true })) this._maybeAutoNext("ended");
      }
      if (this._duration > 30 && this._lastTime >= this._duration - 3) {
        this._recordCurrentEpisodeComplete();
      }
    };
    window.addEventListener("message", this._onMessage);
  }
  _maybeAutoNext(kind) {
    if (kind !== "ended") return;
    if (this._autoFired || this.destroyed) return;
    if (!state.autoNext) return;
    if (this.currentEp >= this.episodes.length - 1) return;
    const elapsedMs = Date.now() - (this._playStartedAt || 0);
    if (elapsedMs < 60_000) return;
    // Some embedded players emit broad "complete" messages before the actual
    // media end. If we still know the playhead is behind the duration, do not
    // skip the final seconds.
    if (this._duration > 30 && this._lastTime > 0 && this._lastTime < this._duration - 1) return;
    if (kind === "ended" && this._duration > 30 && elapsedMs < this._duration * 500) return;
    this._autoFired = true;
    this.playEpisode(this.currentEp + 1);
  }

  _armFallback() {
    if (this._fallbackInt) { clearInterval(this._fallbackInt); this._fallbackInt = null; }
    this._fallbackInt = setInterval(() => {
      if (this.destroyed || this._autoFired) { clearInterval(this._fallbackInt); return; }
      const d = this._duration, t = this._lastTime;
      // Watchdog only mirrors real player time. Never estimate watch progress
      // from wall-clock time spent on the page.
      if (!state.autoNext) return;
      if (this.currentEp >= this.episodes.length - 1) return;
      if (d > 30 && t >= d - 3) {
        this._recordCurrentEpisodeComplete();
      }
    }, 2000);
  }

  _bindPlayerControls() {
    const trigger = $("#avDubTrigger");
    const pop = $("#avDubPop");
    if (trigger && !trigger._bound) {
      trigger._bound = true;
      trigger.addEventListener("click", e => {
        e.stopPropagation();
        this.toggleDubPop();
      });
      document.addEventListener("click", e => {
        if (!pop?.hidden && !pop.contains(e.target) && !trigger.contains(e.target)) this.closeDubPop();
      });
    }
    const prev = $("#avPrevEp");
    const next = $("#avNextEp");
    if (prev && !prev._bound) {
      prev._bound = true;
      prev.addEventListener("click", () => { if (this.currentEp > 0) this.playEpisode(this.currentEp - 1); });
    }
    if (next && !next._bound) {
      next._bound = true;
      next.addEventListener("click", () => { if (this.currentEp < this.episodes.length - 1) this.playEpisode(this.currentEp + 1); });
    }
    const cb = $("#avAutoNext");
    if (cb && !cb._bound) {
      cb._bound = true;
      cb.checked = state.autoNext;
      cb.addEventListener("change", () => prefs.setAutoNext(cb.checked));
    }
  }

  _isNonAnime() {
    const a = this.anime || {};
    if (NON_ANIME_TYPES.has(a.type)) return true;
    const dur = parseEpDurationMin(a.duration);
    if (dur != null) {
      if (a.type === "Movie") return dur < MIN_MOVIE_DURATION;
      return dur < MIN_EPISODE_DURATION;
    }
    return false;
  }

  _nextAiringDate() {
    const a = this.anime;
    const status = (a?.status || "").toLowerCase();
    if (!status.includes("not yet")) return null;
    const iso = a?.aired?.from;
    if (!iso) return "unknown";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "unknown";
    return d > new Date() ? d : "unknown";
  }

  renderComingSoon(releaseAt) {
    const ov = $("#avPlayerOverlay");
    if (!ov) return;
    const hasDate = releaseAt instanceof Date;
    ov.innerHTML = hasDate ? `
      <div class="coming-soon">
        <div class="coming-title">До выхода аниме осталось:</div>
        <div class="coming-timer" id="avCountdown">
          <div class="ct-cell"><span class="ct-num" data-k="d">—</span><span class="ct-lbl">дней</span></div>
          <div class="ct-sep">:</div>
          <div class="ct-cell"><span class="ct-num" data-k="h">—</span><span class="ct-lbl">часов</span></div>
          <div class="ct-sep">:</div>
          <div class="ct-cell"><span class="ct-num" data-k="m">—</span><span class="ct-lbl">минут</span></div>
          <div class="ct-sep">:</div>
          <div class="ct-cell"><span class="ct-num" data-k="s">—</span><span class="ct-lbl">секунд</span></div>
        </div>
        <div class="coming-date">${releaseAt.toLocaleString("ru-RU", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" })}</div>
      </div>` : `
      <div class="coming-soon">
        <div class="coming-title">Аниме анонсировано</div>
        <div class="coming-date" style="max-width:480px;line-height:1.5">
          Дата премьеры пока не объявлена. Эпизоды появятся, как только аниме начнёт выходить — возвращайтесь позже.
        </div>
      </div>`;
    ov.classList.remove("hide");
    $("#avEpisodesList").innerHTML = `<div class="av-empty">Эпизодов пока нет — аниме ещё не вышло</div>`;
    if (!hasDate) return;
    const tick = () => {
      if (this.destroyed) { clearInterval(this._countdownInt); return; }
      const diff = releaseAt.getTime() - Date.now();
      if (diff <= 0) {
        clearInterval(this._countdownInt);
        this.init();
        return;
      }
      const d = Math.floor(diff / 86400000);
      const h = Math.floor((diff % 86400000) / 3600000);
      const m = Math.floor((diff % 3600000) / 60000);
      const s = Math.floor((diff % 60000) / 1000);
      const c = $("#avCountdown");
      if (!c) return;
      const pad = n => String(n).padStart(2, "0");
      c.querySelector('[data-k="d"]').textContent = d;
      c.querySelector('[data-k="h"]').textContent = pad(h);
      c.querySelector('[data-k="m"]').textContent = pad(m);
      c.querySelector('[data-k="s"]').textContent = pad(s);
    };
    tick();
    this._countdownInt = setInterval(tick, 1000);
  }

  renderUnavailable(msg) {
    const ruTitle = this.anime._ru || "";
    const enTitle = this.anime.title_english || this.anime.title || "";
    const jpTitle = this.anime.title_japanese || "";
    const parts = uniq([ruTitle, enTitle, jpTitle]).filter(Boolean);
    const ddgQuery = [...parts, "смотреть онлайн"].join(" ");
    const ddg = `https://duckduckgo.com/?q=${encodeURIComponent(ddgQuery)}`;
    const ov = $("#avPlayerOverlay");
    if (!ov) return;
    ov.innerHTML = `
      <div class="unavail">
        <div class="unavail-icon">
          <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="9"/>
            <path d="M8.5 16c.8-1.5 2.1-2.5 3.5-2.5s2.7 1 3.5 2.5"/>
            <line x1="9" y1="10" x2="9.01" y2="10"/>
            <line x1="15" y1="10" x2="15.01" y2="10"/>
          </svg>
        </div>
        <div class="unavail-title">${msg ? esc(msg) : "Видео пока не найдено"}</div>
        <div class="unavail-sub">К сожалению, ни один из API-источников не содержит данный контент. Если вы хотите посмотреть конкретно это аниме, воспользуйтесь поиском.</div>
        <div class="unavail-links">
          <a href="${esc(ddg)}" target="_blank" rel="noopener" class="unavail-ddg">
            <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M10 2a8 8 0 1 0 4.9 14.3l5.4 5.4 1.4-1.4-5.4-5.4A8 8 0 0 0 10 2m0 2a6 6 0 1 1 0 12 6 6 0 0 1 0-12"/></svg>
            Искать в DuckDuckGo
          </a>
        </div>
      </div>`;
    ov.classList.remove("hide");
    $("#avEpisodesList").innerHTML = `<div class="av-empty">Источник не найден</div>`;
  }

  _expandQueries(titles) {
    const STOP = new Set(["the","and","for","with","to","of","in","on","из","от","был","была","было","так","что","как","это","для","и","в","на","с","а","я","ей","им","их","же","ли","по","не","но","или","к","о","об","из","от"]);
    const out = new Set();
    for (const t of titles) {
      if (!t) continue;
      out.add(t);
      const words = t.replace(/[^\p{L}\s]/gu, " ").split(/\s+/).filter(w => w.length > 4 && !STOP.has(w.toLowerCase()));
      if (words.length >= 3) {
        out.add(words.slice(0, 2).join(" "));
        if (words.length >= 4) out.add(words.slice(-2).join(" "));
        for (const w of words) if (w.length > 6) out.add(w);
      } else if (words.length >= 1) {
        const longest = words.slice().sort((a, b) => b.length - a.length)[0];
        if (longest && longest.length > 5) out.add(longest);
      }
    }
    return [...out].slice(0, 10);
  }

  async discoverBackend(src) {
    if (this.sources[`backend:${src}`]?.episodes?.length) return;
    const yr = this.anime?.year;
    const mal = this.malId;
    const queries = this._expandQueries(this.titles);
    let best = null, bestScore = 0;

    const scoreCandidate = c => {
      const names = [c.title, c.title_en].filter(Boolean);
      let s = 0;
      for (const e of this.titles) for (const n of names) s = Math.max(s, titleSimilarity(n, e));
      const candidateMal = Number(c.mal_id);
      const hasCandidateMal = mal && c.mal_id != null && Number.isFinite(candidateMal);
      if (hasCandidateMal) {
        if (candidateMal === Number(mal)) s = Math.max(s, 1.0);
        else s -= 0.25;
      }
      if (yr && c.year) {
        const diff = Math.abs(c.year - yr);
        if (diff === 0) s += 0.15;
        else if (diff <= 1) s += 0.05;
        else if (diff > 3) s -= 0.2;
      }
      return s;
    };

    const seen = new Set();
    const consider = rows => {
      for (const c of rows || []) {
        const key = c.url || c.key || c.title;
        if (!key || seen.has(key)) continue;
        seen.add(key);
        const s = scoreCandidate(c);
        if (s > bestScore) { bestScore = s; best = c; }
      }
    };

    for (let i = 0; i < queries.length; i += 2) {
      const results = await Promise.all(
        queries.slice(i, i + 2).map(t => api.backendSearch(t, src, yr, mal).catch(() => []))
      );
      for (const r of results) consider(r);
      if (bestScore >= 1.0) break;
    }
    if (!best || bestScore < 0.3) return;
    let eps = [];
    try { eps = await api.backendEpisodes(best.key, src); } catch { return; }
    if (!eps?.length) {
      if (bestScore >= 1.0) this._matchedNoEps = true;
      return;
    }
    this.sources[`backend:${src}`] = {
      label: src,
      episodes: eps.map((e, i) => ({
        num: e.num ?? i + 1,
        name: ruEpisodeTitle(e.title, e.num ?? i + 1),
        key: e.key, _src: src, preview: e.preview,
      })),
      meta: { title: best.title || this.titles[0], year: best.year },
      _isBackend: true,
    };
  }

  async discoverWithFallback() {
    const priority = ["yummy_anime", "oldyummy", "animego", "yummy_anime_org",
                      "dreamcast", "anilibria", "anilibme", "sameband"];
    const available = new Set(state.backendSources || []);
    const pool = priority.filter(s => available.has(s));
    if (!pool.length) return;

    const seq = (this._discoverySeq || 0) + 1;
    this._discoverySeq = seq;
    const adoptFoundSource = () => {
      if (this.destroyed || this._discoverySeq !== seq) return true;
      if (this._activeSource && this.sources[`backend:${this._activeSource}`]?.episodes?.length) return true;
      for (const s of pool) {
        if (this.sources[`backend:${s}`]?.episodes?.length) {
          this._activeSource = s;
          this._renderFoundSource();
          return true;
        }
      }
      return false;
    };

    const runWave = async (wave, waitMs) => {
      let pending = wave.length;
      if (!pending) return false;
      wave.forEach(src => {
        this.discoverBackend(src)
          .catch(() => null)
          .finally(() => {
            pending -= 1;
            adoptFoundSource();
          });
      });
      const deadline = Date.now() + waitMs;
      while (pending > 0 && Date.now() < deadline) {
        if (adoptFoundSource()) return true;
        await new Promise(r => setTimeout(r, 200));
      }
      return adoptFoundSource();
    };

    const waves = [pool.slice(0, 2), pool.slice(2, 5), pool.slice(5)];
    for (const wave of waves) {
      if (adoptFoundSource()) return;
      if (await runWave(wave, 6_000)) return;
      if (this.destroyed || this._discoverySeq !== seq) return;
    }
    adoptFoundSource();
  }

  _adoptAnyReadySource() {
    const priority = ["yummy_anime", "oldyummy", "animego", "yummy_anime_org",
                      "dreamcast", "anilibria", "anilibme", "sameband"];
    for (const s of priority) {
      if (this.sources[`backend:${s}`]?.episodes?.length) {
        this._activeSource = s;
        return this._renderFoundSource();
      }
    }
    return false;
  }

  renderEpisodes() {
    const list = $("#avEpisodesList");
    const search = $("#avEpSearch");
    search.hidden = this.episodes.length < 12;
    search.oninput = e => {
      const q = e.target.value.toLowerCase();
      $$(".ep-row", list).forEach(r => {
        const ok = !q || r.dataset.num.toLowerCase().includes(q) || r.dataset.name.toLowerCase().includes(q);
        r.style.display = ok ? "" : "none";
      });
    };
    list.innerHTML = "";
    const lastProgressEp = this._lastProgressEpisodeNum();
    const completedThrough = this._completedThroughEpisodeNum();
    this.episodes.forEach((e, i) => {
      // Предпочитаем серверный per-episode прогресс (включая implicit-complete
      // для серий, которые «перепрыгнули» при досмотре следующей). Fallback на
      // локальный state.watch для гостей / до hydrate.
      const serverProg = this._epProgress?.get(Number(e.num));
      const prog = serverProg || getProgress(this.malId, e.num);
      const pct = prog?.duration ? Math.min(100, ((prog.time ?? prog.seconds ?? 0) / prog.duration) * 100) : 0;
      const watched = pct > 90 || Number(e.num) <= completedThrough;
      const hasProgress = Number(prog?.seconds ?? prog?.time ?? 0) > 0;
      const canUnwatch = state.user && hasProgress && Number(e.num) === lastProgressEp;
      const b = document.createElement("div");
      b.className = "ep-row" + (i === this.currentEp ? " active" : "") + (watched ? " watched" : "");
      b.setAttribute("role", "button");
      b.tabIndex = 0;
      b.dataset.num = String(e.num);
      b.dataset.name = e.name;
      b.innerHTML = `
        <span class="ep-num">${e.num}</span>
        <span class="ep-name">${esc(e.name)}</span>
        ${canUnwatch ? `
          <button type="button" class="ep-unwatch" aria-label="Отметить серию ${e.num} как непросмотренную">
            <svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">
              <path d="M6 6l12 12M18 6 6 18" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>
            </svg>
            <span class="ep-unwatch-tip">отметить как непросмотренное</span>
          </button>
          <div class="ep-unwatch-confirm" hidden>
            <div class="ep-unwatch-confirm-text">Вы уверены, что хотите отметить, что не смотрели эту серию? Это действие необратимо, так же будет удалена любая активность связанная с просмотром этой серии.</div>
            <div class="ep-unwatch-confirm-actions">
              <button type="button" class="ep-unwatch-yes">Да</button>
              <button type="button" class="ep-unwatch-no">Отмена</button>
            </div>
          </div>` : ""}
        ${pct > 0 && pct < 95 ? `<div class="ep-progress"><span style="width:${pct}%"></span></div>` : ""}`;
      b.addEventListener("click", () => this.playEpisode(i));
      b.addEventListener("keydown", ev => {
        if (ev.key === "Enter" || ev.key === " ") {
          ev.preventDefault();
          this.playEpisode(i);
        }
      });
      const unwatch = $(".ep-unwatch", b);
      if (unwatch) {
        unwatch.addEventListener("click", ev => {
          ev.preventDefault();
          ev.stopPropagation();
          if (ev.shiftKey) this.markEpisodeUnwatched(e.num);
          else this.openEpisodeUnwatchConfirm(e.num, b);
        });
      }
      const confirm = $(".ep-unwatch-confirm", b);
      if (confirm) {
        confirm.addEventListener("click", ev => {
          ev.preventDefault();
          ev.stopPropagation();
        });
        $(".ep-unwatch-yes", confirm)?.addEventListener("click", ev => {
          ev.preventDefault();
          ev.stopPropagation();
          this.markEpisodeUnwatched(e.num);
        });
        $(".ep-unwatch-no", confirm)?.addEventListener("click", ev => {
          ev.preventDefault();
          ev.stopPropagation();
          this.closeEpisodeUnwatchConfirm();
        });
      }
      list.appendChild(b);
    });
  }
  openEpisodeUnwatchConfirm(epNum, row) {
    this.closeEpisodeUnwatchConfirm();
    const pop = $(".ep-unwatch-confirm", row);
    if (!pop) return;
    row.classList.add("confirm-open");
    pop.hidden = false;
    this._unwatchConfirmEp = Number(epNum);
  }
  closeEpisodeUnwatchConfirm() {
    $$("#avEpisodesList .ep-row.confirm-open").forEach(row => {
      row.classList.remove("confirm-open");
      const pop = $(".ep-unwatch-confirm", row);
      if (pop) pop.hidden = true;
    });
    this._unwatchConfirmEp = null;
  }
  async markEpisodeUnwatched(epNum) {
    if (!state.user || this._unwatchingEp) return;
    const n = Number(epNum);
    const row = $(`#avEpisodesList .ep-row[data-num="${CSS.escape(String(n))}"]`);
    const btn = row ? $(".ep-unwatch", row) : null;
    this._unwatchingEp = n;
    if (btn) btn.disabled = true;
    try {
      this.closeEpisodeUnwatchConfirm();
      const r = await account.store.markEpisodeUnwatched(this.malId, n);
      if (Array.isArray(r?.progress)) this._setEpisodeProgressRows(r.progress);
      else await this._fetchEpisodeProgress();
      this.renderEpisodes();
      $$("#avEpisodesList .ep-row").forEach((b, idx) => b.classList.toggle("active", idx === this.currentEp));
    } catch (err) {
      console.warn("markEpisodeUnwatched failed", err);
      this.status("Не удалось снять отметку просмотра");
    } finally {
      this._unwatchingEp = null;
      if (btn) btn.disabled = false;
    }
  }
  async playEpisode(i) {
    if (this.destroyed) return;
    this.closeEpisodeUnwatchConfirm();
    const reqId = ++this._reqId;
    this.currentEp = i;
    this._autoFired = false;
    this._lastTime = 0;
    this._duration = 0;
    this._playStartedAt = Date.now();
    this._sawPlaybackSignal = false;
    this._lastCompleteSignalEp = null;
    this._armFallback();
    $$("#avEpisodesList .ep-row").forEach((b, idx) => b.classList.toggle("active", idx === i));
    // aviev_watch_history теперь создаётся ТОЛЬКО серверным progress-event'ом,
    // когда просмотр перевалил за 5 минут. Не стартуем фейковые записи «просто
    // открыл страницу» — фид «Продолжить просмотр» должен быть честный.
    const ep = this.episodes[i];
    if (!ep) return;
    this._rememberTabEpisode(ep.num);
    $("#avEpInfo").innerHTML = `<span class="sp-ep-num">${ep.num}</span><span class="sp-ep-title">${esc(ep.name)}</span>`;
    const p = $("#avPrevEp"), n = $("#avNextEp");
    if (p) p.disabled = i === 0;
    if (n) n.disabled = i === this.episodes.length - 1;
    this.status(`Загружаю озвучки для эпизода ${ep.num}…`);

    try {
      let dubs = this._dubsCache.get(ep.num);
      if (!dubs) {
        dubs = await this._loadEpisodeDubs(ep, reqId, { retryStale: true });
        if (reqId !== this._reqId) return;
        if (!dubs.length) { this.status("Озвучки не найдены"); return; }
        this._dubsCache.set(ep.num, dubs);
      }
      this.currentDubs = dubs;
      await this._prefetchDubUrls(dubs, reqId);
      if (reqId !== this._reqId) return;

      const preferred = prefs.getDub(this.malId);
      let idx = 0;
      if (preferred) {
        const found = dubs.findIndex(d => this._normDubName(d.label) === preferred);
        if (found >= 0) idx = found;
      }
      if (!preferred && !this._isKodikUrl(dubs[idx]?.iframeUrl)) {
        const kodikIdx = dubs.findIndex(d => this._isKodikUrl(d.iframeUrl));
        if (kodikIdx >= 0) idx = kodikIdx;
      }
      this.currentDubIdx = idx;
      this.renderDubs();
      await this.playDub(idx);
    } catch (e) { this.status(`Ошибка: ${e.message}`); }
  }

  _isKodikUrl(url) {
    return typeof url === "string" && /kodik/i.test(url);
  }

  async _prefetchDubUrls(dubs, reqId) {
    const pending = dubs.filter(d => !d.iframeUrl && d._sourceKey && d._src);
    if (!pending.length) return;
    const timeout = (p, ms) => Promise.race([
      p, new Promise(r => setTimeout(() => r(null), ms)),
    ]);
    await Promise.all(pending.map(async d => {
      try {
        const vids = await timeout(api.backendVideos(d._sourceKey, d._src), 4000);
        if (!vids || reqId !== this._reqId) return;
        const iframe = vids.find(v => v.type === "iframe");
        if (iframe?.url) d.iframeUrl = iframe.url;
      } catch (err) {
        if (this._isStaleBackendKeyError(err)) d._staleSourceKey = true;
      }
    }));
  }
  renderDubs() {
    const box = $("#avDubTabs");
    const trigger = $("#avDubTrigger");
    const label = $(".sp-dub-label");
    if (!box) return;
    box.innerHTML = "";

    if (!this.currentDubs.length) {
      if (trigger) trigger.style.display = "none";
      return;
    }
    if (trigger) trigger.style.display = "inline-flex";

    const cur = this.currentDubs[this.currentDubIdx];
    if (cur && label) {
      const short = cur.label.replace(/^(Озвучка|Субтитры|Оригинал)\s*/i, "").trim() || cur.label;
      label.textContent = short;
    }

    const isSub = d => /субтитры/i.test(d.label);
    const voices = this.currentDubs.map((d, i) => ({ d, i })).filter(({ d }) => !isSub(d));
    const subs = this.currentDubs.map((d, i) => ({ d, i })).filter(({ d }) => isSub(d));
    const sections = [
      { title: "Озвучки", items: voices },
      { title: "Субтитры", items: subs },
    ].filter(s => s.items.length);

    sections.forEach(g => {
      const section = document.createElement("div");
      section.className = "sp-dub-section";
      if (sections.length > 1) {
        const title = document.createElement("div");
        title.className = "sp-dub-sec-title";
        title.textContent = `${g.title} · ${g.items.length}`;
        section.appendChild(title);
      }
      const listWrap = document.createElement("div");
      listWrap.className = "sp-dub-list";
      g.items.forEach(({ d, i }) => {
        const b = document.createElement("button");
        b.className = "sp-dub-item" + (i === this.currentDubIdx ? " active" : "");
        const short = d.label.replace(/^(Озвучка|Субтитры|Оригинал)\s*/i, "").trim() || d.label;
        b.textContent = short;
        b.title = d.label;
        b.addEventListener("click", () => {
          this.currentDubIdx = i;
          prefs.setDub(this.malId, this._normDubName(d.label));
          this.playDub(i);
          this.closeDubPop();
        });
        listWrap.appendChild(b);
      });
      section.appendChild(listWrap);
      box.appendChild(section);
    });
  }

  closeDubPop() {
    const pop = $("#avDubPop"); if (pop) pop.hidden = true;
    const tr = $("#avDubTrigger"); if (tr) tr.classList.remove("open");
  }
  toggleDubPop() {
    const pop = $("#avDubPop");
    const tr = $("#avDubTrigger");
    if (!pop || !tr) return;
    const isOpen = !pop.hidden;
    pop.hidden = isOpen;
    tr.classList.toggle("open", !isOpen);
  }
  async playDub(i, { retryStale = true } = {}) {
    const d = this.currentDubs[i];
    if (!d) return;
    this.currentDubIdx = i;
    this.renderDubs();
    const reqId = this._reqId;

    if (!d.iframeUrl && d._sourceKey) {
      this.status(`Загружаю «${d.label}»…`);
      let vids = [];
      try { vids = await api.backendVideos(d._sourceKey, d._src) || []; }
      catch (err) {
        if (retryStale && this._isStaleBackendKeyError(err)) {
          const oldNorm = this._normDubName(d.label);
          const freshDubs = await this._reloadCurrentEpisodeDubs(reqId);
          if (reqId !== this._reqId) return;
          if (freshDubs?.length) {
            const freshIdx = Math.max(0, freshDubs.findIndex(x => this._normDubName(x.label) === oldNorm));
            this.currentDubIdx = freshIdx;
            this.renderDubs();
            return this.playDub(freshIdx, { retryStale: false });
          }
        }
      }
      if (reqId !== this._reqId) return;
      if (!vids?.length) {
        try {
          const ep = this.episodes[this.currentEp];
          const list = await api.backendSources(ep.yummyKey || ep.key, d._src || this._activeSource);
          if (reqId !== this._reqId) return;
          const match = list.find(s => s.name === d.label) || list[0];
          if (match) {
            d._sourceKey = match.key;
            vids = await api.backendVideos(match.key, d._src) || [];
            if (reqId !== this._reqId) return;
          }
        } catch {}
      }
      const iframe = vids.find(v => v.type === "iframe");
      if (iframe) d.iframeUrl = iframe.url;
    }

    if (!d.iframeUrl) { this.status(`Нет видео для «${d.label}»`); return; }
    this.createIframe(d.iframeUrl);
    this.hideOverlay();
    // Ранее здесь был 10-минутный таймер, который писал фейковую запись в
    // watch_history. Теперь всё это делает серверный progress-event при
    // seconds >= 300 — одна правда на фронте и на бэке.
  }

  createIframe(url) {
    const c = $("#artplayer");
    if (!c) return;
    const playerProxyBase = (() => {
      if (BACKEND) return BACKEND;
      if (location.protocol === "http:" && location.port === "8787") {
        if (location.hostname === "127.0.0.1") return "http://localhost:8787";
        if (location.hostname === "localhost") return "http://127.0.0.1:8787";
      }
      return BACKEND;
    })();
    const proxiedOrigin = (() => {
      if (!playerProxyBase) return false;
      try { return new URL(playerProxyBase, location.origin).origin !== location.origin; }
      catch (_) { return false; }
    })();
    let playerUrl = `${playerProxyBase}/player/frame?url=${encodeURIComponent(url)}`;
    let allowSameOrigin = proxiedOrigin;
    try {
      const u = new URL(url);
      if (/(\.|^)kodikplayer\.com$/i.test(u.hostname)
          && /^\/(uv|video|seria|episode|season|serial)\//i.test(u.pathname)) {
        playerUrl = u.pathname + u.search;
        allowSameOrigin = false;
      }
    } catch (_) {}
    let iframe = c.querySelector("iframe");
    const hardenFrame = (node, sameOrigin = false) => {
      node.allow = "autoplay; fullscreen; picture-in-picture; encrypted-media";
      node.allowFullscreen = true;
      node.referrerPolicy = "no-referrer";
      node.setAttribute("sandbox", `allow-scripts allow-presentation${sameOrigin ? " allow-same-origin" : ""}`);
    };
    if (iframe) {
      hardenFrame(iframe, allowSameOrigin);
      if (iframe.src !== playerUrl) iframe.src = playerUrl;
      this._iframeEl = iframe;
      return;
    }
    iframe = document.createElement("iframe");
    // Harden third-party players: keep playback features, block popups and
    // top-level navigation attempts from ad scripts inside the iframe.
    hardenFrame(iframe, allowSameOrigin);
    iframe.style.cssText = "width:100%;height:100%;border:0;background:#000";
    iframe.src = playerUrl;
    c.appendChild(iframe);
    this._iframeEl = iframe;
  }
  destroyPlayer() {
    const c = $("#artplayer");
    if (c) c.innerHTML = "";
    this._iframeEl = null;
  }
  status(m) { const s = $("#avPlayerStatus"); if (s) s.textContent = m; const o = $("#avPlayerOverlay"); if (o) o.classList.remove("hide"); }
  hideOverlay() { const o = $("#avPlayerOverlay"); if (o) o.classList.add("hide"); }
  destroy() {
    this.destroyed = true;
    if (this._fallbackInt) clearInterval(this._fallbackInt);
    if (this._countdownInt) clearInterval(this._countdownInt);
    if (this._onMessage) window.removeEventListener("message", this._onMessage);
    if (this._shellCleanup) this._shellCleanup();
    this.destroyPlayer();
  }
}

// ---------- router ----------
async function router() {
  state.current?.destroy?.();
  state.current = null;
  clearInterval(heroTimer);
  window.scrollTo({ top: 0, behavior: "instant" });
  account.closeGate();

  const path = location.pathname || "/";
  const query = location.search.slice(1);
  const params = new URLSearchParams(location.search);

  if (path === "/" || path === "") return viewHome();
  if (path === "/top") return viewCatalog("top", query);
  if (path === "/season") return viewCatalog("season", query);
  if (path === "/trending") return viewCatalog("trending", query);
  if (path === "/movies") return viewCatalog("movies", query);
  if (path === "/catalog" || path === "/filter" || path === "/search") return viewCatalog("search", query);
  if (path === "/random") return viewRandom();
  if (path === "/login") return account.viewLogin(params);

  // Старые URL → мягкий редирект на новые
  if (path === "/favorites") { nav("/my/favorites"); return; }
  if (path === "/watching" || path === "/my/watching") { nav("/my/continue"); return; }
  if (path === "/history"  || path === "/my/history")  { nav("/my/continue"); return; }

  if (path === "/my/favorites") return account.viewMyFavorites();
  if (path === "/my/continue")  return account.viewMyContinue();
  if (path === "/my/lists")     return account.viewMyLists();
  let m = path.match(/^\/my\/lists\/([a-z]+)\/?$/);
  if (m) return account.viewMyLists(m[1]);
  if (path === "/my/settings")          return account.viewMySettings();
  if (path === "/my/settings/privacy")  return account.viewMySettings("privacy");

  // Публичный профиль: /@handle (alias или profile{id})
  m = path.match(/^\/@([a-zA-Z0-9_.-]+)\/?$/);
  if (m) return account.viewProfile(m[1]);

  m = path.match(/^\/anime\/(\d+)(?:-[a-z0-9-]+)?\/?$/);
  if (m) return viewAnime(Number(m[1]));

  m = path.match(/^\/genre\/(\d+)\/(.+)$/);
  if (m) return viewGenre(Number(m[1]), decodeURIComponent(m[2]));

  return viewHome();
}

function nav(to) {
  if (typeof to !== "string") return;
  if (to.startsWith("#/")) to = to.slice(1);          // legacy hash → pathname
  if (!to.startsWith("/")) to = "/" + to;
  const current = location.pathname + location.search;
  if (to === current) return;
  history.pushState(null, "", to);
  router();
}

function _migrateHashOnBoot() {
  if (location.hash && location.hash.startsWith("#/")) {
    const newPath = location.hash.slice(1) + location.search;
    history.replaceState(null, "", newPath);
  }
}

// ---------- backend ping ----------
async function refreshBackend() {
  const { ok, sources, vpn } = await api.pingBackend();
  state.backendAvailable = ok;
  state.useProxy = ok && vpn;
  state.backendSources = sources;
}

// ---------- events ----------
function bind() {
  document.addEventListener("keydown", e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); nav("/catalog"); }
  });

  const mobBtn = $("#mobileMenuBtn");
  if (mobBtn) {
    mobBtn.addEventListener("click", () => {
      $("#topMenu").classList.toggle("show");
      $("#darkOverlay").classList.toggle("show");
    });
    $("#darkOverlay").addEventListener("click", () => {
      $("#topMenu").classList.remove("show");
      $("#darkOverlay").classList.remove("show");
    });
  }

  const toTop = $("#toTop");
  window.addEventListener("scroll", () => {
    if (toTop) toTop.classList.toggle("show", window.scrollY > 400);
  });
  toTop?.addEventListener("click", () => window.scrollTo({ top: 0, behavior: "smooth" }));

  window.addEventListener("popstate", () => router());

  // SPA link interceptor — intercept same-origin navigations except when the
  // link is external, has target=_blank, or the user is holding a modifier.
  document.addEventListener("click", (e) => {
    const a = e.target.closest("a[href]");
    if (!a) return;
    const href = a.getAttribute("href") || "";
    if (!href) return;
    if (a.target === "_blank" || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
    if (/^(https?:)?\/\//i.test(href) || href.startsWith("mailto:") || href.startsWith("tel:")) return;
    if (href.startsWith("#") && !href.startsWith("#/")) return;
    let target = href;
    if (href.startsWith("#/")) target = href.slice(1);
    if (!target.startsWith("/")) return;
    e.preventDefault();
    nav(target);
  });
}

// ---------- init ----------
_migrateHashOnBoot();
bind();

(async function boot() {
  await refreshBackend();
  await account.setup({
    state,
    backendUrl: BACKEND,
    nav,
    outlet: $("#outlet"),
    setActive: setActiveNav,
    errorPage,
    makeCard,
    imgUrl,
    enrichWithShiki,
  });
  router();
  setInterval(refreshBackend, 30000);
})();
