/*
 * Сторож рефакторинга: первая загрузка не хуже, чем была.
 *
 * Автор: Чепела Даниил Максимович.
 *
 * Открывает главную и страницу тайтла в чистом браузере (без кеша) по три раза и берёт медиану: число запросов и вес
 * по типам (документ, скрипты, стили, шрифты, картинки, API), время до первой отрисовки (FCP), DOMContentLoaded и
 * load. Вес — сколько байт пришло по сети (encodedDataLength из DevTools). Своё — тема и оболочка — считается
 * отдельно от чужого (каталог, постеры), чтобы шум сети не прятал изменения темы.
 *
 *   node tools/guard/firstload.cjs [--site URL] --save   записать базу (baseline/firstload-<хост>.json)
 *   node tools/guard/firstload.cjs [--site URL]          сравнить с базой
 */
const fs = require("fs");
const path = require("path");
const { launch } = require("./browser.cjs");

const arg = (name, fallback) => { const i = process.argv.indexOf(name); return i > 0 ? process.argv[i + 1] : fallback; };
const SITE = arg("--site", "http://127.0.0.1:8787");
const SAVE = process.argv.includes("--save");
const BASE = path.join(__dirname, "baseline", `firstload-${new URL(SITE).host.replace(/[^\w.-]/g, "_")}.json`);
const PAGES = ["/", "/anime/52991"];
const RUNS = 3;

/** Тип запроса для отчёта: своё (тема) отдельно от данных и картинок. */
function kind(type, url) {
  const own = url.startsWith(SITE);
  if (type === "Document") return "document";
  if (type === "Script") return own ? "script" : "script-3p";
  if (type === "Stylesheet") return own ? "style" : "style-3p";
  if (type === "Font") return own ? "font" : "font-3p";
  if (type === "Image") return "image";
  if (type === "Fetch" || type === "XHR") return "api";
  return "other";
}

/** Один заход: запросы и байты по типам, время отрисовки. */
async function measure(browser, url) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const cdp = await ctx.newCDPSession(page);
  await cdp.send("Network.enable");
  await cdp.send("Network.setCacheDisabled", { cacheDisabled: true });
  const reqs = new Map();
  cdp.on("Network.requestWillBeSent", e => { if (!e.request.url.startsWith("data:")) reqs.set(e.requestId, { url: e.request.url, type: e.type, bytes: 0 }); });
  cdp.on("Network.loadingFinished", e => { const r = reqs.get(e.requestId); if (r) r.bytes = e.encodedDataLength; });
  await page.goto(SITE + url, { waitUntil: "load", timeout: 60000 });
  await page.waitForLoadState("networkidle", { timeout: 20000 }).catch(() => {});
  const timing = await page.evaluate(() => {
    const nav = performance.getEntriesByType("navigation")[0];
    const fcp = performance.getEntriesByName("first-contentful-paint")[0];
    return { fcp: Math.round(fcp?.startTime || 0), dcl: Math.round(nav.domContentLoadedEventEnd), load: Math.round(nav.loadEventEnd) };
  });
  await ctx.close();
  const by = {};
  for (const r of reqs.values()) {
    const k = kind(r.type, r.url);
    by[k] = by[k] || { requests: 0, bytes: 0 };
    by[k].requests += 1;
    by[k].bytes += r.bytes;
  }
  return { ...timing, requests: reqs.size, bytes: [...reqs.values()].reduce((s, r) => s + r.bytes, 0), by };
}

const median = values => values.slice().sort((a, b) => a - b)[Math.floor(values.length / 2)];

(async () => {
  const browser = await launch();
  const result = {};
  for (const url of PAGES) {
    const runs = [];
    for (let i = 0; i < RUNS; i++) runs.push(await measure(browser, url));
    const kinds = [...new Set(runs.flatMap(r => Object.keys(r.by)))].sort();
    result[url] = {
      fcp: median(runs.map(r => r.fcp)), dcl: median(runs.map(r => r.dcl)), load: median(runs.map(r => r.load)),
      requests: median(runs.map(r => r.requests)), bytes: median(runs.map(r => r.bytes)),
      by: Object.fromEntries(kinds.map(k => [k, { requests: median(runs.map(r => r.by[k]?.requests || 0)),
                                                  bytes: median(runs.map(r => r.by[k]?.bytes || 0)) }])),
    };
  }
  await browser.close();
  if (SAVE) {
    fs.mkdirSync(path.dirname(BASE), { recursive: true });
    fs.writeFileSync(BASE, JSON.stringify(result, null, 1));
  }
  const base = !SAVE && fs.existsSync(BASE) ? JSON.parse(fs.readFileSync(BASE, "utf8")) : null;
  for (const [url, now] of Object.entries(result)) {
    const was = base?.[url];
    const d = (a, b) => (was ? ` (было ${b})` : "");
    console.log(`${url}: FCP ${now.fcp} мс${d(now.fcp, was?.fcp)}, DCL ${now.dcl}${d(now.dcl, was?.dcl)}, load ${now.load}${d(now.load, was?.load)}, `
      + `запросов ${now.requests}${d(now.requests, was?.requests)}, ${Math.round(now.bytes / 1024)} КБ${was ? ` (было ${Math.round(was.bytes / 1024)})` : ""}`);
    for (const [k, v] of Object.entries(now.by)) {
      const w = was?.by?.[k];
      console.log(`   ${k.padEnd(10)} ${String(v.requests).padStart(3)} запр. ${String(Math.round(v.bytes / 1024)).padStart(6)} КБ`
        + (w ? `   (было ${w.requests} / ${Math.round(w.bytes / 1024)} КБ)` : ""));
    }
  }
  if (SAVE) console.log(`firstload saved: ${BASE}`);
})().catch(e => { console.error(e); process.exit(2); });
