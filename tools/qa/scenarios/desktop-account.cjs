/*
 * Сценарий QA «desktop-account» — человек с аккаунтом на компьютере: меню профиля и тема, личные разделы, списки, история,
 * настройки, профиль — на подменённых ответах.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, accountMocks, check, shot, settle, watchConsole } = require("../lib.cjs");

module.exports = {
  async desktopAccount(browser) {
    const state = { calls: [] };
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "light", locale: "ru-RU" });
    const page = await ctx.newPage();
    const errors = []; page.on("pageerror", e => errors.push(String(e.message).slice(0, 200)));
    watchConsole(page, errors, "desktop account");
    await accountMocks(state)(page);

    await page.goto(SITE + "/", { waitUntil: "domcontentloaded" }); await settle(page, 2000);
    await page.click("#avAccountBtn"); await page.waitForTimeout(300);
    await check(page, "user menu opens", await page.isVisible("#avAccountDrop"));
    await shot(page, "user-menu");
    await page.click("#avAccountDrop [data-theme-cycle]"); await page.waitForTimeout(300);
    await check(page, "theme item switches to light and keeps the menu open",
      (await page.evaluate(() => document.documentElement.dataset.theme)) === "light" && await page.isVisible("#avAccountDrop"));
    await page.click("#avAccountDrop [data-theme-cycle]"); await page.waitForTimeout(300);
    await check(page, "theme item switches to dark", (await page.evaluate(() => document.documentElement.dataset.theme)) === "dark");
    await shot(page, "home-dark-account");
    await page.click("#avAccountDrop [data-theme-cycle]"); await page.waitForTimeout(300);
    await check(page, "theme back to system", !(await page.evaluate(() => document.documentElement.dataset.theme)));
    await page.keyboard.press("Escape"); await page.waitForTimeout(200);
    await check(page, "user menu closes with Escape", !(await page.isVisible("#avAccountDrop")));
    const asLoginOn = (await page.evaluate(() => fetch("/auth/config").then(r => r.json()))).as_login;
    await check(page, "personal items in the side menu", (await page.$$("#avNavPersonal .app-nav__item")).length === (asLoginOn ? 5 : 4),
      asLoginOn ? "with «Друзья»" : "");

    await page.click('#avNavPersonal [data-nav="my-lists"]'); await settle(page, 1200);
    await check(page, "lists: side menu marks Списки", await page.$eval('#avNavPersonal [data-nav="my-lists"]', el => el.classList.contains("is-active")));
    await page.click('#avListTabs a[data-status="planned"]'); await settle(page, 1200);
    await check(page, "lists: tab changes the URL", /\/my\/lists\/planned/.test(page.url()));
    await check(page, "lists: auto status badge on the auto tile", (await page.$$(".av-card-wrap .tile-flag")).length === 1);
    await page.click(".card-action-menu"); await page.waitForTimeout(300);
    await shot(page, "lists-card-menu");
    await page.click('.card-action-pop .lp-option[data-status="completed"]'); await page.waitForTimeout(800);
    await check(page, "lists: move removes the tile", (await page.$$("#avListGrid .av-card-wrap")).length === 0);
    await check(page, "lists: empty state after the last tile", await page.isVisible("#avListGrid .ed-empty").catch(() => false));
    await shot(page, "lists-after-move");

    await page.goto(SITE + "/my/continue", { waitUntil: "domcontentloaded" }); await settle(page, 1200);
    const n = (await page.$$("#g .av-card-wrap")).length;
    await page.click("#g .card-remove"); await page.waitForTimeout(800);
    await check(page, "continue: remove drops one tile", (await page.$$("#g .av-card-wrap")).length === n - 1, `${n} -> ${(await page.$$("#g .av-card-wrap")).length}`);
    await page.click("#avContinueClear"); await page.waitForTimeout(400);
    await check(page, "continue: clear asks to confirm", await page.isVisible(".confirm-overlay"));
    await check(page, "continue: confirm waits for the countdown", await page.$eval("#avConfirmYes", b => b.disabled));
    await shot(page, "confirm-countdown");
    await page.waitForTimeout(5600);
    await check(page, "continue: confirm unlocks after 5 s", !(await page.$eval("#avConfirmYes", b => b.disabled)));
    await page.click("#avConfirmYes"); await settle(page, 1500);
    await check(page, "continue: empty state after clearing", await page.isVisible(".ed-empty"));
    await shot(page, "continue-empty");

    await page.goto(SITE + "/anime/16498", { waitUntil: "domcontentloaded" }); await settle(page, 5000);
    await check(page, "title (account): picker shows the saved status", (await page.textContent(".list-picker-label")).trim() === "Смотрю");
    await check(page, "title (account): favourite shows as active", (await page.getAttribute("#avFavBtn", "aria-pressed")) === "true");
    await page.click("#avListPickerBtn"); await page.waitForTimeout(300);
    await check(page, "title (account): remove item and divider shown", await page.isVisible("#avListPickerPop .lp-clear") && await page.isVisible("#avListPickerPop .lp-clear-sep"));
    await shot(page, "title-account-picker");
    await page.keyboard.press("Escape");
    await page.goto(SITE + "/anime/1535", { waitUntil: "domcontentloaded" }); await settle(page, 5000);
    await check(page, "title (account): auto status badge", await page.isVisible(".list-picker-auto"));
    await shot(page, "title-account-auto");

    await page.goto(SITE + "/my/favorites", { waitUntil: "domcontentloaded" }); await settle(page, 1200);
    await check(page, "favorites: tiles rendered", (await page.$$("#avFavGrid .av-card")).length > 0);

    await page.goto(SITE + "/my/settings", { waitUntil: "domcontentloaded" }); await settle(page, 1000);
    await page.click('.theme-tabs [data-theme-value="dark"]'); await page.waitForTimeout(300);
    await check(page, "settings: theme tabs set dark", (await page.evaluate(() => document.documentElement.dataset.theme)) === "dark"
      && (await page.getAttribute('.theme-tabs [data-theme-value="dark"]', "aria-checked")) === "true");
    await shot(page, "settings-dark");
    await page.click('.theme-tabs [data-theme-value="system"]');
    await page.click("#avAutoAddToggle"); await page.waitForTimeout(300);
    await check(page, "settings: auto-add switch sends PUT", state.calls.some(c => c.startsWith("PUT /account/settings")));
    await page.goto(SITE + "/my/settings/privacy", { waitUntil: "domcontentloaded" }); await settle(page, 800);
    await page.click("#avHideListsToggle"); await page.waitForTimeout(300);
    await check(page, "privacy: switch sends PUT", state.calls.some(c => c.startsWith("PUT /account/privacy")));

    await page.goto(SITE + "/", { waitUntil: "domcontentloaded" }); await settle(page, 1000);
    await page.click('#avNavPersonal [data-nav="profile"]'); await settle(page, 2000);
    await check(page, "profile: opens from the side menu", /\/@qa-viewer/.test(page.url()));
    const lay = await page.evaluate(() => {
      const r = s => document.querySelector(s)?.getBoundingClientRect();
      const top = r(".act-top"), lib = r(".profile-library"), diary = r(".act-diary");
      const tabs = document.querySelector("#avProfileTabs");
      return { activityFirst: !!top && !!lib && top.bottom <= lib.top,
        sideBySide: !!lib && !!diary && diary.left >= lib.right - 1 && Math.abs(diary.top - lib.top) < 2,
        tabsFit: !!tabs && tabs.scrollWidth <= tabs.clientWidth + 1 };
    });
    await check(page, "profile: activity on top, lists and history side by side", lay.activityFirst && lay.sideBySide, JSON.stringify(lay));
    await check(page, "profile: list tabs wrap instead of running off", lay.tabsFit);
    await shot(page, "profile-overview", true);
    await page.click('[data-profile-mode="diary"]'); await settle(page, 1500);
    await check(page, "profile: diary hides the lists", !(await page.isVisible(".profile-library")));
    await page.click('.act-period[data-days="30"]'); await settle(page, 1500);
    await check(page, "profile: 30 days shows the calendar", await page.isVisible(".act-calendar"));
    await check(page, "profile: diary calendar stays in place while the history scrolls", (await page.$eval(".act-visual", el => getComputedStyle(el).position)) === "sticky");
    await shot(page, "profile-diary-calendar", true);
    const day = await page.$(".act-calendar-days .gh-cell[data-count]:not([data-count='0'])");
    if (day) { await day.click(); await settle(page, 800); }
    await check(page, "profile: a day filters the history", await page.isVisible("#avDateFilter"));
    await page.click("#avDateClear"); await settle(page, 600);
    await check(page, "profile: date filter clears", !(await page.isVisible("#avDateFilter")));
    await page.click('.gh-view-btn[data-view="list"]'); await settle(page, 800);
    await check(page, "profile: feed view hides the periods", !(await page.isVisible(".act-period-tabs")));
    await page.click('.act-filter-btn[data-group="watch"]'); await settle(page, 800);
    await check(page, "profile: filter chip becomes active", await page.$eval('.act-filter-btn[data-group="watch"]', b => b.classList.contains("is-active")));
    await shot(page, "profile-feed", true);
    await page.click('.gh-view-btn[data-view="grid"]'); await settle(page, 600);
    await page.click('[data-profile-mode="overview"]'); await settle(page, 1200);

    await page.click("#avAccountBtn"); await page.click("#avAccountDrop [data-logout]"); await settle(page, 1500);
    await check(page, "logout calls the API and returns home", state.loggedOut === true && new URL(page.url()).pathname === "/");
    await check(page, "desktop account: no JS errors", !errors.length, errors.join(" | "));
    await ctx.close();
  },
};
