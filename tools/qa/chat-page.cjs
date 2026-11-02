/*
 * QA чата серии, страница: открыть тайтл с подменами /comments и /ws, найти кадр плеера с <video>, играть и
 * перематывать настоящее видео, ждать условие, поставить колонки вровень для снимка.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, USER, accountMocks, settle, liveSocket } = require("./lib.cjs");
const { chatRoute, CHAT_ROUTE } = require("./chat-mock.cjs");

// Кадр плеера, где есть <video>
async function videoFrame(page) {
  for (let tries = 0; tries < 4; tries++) {
    for (const f of page.frames()) if (await f.evaluate(() => !!document.querySelector("video")).catch(() => false)) return f;
    await page.waitForTimeout(3000);
  }
  return null;
}

// Видео без звука с минуты t; play=false — на паузе
const playAt = (frame, t, play = true) => frame?.evaluate(async ([at, go]) => {
  const v = document.querySelector("video");
  v.muted = true;
  v.currentTime = at;
  if (go) await v.play().catch(() => {}); else v.pause();
}, [t, play]);
const pause = frame => frame?.evaluate(() => document.querySelector("video").pause());
const videoTime = async frame => (frame ? frame.evaluate(() => document.querySelector("video").currentTime) : -1);
const until = (page, fn, arg, timeout = 15000) => page.waitForFunction(fn, arg, { timeout }).then(() => true, () => false);

// Страница тайтла с подменами; signed — вошедший зритель qa-viewer; path — тайтл (по умолчанию «Фрирен»)
async function openTitle(browser, { width = 1600, height = 900, phone = false, signed = true, db, path = "/anime/52991", scheme = "light" }) {
  const ctx = await browser.newContext({ viewport: { width, height }, locale: "ru-RU", colorScheme: scheme,
    isMobile: phone, hasTouch: phone, deviceScaleFactor: phone ? 2 : 1 });
  const page = await ctx.newPage();
  const errors = [];
  page.on("pageerror", e => errors.push(String(e.message).slice(0, 160)));
  if (signed) await accountMocks({ calls: [] })(page);
  const live = await liveSocket(page, signed ? USER.id : null);
  await page.route(CHAT_ROUTE, chatRoute(db));
  await page.goto(SITE + path, { waitUntil: "domcontentloaded" });
  await settle(page, 8000);
  await page.waitForSelector(".sp-chat__btn", { timeout: 20000 }).catch(() => {});
  return { ctx, page, live, errors, frame: await videoFrame(page) };
}

// Обе колонки вровень для снимка: верх колонки плеера — там, где прилипает колонка чата (шапка + 24 px)
const frameColumns = page => page.evaluate(() => {
  const head = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--header-h")) || 64;
  window.scrollBy({ top: document.querySelector(".watch-main").getBoundingClientRect().top - head - 24, behavior: "instant" });
});
const stripTexts = page => page.$$eval(".chat-strip.is-in", list => list.map(s => s.textContent));
const fullscreenOf = page => page.evaluate(() => {
  const el = document.fullscreenElement;
  return el ? { cls: el.className, strips: el.querySelectorAll(".chat-strip.is-in").length } : null;
});

module.exports = { videoFrame, playAt, pause, videoTime, until, openTitle, frameColumns, stripTexts, fullscreenOf };
