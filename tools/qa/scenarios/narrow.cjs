/*
 * Сценарий QA «narrow» — узкие экраны: ничего не вылезает за край.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, check, shot, overflowing, settle } = require("../lib.cjs");

module.exports = {
  async narrow(browser) {
    for (const w of [360, 320]) {
      const ctx = await browser.newContext({ viewport: { width: w, height: 740 }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, colorScheme: "light", locale: "ru-RU" });
      const page = await ctx.newPage();
      for (const url of ["/", "/catalog", "/anime/52991", "/login"]) {
        await page.goto(SITE + url, { waitUntil: "domcontentloaded" }); await settle(page, url.includes("anime") ? 6000 : 2000);
        const o = await overflowing(page);
        await check(page, `${w}px ${url}: nothing sticks out`, !o.items.length && o.scrollWidth <= w, JSON.stringify(o.items));
        await shot(page, `w${w}-${url.replace(/\W+/g, "_") || "home"}`);
      }
      await ctx.close();
    }
  },
};
