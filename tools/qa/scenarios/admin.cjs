/*
 * Сценарий QA «admin» — решения панели Anime Social на сайте: плашка, «Рекомендуем», источники, скрытый и 18+ тайтл.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, STATUS, check, shot, settle } = require("../lib.cjs");

module.exports = {
  async admin(browser) {
    // Решения панели Anime Social на сайте: плашка, «Рекомендуем» в карусели, порядок источников, скрытый и 18+ тайтл,
    // способы входа. Состояние сайта приходит, как его пришлёт сервер: в /health и по живому соединению
    const site = { notice: { text: "Сегодня ночью профилактика — видео может прерываться", until: Math.floor(Date.now() / 1000) + 3600 },
                   hidden: [], adult: [], pinned: [52991], login: { social: true, password: true }, sources: ["anilibria", "yummy_anime"] };
    const status = () => ({ ok: true, sources: ["anilibria", "yummy_anime"], vpn: STATUS.vpn, site });
    const live = { sockets: [] };
    const searches = [];
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "light", locale: "ru-RU" });
    const page = await ctx.newPage();
    const errors = []; page.on("pageerror", e => errors.push(String(e.message).slice(0, 200)));
    await page.route(/\/health(\?|$)/, route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(status()) }));
    await page.route(/\/src\/search\?/, route => {
      searches.push(new URL(route.request().url()).searchParams.get("source"));
      return route.fulfill({ status: 200, contentType: "application/json", body: "[]" });
    });
    await page.routeWebSocket(/\/ws$/, ws => {
      live.sockets.push(ws);
      ws.onMessage(m => { if (JSON.parse(String(m)).type === "ping") ws.send(JSON.stringify({ type: "pong" })); });
      ws.onClose(() => { live.sockets = live.sockets.filter(x => x !== ws); });
      ws.send(JSON.stringify({ type: "hello", user_id: null, status: status() }));
    });
    const push = async (change) => {
      Object.assign(site, change);
      for (const ws of live.sockets) ws.send(JSON.stringify({ type: "status", status: status() }));
      await page.waitForTimeout(500);
    };

    await page.goto(SITE + "/", { waitUntil: "domcontentloaded" }); await settle(page, 3000);
    await check(page, "admin: the notice from the panel is on the page",
      await page.isVisible("#avNotice") && /профилактика/.test(await page.textContent("#avNotice")));
    const badge = (await page.textContent("#avHero .hero-slide.active .hero-badge").catch(() => "")).trim();
    await check(page, "admin: a pinned title opens the carousel as «Рекомендуем»", badge === "Рекомендуем", badge);
    await shot(page, "admin-home-notice-pinned");
    await page.click("#avNotice .site-notice__close"); await page.waitForTimeout(200);
    await check(page, "admin: the notice can be closed", await page.isHidden("#avNotice"));
    await page.reload({ waitUntil: "domcontentloaded" }); await settle(page, 1500);
    await check(page, "admin: a closed notice stays closed", await page.isHidden("#avNotice"));
    await push({ notice: { text: "Новое объявление", until: null } });
    await check(page, "admin: a new notice arrives at once over the live connection",
      await page.isVisible("#avNotice") && /Новое объявление/.test(await page.textContent("#avNotice")));
    await push({ notice: null });
    await check(page, "admin: and goes away the same way", await page.isHidden("#avNotice"));

    await page.goto(SITE + "/anime/52991", { waitUntil: "domcontentloaded" }); await settle(page, 3000);
    await check(page, "admin: the player tries sources in the panel's order", searches[0] === "anilibria", searches.slice(0, 4).join(","));
    await page.click("#avReportBtn"); await page.waitForTimeout(300);
    await check(page, "admin: a guest is asked to sign in before reporting",
      /Жалобы — для вошедших/.test(await page.textContent(".gate-overlay").catch(() => "")));
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);

    await push({ hidden: [52991] });
    await check(page, "admin: hiding the open title replaces the player at once",
      /Видео недоступно/.test(await page.textContent("#avPlayerOverlay")) && !(await page.$("#avPlayerOverlay .unavail-ddg")));
    await shot(page, "admin-title-hidden");
    await push({ hidden: [] });
    await page.waitForTimeout(800);
    await check(page, "admin: the player comes back when the title is shown again", !/Видео недоступно/.test(await page.textContent("#avPlayerOverlay")));

    await push({ adult: [52991] });
    await check(page, "admin: an 18+ title asks before the video", await page.isVisible(".adult-gate") && await page.isVisible(".title-adult"));
    await shot(page, "admin-title-adult");
    const before = searches.length;
    await page.click("[data-adult-yes]"); await page.waitForTimeout(1000);
    await check(page, "admin: «Мне есть 18» starts the search and is remembered",
      searches.length > before && await page.evaluate(() => localStorage.getItem("av_adult_ok")) === "1", `${before} → ${searches.length}`);
    await page.reload({ waitUntil: "domcontentloaded" }); await settle(page, 2000);
    await check(page, "admin: the question is not asked again", !(await page.isVisible(".adult-gate")) && await page.isVisible(".title-adult"));
    await push({ adult: [], pinned: [] });

    await push({ login: { social: false, password: true } });
    await page.goto(SITE + "/login", { waitUntil: "domcontentloaded" }); await settle(page, 1500);
    await check(page, "admin: with AnimeSocial sign-in off only the mail form is shown",
      await page.isVisible("#avLoginForm") && !(await page.$(".auth-as")) && !(await page.$('[data-auth-switch="as"]')));
    await push({ login: { social: true, password: false } });
    await page.waitForTimeout(1000);
    await check(page, "admin: switching mail off redraws the login page at once",
      await page.isVisible(".auth-as") && !(await page.$("#avLoginForm")) && !(await page.$('[data-auth-switch="mail"]')));
    await shot(page, "admin-login-social-only");
    await push({ login: { social: true, password: true } });
    await check(page, "admin: no page errors", errors.length === 0, errors.join(" | "));
    await ctx.close();
  },
};
