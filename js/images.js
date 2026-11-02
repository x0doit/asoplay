// AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
// All rights reserved. See /COPYRIGHT for full terms.
//
// Картинки с чужих хостов (обложки MAL, AniList, Shikimori…): напрямую из браузера, а через /proxy/img сайта —
// когда напрямую не выходит. Правила приходят с состоянием сервера — при загрузке (/health) и по живому соединению
// (av:status): img_hosts — хосты, которые принимает /proxy/img; proxy_hosts — хосты из панели Anime Social, их
// картинки идут через прокси сразу. VPN-мост сервера здесь ни при чём: он говорит, как сервер ходит наружу, а не
// грузятся ли картинки у посетителя.
//
// Запасной путь: картинка с хоста из img_hosts не загрузилась — ошибка или WAIT_MS, а размеров картинки браузер всё
// ещё не знает, то есть байты не идут (у ленивой — с её появления на экране), — один повтор через прокси. Медленная
// связь, по которой картинка уже грузится, повтора не получает. Повтор перехватывает ошибку раньше обработчиков
// самой картинки (onerror в разметке), чтобы они не убрали её до повтора; не вышло и через прокси — они срабатывают
// как обычно.
// Хост, чья картинка загрузилась через прокси, запоминается на REMEMBER_MS (localStorage av_img_proxy, без него —
// память вкладки): дальше его картинки у этого человека идут через прокси сразу, через сутки — снова напрямую.
// На прокси идут только адреса, которые /proxy/img примет (как url_refusal на сервере); остальные — напрямую.

const WAIT_MS = 6000;
const REMEMBER_MS = 24 * 3600 * 1000;
const MEMORY_KEY = "av_img_proxy";

const rules = { backend: "", available: false, proxyHosts: [], imgHosts: [] };
const memory = new Map();  // хост → до какого времени (мс) его картинки идут через прокси
let memoryLoaded = false;
const timers = new WeakMap();
let lazyObserver = null;

const hostList = list =>
  (Array.isArray(list) ? list.filter(h => typeof h === "string" && h).map(h => h.toLowerCase()) : null);
const listed = (host, list) => list.some(h => host === h || host.endsWith("." + h));
const proxyPrefix = () => `${rules.backend}/proxy/img?url=`;

// Хост адреса, который /proxy/img примет: http или https, без логина, порт обычный, хост из a-z, 0-9, точки
// и дефиса; иначе пусто — такой адрес остаётся напрямую.
function hostOf(u) {
  if (!/^https?:\/\//i.test(u || "")) return "";
  // URL раскрывает %2e в хосте, а сервер смотрит на адрес как есть: такие адреса — напрямую
  if (/[%@\\]/.test(u.replace(/^https?:\/\//i, "").split(/[/?#]/, 1)[0])) return "";
  let parsed;
  try { parsed = new URL(u); } catch (_) { return ""; }
  if (parsed.username || parsed.password || !["", "80", "443"].includes(parsed.port)) return "";
  const host = parsed.hostname.replace(/\.$/, "");
  return /^[a-z0-9.-]+$/.test(host) ? host : "";
}

// Правила из состояния сервера; списков нет в кадре — прежние остаются.
export function configure({ backend, available, proxyHosts, imgHosts } = {}) {
  if (typeof backend === "string") rules.backend = backend.replace(/\/+$/, "");
  rules.available = !!available;
  rules.proxyHosts = hostList(proxyHosts) || rules.proxyHosts;
  rules.imgHosts = hostList(imgHosts) || rules.imgHosts;
}

function loadMemory() {
  if (memoryLoaded) return;
  memoryLoaded = true;
  try {
    const saved = JSON.parse(localStorage.getItem(MEMORY_KEY) || "{}");
    for (const [host, until] of Object.entries(saved)) {
      if (Number(until) > Date.now()) memory.set(host, Number(until));
    }
  } catch (_) { /* без localStorage — память вкладки */ }
}

function saveMemory() {
  try {
    localStorage.setItem(MEMORY_KEY, JSON.stringify(Object.fromEntries(memory)));
  } catch (_) { /* без localStorage — память вкладки */ }
}

// Картинки хоста — через прокси сразу: его запомнили, и сутки ещё не прошли.
export function remembered(host) {
  loadMemory();
  const until = memory.get(host);
  if (!until) return false;
  if (until > Date.now()) return true;
  memory.delete(host);
  saveMemory();
  return false;
}

export function remember(host) {
  loadMemory();
  memory.set(host, Date.now() + REMEMBER_MS);
  saveMemory();
}

// Адрес картинки для src: через прокси — по правилам выше, иначе как есть.
export function url(u) {
  if (!u) return "";
  const host = hostOf(u);
  if (!rules.available || !host || !listed(host, rules.imgHosts)) return u;
  if (listed(host, rules.proxyHosts) || remembered(host)) return proxyPrefix() + encodeURIComponent(u);
  return u;
}

// Прямой адрес картинки, которой ещё можно дать повтор через прокси; иначе пусто.
function retryable(img) {
  const src = img.getAttribute("src") || "";
  if (!rules.available || src.startsWith(proxyPrefix()) || img.dataset.avRetried === src) return "";
  const host = hostOf(src);
  return host && listed(host, rules.imgHosts) ? src : "";
}

// Один повтор через прокси; удался — хост запоминается, и его картинки, что ещё не загрузились, тоже уходят
// на прокси, не дожидаясь своей ошибки. true — повтор начат.
function retry(img) {
  const src = retryable(img);
  if (!src) return false;
  clearTimeout(timers.get(img));
  timers.delete(img);
  img.dataset.avRetried = src;
  const host = hostOf(src);
  const loaded = () => {
    img.removeEventListener("error", failed);
    if (remembered(host)) return;
    remember(host);
    Array.from(img.ownerDocument.images).forEach(other => {
      if (!(other.complete && other.naturalWidth) && hostOf(retryable(other)) === host) retry(other);
    });
  };
  const failed = () => img.removeEventListener("load", loaded);
  img.addEventListener("load", loaded, { once: true });
  img.addEventListener("error", failed, { once: true });
  img.setAttribute("src", proxyPrefix() + encodeURIComponent(src));
  return true;
}

// Часы ожидания: за WAIT_MS картинка не загрузилась, не сломалась и размеров её браузер не знает — повтор через прокси.
function arm(img) {
  const src = img.getAttribute("src");
  if (img.complete || timers.has(img) || !retryable(img)) return;
  const done = () => { clearTimeout(timers.get(img)); timers.delete(img); };
  timers.set(img, setTimeout(() => {
    timers.delete(img);
    if (img.isConnected && !img.complete && !img.naturalWidth && img.getAttribute("src") === src) retry(img);
  }, WAIT_MS));
  img.addEventListener("load", done, { once: true });
  img.addEventListener("error", done, { once: true });
}

function watch(img) {
  const src = retryable(img);
  if (!src) return;
  if (img.complete ? !img.naturalWidth : remembered(hostOf(src))) {
    retry(img);  // сломалась раньше, чем её увидели, или хост уже известен как закрытый
    return;
  }
  if (img.complete) return;
  if (img.loading === "lazy" && lazyObserver) lazyObserver.observe(img);
  else arm(img);
}

// Перехват ошибок, часы для новых картинок и смены src, повтор для уже сломанных; один раз на документ.
export function install(doc = document) {
  if (doc.documentElement.dataset.avImages) return;
  doc.documentElement.dataset.avImages = "1";
  doc.addEventListener("error", (e) => {
    const img = e.target;
    if (img instanceof HTMLImageElement && retry(img)) e.stopPropagation();
  }, true);
  if ("IntersectionObserver" in window) {
    lazyObserver = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        lazyObserver.unobserve(entry.target);
        arm(entry.target);
      }
    });
  }
  new MutationObserver((records) => {
    for (const record of records) {
      if (record.type === "attributes") {
        if (record.target.tagName === "IMG") watch(record.target);
        continue;
      }
      for (const node of record.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.tagName === "IMG") watch(node);
        else node.querySelectorAll("img").forEach(watch);
      }
    }
  }).observe(doc.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ["src"] });
  Array.from(doc.images).forEach(watch);
}
