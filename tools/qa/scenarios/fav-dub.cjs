/*
 * Сценарий QA «favDub» — «Любимая озвучка». Настройки: «Не выбрана», выбор с поиском (список подменён: «Вы выбирали»
 * по выборам на тайтлах, чужое имя с разметкой — только текстом), «Ничего не нашлось», сохранение PUT
 * /account/settings, «Сбросить», ошибка списка с «Повторить», ошибка сохранения; тёмная тема; телефон 390 px — нижняя
 * шторка, список листается пальцем. Плеер на «Фрирен»: озвучки серии подменены именами поверх настоящего ключа —
 * играет любимая, ручной выбор на тайтле важнее, без любимой — первая озвучка; свой плеер любимой ищется первым и один.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, accountMocks, check, shot, overflowing, settle, watchConsole } = require("../lib.cjs");

const XSS = "<img src=x onerror=alert(1)>";
const NAMES = ["AniLibria", "AniDub", "JAM", "Jam Club", "SHIZA Project", "Studio Band", "Dream Cast", XSS,
  "Очень длинное название озвучки, которое не помещается в одну строку даже на широком экране",
  ...Array.from({ length: 40 }, (_, i) => `Команда ${i + 1}`)];

// Страница с подменами аккаунта: любимая (state.favorite), выборы на тайтлах (saved), список имён и его сбои;
// setup — свои подмены и слушатели до перехода
async function open(browser, path, { width = 1440, height = 900, phone = false, scheme = "light", favorite = "",
  saved = { 52991: "anidub", 1535: "sub:crunchyroll" }, failNames = 0, failSave = false, setup = null } = {}) {
  const ctx = await browser.newContext({ viewport: { width, height }, locale: "ru-RU", colorScheme: scheme,
    isMobile: phone, hasTouch: phone, deviceScaleFactor: phone ? 2 : 1 });
  const page = await ctx.newPage();
  const errors = [];
  watchConsole(page, errors, "favDub");
  page.on("pageerror", e => errors.push(String(e.message).slice(0, 160)));
  const state = { calls: [], saved: [], favorite, asked: 0, searches: [] };
  await accountMocks(state)(page);
  // Поверх подмен аккаунта (маршрут, заданный позже, отвечает первым)
  await page.route(/\/account\/(settings|dub-prefs)(\?|$)/, route => {
    const req = route.request();
    if (req.url().includes("dub-prefs")) return route.fulfill({ json: saved });
    if (req.method() !== "PUT") return route.fulfill({ json: { autonext: true, auto_add_lists: true, extra: {}, favorite_dub: state.favorite } });
    const body = JSON.parse(req.postData() || "{}");
    state.saved.push(body);
    if (failSave) return route.fulfill({ status: 500, body: "fail" });
    if ("favorite_dub" in body) state.favorite = body.favorite_dub;
    return route.fulfill({ json: { ok: true } });
  });
  await page.route(/\/src\/dub-names(\?|$)/, route => (++state.asked <= failNames
    ? route.fulfill({ status: 500, body: "fail" }) : route.fulfill({ json: NAMES })));
  await setup?.(page, state);
  await page.goto(SITE + path, { waitUntil: "domcontentloaded" });
  return { ctx, page, state, errors };
}
const ready = page => page.waitForSelector("#avFavDubPick", { timeout: 15000 }).then(() => settle(page, 300), () => {});
const pick = (page, name) => page.locator(".fav-dub__name", { hasText: new RegExp(`^${name}$`) }).first().click();

const row = page => page.evaluate(() => ({ value: document.querySelector("#avFavDubValue")?.textContent || null,
  reset: !document.querySelector("#avFavDubReset")?.hidden, note: document.querySelector("#avFavDubNote")?.textContent || "" }));
const picker = page => page.evaluate(() => {
  const list = document.querySelector(".fav-dub__list");
  if (!list) return null;
  const names = sel => [...list.querySelectorAll(sel)].map(n => n.textContent);
  const panel = document.querySelector(".fav-dub-overlay .av-dialog__panel").getBoundingClientRect();
  return { groups: names(".fav-dub__group-title"), items: names(".fav-dub__name"), active: names(".fav-dub__item.is-active .fav-dub__name"),
    imgs: list.querySelectorAll("img").length, empty: list.querySelector(".ed-empty__title")?.textContent || "",
    text: list.querySelector(".ed-empty__text")?.textContent || "", retry: !!list.querySelector(".ed-empty__action"),
    scrollable: list.scrollHeight > list.clientHeight + 4, scrollTop: Math.round(list.scrollTop),
    bottom: Math.round(innerHeight - panel.bottom), width: Math.round(panel.width) };
});
const openPicker = async page => {
  await page.click("#avFavDubPick");
  await page.waitForFunction(() => document.querySelector(".fav-dub__list .fav-dub__item, .fav-dub__list .ed-empty__action")
    && !document.querySelector(".fav-dub-overlay .av-dialog__panel").getAnimations().length, null, { timeout: 8000 }).catch(() => {});
  return picker(page);
};
// Палец ведёт по списку вверх на 264 px: касания по одному, как на телефоне (жест прокрутки CDP их не шлёт)
async function swipeUp(ctx, page, selector) {
  const box = await page.locator(selector).boundingBox();
  const x = Math.round(box.x + box.width / 2), y = Math.round(box.y + box.height - 20);
  const cdp = await ctx.newCDPSession(page);
  const touch = (type, at) => cdp.send("Input.dispatchTouchEvent", { type, touchPoints: type === "touchEnd" ? [] : [{ x, y: at }] });
  await touch("touchStart", y);
  for (let k = 1; k <= 12; k++) { await touch("touchMove", y - k * 22); await page.waitForTimeout(16); }
  await touch("touchEnd", y - 264);
  await page.waitForTimeout(300);
}
const search = async (page, text) => { await page.fill(".fav-dub-overlay .ed-search__input", text); return picker(page); };
const closed = page => page.evaluate(() => !document.querySelector(".fav-dub-overlay"));

async function settings(browser) {
  let t = await open(browser, "/my/settings");
  await ready(t.page);
  const start = await row(t.page);
  const list = await openPicker(t.page);
  await shot(t.page, "favdub-picker-desktop");
  const jam = await search(t.page, "jam");
  const none = await search(t.page, "zzz");
  await search(t.page, "anilibria");
  await pick(t.page, "AniLibria");
  await t.page.waitForTimeout(300);
  const chosen = { closed: await closed(t.page), saved: t.state.saved.at(-1), row: await row(t.page) };
  const again = await openPicker(t.page);
  await t.page.keyboard.press("Escape");
  await t.page.click("#avFavDubReset");
  await t.page.waitForTimeout(300);
  const reset = { saved: t.state.saved.at(-1), row: await row(t.page) };
  await check(t.page, "favDub: the row starts «Не выбрана» with no reset; the list shows «Вы выбирали» from the titles, then all, foreign markup as text",
    start.value === "Не выбрана" && !start.reset && list?.groups.join() === "Вы выбирали,Все озвучки" && list.items[0] === "AniDub"
      && list.items.includes(XSS) && list.imgs === 0 && list.items.length === NAMES.length, JSON.stringify({ start, list: { ...list, items: list?.items.slice(0, 4) } }));
  await check(t.page, "favDub: search narrows to matches and says when nothing is found",
    jam?.items.join() === "JAM,Jam Club" && none?.empty === "Ничего не нашлось" && /появится в списке/.test(none.text), JSON.stringify({ jam: jam?.items, none }));
  await check(t.page, "favDub: a pick saves favorite_dub, closes the window, shows the name and a reset; the reset saves an empty name",
    chosen.closed && chosen.saved?.favorite_dub === "AniLibria" && chosen.row.value === "AniLibria" && chosen.row.reset
      && again?.active.join() === "AniLibria" && reset.saved?.favorite_dub === "" && reset.row.value === "Не выбрана" && !reset.row.reset,
    JSON.stringify({ chosen, again: again?.active, reset }));
  await check(t.page, "favDub: no page errors on the settings page", !t.errors.length, t.errors.join(" | "));
  await t.ctx.close();

  t = await open(browser, "/my/settings", { failNames: 1, failSave: true, scheme: "dark" });
  await ready(t.page);
  const failed = await openPicker(t.page);
  await shot(t.page, "favdub-list-error-dark");
  await t.page.click(".fav-dub__list .ed-empty__action");
  await t.page.waitForSelector(".fav-dub__item", { timeout: 8000 }).catch(() => {});
  const retried = await picker(t.page);
  await shot(t.page, "favdub-picker-dark");
  await pick(t.page, "JAM");
  await t.page.waitForTimeout(400);
  const notSaved = await row(t.page);
  await check(t.page, "favDub: a list that failed offers «Повторить» and loads on it; a failed save keeps the old value and says so",
    failed?.empty === "Не удалось загрузить список" && failed.retry && retried?.items.length === NAMES.length
      && notSaved.value === "Не выбрана" && /Не удалось сохранить/.test(notSaved.note), JSON.stringify({ failed, retried: retried?.items.length, notSaved }));
  await t.ctx.close();

  t = await open(browser, "/my/settings", { width: 390, height: 844, phone: true, favorite: NAMES[8] });
  await ready(t.page);
  const rowOverflow = await overflowing(t.page);
  await shot(t.page, "favdub-row-phone");
  const sheet = await openPicker(t.page);
  await swipeUp(t.ctx, t.page, ".fav-dub__list");
  const swiped = await picker(t.page);
  await shot(t.page, "favdub-sheet-phone");
  await check(t.page, "favDub: on a phone the long name fits the row, the window is a bottom sheet and its list scrolls under the finger",
    rowOverflow.scrollWidth <= rowOverflow.vw && !rowOverflow.items.length && sheet?.bottom === 0 && sheet.width === 390
      && sheet.scrollable && swiped?.scrollTop > 40 && !(await closed(t.page)), JSON.stringify({ rowOverflow, sheet: { bottom: sheet?.bottom, width: sheet?.width }, scrollTop: swiped?.scrollTop }));
  await t.ctx.close();
}

// Плеер: озвучки серии — подменённые имена поверх настоящего ключа первой озвучки; что выбрал плеер и в каком
// порядке он искал тайтл у источников
const DUBS = key => [{ name: "Озвучка SHIZA Project", norm: "shiza project", kind: "voice", key },
  { name: "Озвучка AniLibria.TV", norm: "anilibria tv", kind: "voice", key }, { name: "Субтитры AniLibria", norm: "anilibria", kind: "sub", key }];
async function player(browser, opts) {
  const t = await open(browser, "/anime/52991", { ...opts, setup: async (page, state) => {
    page.on("request", r => { if (r.url().includes("/src/search?")) state.searches.push({ src: new URL(r.url()).searchParams.get("source"), at: Date.now() }); });
    page.on("requestfinished", r => { if (r.url().includes("/src/search?") && r.url().includes("source=anilibria")) state.anilibriaDone = Date.now(); });
    await page.route(/\/src\/dubs\?/, async route => {
      const real = await (await route.fetch()).json().catch(() => []);
      const key = Array.isArray(real) && real[0]?.key;
      return route.fulfill({ json: key ? DUBS(key) : real });
    });
  } });
  const label = await t.page.waitForFunction(() => {
    const text = document.querySelector(".sp-dub-label")?.textContent?.trim();
    return text && text !== "Озвучка" ? text : null;
  }, null, { timeout: 60000 }).then(h => h.jsonValue(), () => null);
  const searches = t.state.searches, others = searches.filter(s => s.src !== "anilibria");
  const out = { label, searches: searches.slice(0, 4).map(s => s.src), errors: t.errors,
    anilibriaFirstAlone: searches[0]?.src === "anilibria" && (!others.length || others[0].at >= (t.state.anilibriaDone || Infinity) - 50) };
  await t.ctx.close();
  return out;
}

module.exports = {
  async favDub(browser) {
    await settings(browser);
    const fav = await player(browser, { favorite: "AniLibria", saved: {} });
    const manual = await player(browser, { favorite: "AniLibria", saved: { 52991: "shiza project" } });
    const plain = await player(browser, { favorite: "", saved: {} });
    await check(null, "favDub: the player plays the favourite, a dub chosen by hand on the title beats it, without one the first voice-over",
      fav.label === "AniLibria.TV" && manual.label === "SHIZA Project" && plain.label === "SHIZA Project", JSON.stringify({ fav: fav.label, manual: manual.label, plain: plain.label }));
    await check(null, "favDub: the favourite's own player is searched first and alone; without a favourite the panel order stays",
      fav.searches[0] === "anilibria" && fav.anilibriaFirstAlone === true && manual.searches[0] !== "anilibria" && plain.searches[0] !== "anilibria",
      JSON.stringify({ fav: [fav.searches, fav.anilibriaFirstAlone], manual: manual.searches, plain: plain.searches }));
    await check(null, "favDub: no page errors on the title page", ![...fav.errors, ...manual.errors, ...plain.errors].length,
      [...fav.errors, ...manual.errors, ...plain.errors].join(" | "));
  },
};
