/*
 * Сценарий QA «realtime» — вкладка не опрашивает /health: состояние сервера приходит по живому соединению.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, STATUS, check, settle } = require("../lib.cjs");

module.exports = {
  // The tab no longer asks /health every 30 seconds: the server status comes over the live connection
  async realtime(browser) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: "ru-RU" });
    const page = await ctx.newPage();
    const health = [];
    page.on("request", r => { if (new URL(r.url()).pathname === "/health") health.push(r.url()); });
    await page.routeWebSocket(/\/ws$/, ws => {
      ws.onMessage(m => { if (JSON.parse(String(m)).type === "ping") ws.send(JSON.stringify({ type: "pong" })); });
      ws.send(JSON.stringify({ type: "hello", user_id: null, status: STATUS }));
    });
    await page.clock.install();
    await page.goto(SITE + "/catalog", { waitUntil: "domcontentloaded" }); await settle(page, 1500);
    const atBoot = health.length;
    await page.clock.runFor(95_000);
    await page.waitForTimeout(500);
    await check(page, "realtime: one /health at boot and no polling after", atBoot === 1 && health.length === 1, `${atBoot} → ${health.length}`);
    await ctx.close();
  },
};
