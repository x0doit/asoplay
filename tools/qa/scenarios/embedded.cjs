/*
 * Сценарий QA «embedded» — AsoPlay внутри фрейма AnimeSocial: компактный режим и лист «Все разделы».
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, check, shot } = require("../lib.cjs");

module.exports = {
  // AsoPlay inside an AnimeSocial frame: compact mode and the «Все разделы» sheet.
  async embedded(browser) {
    const ctx = await browser.newContext({ viewport: { width: 1040, height: 740 }, locale: "ru-RU" });
    const page = await ctx.newPage();
    await page.setContent(`<body style="margin:20px"><iframe src="${SITE}/anime/52991" style="width:1000px;height:700px;border:0"></iframe></body>`);
    await page.waitForTimeout(7000);
    const frame = page.frames().find(f => f.url().startsWith(SITE));
    await check(page, "embedded: compact frame mode", !!frame && await frame.evaluate(() => document.documentElement.classList.contains("is-compact-frame")));
    if (frame) {
      await frame.click(".app-menu-toggle"); await frame.waitForTimeout(600);
      const w = await frame.$eval("#appSheet .app-sheet__panel", el => Math.round(el.getBoundingClientRect().width));
      await check(page, "embedded: the menu opens as a sheet no wider than 480px", w > 0 && w <= 480, `${w}`);
      await shot(page, "embedded-menu");
    }
    await ctx.close();
  },
};
