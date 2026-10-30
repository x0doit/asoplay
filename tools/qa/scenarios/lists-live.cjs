/*
 * Сценарий QA «lists-live» — правило 30 дней, пока открыты «Списки» или свой профиль: кадр «lists» по /ws убирает
 * карточку из «Смотрю», приносит её в «Брошено», меняет счётчики вкладок — на месте, без экрана загрузки.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, USER, STATUS, accountMocks, check, shot, settle } = require("../lib.cjs");

module.exports = {
  async listsLive(browser) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, colorScheme: "light", locale: "ru-RU" });
    const page = await ctx.newPage();
    const errors = []; page.on("pageerror", e => errors.push(String(e.message).slice(0, 200)));
    const state = { calls: [] };
    await accountMocks(state)(page);
    const weekAgo = new Date(Date.now() - 7 * 864e5).toISOString();
    for (const [mal_id, title] of [[20, "Наруто"], [21, "Ван-Пис"]]) {
      state.lists.push({ mal_id, title, poster_url: "", status: "watching", status_source: "auto", is_favorite: false, updated_at: weekAgo });
    }
    const sockets = [];
    await page.routeWebSocket(/\/ws$/, ws => {
      sockets.push(ws);
      ws.onMessage(m => { if (JSON.parse(String(m)).type === "ping") ws.send(JSON.stringify({ type: "pong" })); });
      ws.send(JSON.stringify({ type: "hello", user_id: USER.id, status: STATUS }));
    });
    // Проход перенёс тайтл: сервер уже отдаёт его в «Брошено», а вкладки получают кадр с записью
    const move = async (mal) => {
      const entry = state.lists.find(l => l.mal_id === mal);
      Object.assign(entry, { status: "dropped", status_source: "auto", updated_at: new Date().toISOString() });
      for (const ws of sockets) ws.send(JSON.stringify({ type: "lists", items: [{ ...entry, added_at: weekAgo }] }));
      await page.waitForTimeout(800);
    };
    // Экран загрузки после кадра — это мигание, которого быть не должно
    const watchLoading = () => page.evaluate(() => {
      window.__avLoading = false;
      new MutationObserver(list => {
        for (const m of list) for (const n of m.addedNodes) {
          if (n.nodeType === 1 && (n.matches(".ed-loading") || n.querySelector(".ed-loading"))) window.__avLoading = true;
        }
      }).observe(document.body, { childList: true, subtree: true });
    });
    const sawLoading = () => page.evaluate(() => window.__avLoading);
    const badge = (tabs, status) => page.$eval(`${tabs} [data-status="${status}"]`,
      el => Number(el.querySelector(".ed-tabs__count")?.textContent || 0));
    const cards = () => page.$$eval("#avListGrid .av-card-wrap", els => els.map(e => Number(e.dataset.mal)));

    // Свой профиль на вкладке «Смотрю»
    await page.goto(SITE + "/@qa-viewer", { waitUntil: "domcontentloaded" }); await settle(page, 2000);
    const profileCards = () => page.$$eval("#avProfileList .av-card", els => els.map(e => Number(e.dataset.mal)));
    const p0 = { cards: await profileCards(), watching: await badge("#avProfileTabs", "watching"),
      dropped: await badge("#avProfileTabs", "dropped"), stats: await page.textContent(".profile-stats") };
    await watchLoading();
    await move(20);
    const p1 = { cards: await profileCards(), watching: await badge("#avProfileTabs", "watching"),
      dropped: await badge("#avProfileTabs", "dropped"), stats: await page.textContent(".profile-stats") };
    await check(page, "lists live: own profile — the title leaves «Смотрю», counters follow, the totals stay",
      p0.cards.includes(20) && !p1.cards.includes(20) && p1.watching === p0.watching - 1 && p1.dropped === p0.dropped + 1
      && p1.stats === p0.stats, JSON.stringify({ p0, p1 }));
    await check(page, "lists live: own profile — no loading screen", !(await sawLoading()));

    // «Списки → Смотрю»: карточка уходит, счётчики меняются, страница та же
    await page.goto(SITE + "/my/lists/watching", { waitUntil: "domcontentloaded" }); await settle(page, 1500);
    await page.evaluate(() => { document.querySelector("#avListGrid").dataset.qaMark = "1"; });
    const w0 = { cards: await cards(), watching: await badge("#avListTabs", "watching"), dropped: await badge("#avListTabs", "dropped") };
    await watchLoading();
    await move(21);
    const w1 = { cards: await cards(), watching: await badge("#avListTabs", "watching"), dropped: await badge("#avListTabs", "dropped") };
    await check(page, "lists live: the moved title leaves «Смотрю» at once",
      w0.cards.includes(21) && !w1.cards.includes(21) && w1.cards.length === w0.cards.length - 1, JSON.stringify({ w0, w1 }));
    await check(page, "lists live: tab counters follow — «Смотрю» −1, «Брошено» +1",
      w1.watching === w0.watching - 1 && w1.dropped === w0.dropped + 1, JSON.stringify({ w0, w1 }));
    await check(page, "lists live: no loading screen, the same page",
      !(await sawLoading()) && await page.$eval("#avListGrid", el => el.dataset.qaMark === "1"));
    await shot(page, "lists-live-watching");

    // «Списки → Брошено»: карточка приходит первой, с пометкой автостатуса
    await page.goto(SITE + "/my/lists/dropped", { waitUntil: "domcontentloaded" }); await settle(page, 1500);
    const d0 = await cards();
    await watchLoading();
    await move(16498);
    const d1 = await cards();
    await check(page, "lists live: a title moved to «Брошено» comes in first, marked auto",
      d1[0] === 16498 && d1.length === d0.length + 1 && await page.isVisible('#avListGrid .av-card-wrap[data-mal="16498"] .tile-flag'),
      JSON.stringify({ d0, d1 }));
    await check(page, "lists live: «Брошено» counter matches its cards, no loading screen",
      (await badge("#avListTabs", "dropped")) === d1.length && !(await sawLoading()));
    await shot(page, "lists-live-dropped");

    // Последняя карточка ушла из «Смотрю» — пустое состояние, тоже на месте
    await page.goto(SITE + "/my/lists/watching", { waitUntil: "domcontentloaded" }); await settle(page, 1500);
    const last = await cards();
    await watchLoading();
    for (const mal of last) await move(mal);
    await check(page, "lists live: the last title leaving «Смотрю» shows the empty state",
      last.length > 0 && (await cards()).length === 0 && /пока пусто/.test(await page.textContent("#avListGrid"))
      && (await badge("#avListTabs", "watching")) === 0 && !(await sawLoading()), JSON.stringify(last));
    await check(page, "lists live: no JS errors", !errors.length, errors.join(" | "));
    await ctx.close();
  },
};
