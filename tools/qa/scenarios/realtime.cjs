/*
 * Сценарий QA «realtime» — вкладка не опрашивает /health: состояние сервера приходит по живому соединению; кадр
 * «lists» (правило 30 дней) сразу перекрашивает список на странице тайтла.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, USER, STATUS, accountMocks, check, settle } = require("../lib.cjs");

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

    // Правило 30 дней перенесло тайтл, пока страница открыта: кадр «lists» по /ws — и список уже «Брошено» (авто)
    const signed = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: "ru-RU" });
    const title = await signed.newPage();
    await accountMocks({ calls: [] })(title);
    const sockets = [];
    await title.routeWebSocket(/\/ws$/, ws => {
      sockets.push(ws);
      ws.onMessage(m => { if (JSON.parse(String(m)).type === "ping") ws.send(JSON.stringify({ type: "pong" })); });
      ws.send(JSON.stringify({ type: "hello", user_id: USER.id, status: STATUS }));
    });
    await title.goto(SITE + "/anime/16498", { waitUntil: "domcontentloaded" }); await settle(title, 5000);
    const label = async () => (await title.textContent(".list-picker-label")).trim();
    const before = await label();
    const moved = { mal_id: 16498, status: "dropped", status_source: "auto", is_favorite: true, title: "Атака титанов",
      poster_url: "", added_at: "2026-09-01T12:00:00", updated_at: "2026-10-31T04:00:00" };
    for (const ws of sockets) ws.send(JSON.stringify({ type: "lists", items: [moved] }));
    await title.waitForTimeout(300);
    await check(title, "realtime: a «lists» frame repaints the title's list at once, marked auto",
      before === "Смотрю" && await label() === "Брошено" && await title.isVisible(".list-picker-auto"), `${before} → ${await label()}`);
    await signed.close();
  },
};
