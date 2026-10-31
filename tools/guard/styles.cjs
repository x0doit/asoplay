/*
 * Сторож рефакторинга: вычисленные стили страниц не меняются.
 *
 * Автор: Чепела Даниил Максимович.
 *
 * Снимает getComputedStyle видимых элементов ключевых страниц на компьютере, планшете, телефоне и телефоне боком
 * (светлая тема везде, тёмная — на компьютере и телефоне). Чтобы сравнение не дрожало от живых данных:
 *  - ответы API пишутся в HAR при --save (свой файл на каждый вид: в одном контексте все страницы вида) и дальше
 *    проигрываются из него — и база, и сравнение снимаются в одном режиме проигрывания;
 *  - время зафиксировано, картинки не грузятся, /ws отвечает одним и тем же, анимации и переходы выключены.
 * Тема (CSS, JS, шрифты) всегда берётся с сервера — это и проверяется.
 *
 *   node tools/guard/styles.cjs --save   записать ответы API и базу (tools/guard/baseline/styles/)
 *   node tools/guard/styles.cjs          сравнить; выход 1 — если у какого-нибудь элемента изменился стиль
 */
const fs = require("fs");
const path = require("path");
const zlib = require("zlib");
const { launch } = require("./browser.cjs");

const SITE = process.env.SITE || "http://127.0.0.1:8787";
const DIR = path.join(__dirname, "baseline", "styles");
const SAVE = process.argv.includes("--save");
const NOW = Date.parse("2026-10-30T12:00:00Z");
const API = /\/(proxy|shiki|src|health|auth|account|profile|random|reports|title-pages|sources)(\/|\?|$)|graphql\.anilist\.co/;
const PIXEL = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=", "base64");
const STILL = "*,*::before,*::after{transition-duration:0s!important;transition-delay:0s!important;"
  + "animation-duration:0s!important;animation-delay:0s!important;caret-color:transparent!important}";
const PAGES = ["/", "/catalog", "/top", "/anime/52991", "/login"];
const VIEWS = [
  { tag: "desktop", width: 1440, height: 900 },
  { tag: "tablet", width: 768, height: 1024, isMobile: true, hasTouch: true },
  { tag: "phone", width: 390, height: 844, isMobile: true, hasTouch: true },
  { tag: "landscape", width: 844, height: 390, isMobile: true, hasTouch: true },
  { tag: "desktop-dark", width: 1440, height: 900, dark: true },
  { tag: "phone-dark", width: 390, height: 844, isMobile: true, hasTouch: true, dark: true },
];
const PROPS = ["display", "position", "top", "right", "bottom", "left", "width", "height", "min-width", "max-width",
  "min-height", "max-height", "margin-top", "margin-right", "margin-bottom", "margin-left", "padding-top", "padding-right",
  "padding-bottom", "padding-left", "border-top-width", "border-right-width", "border-bottom-width", "border-left-width",
  "border-top-color", "border-bottom-color", "border-radius", "color", "background-color", "background-image",
  "font-family", "font-size", "font-weight", "line-height", "letter-spacing", "text-align", "text-transform",
  "text-decoration-line", "white-space", "overflow-x", "overflow-y", "opacity", "visibility", "z-index", "transform",
  "box-shadow", "flex-direction", "flex-wrap", "flex-grow", "flex-shrink", "flex-basis", "justify-content",
  "align-items", "gap", "grid-template-columns", "grid-template-rows", "order", "cursor", "pointer-events"];

/** Стили всех видимых элементов страницы: путь элемента → значения свойств. */
async function capture(page) {
  return page.evaluate((props) => {
    const pathOf = el => {
      const parts = [];
      for (let node = el; node && node.nodeType === 1 && node !== document.documentElement; node = node.parentElement) {
        let i = 1;
        for (let s = node.previousElementSibling; s; s = s.previousElementSibling) if (s.tagName === node.tagName) i++;
        parts.unshift(`${node.tagName.toLowerCase()}${node.id ? "#" + node.id : ""}:${i}`);
      }
      return parts.join(">");
    };
    const out = {};
    for (const el of [document.documentElement, ...document.body.querySelectorAll("*")]) {
      if (el.tagName === "STYLE" && el.dataset.guard) continue;
      const box = el.getBoundingClientRect();
      if (el !== document.documentElement && (!box.width || !box.height)) continue;
      const cs = getComputedStyle(el);
      out[pathOf(el) || "html"] = props.map(p => cs.getPropertyValue(p)).join("|");
    }
    return out;
  }, PROPS);
}

/** Контекст одного вида: ответы API из своего HAR (или запись в него), без картинок и анимаций. */
async function viewContext(browser, view, record) {
  const ctx = await browser.newContext({ viewport: { width: view.width, height: view.height }, isMobile: !!view.isMobile,
    hasTouch: !!view.hasTouch, colorScheme: view.dark ? "dark" : "light", locale: "ru-RU", deviceScaleFactor: 1,
    reducedMotion: "reduce" });
  await ctx.routeFromHAR(path.join(DIR, `api-${view.tag}.har`), { url: API, update: record, notFound: record ? "fallback" : "abort" });
  // Вместо картинок — одна и та же прозрачная PNG 1×1: без сети и без onerror, который прячет карточку. По типу
  // запроса, а не по адресу: постеры MAL идут как /proxy/img?url=…jpg%3Fs%3D<хеш> и расширением не кончаются
  await ctx.route("**/*", route => route.request().resourceType() === "image"
    ? route.fulfill({ status: 200, contentType: "image/png", body: PIXEL }) : route.fallback());
  await ctx.addInitScript(css => document.addEventListener("DOMContentLoaded", () => {
    const style = document.createElement("style");
    style.dataset.guard = "1";
    style.textContent = css;
    document.head.appendChild(style);
  }), STILL);
  return ctx;
}

/** Одна страница: зафиксировать время и /ws, открыть, дождаться, снять стили. */
async function shot(ctx, url) {
  const page = await ctx.newPage();
  await page.clock.install({ time: NOW });
  await page.routeWebSocket(/\/ws$/, ws => {
    ws.onMessage(m => { if (JSON.parse(String(m)).type === "ping") ws.send(JSON.stringify({ type: "pong" })); });
    ws.send(JSON.stringify({ type: "hello", user_id: null, status: { ok: true, sources: ["yummy_anime"], vpn: false } }));
  });
  await page.goto(SITE + url, { waitUntil: "domcontentloaded" });
  await page.waitForLoadState("networkidle", { timeout: 15000 }).catch(() => {});
  await page.clock.runFor(4000);
  await page.waitForTimeout(800);
  // Картинки обрываются, а их onerror прячет карточку: ждём, пока каждая начатая картинка завершится
  await page.waitForFunction(() => [...document.images].every(img => !img.currentSrc || img.complete), null,
    { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(200);
  const styles = await capture(page);
  await page.close();
  return styles;
}

/** Первые различия двух снимков: какой элемент, какое свойство, было → стало. */
function diff(before, after) {
  const out = [];
  for (const key of new Set([...Object.keys(before), ...Object.keys(after)])) {
    if (before[key] === after[key]) continue;
    if (!(key in before) || !(key in after)) { out.push(`${key}: ${key in before ? "исчез" : "появился"}`); continue; }
    const a = before[key].split("|"), b = after[key].split("|");
    PROPS.forEach((p, i) => { if (a[i] !== b[i]) out.push(`${key} ${p}: ${a[i]} → ${b[i]}`); });
  }
  return out;
}

(async () => {
  if (!SAVE && !(fs.existsSync(DIR) && fs.readdirSync(DIR).some(f => f.endsWith(".json.gz")))) {
    console.log("styles: нет базы tools/guard/baseline/styles — снимите её через --save на коде до шага");
    process.exit(1);
  }
  fs.mkdirSync(DIR, { recursive: true });
  const browser = await launch();
  if (SAVE) {
    for (const view of VIEWS) {
      const ctx = await viewContext(browser, view, true);
      for (const url of PAGES) await shot(ctx, url);
      await ctx.close();
    }
  }
  let changed = 0, total = 0;
  for (const view of VIEWS) {
    const ctx = await viewContext(browser, view, false);
    for (const url of PAGES) {
      const name = `${view.tag}${url.replace(/[^\w]+/g, "_")}.json.gz`;
      const styles = await shot(ctx, url);
      total++;
      if (SAVE) { fs.writeFileSync(path.join(DIR, name), zlib.gzipSync(JSON.stringify(styles))); continue; }
      const before = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(DIR, name))));
      const lines = diff(before, styles);
      if (lines.length) {
        changed++;
        console.log(`== ${view.tag} ${url}: ${lines.length} differences`);
        console.log(lines.slice(0, 12).join("\n"));
      }
    }
    await ctx.close();
  }
  await browser.close();
  console.log(SAVE ? `styles saved: ${total} snapshots` : `styles: ${total} snapshots, ${changed} changed`);
  process.exit(changed ? 1 : 0);
})().catch(e => { console.error(e); process.exit(2); });
