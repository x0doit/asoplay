// -*- coding: utf-8 -*-
/*
 * AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
 * All rights reserved. See /COPYRIGHT for full terms.
 *
 * SPA entry point, router and public views. Personal sections (login,
 * favorites, history, watching, settings) live in /js/account.js and own
 * the server-backed store. Guests cannot read or write personal data;
 * any attempt to do so surfaces a guest-gate.
 */

import * as account from "./js/account.js?v=20261029-eddas2";
import { setupNavigation, setActiveSection, SheetDrag } from "./js/navigation.js?v=20261029-eddas2";
import { getTrailerId, bindTrailer } from "./js/trailer.js?v=20261029-eddas2";

// Mark iframe embeds early enough for compact app-like layout rules.
(() => {
  const root = document.documentElement;
  const syncFrameMode = () => {
    let embedded = false;
    try { embedded = window.self !== window.top; }
    catch (_) { embedded = true; }
    root.classList.toggle("is-embedded", embedded);
    root.classList.toggle(
      "is-compact-frame",
      embedded && (window.innerWidth <= 1100 || window.innerHeight <= 760)
    );
  };
  syncFrameMode();
  window.addEventListener("resize", syncFrameMode, { passive: true });
})();

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

const ANILIST_DIRECT = "https://graphql.anilist.co";
const SHIKI_ORIGIN = "https://shikimori.io";
const JIKAN = () => `${BACKEND}/proxy/jikan`;
const ANILIST = () => state.useProxy ? `${BACKEND}/proxy/anilist` : ANILIST_DIRECT;
const imgUrl = (u) => (u && state.proxyImages ? `${BACKEND}/proxy/img?url=${encodeURIComponent(u)}` : (u || ""));

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s = "") => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const uniq = a => [...new Set(a)];

// Empty, error and "not found" states share the EDDAS EmptyState: icon, title, one hint, actions.
const EMPTY_ICONS = {
  empty: "fa-regular fa-folder-open",
  confused: "fas fa-magnifying-glass",
  sad: "fas fa-triangle-exclamation",
};
function errorPage(opts = {}) {
  const { code, title, message, action, secondary, variant = "sad", icon } = opts;
  const glyph = icon || (code === "404" ? "fas fa-compass" : EMPTY_ICONS[variant] || EMPTY_ICONS.sad);
  return `
    <div class="ed-empty err-page">
      <div class="ed-empty__icon"><i class="${glyph}" aria-hidden="true"></i></div>
      <h2 class="ed-empty__title">${esc(title || "")}</h2>
      ${message ? `<p class="ed-empty__text">${typeof message === "string" && message.includes("<") ? message : esc(message)}</p>` : ""}
      ${code ? `<p class="err-code">Код ошибки ${esc(String(code))}</p>` : ""}
      ${(action || secondary) ? `
        <div class="ed-empty__action">
          ${action ? `<a class="ed-btn ed-btn--primary" href="${esc(action.href)}">${esc(action.label)}</a>` : ""}
          ${secondary ? `<a class="ed-btn ed-btn--secondary" href="${esc(secondary.href)}">${esc(secondary.label)}</a>` : ""}
        </div>` : ""}
    </div>`;
}

function loadingBlock(text = "Загружаем…") {
  return `<div class="ed-loading"><span class="ed-spinner" aria-hidden="true"></span>${esc(text)}</div>`;
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
  "Survival": "Выживание", "Detective": "Детективы",
  "Workplace": "Работа", "Video Game": "Видеоигры", "Adult Cast": "Взрослый каст",
  "Idols (Female)": "Айдолы",
  "Idols (Male)": "Айдолы (муж.)", "Love Polygon": "Любовный многоугольник",
  "Medical": "Медицина", "Otaku Culture": "Отаку-культура",
  "Performing Arts": "Искусства", "Pets": "Питомцы",
  "Racing": "Гонки", "Reverse Harem": "Обратный гарем",
  "Romantic Subtext": "Романтический подтекст", "Team Sports": "Командный спорт",
  "Visual Arts": "Изобразительные искусства",
  "Anthropomorphic": "Антропоморфизм", "CGDCT": "Милые девушки", "Childcare": "Забота о детях",
  "Combat Sports": "Единоборства", "Crossdressing": "Переодевание", "Delinquents": "Хулиганы",
  "Educational": "Образовательное", "Gore": "Жестокость", "High Stakes Game": "Игра на выживание",
  "Iyashikei": "Иясикэй", "Love Status Quo": "Любовь без перемен", "Magical Sex Shift": "Смена пола",
  "Mahou Shoujo": "Махо-сёдзё", "Organized Crime": "Организованная преступность", "Showbiz": "Шоу-бизнес",
  "Strategy Game": "Стратегические игры", "Urban Fantasy": "Городское фэнтези", "Villainess": "Злодейка",
  "Erotica": "Эротика", "Hentai": "Хентай",
};
const tr = (m, v) => m[v] || v;
const trGenre = v => GENRE_RU[v] || v;
const hasCyrillic = s => /[\u0400-\u04FF]/.test(s || "");

function fitHeroTitle(el) {
  if (!el) return;
  const len = (el.textContent || "").length;
  let fs = "";
  if (len > 180) fs = "18px";
  else if (len > 140) fs = "22px";
  else if (len > 100) fs = "26px";
  else if (len > 70)  fs = "32px";
  else if (len > 45)  fs = "38px";
  el.style.setProperty("--title-size", fs);
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
  proxyImages: false,
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
      <button type="button" class="ed-iconbtn lb-close" aria-label="Закрыть" title="Закрыть"><i class="fas fa-xmark" aria-hidden="true"></i></button>
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
const CATALOG_TIMEOUT = 30000; // Includes MAL rate limiting and local parser work.
const debounce = (fn, ms = 400) => { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; };

const season = () => { const m = new Date().getMonth() + 1; return m <= 3 ? "winter" : m <= 6 ? "spring" : m <= 9 ? "summer" : "fall"; };
const SEASON_RU = { winter: "Зима", spring: "Весна", summer: "Лето", fall: "Осень" };
const year = () => new Date().getFullYear();

async function loadCatalogData(path, select = response => response.data) {
  const response = await fetchJson(`${JIKAN()}${path}`, { timeout: CATALOG_TIMEOUT });
  const data = select(response);
  if (data == null) throw new Error("Invalid catalog response");
  return data;
}

function catalogPage(response) {
  if (!Array.isArray(response.data)) throw new Error("Invalid catalog response");
  return { data: response.data, pagination: response.pagination || {} };
}
function renderCatalogFailure(container, error, russianSearch = false) {
  if (!container.isConnected) return;
  container.innerHTML = errorPage({
    title: "Каталог временно недоступен",
    message: error.catalogMessage || ([400, 422].includes(error.status)
      ? "Проверьте параметры поиска и фильтров. Источник не смог обработать этот запрос."
      : russianSearch
      ? "Сервис поиска по русским названиям не отвечает. Попробуйте оригинальное название или повторите позже."
      : "Не удалось загрузить данные каталога. Выбранные фильтры сохранены. Попробуйте ещё раз чуть позже."),
    action: { href: location.pathname + location.search, label: "Повторить" },
    secondary: { href: "/catalog", label: "Изменить фильтры" },
  });
}
function loadCatalogPage(params = {}, seasonal = false) {
  const query = new URLSearchParams({ sfw: "true", limit: "24" });
  for (const [key, value] of Object.entries(params)) if (value !== "" && value != null) query.set(key, value);
  const path = seasonal ? `/seasons/${year()}/${season()}` : "/anime";
  return loadCatalogData(`${path}?${query}`, catalogPage);
}

// ---------- API ----------
const api = {
  search: (q, lim = 24) => loadCatalogPage({ q, limit: lim, order_by: "popularity" }).then(d => d.data),
  top: (lim = 18) => loadCatalogData(`/top/anime?limit=${lim}&filter=bypopularity`),
  season: (lim = 18) => loadCatalogData(`/seasons/${year()}/${season()}?limit=${lim}&sfw=true`),
  details: id => loadCatalogData(`/anime/${id}/full`),
  characters: id => loadCatalogData(`/anime/${id}/characters`).catch(() => []),
  recommendations: id => loadCatalogData(`/anime/${id}/recommendations`).catch(() => []),
  byGenre: (g, l = 24) => loadCatalogPage({ genres: g, limit: l, order_by: "popularity" }).then(d => d.data),
  genres: () => loadCatalogData("/genres/anime")
    .then(data => (data || []).filter(g => !/Hentai|Erotica/i.test(g.name))),
  movies: (lim = 24, page = 1) => loadCatalogPage({ type: "movie", order_by: "popularity", limit: lim, page }).then(d => d.data),
  random: () => loadCatalogData(`/random/anime?sfw=true&_=${Date.now()}`),
  catalog: params => loadCatalogPage(params),
  seasonNow: params => loadCatalogPage(params, true),
  topPaged: (page = 1) =>
    loadCatalogData(`/top/anime?limit=24&filter=bypopularity&page=${page}`, catalogPage),

  trendingRich: async (n = 8) => {
    try {
      const j = await fetchJson(ANILIST(), {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          query: `query ($n:Int){ Page(perPage:$n){ media(type:ANIME, isAdult:false, sort:TRENDING_DESC, status_in:[RELEASING,FINISHED]){
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
  trending: (lim = 18) => loadCatalogPage({ status: "airing", order_by: "popularity", limit: lim }).then(d => d.data),

  shiki: async id => { try { return await fetchJson(`${BACKEND}/shiki/anime/${id}`, { timeout: 6000 }); } catch { return null; } },
  shikiBatch: async ids => {
    const list = ids.filter(Boolean).slice(0, 50);
    if (!list.length) return {};
    try { return await fetchJson(`${BACKEND}/shiki/batch?ids=${list.join(",")}`, { timeout: 6000 }); } catch { return {}; }
  },
  shikiSearch: async (q, lim = 24, page = 1) => {
    return fetchJson(
      `${BACKEND}/shiki/search?q=${encodeURIComponent(q)}&limit=${lim}&page=${page}`,
      { timeout: 8000 }
    );
  },
  anilistByMal: async id => {
    try {
      const j = await fetchJson(ANILIST(), {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({
          query: `query($id:Int){Media(idMal:$id,type:ANIME){idMal title{romaji english native} synonyms bannerImage coverImage{extraLarge large} description(asHtml:false) tags{name} averageScore episodes format seasonYear genres status duration studios{nodes{name}}}}`,
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
// Poster tile: EDDAS Tile (ed-tile) with the av-card hooks the enrichment and list code look for.
function posterTile({ href, mal, poster, title, meta = "", score = null, flag = "" }) {
  return `
    <a class="ed-tile av-card" href="${esc(href)}" data-mal="${esc(String(mal))}">
      <span class="ed-tile__media av-card-img">
        <i class="fas fa-film" aria-hidden="true"></i>
        <img src="${esc(poster)}" loading="lazy" alt="${esc(title)}"
             onerror="this.dataset.broken='1';this.removeAttribute('src');" />
        ${score ? `<span class="tile-score"><i class="fas fa-star" aria-hidden="true"></i>${esc(String(score))}</span>` : ""}
        ${flag ? `<span class="tile-flag">${esc(flag)}</span>` : ""}
      </span>
      <span class="ed-tile__title av-card-title" title="${esc(title)}">${esc(title)}</span>
      ${meta ? `<span class="ed-tile__meta av-card-meta">${esc(meta)}</span>` : ""}
    </a>`;
}

function makeCard(a) {
  const poster = a.images?.jpg?.large_image_url || a.images?.jpg?.image_url || a.cover || "";
  const title = a._ru || a.title_english || a.title || "Без названия";
  const statusRu = tr(STATUS_RU, a.status || "");
  const meta = [];
  if (a.type) meta.push(tr(TYPE_RU, a.type));
  if (a.episodes) meta.push(`${a.episodes} эп.`);
  if (a.year || a.aired?.prop?.from?.year) meta.push(a.year || a.aired.prop.from.year);
  const holder = document.createElement("div");
  holder.innerHTML = posterTile({
    href: `/anime/${a.mal_id}/`, mal: a.mal_id, poster: poster ? imgUrl(poster) : "", title,
    meta: meta.join(", "), score: a.score,
    flag: statusRu === "Онгоинг" || statusRu === "Анонс" ? statusRu : "",
  });
  const link = holder.firstElementChild;
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
        ? (shikiPath.startsWith("http") ? shikiPath : SHIKI_ORIGIN + shikiPath)
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
  el.removeAttribute("aria-busy");
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
  el.innerHTML = Array.from({ length: n }, () =>
    `<div class="ed-tile tile-skeleton" aria-hidden="true"><span class="ed-tile__media"></span><span class="skeleton-line"></span><span class="skeleton-line is-short"></span></div>`
  ).join("");
  el.setAttribute("aria-busy", "true");
}

// ---------- hero carousel ----------
let heroTimer = null;
async function renderHeroCarousel(container) {
  container.innerHTML = `
    <section class="ed-panel hero-carousel" id="avHero" role="region" aria-roledescription="карусель" aria-label="Аниме в тренде">
      <div class="hero-slides" id="avHeroSlides"></div>
      <div class="hero-footer" hidden>
        <a class="ed-btn ed-btn--primary hero-watch"><i class="fas fa-play" aria-hidden="true"></i>Смотреть</a>
        <div class="hero-ctrl">
          <span class="hero-counter" role="status" aria-live="off"><b>1</b> из <span class="hero-total"></span></span>
          <button type="button" class="ed-iconbtn ed-iconbtn--tonal hero-nav prev" aria-label="Предыдущее аниме" title="Предыдущее аниме"><i class="fas fa-chevron-left" aria-hidden="true"></i></button>
          <button type="button" class="ed-iconbtn ed-iconbtn--tonal hero-nav next" aria-label="Следующее аниме" title="Следующее аниме"><i class="fas fa-chevron-right" aria-hidden="true"></i></button>
        </div>
      </div>
    </section>`;

  const slides = await api.trendingRich(8).catch(() => []);
  if (!container.isConnected) return;
  if (!slides.length) { container.innerHTML = ""; return; }
  const hero = $("#avHero", container);
  const sw = $("#avHeroSlides", container);
  const watch = $(".hero-watch", container);
  const counter = $(".hero-counter", container);
  const rendered = new Map();
  slides.forEach((s, i) => {
    const el = document.createElement("div");
    el.className = "hero-slide" + (i === 0 ? " active" : "");
    el.setAttribute("role", "group");
    el.setAttribute("aria-roledescription", "слайд");
    el.innerHTML = `
      <div class="hero-bg"><img src="${esc(imgUrl(s.banner))}" alt="" loading="${i === 0 ? "eager" : "lazy"}" fetchpriority="${i === 0 ? "high" : "low"}" decoding="async" /></div>
      <div class="hero-content">
        <span class="ed-status ed-status--brand hero-badge">${i + 1}-е место в тренде</span>
        <h2 class="hero-title" title="${esc(s.title)}">${esc(s.title)}</h2>
        <div class="title-stats hero-stats">
          ${s.score ? `<span class="title-score"><i class="fas fa-star" aria-hidden="true"></i>${s.score}</span>` : ""}
          ${s.episodes ? `<span>${s.episodes} эп.</span>` : ""}
          ${s.type ? `<span>${tr(TYPE_RU, s.type)}</span>` : ""}
          ${s.year ? `<span>${s.year}</span>` : ""}
        </div>
        ${s.genres?.length ? `<p class="hero-genres">${esc(s.genres.map(trGenre).join(", "))}</p>` : ""}
        <p class="hero-desc">${esc(s.description).slice(0, 280)}</p>
      </div>`;
    sw.appendChild(el);
    rendered.set(s.mal_id, { el });
  });

  let idx = 0;
  const go = n => {
    idx = (n + slides.length) % slides.length;
    $$(".hero-slide", container).forEach((el, i) => {
      el.classList.toggle("active", i === idx);
      el.inert = i !== idx;
      el.setAttribute("aria-hidden", String(i !== idx));
      el.setAttribute("aria-label", `${i + 1} из ${slides.length}`);
    });
    $("b", counter).textContent = String(idx + 1);
    $(".hero-total", counter).textContent = String(slides.length);
    counter.setAttribute("aria-label", `Аниме ${idx + 1} из ${slides.length}`);
    watch.href = `/anime/${slides[idx].mal_id}/`;
    watch.setAttribute("aria-label", `Смотреть: ${slides[idx].title}`);
    $(".hero-ctrl", container).hidden = slides.length < 2;
  };
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  let hovering = false;
  let touch = null;
  const restartTimer = () => {
    clearInterval(heroTimer);
    if (slides.length < 2 || reducedMotion.matches) return;
    heroTimer = setInterval(() => {
      if (!container.isConnected) { clearInterval(heroTimer); return; }
      if (!document.hidden && !hovering && !touch && !hero.contains(document.activeElement)) go(idx + 1);
    }, 6500);
  };
  function goTo(n) { go(n); restartTimer(); }
  $(".hero-nav.prev", container).addEventListener("click", () => goTo(idx - 1));
  $(".hero-nav.next", container).addEventListener("click", () => goTo(idx + 1));
  hero.addEventListener("pointerenter", e => { if (e.pointerType === "mouse") hovering = true; });
  hero.addEventListener("pointerleave", e => { if (e.pointerType === "mouse") hovering = false; });
  hero.addEventListener("keydown", e => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    goTo(idx + (e.key === "ArrowRight" ? 1 : -1));
  });
  hero.addEventListener("pointerdown", e => {
    if (e.pointerType !== "touch" || !e.isPrimary || e.target.closest("a, button")) return;
    touch = { id: e.pointerId, x: e.clientX, y: e.clientY };
    hero.setPointerCapture(e.pointerId);
  });
  hero.addEventListener("pointerup", e => {
    if (!touch || touch.id !== e.pointerId) return;
    const dx = e.clientX - touch.x;
    const dy = e.clientY - touch.y;
    touch = null;
    if (Math.abs(dx) >= 48 && Math.abs(dx) > Math.abs(dy) * 1.5) goTo(idx + (dx < 0 ? 1 : -1));
  });
  hero.addEventListener("pointercancel", () => { touch = null; });
  hero.addEventListener("lostpointercapture", () => { touch = null; });
  go(0);
  restartTimer();
  $(".hero-footer", container).hidden = false;

  // Enrichment must not hold back the first slide or its image request.
  const applyTitles = (map) => {
    if (!container.isConnected) return;
    const activeSlide = slides[idx];
    slides.forEach(s => {
      const ru = map?.[s.mal_id]?.russian;
      if (ru) {
        s.title = ru;
        const title = $(".hero-title", rendered.get(s.mal_id).el);
        title.textContent = ru;
        title.title = ru;
      }
    });
    const seen = new Set();
    for (let i = slides.length - 1; i >= 0; i--) {
      const s = slides[i];
      const key = normTitle(s.title);
      if (seen.has(key)) {
        const { el } = rendered.get(s.mal_id);
        el.remove();
        rendered.delete(s.mal_id);
        slides.splice(i, 1);
      } else seen.add(key);
    }
    slides.forEach((s, i) => {
      const { el } = rendered.get(s.mal_id);
      $(".hero-badge", el).textContent = `${i + 1}-е место в тренде`;
    });
    const activeIndex = slides.indexOf(activeSlide);
    go(activeIndex >= 0 ? activeIndex : Math.min(idx, slides.length - 1));
    restartTimer();
  };
  async function enrichSlides() {
    const map = state.backendAvailable ? await api.shikiBatch(slides.map(s => s.mal_id)) : {};
    if (!container.isConnected) return;
    applyTitles(map);
    slides.forEach(async s => {
      if (!s.description || hasCyrillic(s.description)) return;
      let ru = "";
      try {
        const shiki = await api.shiki(s.mal_id);
        const cleaned = cleanBBCode(shiki?.description || "");
        if (cleaned && hasCyrillic(cleaned)) ru = cleaned;
      } catch {}
      if (!ru) ru = await translateToRu(s.description);
      if (!ru || !container.isConnected) return;
      const slideEl = rendered.get(s.mal_id)?.el;
      const p = slideEl?.querySelector(".hero-desc");
      if (p) p.textContent = ru.slice(0, 280);
    });
  }
  enrichSlides();
}

// ---------- views: catalog family ----------
function setActiveNav(view) {
  setActiveSection(view);
}

function homeSection(id, title, href, linkLabel) {
  return `
    <section class="ed-panel home-section" aria-labelledby="${id}Title">
      <div class="ed-panel__head">
        <h2 class="section-head__title" id="${id}Title">${esc(title)}</h2>
        <a class="section-head__link" href="${href}">${esc(linkLabel)}</a>
      </div>
      <div class="ed-panel__body">
        <div class="ed-grid av-grid home-grid" id="${id}"></div>
      </div>
    </section>`;
}

async function viewHome() {
  setActiveNav("home");
  clearInterval(heroTimer);
  $("#outlet").innerHTML = `
    <div class="page-stack home">
      <h1 class="visually-hidden">AsoPlay — аниме онлайн</h1>
      <div id="avCarousel"></div>
      ${homeSection("topGrid", "Топ по популярности", "/top", "Весь топ")}
      ${homeSection("seasonGrid", "Текущий сезон", "/season", "Весь сезон")}
      ${homeSection("trendGrid", "Сейчас смотрят", "/trending", "Все онгоинги")}
    </div>`;
  renderHeroCarousel($("#avCarousel"));
  ["topGrid", "seasonGrid", "trendGrid"].forEach(id => renderSkeletons($(`#${id}`), 12));
  const sections = [
    [$("#topGrid"), () => api.top(18)],
    [$("#seasonGrid"), () => api.season(18)],
    [$("#trendGrid"), () => api.trending(18)],
  ];
  await Promise.all(sections.map(async ([container, load]) => {
    try {
      const data = await load();
      if (container.isConnected) renderGrid(container, data, 12);
    } catch {
      if (container.isConnected) {
        container.removeAttribute("aria-busy");
        container.innerHTML = errorPage({
          title: "Подборка временно недоступна",
          message: "Источник каталога не ответил. Обновите страницу чуть позже.",
          variant: "sad",
        });
      }
    }
  }));
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
  trending: "Сейчас смотрят",
  movies: "Фильмы",
  search: "Каталог",
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
    ? (raw.startsWith("http") ? raw : SHIKI_ORIGIN + raw)
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
    status: { released: "Finished Airing", ongoing: "Currently Airing", anons: "Not yet aired" }[a.status] || "",
    duration: a.duration ? `${a.duration} min per ep` : "",
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
  const title = kind === "season" ? `Сезон: ${tr(SEASON_RU, season()).toLowerCase()} ${year()}`
    : isSearch && q ? `Поиск: «${q}»` : CATALOG_TITLES[kind];
  const typeFixed = kind === "movies";
  const option = (value, label, current) => `<option value="${value}" ${current === value ? "selected" : ""}>${label}</option>`;

  $("#outlet").innerHTML = `
    <div class="page-stack catalog">
      <div class="page-head">
        <h1 class="page-head__title" title="${esc(title)}">${esc(title)}</h1>
        <div class="page-head__actions">
          <button type="button" class="ed-btn ed-btn--secondary" id="filterToggle" aria-expanded="false" aria-controls="catalogFilter">
            <i class="fas fa-sliders" aria-hidden="true"></i>Фильтры<span class="ed-badge" id="filterCount" hidden></span>
          </button>
        </div>
      </div>
      ${isSearch ? `
        <form class="catalog-search" id="searchForm" role="search">
          <div class="ed-search">
            <i class="ed-search__icon fas fa-search" aria-hidden="true"></i>
            <input class="ed-search__input" id="sfQ" type="search" placeholder="Название аниме" value="${esc(q)}" autocomplete="off" aria-label="Название аниме" enterkeyhint="search" />
          </div>
          <button type="submit" class="ed-btn ed-btn--secondary">Найти</button>
        </form>` : ""}
      <section class="ed-panel catalog-filter" id="catalogFilter" aria-label="Фильтры" hidden>
        <div class="ed-panel__body ed-form">
          <div class="cf-grid">
            ${!typeFixed ? `
            <div class="ed-field">
              <label class="ed-field__label" for="cfType">Тип</label>
              <select class="ed-select" id="cfType">
                ${option("", "Любой", type)}${option("tv", "Сериал", type)}${option("movie", "Фильм", type)}
                ${option("ova", "OVA", type)}${option("ona", "ONA", type)}${option("special", "Спешл", type)}${option("music", "Клип", type)}
              </select>
            </div>` : ""}
            <div class="ed-field">
              <label class="ed-field__label" for="cfStatus">Статус</label>
              <select class="ed-select" id="cfStatus">
                ${option("", "Любой", status)}${option("airing", "Онгоинг", status)}${option("complete", "Вышло", status)}${option("upcoming", "Анонс", status)}
              </select>
            </div>
            <div class="ed-field">
              <span class="ed-field__label" id="cfYearLabel">Годы выхода</span>
              <div class="cf-year" role="group" aria-labelledby="cfYearLabel">
                <input class="ed-input" type="number" inputmode="numeric" id="cfFrom" placeholder="С" aria-label="С года" min="1960" max="${year() + 1}" value="${esc(fromYear)}" />
                <input class="ed-input" type="number" inputmode="numeric" id="cfTo" placeholder="По" aria-label="По год" min="1960" max="${year() + 1}" value="${esc(toYear)}" />
              </div>
            </div>
            <div class="ed-field">
              <label class="ed-field__label" for="cfOrder">Сортировка</label>
              <select class="ed-select" id="cfOrder">
                ${option("", "По умолчанию", order)}${option("popularity", "По популярности", order)}${option("score", "По рейтингу", order)}
                ${option("start_date", "По дате выхода", order)}${option("title", "По названию", order)}${option("episodes", "По количеству серий", order)}
              </select>
            </div>
          </div>
          <label class="ed-check"><input type="checkbox" id="cfSortAsc" ${sort === "asc" ? "checked" : ""} /><span class="ed-check__text">По возрастанию</span></label>
          <div class="ed-field">
            <span class="ed-field__label" id="cfGenresLabel">Жанры</span>
            <div class="cf-genres" id="cfGenres" role="group" aria-labelledby="cfGenresLabel">${loadingBlock("Загружаем жанры…")}</div>
          </div>
          <label class="ed-check"><input type="checkbox" id="cfGenresAll" ${genresAll ? "checked" : ""} /><span class="ed-check__text">Только аниме со всеми выбранными жанрами</span></label>
          <div class="ed-form__actions ed-form__actions--start">
            <button type="button" class="ed-btn ed-btn--primary" id="cfApply">Применить</button>
            <button type="button" class="ed-btn ed-btn--ghost" id="cfReset">Сбросить</button>
          </div>
        </div>
      </section>
      <section class="ed-panel catalog-results" aria-label="Результаты">
        <div class="ed-panel__body">
          <div class="ed-grid av-grid catalog-grid" id="g"></div>
          <nav class="pager" id="pager" aria-label="Страницы каталога"></nav>
        </div>
      </section>
    </div>`;

  // The query lives in the search field, so the badge counts only what the panel holds.
  const activeCount = [type, status, fromYear, toYear, order, sort, genres.length ? "y" : ""].filter(Boolean).length;
  if (activeCount) {
    $("#filterCount").textContent = String(activeCount);
    $("#filterCount").hidden = false;
  }

  const genreBox = $("#cfGenres");
  getGenres().then(list => {
    const box = genreBox;
    if (!box.isConnected) return;
    box.innerHTML = list.length ? "" : `<span class="cf-note">Список жанров не загрузился. Остальные фильтры работают.</span>`;
    list.forEach(g => {
      const chip = document.createElement("button");
      const active = genres.includes(String(g.mal_id));
      chip.type = "button";
      chip.className = "ed-chip cf-chip" + (active ? " is-active" : "");
      chip.setAttribute("aria-pressed", String(active));
      chip.dataset.id = g.mal_id;
      chip.textContent = trGenre(g.name);
      chip.addEventListener("click", () => {
        const on = chip.classList.toggle("is-active");
        chip.setAttribute("aria-pressed", String(on));
      });
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
    const active = [...$$("#cfGenres .cf-chip.is-active")].map(b => b.dataset.id).join(",");
    if (active) params.set("genres", active);
    if ($("#cfGenresAll")?.checked && active.includes(",")) params.set("genres_all", "1");
    const qs = params.toString();
    nav(qs ? `/${catalogPathFor(kind)}?${qs}` : `/${catalogPathFor(kind)}`);
  };

  const setFilterOpen = (open) => {
    $("#catalogFilter").hidden = !open;
    $("#filterToggle").setAttribute("aria-expanded", String(open));
  };
  $("#filterToggle").addEventListener("click", () => setFilterOpen($("#catalogFilter").hidden));
  if (activeCount) setFilterOpen(true);
  $("#cfApply").addEventListener("click", applyFilters);
  $("#cfReset").addEventListener("click", () => nav(`/${catalogPathFor(kind)}`));
  $("#searchForm")?.addEventListener("submit", e => { e.preventDefault(); applyFilters(); });

  const g = $("#g");
  const pager = $("#pager");
  const russianSearch = isSearch && q && hasCyrillic(q);
  renderSkeletons(g, 24);
  try {
    let data, pagination;
    const seasonSimple = kind === "season" && !genres.length && !fromYear && !toYear && !q && !status && !order && !sort;
    // Поиск на русском — MAL/Jikan не индексирует русские названия, поэтому при
    // кириллическом запросе мы идём через Shikimori и нормализуем его ответ
    // в Jikan-формат, чтобы остальной рендер не изменился.

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
      pagination = raw?.pagination || {
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
        const results = await Promise.all(genres.map(gid => api.catalog({ ...baseParams, genres: gid })));
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
    if (!g.isConnected) return;
    renderGrid(g, data, 24, kind);
    renderPager(pager, kind, queryStr || "", page, pagination);
  } catch (e) { renderCatalogFailure(g, e, russianSearch); }
}

function catalogPathFor(kind) { return kind === "search" ? "catalog" : kind; }

function renderPager(el, kind, queryStr, page, pag) {
  el.innerHTML = "";
  const last = pag?.last_visible_page || (pag?.has_next_page ? page + 1 : page);
  if (last <= 1) return;
  const frag = document.createDocumentFragment();
  const mkBtn = (label, targetPage, opts = {}) => {
    const b = document.createElement(opts.disabled ? "span" : "a");
    b.className = "ed-btn ed-btn--sm pager-btn " + (opts.current ? "ed-btn--secondary" : "ed-btn--ghost")
      + (opts.disabled ? " is-disabled" : "");
    if (opts.icon) {
      b.innerHTML = `<i class="fas ${opts.icon}" aria-hidden="true"></i>`;
      b.setAttribute("aria-label", label);
      b.title = label;
    } else b.textContent = label;
    if (opts.current) b.setAttribute("aria-current", "page");
    if (opts.disabled) b.setAttribute("aria-disabled", "true");
    else {
      const p = new URLSearchParams(queryStr || "");
      if (targetPage <= 1) p.delete("page");
      else p.set("page", targetPage);
      b.href = `/${catalogPathFor(kind)}${p.toString() ? "?" + p.toString() : ""}`;
    }
    return b;
  };
  frag.appendChild(mkBtn("Предыдущая страница", page - 1, { disabled: page <= 1, icon: "fa-chevron-left" }));
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
  frag.appendChild(mkBtn("Следующая страница", page + 1, { disabled: page >= last, icon: "fa-chevron-right" }));
  el.appendChild(frag);
}

async function viewRandom() {
  const route = routeVersion;
  setActiveNav("random");
  const outlet = $("#outlet");
  outlet.innerHTML = `
    <div class="ed-empty random-loader" role="status">
      <div class="ed-empty__icon"><span class="ed-spinner" aria-hidden="true"></span></div>
      <h1 class="ed-empty__title">Подбираем случайное аниме</h1>
      <p class="ed-empty__text" id="avRandStatus">Выбираем тайтл из каталога…</p>
      <p class="random-progress" id="avRandProgress"></p>
    </div>`;
  const setStatus = (txt) => { const el = $("#avRandStatus"); if (el) el.textContent = txt; };
  const setProgress = (i, max) => { const el = $("#avRandProgress"); if (el) el.textContent = max ? `Попытка ${i} из ${max}` : ""; };

  for (let i = 0; i < 2; i++) {
    if (route !== routeVersion) return;
    setProgress(i + 1, 2);
    if (i) setStatus("Ищем в каталоге MyAnimeList…");
    try {
      const r = i ? await api.random() : await fetchJson(`${BACKEND}/random/watchable?_=${Date.now()}`, { timeout: 9000 });
      if (route !== routeVersion) return;
      if (r?.mal_id) {
        setStatus(`Открываем «${r.title || "MAL #" + r.mal_id}»`);
        nav(`/anime/${r.mal_id}/`);
        return;
      }
    } catch { /* retry */ }
  }
  if (route !== routeVersion) return;
  outlet.innerHTML = errorPage({
    title: "Каталог временно недоступен",
    message: "Не удалось получить случайное аниме из доступных источников. Попробуйте ещё раз чуть позже.",
    action: { href: "/random", label: "Попробовать ещё раз" },
    secondary: { href: "/", label: "На главную" },
  });
}

async function viewGenre(id, name) {
  setActiveNav(null);
  $("#outlet").innerHTML = `
    <div class="page-stack">
      <div class="page-head"><h1 class="page-head__title">Жанр: ${esc(trGenre(name))}</h1></div>
      <section class="ed-panel catalog-results"><div class="ed-panel__body"><div class="ed-grid av-grid catalog-grid" id="g"></div></div></section>
    </div>`;
  const g = $("#g"); renderSkeletons(g, 18);
  try {
    const data = await api.byGenre(id, 24);
    if (g.isConnected) renderGrid(g, data);
  } catch (e) { renderCatalogFailure(g, e); }
}

// ---------- anime detail ----------
function cleanBBCode(s) {
  return (s || "")
    .replace(/\[\/?(b|i|u|s|url[^\]]*|character[^\]]*|anime[^\]]*|manga[^\]]*|ranobe[^\]]*|person[^\]]*|studio[^\]]*|spoiler[^\]]*)\]/gi, "")
    // Shikimori wiki links: [[Синигами]] or [[Синигами|синигами]] read as plain words.
    .replace(/\[\[([^\]|]+)\|([^\]]+)\]\]/g, "$2")
    .replace(/\[\[([^\]]+)\]\]/g, "$1")
    .replace(/\[br\]/gi, "\n").trim();
}

async function viewAnime(malId) {
  const route = routeVersion;
  setActiveNav(null);
  $("#outlet").innerHTML = loadingBlock("Загружаем аниме…");

  const preload = _consumeSSRPreload(malId);
  const jikanP = api.details(malId);
  const shikiP = api.shiki(malId).catch(() => null);
  const anilistP = api.anilistByMal(malId).catch(() => null);
  const charsP = api.characters(malId).catch(() => []);
  const recsP = api.recommendations(malId).catch(() => []);

  let a;
  try { a = await jikanP; }
  catch (e) {
    if (route !== routeVersion) return;
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
      if (route !== routeVersion) return;
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
          ? "Не удалось получить данные MyAnimeList. Попробуйте повторить запрос чуть позже."
          : "Не удалось получить данные об аниме. Проверьте соединение и повторите попытку.",
        action: { href: `/anime/${malId}/`, label: "Повторить" },
        secondary: { href: "/", label: "На главную" },
      });
    }
    return;
  }

  if (route !== routeVersion) return;
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
    .then(al => { if (al && !player.destroyed) applyAnilist(al); })
    .finally(() => setTimeout(() => { if (!player.destroyed) ensureRussianDescription(); }, 400));
  charsP.then(chars => { if (!player.destroyed) renderCharacters(chars); });
  recsP.then(recs => { if (!player.destroyed) renderRecommendations(recs); });

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
  const trailerId = getTrailerId(a.trailer);
  const trailerButton = trailerId ? `<button class="ed-btn ed-btn--secondary cinema-trailer-action" type="button" data-open-trailer aria-haspopup="dialog"><i class="fab fa-youtube" aria-hidden="true"></i>Трейлер</button>` : "";
  const duration = a.duration ? a.duration.replace("per ep", "/эп.").replace("min", "мин").replace(/\s*\/\s*/, " / ").replace(/\.$/, "") : "";
  const externalLink = (href, label) => `<a href="${esc(href)}" target="_blank" rel="noopener" class="ed-btn ed-btn--ghost ed-btn--sm">${label}<i class="fas fa-arrow-up-right-from-square" aria-hidden="true"></i></a>`;
  const seasons = (a.relations || []).filter(r =>
    /Sequel|Prequel|Side story|Parent story|Alternative version|Summary/i.test(r.relation)
  ).flatMap(r => r.entry.filter(e => e.type === "anime").map(e => ({ ...e, relation: r.relation })));

  const statusRu = tr(STATUS_RU, a.status || "");
  const typeRu = tr(TYPE_RU, a.type || "");
  const statusTone = statusRu === "Онгоинг" ? "ed-status--brand" : statusRu === "Вышло" ? "ed-status--success" : statusRu === "Анонс" ? "ed-status--warning" : "";
  const statusChip = statusRu ? `<span class="ed-status ${statusTone}">${esc(statusRu)}</span>` : "";
  const social = state.social || {};

  $("#outlet").innerHTML = `
    <div class="title-page">
    <section class="ed-panel title-hero${trailerId ? " has-trailer" : ""}" id="avHero" aria-labelledby="avHeroTitle">
      <div class="title-cover" id="avHeroCover" hidden><img alt="" id="avHeroCoverImg" decoding="async" /></div>
      <div class="title-hero__body">
        <button type="button" class="title-poster cinema-poster" aria-label="Открыть постер">
          <i class="fas fa-film" aria-hidden="true"></i>
          <img src="${imgUrl(poster)}" alt="" onerror="this.remove()" />
        </button>
        <div class="title-head">
          <div class="title-tags">
            ${a.type ? `<span>${esc(typeRu)}</span>` : ""}
            ${a.year ? `<span>${a.year}</span>` : ""}
            ${statusChip}
          </div>
          <h1 class="title-name" id="avHeroTitle">${esc(titleMain)}</h1>
          <p class="title-original" id="avHeroSub">${esc([titleOrig, a.title_japanese].filter(Boolean).join(", "))}</p>
        </div>
        <div class="title-meta">
          <div class="title-stats">
            ${a.score ? `<span class="title-score"><i class="fas fa-star" aria-hidden="true"></i>${a.score}</span>` : ""}
            ${a.episodes ? `<span>${a.episodes} эп.</span>` : ""}
            ${duration ? `<span>${esc(duration)}</span>` : ""}
            ${genres ? `<span class="title-stats__genres">${genres}</span>` : ""}
          </div>
          <p class="title-desc" id="avHeroDesc">${esc(shortDesc)}</p>
        </div>
          <div class="title-actions">
            <button class="ed-btn ed-btn--primary" id="avCtaPlay" type="button"><i class="fas fa-play" aria-hidden="true"></i>Смотреть</button>
            ${trailerButton}
            <div class="list-picker" id="avListPicker">
              <button class="ed-btn ed-btn--secondary list-picker-trigger" id="avListPickerBtn" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="avListPickerPop">
                <i class="fas fa-list" aria-hidden="true"></i><span class="list-picker-label">В список</span><span class="ed-badge ed-badge--muted list-picker-auto" title="Статус выставлен автоматически" hidden>авто</span><i class="fas fa-chevron-down list-picker-chev" aria-hidden="true"></i>
              </button>
              <div class="ed-menu list-picker-pop" id="avListPickerPop" role="menu" hidden>
                <button type="button" class="ed-menu__item lp-option" data-status="watching" role="menuitemradio" aria-checked="false"><i class="fas fa-eye" aria-hidden="true"></i>Смотрю</button>
                <button type="button" class="ed-menu__item lp-option" data-status="planned" role="menuitemradio" aria-checked="false"><i class="fas fa-bookmark" aria-hidden="true"></i>В планах</button>
                <button type="button" class="ed-menu__item lp-option" data-status="completed" role="menuitemradio" aria-checked="false"><i class="fas fa-circle-check" aria-hidden="true"></i>Просмотрено</button>
                <button type="button" class="ed-menu__item lp-option" data-status="postponed" role="menuitemradio" aria-checked="false"><i class="fas fa-pause" aria-hidden="true"></i>Отложено</button>
                <button type="button" class="ed-menu__item lp-option" data-status="dropped" role="menuitemradio" aria-checked="false"><i class="fas fa-ban" aria-hidden="true"></i>Брошено</button>
                <div class="ed-menu__divider lp-clear-sep" role="separator"></div>
                <button type="button" class="ed-menu__item is-danger lp-option lp-clear" data-status="" role="menuitem"><i class="fas fa-xmark" aria-hidden="true"></i>Убрать из списков</button>
              </div>
            </div>
            <button class="ed-btn ed-btn--secondary" id="avFavBtn" type="button" aria-pressed="false">
              <i class="far fa-star fav-icon" aria-hidden="true"></i><span class="fav-label">В избранное</span>
            </button>
          </div>
          <div class="title-links">
            ${a.url ? externalLink(a.url, "MyAnimeList") : ""}
            ${externalLink(`${SHIKI_ORIGIN}/animes?search=${encodeURIComponent(titleMain)}`, "Shikimori")}
            ${externalLink(`https://anilist.co/search/anime?search=${encodeURIComponent(titleMain)}`, "AniList")}
          </div>
        ${trailerId ? `
        <div class="cinema-trailer">
          <button class="ed-vcard__thumb cinema-trailer-preview" type="button" data-open-trailer aria-label="Смотреть трейлер" aria-haspopup="dialog">
            <img src="https://i.ytimg.com/vi/${trailerId}/hqdefault.jpg" alt="" width="480" height="360" decoding="async" />
            <span class="ed-vcard__play"></span>
          </button>
          <div class="ed-vcard__meta">Трейлер на YouTube</div>
        </div>` : ""}
      </div>
    </section>

    <div class="ed-note av-support" role="note">
      <i class="fas fa-handshake" aria-hidden="true"></i>
      <div>AsoPlay работает при поддержке <a href="${esc(social.site_url || "https://animesocial.online")}" target="_blank" rel="noopener">${esc(social.site_name || "AnimeSocial")}</a> — первой социальной сети для анимешников.</div>
    </div>

    <div class="title-layout detail-grid">
      <div class="watch-main">
        ${trailerId ? `<div class="trailer-compact-action">${trailerButton}</div>` : ""}
        <nav class="seasons-bar" id="avSeasonsBar" aria-label="Сезоны и связанные тайтлы" hidden></nav>

        <section class="ed-panel sp-player" id="avPlayerCard" aria-label="Плеер">
          <div class="sp-head">
            <div class="sp-ep" id="avEpInfo"></div>
            <button class="ed-btn ed-btn--secondary ed-btn--sm sp-dub-trigger" id="avDubTrigger" type="button" aria-haspopup="dialog" aria-expanded="false" aria-controls="avDubPop">
              <i class="fas fa-microphone" aria-hidden="true"></i><span class="sp-dub-label">Озвучка</span><i class="fas fa-chevron-down sp-dub-chev" aria-hidden="true"></i>
            </button>
          </div>

          <div class="sp-video">
            <div id="artplayer"></div>
            <div class="player-overlay" id="avPlayerOverlay">
              <span class="ed-spinner spinner" aria-hidden="true"></span>
              <div id="avPlayerStatus">Ищем источники…</div>
            </div>
          </div>

          <div class="sp-foot">
            <div class="sp-prev-next">
              <button class="ed-btn ed-btn--ghost ed-btn--sm sp-nav" id="avPrevEp" type="button"><i class="fas fa-backward-step" aria-hidden="true"></i><span>Предыдущая</span></button>
              <button class="ed-btn ed-btn--ghost ed-btn--sm sp-nav" id="avNextEp" type="button"><span>Следующая</span><i class="fas fa-forward-step" aria-hidden="true"></i></button>
            </div>
            <label class="ed-switch sp-auto"><span>Автопереключение серий</span><input type="checkbox" role="switch" id="avAutoNext" /></label>
          </div>

          <div class="sp-dub-pop" id="avDubPop" hidden>
            <div class="sp-dub-scrim" data-dub-close></div>
            <div class="ed-menu sp-dub-panel" role="dialog" aria-label="Озвучка и субтитры">
              <div class="app-sheet__grip sp-dub-grip"></div>
              <div class="sp-dub-pop-inner" id="avDubTabs"></div>
            </div>
          </div>
        </section>
      </div>

      <aside class="ep-sidebar">
        <section class="ed-panel ep-panel" aria-labelledby="avEpTitle">
          <div class="ed-panel__head"><span id="avEpTitle">Эпизоды</span><span class="ep-count" id="avEpCount"></span></div>
          <div class="ed-panel__body">
            <div class="ed-search ep-search-wrap" hidden>
              <i class="ed-search__icon fas fa-search" aria-hidden="true"></i>
              <input type="search" class="ed-search__input ep-search" id="avEpSearch" placeholder="Номер или название" aria-label="Найти серию" />
            </div>
            <div class="episodes-list" id="avEpisodesList">${loadingBlock("Ищем источник…")}</div>
          </div>
        </section>
      </aside>

      <div class="detail-main">
        <section class="ed-panel" aria-labelledby="avAboutTitle">
          <div class="ed-panel__head"><span id="avAboutTitle">Описание</span></div>
          <div class="ed-panel__body info-body">
            <div class="prose synopsis" id="avSynopsis">${synopsis ? esc(synopsis) : `<span class="muted">Описания пока нет.</span>`}</div>
            <dl class="ed-kv content-main-info">
              ${a.status ? `<dt>Статус</dt><dd>${statusChip}</dd>` : ""}
              ${a.type ? `<dt>Тип</dt><dd>${esc(typeRu)}</dd>` : ""}
              ${a.year ? `<dt>Год</dt><dd>${a.year}</dd>` : ""}
              ${a.episodes ? `<dt>Серий</dt><dd>${a.episodes}</dd>` : ""}
              ${duration ? `<dt>Длительность</dt><dd>${esc(duration)}</dd>` : ""}
              ${a.rating ? `<dt>Возрастной рейтинг</dt><dd>${esc(a.rating)}</dd>` : ""}
              ${a.studios?.length ? `<dt>Студии</dt><dd>${a.studios.map(s => esc(s.name)).join(", ")}</dd>` : ""}
              ${genres ? `<dt>Жанры</dt><dd>${genres}</dd>` : ""}
              ${a.score ? `<dt>Оценка MyAnimeList</dt><dd><span class="title-score"><i class="fas fa-star" aria-hidden="true"></i>${a.score}</span> <span class="muted">${a.scored_by ? `${Number(a.scored_by).toLocaleString("ru-RU")} голосов` : ""}</span></dd>` : ""}
              <dt>Ваша оценка</dt><dd><span class="rate-block" id="avRate"></span></dd>
            </dl>
          </div>
        </section>

        <section class="ed-panel" aria-labelledby="avCharsTitle">
          <div class="ed-panel__head"><span id="avCharsTitle">Персонажи</span></div>
          <div class="ed-panel__body">
            <div class="chars-grid" id="avChars">
              ${Array.from({ length: 6 }).map(() => `<div class="char char-skeleton" aria-hidden="true"><span class="char-img"></span><span class="char-info"><span class="skeleton-line"></span><span class="skeleton-line is-short"></span></span></div>`).join("")}
            </div>
          </div>
        </section>

        <section class="ed-panel recs-panel" aria-labelledby="avRecsTitle">
          <div class="ed-panel__head"><span id="avRecsTitle">Похожее</span></div>
          <div class="ed-panel__body">
            <div class="ed-grid av-grid recs-grid" id="avRecs">${loadingBlock()}</div>
          </div>
        </section>
      </div>
    </div>
    </div>`;

  fitHeroTitle($("#avHeroTitle"));
  cleanup.push(bindTrailer($("#outlet"), trailerId, () => $("#avHeroTitle")?.textContent || titleMain));

  $("#avCtaPlay")?.addEventListener("click", () => $("#avPlayerCard").scrollIntoView({ behavior: "smooth", block: "start" }));

  const pstr = $(".cinema-poster");
  if (pstr) pstr.addEventListener("click", () => lightbox.open(poster));

  const malId = a.mal_id;
  const pickerLabel = { watching: "Смотрю", planned: "В планах", completed: "Просмотрено", postponed: "Отложено", dropped: "Брошено" };

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
    const icon = favBtn.querySelector(".fav-icon");
    if (icon) icon.className = `${isFav ? "fas" : "far"} fa-star fav-icon`;
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
    const autoBadge = $(".list-picker-auto", picker);
    if (autoBadge) autoBadge.hidden = !(status && auto);
    [...pickerPop.querySelectorAll(".lp-option")].forEach(el => {
      const on = el.dataset.status === (status || "");
      el.classList.toggle("is-active", on);
      if (el.getAttribute("role") === "menuitemradio") el.setAttribute("aria-checked", String(on));
    });
    // "Убрать из списков" and its divider only make sense for a title that is in a list.
    pickerPop.querySelector(".lp-clear").hidden = !status;
    pickerPop.querySelector(".lp-clear-sep").hidden = !status;
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
    // Under the button, or above it when the bottom of the screen has no room; never past the right edge.
    const position = () => {
      const r = pickerBtn.getBoundingClientRect();
      const gap = 6, edge = 8;
      pickerPop.style.position = "fixed";
      pickerPop.style.minWidth = `${Math.max(r.width, 220)}px`;
      const w = pickerPop.offsetWidth, h = pickerPop.offsetHeight;
      const fitsBelow = r.bottom + gap + h <= window.innerHeight - edge;
      const fitsAbove = r.top - gap - h >= edge;
      pickerPop.style.top = `${fitsBelow || !fitsAbove ? r.bottom + gap : r.top - gap - h}px`;
      pickerPop.style.left = `${Math.max(edge, Math.min(r.left, window.innerWidth - w - edge))}px`;
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
  box.setAttribute("role", "group");
  box.setAttribute("aria-label", "Ваша оценка");
  for (let i = 1; i <= 10; i++) {
    const s = document.createElement("button");
    s.type = "button";
    s.className = "rate-star" + (i <= current ? " filled" : "");
    s.innerHTML = `<i class="fas fa-star" aria-hidden="true"></i>`;
    s.title = `Оценить на ${i}`;
    s.setAttribute("aria-label", `Оценить на ${i} из 10`);
    s.setAttribute("aria-pressed", String(i === current));
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
  lbl.textContent = current ? `${current} из 10` : (state.user ? "Не оценено" : "Войдите, чтобы оценить");
  box.appendChild(lbl);
}

function renderSeasonsBar(currentAnime, relations) {
  const bar = $("#avSeasonsBar");
  if (!bar) return;
  if (!relations?.length) { bar.hidden = true; return; }
  // MAL writes relation types in either case ("Side story" and "Side Story"), so keys are lower case.
  const LABELS = {
    "side story": "Побочная история", "summary": "Пересказ",
    "alternative version": "Альтернативная версия", "parent story": "Основная история",
    "alternative setting": "Другой сеттинг", "full story": "Полная версия",
    "spin-off": "Спин-офф", "character": "Те же персонажи", "other": "Другое",
  };
  const relationLabel = (relation) => LABELS[String(relation || "").toLowerCase()] || relation;
  const preqs = relations.filter(r => /^prequel$/i.test(r.relation));
  const seqs = relations.filter(r => /^sequel$/i.test(r.relation));
  const others = relations.filter(r => !/^(prequel|sequel)$/i.test(r.relation));
  const chain = [
    ...preqs.map(r => ({ mal_id: r.mal_id, name: r.name })),
    { mal_id: currentAnime.mal_id, name: currentAnime.title_english || currentAnime.title, current: true },
    ...seqs.map(r => ({ mal_id: r.mal_id, name: r.name })),
  ];
  bar.hidden = false;
  bar.innerHTML = "";
  chain.forEach((it, i) => {
    const el = document.createElement(it.current ? "span" : "a");
    el.className = "season-tab" + (it.current ? " is-active" : "");
    if (it.current) el.setAttribute("aria-current", "page");
    else el.href = `/anime/${it.mal_id}/`;
    el.innerHTML = `<span class="season-lbl">${i + 1}-й сезон</span><span class="season-name">${esc(it.name)}</span>`;
    bar.appendChild(el);
  });
  others.forEach(r => {
    const el = document.createElement("a");
    el.className = "season-tab";
    el.href = `/anime/${r.mal_id}/`;
    el.innerHTML = `<span class="season-lbl">${esc(relationLabel(r.relation))}</span><span class="season-name">${esc(r.name)}</span>`;
    bar.appendChild(el);
  });
}

function applyShiki(malId, a, shiki) {
  if (!shiki) return;
  if (shiki.russian) {
    const tEl = $("#avHeroTitle"); if (tEl) { tEl.textContent = shiki.russian; fitHeroTitle(tEl); }
    const sEl = $("#avHeroSub");
    const orig = a.title_english || a.title;
    if (sEl) sEl.textContent = [orig, a.title_japanese].filter(Boolean).join(", ");
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
    const cover = $("#avHeroCover");
    const img = $("#avHeroCoverImg");
    if (cover && img) {
      img.onload = () => { cover.hidden = false; };
      img.onerror = () => { cover.hidden = true; };
      img.src = imgUrl(al.bannerImage);
    }
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
  if (!chars?.length) {
    box.innerHTML = errorPage({ title: "Персонажей пока нет", message: "MyAnimeList ещё не добавил персонажей этого тайтла.", icon: "fas fa-user-group" });
    return;
  }
  const ROLE_RU = { Main: "Главная роль", Supporting: "Второстепенная роль" };
  box.innerHTML = "";
  chars.slice(0, 12).forEach(c => {
    const el = document.createElement("div");
    el.className = "char";
    const va = (c.voice_actors || []).find(v => v.language === "Japanese") || c.voice_actors?.[0];
    const img = c.character?.images?.jpg?.image_url || "";
    el.innerHTML = `
      <button type="button" class="char-img" aria-label="Открыть изображение" ${img ? "" : "disabled"}>
        <i class="fas fa-user" aria-hidden="true"></i>${img ? `<img src="${imgUrl(img)}" loading="lazy" alt="" onerror="this.remove()">` : ""}
      </button>
      <div class="char-info">
        <div class="char-name">${esc(c.character?.name || "")}</div>
        <div class="char-role">${esc(ROLE_RU[c.role] || c.role || "")}</div>
        ${va ? `<div class="char-va">${esc(va.person?.name || "")}</div>` : ""}
      </div>`;
    if (img) $(".char-img", el).addEventListener("click", () => lightbox.open(img));
    box.appendChild(el);
  });
}
function renderRecommendations(recs) {
  const box = $("#avRecs"); if (!box) return;
  if (!recs?.length) {
    box.innerHTML = errorPage({ title: "Похожих тайтлов пока нет", message: "Рекомендации появятся, когда их добавят пользователи MyAnimeList.", icon: "fas fa-shapes" });
    return;
  }
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
// Player frames served from our own origin run sandboxed with no storage, so
// the page keeps the volume (and the volume keys a third-party player saves)
// and hands them to every new frame in its URL fragment.
const PLAYER_VOLUME_KEY = "av_player_volume";
const FRAME_STORAGE_KEY = "av_frame_storage";
const playerVolume = {
  _read(key) {
    try {
      const value = JSON.parse(localStorage.getItem(key) || "null");
      return value && typeof value === "object" && !Array.isArray(value) ? value : null;
    } catch (_) { return null; }
  },
  load() {
    const saved = this._read(PLAYER_VOLUME_KEY);
    const volume = saved?.volume;
    return typeof volume === "number" && volume >= 0 && volume <= 1
      ? { volume, muted: saved.muted === true } : null;
  },
  // Takes our frame's {volume, muted} and Kodik's volume_change payload.
  save(value) {
    const volume = typeof value === "number" ? value : value?.volume;
    if (typeof volume !== "number" || !(volume >= 0 && volume <= 1)) return;
    try { localStorage.setItem(PLAYER_VOLUME_KEY, JSON.stringify({ volume, muted: value?.muted === true })); }
    catch (_) {}
  },
  rememberStorage(host, entries) {
    if (!host || !entries || typeof entries !== "object") return;
    const kept = {};
    for (const [key, value] of Object.entries(entries).slice(0, 6)) {
      if (key.length <= 100 && typeof value === "string" && value.length <= 2048) kept[key] = value;
    }
    const all = this._read(FRAME_STORAGE_KEY) || {};
    delete all[host];
    if (Object.keys(kept).length) all[host] = kept;
    const hosts = Object.keys(all);
    hosts.slice(0, Math.max(0, hosts.length - 12)).forEach(old => delete all[old]);
    try { localStorage.setItem(FRAME_STORAGE_KEY, JSON.stringify(all)); } catch (_) {}
  },
  fragment(host) {
    const stored = this._read(FRAME_STORAGE_KEY)?.[host];
    const handoff = { ...this.load(), storage: stored && typeof stored === "object" ? stored : {} };
    return `#asoplay=${encodeURIComponent(JSON.stringify(handoff))}`;
  },
};

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
    // Dub carried to the next episode: the viewer's pick, else the first automatic choice.
    this._sessionDubKey = null;
    this._dubsCache = new Map();
    this._reqId = 0;
    this._activeSource = null;
    this._iframeEl = null;
    this._frameHost = "";
    this._frameIsKodik = false;
    this._kodikVolumeSent = false;
    this._playerContextMenuEl = null;
    this._onPlayerContextMenu = null;
    this._onPlayerMenuOutside = null;
    this._onPlayerMenuKeydown = null;
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

  _isSubDub(dub) {
    return /субтитры/i.test(dub?.label || "");
  }

  // A team's voice-over and subtitles share a name, so the key keeps them apart.
  // Keys saved before the split carry no prefix and mean the voice-over.
  _dubKey(dub) {
    const name = this._normDubName(dub?.label);
    return (this._isSubDub(dub) ? `sub:${name}` : name).slice(0, 128);
  }

  // Wanted dub first, otherwise the first voice-over: subtitles only when nothing is voiced.
  _pickDubIndex(dubs) {
    const wanted = this._sessionDubKey || prefs.getDub(this.malId);
    let idx = wanted ? dubs.findIndex(d => this._dubKey(d) === wanted) : -1;
    if (idx < 0) idx = Math.max(0, dubs.findIndex(d => !this._isSubDub(d)));
    if (!this._sessionDubKey) this._sessionDubKey = wanted || this._dubKey(dubs[idx]);
    return idx;
  }

  async addTitles(newTitles) {
    if (this._gated) return;
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
    if (this._gated || this._red) return; this._red = true;
    try {
      await this.discoverWithFallback();
      if (this.destroyed) return;
      this._dubsCache.clear();
      this._renderFoundSource();
    } finally { this._red = false; }
  }
  async init() {
    this.status("Ищем аниме…");
    if (!state.backendAvailable) { this.renderUnavailable("Бэкенд не запущен"); return; }
    if (this._isNonAnime()) {
      // Late titles must not start a search behind this banner.
      this._gated = true;
      this.renderUnavailable("Это клип или промо-ролик",
        "Плеер показывает сериалы, фильмы и спешлы. Если вы хотите посмотреть конкретно этот ролик, воспользуйтесь поиском.");
      return;
    }
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
    // A source found after "not found" (late titles) needs the status area back.
    const overlay = $("#avPlayerOverlay");
    if (overlay?.querySelector(".unavail")) overlay.innerHTML = `<span class="ed-spinner spinner" aria-hidden="true"></span><div id="avPlayerStatus"></div>`;
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
          _src: e._src,
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
      if (!await this.discoverBackend(src, { refresh: true })) return false;
    } catch (_) {
      return false;
    }
    if (reqId !== this._reqId || this.destroyed || src !== this._activeSource) return false;
    const fresh = this.sources[`backend:${src}`]?.episodes;
    if (!fresh?.length) return false;

    const nextEpisodes = this._buildEpisodeList(fresh);
    const currentIdx = nextEpisodes.findIndex(e => e.num === currentNum);
    if (currentIdx < 0) return false;
    this.episodes = nextEpisodes;
    this.currentEp = currentIdx;
    this._dubsCache.clear();
    this.renderEpisodes();
    $$("#avEpisodesList .ep-row").forEach((b, idx) => b.classList.toggle("active", idx === this.currentEp));
    this._syncCurrentEpisodeTitle();
    return true;
  }

  async _loadEpisodeDubsFromSource(src, ep, reqId) {
    const epKey = ep?.yummyKey || ep?.key;
    if (!epKey || !src) return [];
    const list = await api.backendSources(epKey, src);
    if (reqId !== this._reqId) return [];
    return (list || []).map(s => ({ label: s.name, _sourceKey: s.key, _src: src }));
  }

  async _loadEpisodeDubs(ep, reqId, { retryStale = true } = {}) {
    const src = ep?._src || this._activeSource;
    try {
      return await this._loadEpisodeDubsFromSource(src, ep, reqId);
    } catch (err) {
      if (retryStale && src === this._activeSource && this._isStaleBackendKeyError(err)) {
        const refreshed = await this._refreshActiveSourceKeys(reqId);
        if (reqId !== this._reqId || this.destroyed) return [];
        if (!refreshed) throw err;
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

  _backendSourcePriority() {
    return ["yummy_anime", "oldyummy", "animego", "yummy_anime_org",
            "dreamcast", "anilibria", "anilibme", "sameband"];
  }

  _activateSourceForEpisode(src, episodes, index) {
    this._activeSource = src;
    this.episodes = episodes;
    this.currentEp = index;
    this._dubsCache.clear();
    $("#avEpCount").textContent = `${this.episodes.length} эп.`;
    this.renderEpisodes();
    $$("#avEpisodesList .ep-row").forEach((b, idx) => b.classList.toggle("active", idx === this.currentEp));
    const ep = this.episodes[this.currentEp];
    $("#avEpInfo").innerHTML = `<span class="sp-ep-num">${ep.num}</span><span class="sp-ep-title">${esc(ep.name)}</span>`;
    const p = $("#avPrevEp"), n = $("#avNextEp");
    if (p) p.disabled = this.currentEp === 0;
    if (n) n.disabled = this.currentEp === this.episodes.length - 1;
    return ep;
  }

  async _tryAlternativeSourceForEpisode(episodeNum, reqId) {
    const available = new Set(state.backendSources || []);
    let lastError = null;
    for (const src of this._backendSourcePriority()) {
      if (src === this._activeSource || !available.has(src)) continue;
      try {
        if (!this.sources[`backend:${src}`]?.episodes?.length) {
          await this.discoverBackend(src);
        }
        if (reqId !== this._reqId || this.destroyed) return null;
        const srcData = this.sources[`backend:${src}`];
        if (!srcData?.episodes?.length) continue;
        let episodes = this._buildEpisodeList(srcData.episodes);
        let index = episodes.findIndex(e => Number(e.num) === Number(episodeNum));
        if (index < 0) continue;
        let ep = episodes[index];
        let dubs = [];
        try {
          dubs = await this._loadEpisodeDubsFromSource(src, ep, reqId);
        } catch (err) {
          if (!this._isStaleBackendKeyError(err)) throw err;
          delete this.sources[`backend:${src}`];
          await this.discoverBackend(src);
          if (reqId !== this._reqId || this.destroyed) return null;
          const freshData = this.sources[`backend:${src}`];
          if (!freshData?.episodes?.length) continue;
          episodes = this._buildEpisodeList(freshData.episodes);
          index = episodes.findIndex(e => Number(e.num) === Number(episodeNum));
          if (index < 0) continue;
          ep = episodes[index];
          dubs = await this._loadEpisodeDubsFromSource(src, ep, reqId);
        }
        if (reqId !== this._reqId || this.destroyed) return null;
        if (!dubs.length) continue;
        const activeEp = this._activateSourceForEpisode(src, episodes, index);
        this._dubsCache.set(activeEp.num, dubs);
        return { ep: activeEp, dubs };
      } catch (err) {
        lastError = err;
        console.warn("episode source fallback skipped", src, err);
      }
    }
    if (lastError) throw lastError;
    return null;
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

  _flashPlayerNotice(text, danger = false) {
    const c = $("#artplayer");
    if (!c) return;
    let note = c.querySelector(".player-shot-notice");
    if (!note) {
      note = document.createElement("div");
      note.className = "player-shot-notice";
      note.setAttribute("role", "status");
      c.appendChild(note);
    }
    note.textContent = text;
    note.classList.toggle("is-danger", !!danger);
    clearTimeout(this._shotNoticeTimer);
    this._shotNoticeTimer = setTimeout(() => note.remove(), 1800);
  }

  _screenshotFilename(payload = {}) {
    const animeId = this.anime?.mal_id || this.anime?.id || "anime";
    const ep = this.episodes[this.currentEp]?.num || this.currentEp + 1 || "episode";
    const secs = Math.max(0, Math.floor(Number(payload.time) || this._lastTime || 0));
    const hh = String(Math.floor(secs / 3600)).padStart(2, "0");
    const mm = String(Math.floor((secs % 3600) / 60)).padStart(2, "0");
    const ss = String(secs % 60).padStart(2, "0");
    return `AsoPlay-${animeId}-ep${ep}-${hh}-${mm}-${ss}.png`;
  }

  async _downloadPlayerScreenshot(payload = {}) {
    const dataUrl = String(payload.dataUrl || "");
    if (!dataUrl.startsWith("data:image/png")) {
      this._flashPlayerNotice("Скриншот недоступен для этого источника", true);
      return;
    }
    try {
      const blob = await (await fetch(dataUrl)).blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = this._screenshotFilename(payload);
      link.rel = "noopener";
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      this._flashPlayerNotice("Скриншот сохранён");
    } catch (_) {
      this._flashPlayerNotice("Не удалось сохранить скриншот", true);
    }
  }

  _handlePlayerScreenshotMessage(data) {
    if (data?.type === "asoplay:screenshot") {
      this._downloadPlayerScreenshot(data);
      return true;
    }
    if (data?.type === "asoplay:screenshot-error") {
      this._flashPlayerNotice(data.message || "Скриншот недоступен", true);
      return true;
    }
    return false;
  }

  _handlePlayerVolumeMessage(data) {
    if (data?.type === "asoplay:storage") {
      playerVolume.rememberStorage(this._frameHost, data.entries);
      return true;
    }
    if (data?.key === "kodik_player_volume_change") {
      playerVolume.save(data.value);
      return true;
    }
    // Kodik keeps no volume in its storage; its API takes ours once it plays.
    if (this._frameIsKodik && !this._kodikVolumeSent
        && (data?.key === "kodik_player_play" || data?.key === "kodik_player_video_started")) {
      this._kodikVolumeSent = true;
      const saved = playerVolume.load();
      const target = this._iframeEl?.contentWindow;
      if (saved && target) {
        target.postMessage({ key: "kodik_player_api", value: { method: "volume", volume: saved.volume } }, "*");
        if (saved.muted) target.postMessage({ key: "kodik_player_api", value: { method: "mute" } }, "*");
      }
    }
    return false;
  }

  _playerAnimeUrl() {
    try {
      return new URL(location.pathname || `/anime/${this.malId}/`, location.origin).href;
    } catch (_) {
      return `${location.origin}/anime/${this.malId}/`;
    }
  }

  async _copyPlayerAnimeLink() {
    const url = this._playerAnimeUrl();
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(url);
      } else {
        throw new Error("clipboard unavailable");
      }
      this._flashPlayerNotice("Ссылка на аниме скопирована");
    } catch (_) {
      try {
        const field = document.createElement("textarea");
        field.value = url;
        field.setAttribute("readonly", "");
        field.style.cssText = "position:fixed;left:-9999px;top:-9999px";
        document.body.appendChild(field);
        field.select();
        document.execCommand("copy");
        field.remove();
        this._flashPlayerNotice("Ссылка на аниме скопирована");
      } catch {
        this._flashPlayerNotice("Не удалось скопировать ссылку", true);
      }
    }
  }

  _requestPlayerScreenshot() {
    try {
      if (!this._iframeEl?.contentWindow) throw new Error("iframe unavailable");
      this._iframeEl.contentWindow.postMessage({ type: "asoplay:capture-screenshot" }, "*");
    } catch (_) {
      this._flashPlayerNotice("Скриншот недоступен для этого плеера", true);
    }
  }

  _hidePlayerContextMenu() {
    this._playerContextMenuEl?.remove();
    this._playerContextMenuEl = null;
    if (this._onPlayerMenuOutside) {
      document.removeEventListener("pointerdown", this._onPlayerMenuOutside, true);
      this._onPlayerMenuOutside = null;
    }
    if (this._onPlayerMenuKeydown) {
      document.removeEventListener("keydown", this._onPlayerMenuKeydown);
      this._onPlayerMenuKeydown = null;
    }
  }

  _runPlayerMenuAction(action) {
    this._hidePlayerContextMenu();
    if (action === "screenshot") this._requestPlayerScreenshot();
    else if (action === "copy-link") this._copyPlayerAnimeLink();
    else if (action === "animesocial") window.open("https://AnimeSocial.online", "_blank", "noopener,noreferrer");
  }

  _showPlayerContextMenu(clientX, clientY) {
    this._hidePlayerContextMenu();
    const menu = document.createElement("div");
    menu.className = "ed-menu asoplayer-menu";
    menu.setAttribute("role", "menu");
    menu.innerHTML = `
      <button type="button" class="ed-menu__item" role="menuitem" data-action="screenshot"><i class="fas fa-camera" aria-hidden="true"></i>Сделать скриншот</button>
      <button type="button" class="ed-menu__item" role="menuitem" data-action="copy-link"><i class="fas fa-link" aria-hidden="true"></i>Копировать ссылку на аниме</button>
      <button type="button" class="ed-menu__item" role="menuitem" data-action="animesocial"><i class="fas fa-users" aria-hidden="true"></i>Открыть AnimeSocial</button>
      <div class="ed-menu__divider" role="separator"></div>
      <div class="asoplayer-menu-brand">AsoPlayer 0.0.1</div>
    `;
    menu.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-action]");
      if (!button) return;
      this._runPlayerMenuAction(button.dataset.action);
    });
    document.body.appendChild(menu);
    const gap = 8;
    const rect = menu.getBoundingClientRect();
    const maxX = Math.max(gap, window.innerWidth - rect.width - gap);
    const maxY = Math.max(gap, window.innerHeight - rect.height - gap);
    menu.style.left = `${Math.min(Math.max(gap, clientX), maxX)}px`;
    menu.style.top = `${Math.min(Math.max(gap, clientY), maxY)}px`;
    this._playerContextMenuEl = menu;
    this._onPlayerMenuOutside = (event) => {
      if (!this._playerContextMenuEl?.contains(event.target)) this._hidePlayerContextMenu();
    };
    this._onPlayerMenuKeydown = (event) => {
      if (event.key === "Escape") this._hidePlayerContextMenu();
    };
    setTimeout(() => {
      document.addEventListener("pointerdown", this._onPlayerMenuOutside, true);
      document.addEventListener("keydown", this._onPlayerMenuKeydown);
    }, 0);
  }

  _handlePlayerContextMenuMessage(data) {
    if (data?.type === "asoplay:player-pointerdown") {
      this._hidePlayerContextMenu();
      return true;
    }
    if (data?.type !== "asoplay:player-context-menu") return false;
    const iframe = this._iframeEl;
    const rect = iframe?.getBoundingClientRect?.();
    const baseX = rect ? rect.left : 0;
    const baseY = rect ? rect.top : 0;
    const x = baseX + (Number(data.x) || 0);
    const y = baseY + (Number(data.y) || 0);
    this._showPlayerContextMenu(x, y);
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
      const d = e.data;
      if (d && typeof d === "object" && this._handlePlayerContextMenuMessage(d)) return;
      if (d && typeof d === "object" && this._handlePlayerScreenshotMessage(d)) return;
      if (d && typeof d === "object" && this._handlePlayerVolumeMessage(d)) return;
      this._dubSawEvent = true;
      const since = Date.now() - (this._playStartedAt || 0);
      if (since < 5000) return;
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
        if (pop?.hidden) return;
        if (e.target.closest?.("[data-dub-close]") || (!pop.contains(e.target) && !trigger.contains(e.target))) this.closeDubPop();
      });
      document.addEventListener("keydown", e => {
        if (e.key === "Escape" && pop && !pop.hidden) this.closeDubPop();
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
    // Type only: real ONA/TV shorts can run a minute per episode. Catalog grids
    // keep their own duration filter (_isRealAnime).
    return NON_ANIME_TYPES.has(this.anime?.type);
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
        <div class="coming-title">До выхода осталось</div>
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
        <div class="coming-date">Дата премьеры пока не объявлена. Серии появятся здесь, когда аниме начнёт выходить.</div>
      </div>`;
    ov.classList.remove("hide");
    $("#avEpisodesList").innerHTML = errorPage({ title: "Серий пока нет", message: "Аниме ещё не вышло.", icon: "fas fa-hourglass-half" });
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

  renderUnavailable(msg, sub = "") {
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
        <div class="unavail-icon"><i class="fas fa-video-slash" aria-hidden="true"></i></div>
        <div class="unavail-title">${msg ? esc(msg) : "Видео пока не найдено"}</div>
        <div class="unavail-sub">${sub ? esc(sub) : "Ни один источник не отдаёт это аниме. Попробуйте найти его через поиск."}</div>
        <div class="unavail-links">
          <a href="${esc(ddg)}" target="_blank" rel="noopener" class="ed-btn ed-btn--secondary unavail-ddg">
            <i class="fas fa-magnifying-glass" aria-hidden="true"></i>Искать в DuckDuckGo
          </a>
        </div>
      </div>`;
    ov.classList.remove("hide");
    $("#avEpisodesList").innerHTML = errorPage({ title: "Источник не найден", message: "Серии появятся, когда аниме добавят в источники.", icon: "fas fa-video-slash" });
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

  async discoverBackend(src, { refresh = false } = {}) {
    if (!refresh && this.sources[`backend:${src}`]?.episodes?.length) return true;
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
    if (!best || bestScore < 0.3) return false;
    let eps = [];
    try { eps = await api.backendEpisodes(best.key, src); } catch { return false; }
    if (!eps?.length) {
      if (bestScore >= 1.0) this._matchedNoEps = true;
      return false;
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
    return true;
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
    // A late discovery must not pair another source with already-rendered keys.
    if (this._activeSource && this.sources[`backend:${this._activeSource}`]?.episodes?.length) {
      return this._renderFoundSource();
    }
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
    (search.closest(".ep-search-wrap") || search).hidden = this.episodes.length < 12;
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
        <i class="fas fa-check ep-watched-icon" aria-hidden="true"></i>
        ${canUnwatch ? `
          <button type="button" class="ed-iconbtn ep-unwatch" aria-label="Отметить серию ${e.num} как непросмотренную" title="Отметить как непросмотренную">
            <i class="fas fa-rotate-left" aria-hidden="true"></i>
          </button>
          <div class="ep-unwatch-confirm" role="alertdialog" aria-label="Отметить серию как непросмотренную" hidden>
            <div class="ep-unwatch-confirm-text">Отметить серию ${e.num} как непросмотренную? Прогресс и активность этой серии удалятся, вернуть их нельзя.</div>
            <div class="ep-unwatch-confirm-actions">
              <button type="button" class="ed-btn ed-btn--danger ed-btn--sm ep-unwatch-yes">Отметить</button>
              <button type="button" class="ed-btn ed-btn--ghost ed-btn--sm ep-unwatch-no">Отмена</button>
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
    let ep = this.episodes[i];
    if (!ep) return;
    this._rememberTabEpisode(ep.num);
    $("#avEpInfo").innerHTML = `<span class="sp-ep-num">${ep.num}</span><span class="sp-ep-title">${esc(ep.name)}</span>`;
    const p = $("#avPrevEp"), n = $("#avNextEp");
    if (p) p.disabled = i === 0;
    if (n) n.disabled = i === this.episodes.length - 1;
    this.status(`Загружаем озвучки для ${ep.num}-й серии…`);

    try {
      let dubs = this._dubsCache.get(ep.num);
      if (!dubs) {
        let loadError = null;
        try {
          dubs = await this._loadEpisodeDubs(ep, reqId, { retryStale: true });
        } catch (err) {
          loadError = err;
          console.warn("episode dubs failed", err);
          dubs = [];
        }
        if (reqId !== this._reqId) return;
        if (!dubs.length) {
          const fallback = await this._tryAlternativeSourceForEpisode(ep.num, reqId);
          if (reqId !== this._reqId) return;
          if (fallback) {
            ep = fallback.ep;
            dubs = fallback.dubs;
          }
        }
        if (!dubs.length) {
          if (loadError) throw loadError;
          this.status("Озвучки не найдены");
          return;
        }
        this._dubsCache.set(ep.num, dubs);
      }
      this.currentDubs = dubs;
      await this._prefetchDubUrls(dubs, reqId);
      if (reqId !== this._reqId) return;

      const idx = this._pickDubIndex(dubs);
      this.currentDubIdx = idx;
      this.renderDubs();
      await this.playDub(idx);
    } catch (e) {
      if (reqId !== this._reqId || this.destroyed) return;
      console.warn("episode playback load failed", e);
      this.currentDubs = [];
      this.renderDubs();
      this.status("Не удалось загрузить озвучки. Попробуйте ещё раз.", {
        retry: () => this.playEpisode(this.currentEp),
      });
    }
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

    const voices = this.currentDubs.map((d, i) => ({ d, i })).filter(({ d }) => !this._isSubDub(d));
    const subs = this.currentDubs.map((d, i) => ({ d, i })).filter(({ d }) => this._isSubDub(d));
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
        title.innerHTML = `${esc(g.title)}<span class="ed-badge ed-badge--muted">${g.items.length}</span>`;
        section.appendChild(title);
      }
      const listWrap = document.createElement("div");
      listWrap.className = "sp-dub-list";
      g.items.forEach(({ d, i }) => {
        const b = document.createElement("button");
        const active = i === this.currentDubIdx;
        b.type = "button";
        b.className = "ed-menu__item sp-dub-item" + (active ? " is-active" : "");
        b.setAttribute("aria-pressed", String(active));
        const short = d.label.replace(/^(Озвучка|Субтитры|Оригинал)\s*/i, "").trim() || d.label;
        b.innerHTML = `<span class="sp-dub-name">${esc(short)}</span>${active ? '<i class="fas fa-check" aria-hidden="true"></i>' : ""}`;
        b.title = d.label;
        b.addEventListener("click", () => {
          this.currentDubIdx = i;
          this._sessionDubKey = this._dubKey(d);
          prefs.setDub(this.malId, this._sessionDubKey);
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
    const pop = $("#avDubPop");
    const tr = $("#avDubTrigger");
    if (pop && !pop.hidden && pop.contains(document.activeElement)) tr?.focus({ preventScroll: true });
    if (pop) pop.hidden = true;
    if (tr) {
      tr.classList.remove("open");
      tr.setAttribute("aria-expanded", "false");
    }
    document.body.classList.remove("is-dub-sheet");
  }
  toggleDubPop() {
    const pop = $("#avDubPop");
    const tr = $("#avDubTrigger");
    if (!pop || !tr) return;
    const isOpen = !pop.hidden;
    if (isOpen) { this.closeDubPop(); return; }
    pop.hidden = false;
    tr.classList.add("open");
    tr.setAttribute("aria-expanded", "true");
    // On a phone the list is a bottom sheet: it follows the finger and closes when swiped down.
    const sheet = window.matchMedia("(max-width: 767px)").matches;
    document.body.classList.toggle("is-dub-sheet", sheet);
    const panel = pop.querySelector(".sp-dub-panel");
    if (sheet && panel) {
      SheetDrag.bind(panel, { scrim: pop.querySelector(".sp-dub-scrim"), scroll: ".sp-dub-panel", close: () => this.closeDubPop() });
    }
    (pop.querySelector(".sp-dub-item.is-active") || pop.querySelector(".sp-dub-item"))?.focus({ preventScroll: true });
  }
  async playDub(i, { retryStale = true } = {}) {
    const d = this.currentDubs[i];
    if (!d) return;
    this.currentDubIdx = i;
    this.renderDubs();
    const reqId = this._reqId;

    if (!d.iframeUrl && d._sourceKey) {
      this.status(`Загружаем «${d.label}»…`);
      let vids = [];
      try { vids = await api.backendVideos(d._sourceKey, d._src) || []; }
      catch (err) {
        if (retryStale && this._isStaleBackendKeyError(err)) {
          const oldKey = this._dubKey(d);
          const freshDubs = await this._reloadCurrentEpisodeDubs(reqId);
          if (reqId !== this._reqId) return;
          if (freshDubs?.length) {
            const freshIdx = Math.max(0, freshDubs.findIndex(x => this._dubKey(x) === oldKey));
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
    if (!this._onPlayerContextMenu) {
      this._onPlayerContextMenu = (event) => {
        event.preventDefault();
        this._showPlayerContextMenu(event.clientX, event.clientY);
      };
      c.addEventListener("contextmenu", this._onPlayerContextMenu);
    }
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
    let frameHost = "";
    try {
      const u = new URL(url);
      frameHost = u.hostname.toLowerCase();
      if (/(\.|^)kodikplayer\.com$/i.test(u.hostname)
          && /^\/(uv|video|seria|episode|season|serial)\//i.test(u.pathname)) {
        playerUrl = new URL(u.pathname + u.search, playerProxyBase || location.origin).href;
        try { allowSameOrigin = new URL(playerUrl).origin !== location.origin; }
        catch (_) { allowSameOrigin = false; }
      }
    } catch (_) {}
    this._frameHost = frameHost;
    this._frameIsKodik = /(\.|^)kodikplayer\.com$/.test(frameHost);
    playerUrl += playerVolume.fragment(frameHost);
    let iframe = c.querySelector("iframe");
    const hardenFrame = (node, sameOrigin = false) => {
      node.allow = "autoplay; fullscreen; picture-in-picture; encrypted-media";
      node.removeAttribute("allowfullscreen");
      node.referrerPolicy = "no-referrer";
      node.setAttribute("sandbox", `allow-scripts allow-presentation${sameOrigin ? " allow-same-origin" : ""}`);
    };
    if (iframe) {
      hardenFrame(iframe, allowSameOrigin);
      // The fragment only carries the saved volume, so it alone is no reason to reload.
      if (iframe.src.split("#")[0] !== playerUrl.split("#")[0]) {
        iframe.src = playerUrl;
        this._kodikVolumeSent = false;
      }
      this._iframeEl = iframe;
      return;
    }
    this._kodikVolumeSent = false;
    iframe = document.createElement("iframe");
    // Harden third-party players: keep playback features, block popups and
    // top-level navigation attempts from ad scripts inside the iframe.
    hardenFrame(iframe, allowSameOrigin);
    iframe.className = "player-frame";
    iframe.src = playerUrl;
    c.appendChild(iframe);
    this._iframeEl = iframe;
  }
  destroyPlayer() {
    const c = $("#artplayer");
    if (c && this._onPlayerContextMenu) c.removeEventListener("contextmenu", this._onPlayerContextMenu);
    this._onPlayerContextMenu = null;
    if (c) c.innerHTML = "";
    this._iframeEl = null;
  }
  status(m, { retry } = {}) {
    const s = $("#avPlayerStatus");
    if (s) s.textContent = m;
    const o = $("#avPlayerOverlay");
    if (!o) return;
    o.querySelector(".player-retry")?.remove();
    const spinner = o.querySelector(".spinner");
    if (spinner) spinner.hidden = !!retry;
    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "ed-btn ed-btn--primary player-retry";
      button.textContent = "Повторить";
      button.onclick = retry;
      o.append(button);
    }
    o.classList.remove("hide");
  }
  hideOverlay() { const o = $("#avPlayerOverlay"); if (o) o.classList.add("hide"); }
  destroy() {
    this.destroyed = true;
    if (this._fallbackInt) clearInterval(this._fallbackInt);
    if (this._countdownInt) clearInterval(this._countdownInt);
    if (this._shotNoticeTimer) clearTimeout(this._shotNoticeTimer);
    this._hidePlayerContextMenu();
    if (this._onMessage) window.removeEventListener("message", this._onMessage);
    if (this._shellCleanup) this._shellCleanup();
    this.destroyPlayer();
  }
}

// ---------- router ----------
let routeVersion = 0;
async function router() {
  closeMobileMenu();
  routeVersion++;
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
  if (to === current) { router(); return; }
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
  state.useProxy = ok;
  state.proxyImages = ok && vpn;
  state.backendSources = sources;
}

// ---------- events ----------
let closeMobileMenu = () => {};
function bind() {
  document.addEventListener("keydown", e => {
    if (!(e.ctrlKey || e.metaKey) || e.key.toLowerCase() !== "k") return;
    e.preventDefault();
    const field = $("#appSearchInput");
    if (field && field.getClientRects().length) field.focus();
    else nav("/catalog");
  });

  closeMobileMenu = setupNavigation({ nav });

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
    loadingBlock,
    makeCard,
    imgUrl,
    enrichWithShiki,
  });
  router();
  setInterval(refreshBackend, 30000);
})();
