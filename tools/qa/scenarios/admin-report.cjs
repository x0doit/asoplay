/*
 * Сценарий QA «admin-report» — жалоба «Не работает»: причины, лимит, отправка, закрытие окна.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, accountMocks, check, shot, overflowing, settle } = require("../lib.cjs");

module.exports = {
  async adminReport(browser) {
    // «Не работает» от вошедшего: окно — как жалоба на сайте соцсети, жалоба уходит с тем, что в плеере
    const state = { calls: [] };
    const sent = [];
    let reportStatus = 200;
    const setup = async (page) => {
      await accountMocks(state)(page);
      await page.route(/\/src\/search\?/, route => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
      await page.route(/\/reports$/, route => {
        if (route.request().method() !== "POST") return route.fallback();
        sent.push(JSON.parse(route.request().postData() || "{}"));
        return route.fulfill({ status: reportStatus, contentType: "application/json",
          headers: reportStatus === 429 ? { "Retry-After": "1200" } : {},
          body: JSON.stringify(reportStatus === 200 ? { ok: true, id: 1, repeated: false } : { detail: "rate_limited" }) });
      });
    };
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "light", locale: "ru-RU" });
    const page = await ctx.newPage();
    const errors = []; page.on("pageerror", e => errors.push(String(e.message).slice(0, 200)));
    await setup(page);
    await page.goto(SITE + "/anime/52991", { waitUntil: "domcontentloaded" }); await settle(page, 3500);
    await page.click("#avReportBtn"); await page.waitForTimeout(600);
    const reasons = await page.$$eval(".report-box__reason .ed-check__text", els => els.map(e => e.textContent.trim()));
    await check(page, "report: the reasons of the dialog, the first one chosen",
      reasons.length === 6 && reasons[0] === "Видео не запускается" && await page.isChecked('.report-box__reason input[value="no_video"]'), reasons.join("|"));
    await page.click('.report-box__reason:has(input[value="no_sound"])');
    await page.fill("#avReportText", "Звук отстаёт на две секунды");
    await shot(page, "report-dialog");
    reportStatus = 429;
    await page.click("#avReportSend"); await page.waitForTimeout(400);
    await check(page, "report: the hourly limit is explained", /Слишком много жалоб/.test(await page.textContent("#avReportNote")));
    reportStatus = 200;
    await page.click("#avReportSend"); await page.waitForTimeout(500);
    const body = sent[sent.length - 1] || {};
    await check(page, "report: sent with the title, the reason and the comment",
      body.mal_id === 52991 && body.kind === "no_sound" && body.text === "Звук отстаёт на две секунды", JSON.stringify(body));
    await check(page, "report: the dialog thanks and closes", /Жалоба отправлена/.test(await page.textContent(".report-overlay")));
    await shot(page, "report-sent");
    await page.click('.report-overlay .ed-btn[data-dialog-close]'); await page.waitForTimeout(300);
    await check(page, "report: closed", !(await page.$(".report-overlay")));
    await check(page, "report: no page errors", errors.length === 0, errors.join(" | "));
    await ctx.close();

    const phoneCtx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: "ru-RU" });
    const phone = await phoneCtx.newPage();
    await setup(phone);
    await phone.goto(SITE + "/anime/52991", { waitUntil: "domcontentloaded" }); await settle(phone, 3500);
    const over = await overflowing(phone);
    const btn = await phone.$eval("#avReportBtn", el => { const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; });
    await check(phone, "report phone: the button gets its own row, nothing sticks out",
      over.scrollWidth <= over.vw && btn.w > 300 && btn.h >= 32, `${JSON.stringify(btn)} ${over.items.join(", ")}`);
    await phone.click("#avReportBtn"); await phone.waitForTimeout(600);
    await shot(phone, "report-dialog-phone");
    await check(phone, "report phone: the dialog opens as a sheet", await phone.isVisible(".report-overlay .report-box"));
    await phoneCtx.close();
  },
};
