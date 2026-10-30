/*
 * Сценарий QA «landscape» — телефон боком: плеер с шапкой помещается между шапкой сайта и нижней панелью.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, check, shot, settle } = require("../lib.cjs");

module.exports = {
  // A phone turned sideways: the player with its head fits between the site header and the tab bar.
  async landscape(browser) {
    for (const vp of [{ width: 844, height: 390 }, { width: 740, height: 360 }]) {
      const ctx = await browser.newContext({ viewport: vp, isMobile: true, hasTouch: true, deviceScaleFactor: 2, colorScheme: "dark", locale: "ru-RU" });
      const page = await ctx.newPage();
      await page.goto(SITE + "/anime/52991", { waitUntil: "domcontentloaded" }); await settle(page, 6000);
      await page.tap("#avCtaPlay"); await page.waitForTimeout(1500);
      const m = await page.evaluate(() => {
        const r = s => document.querySelector(s).getBoundingClientRect();
        const tab = document.querySelector(".app-tabbar");
        const limit = tab && getComputedStyle(tab).display !== "none" ? tab.getBoundingClientRect().top : innerHeight;
        return { header: Math.round(r(".app-header").bottom), head: Math.round(r(".sp-head").top), video: Math.round(r(".sp-video").bottom),
          limit: Math.round(limit), toTop: getComputedStyle(document.querySelector("#toTop")).display };
      });
      await check(page, `landscape ${vp.width}: player head and video fit between the bars`, m.head >= m.header - 1 && m.video <= m.limit + 1, JSON.stringify(m));
      await check(page, `landscape ${vp.width}: back-to-top button stays off the video`, m.toTop === "none");
      await shot(page, `landscape-${vp.width}-player`);
      await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(300);
      await page.tap(".title-actions .cinema-trailer-action"); await page.waitForTimeout(900);
      const tv = await page.evaluate(() => {
        const v = document.querySelector(".trailer-video")?.getBoundingClientRect();
        return v ? { top: Math.round(v.top), bottom: Math.round(v.bottom), vh: innerHeight } : null;
      });
      await check(page, `landscape ${vp.width}: trailer video fits the screen`, !!tv && tv.top >= 0 && tv.bottom <= tv.vh, JSON.stringify(tv));
      await shot(page, `landscape-${vp.width}-trailer`);
      await ctx.close();
    }
  },
};
