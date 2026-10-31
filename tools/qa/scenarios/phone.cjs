/*
 * Сценарий QA «phone» — телефон в обеих темах: шапка, свайпы, поиск, меню, плеер и списки.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, accountMocks, check, shot, overflowing, settle, watchConsole } = require("../lib.cjs");

module.exports = {
  async phone(browser) {
    const state = { calls: [] };
    for (const theme of ["light", "dark"]) {
      const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, colorScheme: theme, locale: "ru-RU" });
      const page = await ctx.newPage();
      const errors = []; page.on("pageerror", e => errors.push(String(e.message).slice(0, 200)));
      watchConsole(page, errors, `phone ${theme}`);
      const swipeDown = async (sel) => {
        const box = await (await page.$(sel)).boundingBox();
        const cdp = await ctx.newCDPSession(page);
        const x = box.x + box.width / 2, y0 = box.y + 24;
        await cdp.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y: y0 }] });
        for (let i = 1; i <= 14; i++) { await cdp.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: y0 + i * 28 }] }); await page.waitForTimeout(16); }
        await cdp.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        await page.waitForTimeout(700);
      };

      await page.goto(SITE + "/", { waitUntil: "domcontentloaded" }); await settle(page, 2500);
      await check(page, `phone ${theme}: header is fixed`, (await page.$eval(".app-header", el => getComputedStyle(el).position)) === "fixed");
      await check(page, `phone ${theme}: page does not zoom`, (await page.$eval("html", el => getComputedStyle(el).touchAction)) === "pan-x pan-y");
      const heroTitle = () => page.textContent(".hero-slide.active .hero-title");
      const t0 = await heroTitle();
      const hb = await (await page.$(".hero-carousel .hero-bg")).boundingBox();
      const cdpHero = await ctx.newCDPSession(page);
      const hx = hb.x + hb.width * 0.8, hy = hb.y + hb.height / 2;
      await cdpHero.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x: hx, y: hy }] });
      for (let i = 1; i <= 12; i++) { await cdpHero.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x: hx - i * 20, y: hy + i }] }); await page.waitForTimeout(16); }
      await cdpHero.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
      await page.waitForTimeout(500);
      await check(page, `phone ${theme}: a swipe turns the carousel`, (await heroTitle()) !== t0);
      const heroHeights = [];
      for (let i = 0; i < 5; i++) {
        heroHeights.push(await page.$eval("#avHero", el => Math.round(el.getBoundingClientRect().height)));
        await page.tap(".hero-nav.next"); await page.waitForTimeout(150);
      }
      await check(page, `phone ${theme}: carousel height does not jump between slides`, new Set(heroHeights).size === 1, heroHeights.join(","));
      await page.tap("#appSearchToggle"); await page.waitForTimeout(300);
      await check(page, `phone ${theme}: search opens under the header`, await page.isVisible("#appSearch"));
      await page.fill("#appSearchInput", "ван пис"); await page.press("#appSearchInput", "Enter"); await settle(page, 2500);
      await check(page, `phone ${theme}: search submits to the catalog and closes`, /\/catalog\?q=/.test(page.url()) && !(await page.isVisible("#appSearch")));
      const cols = await page.$eval("#g", el => getComputedStyle(el).gridTemplateColumns.split(" ").length).catch(() => 0);
      await check(page, `phone ${theme}: catalog in two columns`, cols === 2, `${cols}`);
      await shot(page, `phone-${theme}-search`);
      await page.tap(".app-tabbar [data-sheet-open]"); await page.waitForTimeout(400);
      await page.tap('#appSheet a[data-nav="top"]'); await settle(page, 2000);
      await check(page, `phone ${theme}: sheet link navigates and closes the sheet`, /\/top/.test(page.url()) && !(await page.isVisible("#appSheet .app-sheet__panel")));
      const o1 = await overflowing(page);
      await check(page, `phone ${theme}: top page fits`, !o1.items.length && o1.scrollWidth <= 390, JSON.stringify(o1.items));

      await page.goto(SITE + "/anime/52991", { waitUntil: "domcontentloaded" }); await settle(page, 6000);
      await check(page, `phone ${theme}: trailer button instead of preview`, await page.isVisible(".title-actions .cinema-trailer-action") && !(await page.isVisible(".cinema-trailer")));
      await shot(page, `phone-${theme}-title-top`);
      await page.tap("#avCtaPlay"); await page.waitForTimeout(1200);
      const top = await page.$eval("#avPlayerCard", el => el.getBoundingClientRect().top);
      await check(page, `phone ${theme}: «Смотреть» keeps the player below the header`, top >= 47, `top ${Math.round(top)}`);
      const epsMax = await page.$eval("#avEpisodesList", el => Math.round(parseFloat(getComputedStyle(el).maxHeight)));
      await check(page, `phone ${theme}: episode list stays under half the screen`, epsMax <= Math.round(844 * 0.52) + 1, `max-height ${epsMax}`);
      await shot(page, `phone-${theme}-player`);
      await page.tap("#avDubTrigger"); await page.waitForTimeout(500);
      await check(page, `phone ${theme}: dub sheet opens`, await page.isVisible(".sp-dub-panel"));
      await shot(page, `phone-${theme}-dub-sheet`);
      await swipeDown(".sp-dub-panel");
      await check(page, `phone ${theme}: dub sheet swipes away`, !(await page.isVisible("#avDubPop")));
      await page.tap("#avFavBtn"); await page.waitForTimeout(500);
      await check(page, `phone ${theme}: gate is a bottom sheet`, await page.isVisible(".gate-overlay .av-dialog__grip"));
      await shot(page, `phone-${theme}-gate`);
      await swipeDown(".gate-overlay .av-dialog__panel");
      await check(page, `phone ${theme}: gate swipes away`, !(await page.$(".gate-overlay")));
      const o2 = await overflowing(page);
      await check(page, `phone ${theme}: title page fits`, !o2.items.length && o2.scrollWidth <= 390, JSON.stringify(o2.items));

      await accountMocks(state)(page);
      await page.goto(SITE + "/my/lists", { waitUntil: "domcontentloaded" }); await settle(page, 1500);
      const btns = await page.$$(".card-action-menu");
      if (btns[0]) { await btns[0].tap(); await page.waitForTimeout(300); }
      const r = await page.$eval(".card-action-pop:not([hidden])", el => { const b = el.getBoundingClientRect(); return [b.left, b.right]; }).catch(() => [0, 0]);
      await check(page, `phone ${theme}: card menu inside the screen`, r[0] >= 7 && r[1] <= 383, JSON.stringify(r.map(Math.round)));
      await shot(page, `phone-${theme}-lists-menu`);
      await page.goto(SITE + "/@qa-viewer", { waitUntil: "domcontentloaded" }); await settle(page, 2500);
      const o3 = await overflowing(page);
      await check(page, `phone ${theme}: profile fits`, !o3.items.length && o3.scrollWidth <= 390, JSON.stringify(o3.items));
      await shot(page, `phone-${theme}-profile`, true);
      await page.goto(SITE + "/my/settings", { waitUntil: "domcontentloaded" }); await settle(page, 1000);
      await shot(page, `phone-${theme}-settings`, true);
      await check(page, `phone ${theme}: no JS errors`, !errors.length, errors.join(" | "));
      await ctx.close();
    }
  },
};
