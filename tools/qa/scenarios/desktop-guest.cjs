/*
 * Сценарий QA «desktop-guest» — гость на компьютере: главная и карусель, шапка и поиск, каталог и фильтры, тайтл, плеер, вход.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, check, shot, overflowing, pickerPlacement, CONSOLE_NOISE, settle } = require("../lib.cjs");

module.exports = {
  async desktopGuest(browser) {
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, colorScheme: "light", locale: "ru-RU" });
    const page = await ctx.newPage();
    const errors = []; page.on("pageerror", e => errors.push(String(e.message).slice(0, 200)));
    page.on("console", m => { if (m.type() === "error" && !CONSOLE_NOISE.test(m.text())) errors.push(m.text().slice(0, 200)); });

    await page.goto(SITE + "/", { waitUntil: "domcontentloaded" }); await settle(page, 2500);
    const first = await page.textContent(".hero-slide.active .hero-title").catch(() => "");
    await page.click(".hero-nav.next"); await page.waitForTimeout(400);
    const second = await page.textContent(".hero-slide.active .hero-title").catch(() => "");
    await check(page, "home: carousel next changes the slide", first && second && first !== second, `${first} -> ${second}`);
    await check(page, "home: nav marks Главная", await page.$eval('.app-nav [data-nav="home"]', el => el.classList.contains("is-active")));
    await shot(page, "home-desktop");

    await page.fill("#appSearchInput", "наруто"); await page.press("#appSearchInput", "Enter"); await settle(page, 2000);
    await check(page, "header search goes to /catalog?q=", /\/catalog\?q=/.test(page.url()), page.url());
    await check(page, "catalog: results rendered", (await page.$$("#g .av-card")).length > 0);
    await check(page, "catalog: search field keeps the query", (await page.inputValue("#sfQ")) === "наруто");
    await shot(page, "search-results");

    await page.goto(SITE + "/catalog", { waitUntil: "domcontentloaded" }); await settle(page, 1500);
    await page.click("#filterToggle"); await page.waitForTimeout(300);
    await check(page, "catalog: filter panel opens", await page.isVisible("#catalogFilter"));
    await page.waitForSelector("#cfGenres .cf-chip", { timeout: 15000 }).catch(() => {});
    const genresBox = await page.$eval("#cfGenres", el => ({ h: el.clientHeight, sh: el.scrollHeight }));
    await check(page, "catalog: genres scroll inside their box, not a wall of chips", genresBox.h <= 210 && genresBox.sh > genresBox.h, JSON.stringify(genresBox));
    const chips = await page.$$("#cfGenres .cf-chip");
    if (chips[0]) await chips[0].click();
    if (chips[2]) await chips[2].click();
    await check(page, "catalog: chips toggle aria-pressed", (await page.$$eval("#cfGenres .cf-chip[aria-pressed=true]", e => e.length)) === 2);
    await page.selectOption("#cfType", "movie");
    await shot(page, "catalog-filters-open");
    await page.click("#cfApply"); await settle(page, 2500);
    await check(page, "catalog: apply puts type and genres in the URL", /type=movie/.test(page.url()) && /genres=/.test(page.url()), page.url());
    await check(page, "catalog: badge counts the filters", (await page.textContent("#filterCount")).trim() === "2");
    await shot(page, "catalog-filtered");
    await page.click("#cfReset"); await settle(page, 1500);
    await check(page, "catalog: reset clears the URL", new URL(page.url()).search === "", page.url());
    const pageTwo = await page.$('#pager a.pager-btn[href*="page=2"]');
    if (pageTwo) { await pageTwo.click(); await settle(page, 2000); }
    await check(page, "catalog: pager goes to page 2", /page=2/.test(page.url()), page.url());
    await check(page, "catalog: current page marked", (await page.$eval("#pager [aria-current=page]", el => el.textContent).catch(() => "")) === "2");

    await page.goto(SITE + "/anime/52991", { waitUntil: "domcontentloaded" }); await settle(page, 6000);
    await check(page, "title: preview shown at 1440", await page.isVisible(".cinema-trailer"));
    await check(page, "title: trailer button hidden at 1440", !(await page.isVisible(".title-actions .cinema-trailer-action")));
    await shot(page, "title-desktop-top");
    await page.click("#avCtaPlay"); await page.waitForTimeout(1200);
    const playerTop = await page.$eval("#avPlayerCard", el => el.getBoundingClientRect().top);
    const headerBottom = await page.$eval(".app-header", el => el.getBoundingClientRect().bottom);
    await check(page, "title: «Смотреть» scrolls the player below the header", playerTop >= headerBottom - 1, `player ${Math.round(playerTop)} header ${Math.round(headerBottom)}`);
    await shot(page, "title-after-watch-click");

    await page.click("#avDubTrigger"); await page.waitForTimeout(400);
    await check(page, "dub menu opens", await page.isVisible("#avDubPop"));
    await check(page, "dub trigger aria-expanded", (await page.getAttribute("#avDubTrigger", "aria-expanded")) === "true");
    await shot(page, "dub-menu-desktop");
    const before = await page.textContent(".sp-dub-label");
    const items = await page.$$(".sp-dub-item");
    if (items[1]) await items[1].click();
    await page.waitForTimeout(800);
    await check(page, "dub choice changes the label and closes the menu", (await page.textContent(".sp-dub-label")) !== before && !(await page.isVisible("#avDubPop")));

    const rows = await page.$$("#avEpisodesList .ep-row");
    if (rows[1]) await rows[1].click();
    await page.waitForTimeout(800);
    await check(page, "episode 2 becomes active", (await page.textContent("#avEpInfo .sp-ep-num")).trim() === "2");
    await check(page, "prev enabled on episode 2", !(await page.$eval("#avPrevEp", b => b.disabled)));

    await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(300);
    await page.click("#avListPickerBtn"); await page.waitForTimeout(300);
    await check(page, "list picker opens", await page.isVisible("#avListPickerPop"));
    const pk = await pickerPlacement(page);
    await check(page, "list picker sits at its button, inside the screen", pk.ok, JSON.stringify(pk));
    await check(page, "guest picker has no orphan divider", !(await page.isVisible("#avListPickerPop .lp-clear-sep")));
    await shot(page, "list-picker-open");
    await page.click('#avListPickerPop .lp-option[data-status="watching"]'); await page.waitForTimeout(500);
    await check(page, "guest status change opens the gate", await page.isVisible(".gate-overlay"));
    await shot(page, "gate-desktop");
    await page.keyboard.press("Escape"); await page.waitForTimeout(300);
    await check(page, "gate closes with Escape", !(await page.$(".gate-overlay")));
    await page.click("#avFavBtn"); await page.waitForTimeout(400);
    await check(page, "guest favourite opens the gate", await page.isVisible(".gate-overlay"));
    await page.click(".gate-overlay [data-dialog-close].gate-close"); await page.waitForTimeout(300);
    await page.click(".rate-star >> nth=6"); await page.waitForTimeout(400);
    await check(page, "guest rating opens the gate", await page.isVisible(".gate-overlay"));
    await page.click(".av-dialog__scrim", { position: { x: 10, y: 10 } }); await page.waitForTimeout(300);
    await check(page, "gate closes on the scrim", !(await page.$(".gate-overlay")));

    await page.click(".cinema-trailer-preview"); await page.waitForTimeout(800);
    await check(page, "trailer dialog opens", await page.isVisible(".trailer-dialog"));
    await shot(page, "trailer-dialog");
    await page.click(".trailer-close"); await page.waitForTimeout(300);
    await check(page, "trailer dialog closes", !(await page.$(".trailer-dialog")));
    await page.click(".title-poster"); await page.waitForTimeout(500);
    await check(page, "poster lightbox opens", await page.isVisible(".lb-overlay"));
    await shot(page, "lightbox");
    await page.keyboard.press("Escape"); await page.waitForTimeout(300);
    await check(page, "lightbox closes with Escape", !(await page.$(".lb-overlay")));
    const ov = await overflowing(page);
    await check(page, "title desktop: nothing sticks out", !ov.items.length && ov.scrollWidth <= ov.vw, JSON.stringify(ov.items));
    await shot(page, "title-desktop-full", true);

    for (const w of [1366, 1179, 1024, 900, 768]) {
      await page.setViewportSize({ width: w, height: 860 }); await page.waitForTimeout(500);
      await page.evaluate(() => window.scrollTo(0, 0));
      const btn = await page.isVisible(".title-actions .cinema-trailer-action");
      const prev = await page.isVisible(".cinema-trailer");
      await check(page, `title ${w}: exactly one trailer control`, btn !== prev, `button ${btn} preview ${prev}`);
      const o = await overflowing(page);
      await check(page, `title ${w}: nothing sticks out`, !o.items.length && o.scrollWidth <= o.vw, JSON.stringify(o.items));
      await shot(page, `title-${w}`);
      if (w <= 900) {
        await page.$eval("#avListPickerBtn", el => el.scrollIntoView({ block: "center" }));
        await page.click("#avListPickerBtn"); await page.waitForTimeout(300);
        const p = await pickerPlacement(page);
        await check(page, `title ${w}: list picker sits at its button`, p.ok, JSON.stringify(p));
        await page.keyboard.press("Escape"); await page.waitForTimeout(200);
      }
    }

    // A short laptop window: the trailer video with its controls fits on the screen.
    await page.setViewportSize({ width: 1366, height: 600 }); await page.waitForTimeout(400);
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.click(".cinema-trailer-preview"); await page.waitForTimeout(800);
    const tv = await page.evaluate(() => {
      const v = document.querySelector(".trailer-video").getBoundingClientRect();
      const d = document.querySelector(".trailer-dialog").getBoundingClientRect();
      return { video: Math.round(v.bottom), dialog: Math.round(d.bottom), vh: innerHeight };
    });
    await check(page, "trailer 1366x600: the whole video fits", tv.video <= tv.dialog + 1 && tv.video <= tv.vh, JSON.stringify(tv));
    await page.click(".trailer-close"); await page.waitForTimeout(300);

    // Seasons row with more entries than fit: wheel, mouse drag and a plain click.
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(SITE + "/anime/5114", { waitUntil: "domcontentloaded" }); await settle(page, 5000);
    const bar = await page.$("#avSeasonsBar");
    const wide = bar && await bar.evaluate(el => el.scrollWidth > el.clientWidth + 1);
    await check(page, "seasons: the row overflows (test needs it)", wide);
    if (wide) {
      await bar.scrollIntoViewIfNeeded();
      const box = await bar.boundingBox();
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.wheel(0, 240); await page.waitForTimeout(300);
      const afterWheel = await bar.evaluate(el => el.scrollLeft);
      await check(page, "seasons: mouse wheel scrolls the row", afterWheel > 0, `scrollLeft ${afterWheel}`);
      await check(page, "seasons: scrollbar visible for a mouse", await bar.evaluate(el => getComputedStyle(el).scrollbarWidth !== "none"));
      const names = await page.$$eval("#avSeasonsBar .season-name", els => els.map(e => e.scrollWidth <= e.clientWidth + 1));
      await check(page, "seasons: names are not cut", names.every(Boolean));
      await bar.evaluate(el => { el.scrollLeft = 0; });
      const url = page.url();
      await page.mouse.move(box.x + box.width - 40, box.y + box.height / 2);
      await page.mouse.down(); await page.mouse.move(box.x + 60, box.y + box.height / 2, { steps: 12 }); await page.mouse.up();
      await page.waitForTimeout(600);
      await check(page, "seasons: mouse drag scrolls the row", (await bar.evaluate(el => el.scrollLeft)) > 0);
      await check(page, "seasons: a drag does not open a link", page.url() === url, page.url());
      await shot(page, "seasons-scrolled");
      await page.click("#avSeasonsBar a.season-tab"); await settle(page, 2500);
      await check(page, "seasons: a click still opens the title", page.url() !== url, page.url());
    }

    await page.goto(SITE + "/login", { waitUntil: "domcontentloaded" }); await settle(page, 1000);
    // With sign-in through AnimeSocial on, an unknown social state opens the button view first
    if (await page.isVisible('[data-auth-switch="mail"]')) { await page.click('[data-auth-switch="mail"]'); await page.waitForTimeout(200); }
    await page.click("#avPassToggle");
    await check(page, "login: password toggle shows the text", (await page.getAttribute("#avLoginPass", "type")) === "text");
    await page.click("#avPassToggle");
    // The real form would ask the social network and spend its failed-login limit for this address
    await page.route(/\/auth\/login$/, route => route.fulfill({ status: 401, contentType: "application/json", body: JSON.stringify({ detail: "invalid credentials" }) }));
    await page.fill("#avLoginInput", "qa-no-such-user@example.test"); await page.fill("#avLoginPass", "wrong-password");
    await page.click(".auth-submit"); await page.waitForTimeout(800);
    await page.unroute(/\/auth\/login$/);
    await check(page, "login: wrong credentials show an error under the form", await page.isVisible("#avLoginErr"), await page.textContent("#avLoginErrText").catch(() => ""));
    await shot(page, "login-error");

    await page.goto(SITE + "/random", { waitUntil: "domcontentloaded" }); await settle(page, 4000);
    await check(page, "random opens a title page", /\/anime\/\d+/.test(page.url()), page.url());

    await check(page, "desktop guest: no JS errors", !errors.length, errors.join(" | "));
    await ctx.close();
  },
};
