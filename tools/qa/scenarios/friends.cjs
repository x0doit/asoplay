/*
 * Сценарий QA «friends» — друзья из AnimeSocial: /my/friends с присутствием, фильтры и пустые состояния, заявки, «Написать»,
 * переключатели приватности и блок «Друзья» на странице тайтла — компьютер и телефон.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, USER, STATUS, accountMocks, check, shot, overflowing, settle, watchConsole } = require("../lib.cjs");

// «был в сети 5 минут назад» — как пишет AnimeSocial: единицы от года до минуты и русские формы числа
const AGO_UNITS = [[31_570_560, ["год", "года", "лет"]], [2_630_880, ["месяц", "месяца", "месяцев"]],
  [604_800, ["неделю", "недели", "недель"]], [86_400, ["день", "дня", "дней"]], [3_600, ["час", "часа", "часов"]],
  [60, ["минуту", "минуты", "минут"]]];
const ruForm = (n, forms) => forms[n % 100 > 10 && n % 100 < 20 ? 2 : n % 10 === 1 ? 0 : n % 10 >= 2 && n % 10 <= 4 ? 1 : 2];
const agoText = diff => {
  for (const [len, forms] of AGO_UNITS) if (diff >= len) { const n = Math.floor(diff / len); return `${n} ${ruForm(n, forms)} назад`; }
  return "только что";
};
// Подписи «был в сети …», верные хоть для одного мгновения между from и to (мс по часам страницы). Страница пишет подпись
// по своим часам в миг отрисовки, а когда именно она рисовала, снаружи известно только с точностью до этого промежутка:
// ни запаса на медленный прогон, ни застывших часов, от которых встал бы и остальной код страницы со временем
const seenTexts = (lastSeen, word, from, to) => {
  const out = new Set();
  for (let s = Math.floor(from / 1000); s <= Math.floor(to / 1000); s++) out.add(`${word} в сети ${agoText(Math.max(0, s - lastSeen))}`);
  return out;
};

module.exports = {
  // Friends from AnimeSocial: /my/friends with presence, the filter and every empty state, the «?as=» notice,
  // the presence switches in settings and the «Друзья» block on a title page — desktop (light) and phone (dark).
  async friends(browser) {
    const sec = Math.floor(Date.now() / 1000);
    const F = (id, name, extra = {}) => ({ id, name, avatar: null, sex: 1, profile_path: `/@friend${id}`, social_url: `https://social.test/profile${id}`,
      verified: false, elite: false, on_asoplay: false, watching: null, social: null, last_seen: null, ...extra });
    const FRIENDS = { connected: true, total: 5, online: 3, requests: { incoming: 1, outgoing: 2 }, items: [
      F(1, "Сакура Харуно", { on_asoplay: true, verified: true, watching: { mal_id: 52991, title: "Фрирен, провожающая в последний путь", path: "/anime/52991" } }),
      F(2, "Наруто Узумаки", { on_asoplay: true, social: "site", elite: true }),
      F(3, "Хината Хьюга с очень длинным именем, которое не помещается в строку на телефоне", { social: "mobile", sex: 2 }),
      F(4, "Какаши Хатаке", { last_seen: sec - 300 }),
      F(5, "Ино Яманака", { last_seen: sec - 3 * 86400, sex: 2 }),
    ] };
    for (const vp of [{ width: 1280, height: 860, phone: false }, { width: 390, height: 844, phone: true }]) {
      const tag = vp.phone ? "phone" : "desktop";
      const ctx = await browser.newContext({ viewport: { width: vp.width, height: vp.height }, isMobile: vp.phone, hasTouch: vp.phone,
        deviceScaleFactor: vp.phone ? 2 : 1, colorScheme: vp.phone ? "dark" : "light", locale: "ru-RU" });
      const page = await ctx.newPage();
      const errors = []; page.on("pageerror", e => errors.push(String(e.message).slice(0, 200)));
      watchConsole(page, errors, `friends ${tag}`);
      const state = { calls: [], privacyPuts: [], friends: FRIENDS, friendsStatus: 200, title: { connected: true, total: 0, items: [] } };
      await accountMocks(state)(page);
      const json = (route, body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
      await page.route("**/auth/config", async route => {
        const resp = await route.fetch();
        await route.fulfill({ response: resp, json: { ...(await resp.json()), as_login: true, as_api_url: "https://social.test/api/v2" } });
      });
      await page.route(/\/account\/friends(\?|$)/, route => { state.calls.push("GET /account/friends"); return json(route, state.friends, state.friendsStatus); });
      await page.route(/\/account\/friends\/title\/\d+/, route => json(route, state.title));
      state.pending = { incoming: [F(40, "Киба Инузука", { location: "Коноха, Страна Огня" })], outgoing: [F(41, "Ли Рок"), F(42, "Тентен")] };
      state.actions = [];
      state.actionStatus = 200;
      await page.route(/\/account\/friends\/requests\?type=(incoming|outgoing)/, route => {
        const kind = new URL(route.request().url()).searchParams.get("type");
        return json(route, { items: state.pending[kind], next_cursor: null, total: state.pending[kind].length });
      });
      await page.route(/\/account\/friends\/suggestions$/, route => json(route, { items: [
        { ...F(50, "Шикамару Нара"), mutual: 3, location: null }, { ...F(51, "Чоджи Акимичи"), mutual: 0, location: "Коноха" }] }));
      await page.route(/\/account\/friends\/\d+(\/accept|\/decline)?$/, route => {
        const url = new URL(route.request().url());
        state.actions.push(`${route.request().method()} ${url.pathname}`);
        if (state.actionStatus !== 200) return json(route, { detail: "rate_limited" }, state.actionStatus);
        if (/\/accept|\/decline/.test(url.pathname)) state.pending.incoming = [];
        return json(route, { status: "none", friends_count: 5, friend_requests: 0 });
      });
      const live = { sockets: [], frames: [] };
      await page.routeWebSocket(/\/ws$/, ws => {
        live.sockets.push(ws);
        ws.onMessage(m => {
          const frame = JSON.parse(String(m));
          live.frames.push(frame);
          if (frame.type === "ping") ws.send(JSON.stringify({ type: "pong" }));
        });
        ws.onClose(() => { live.sockets = live.sockets.filter(x => x !== ws); });
        ws.send(JSON.stringify({ type: "hello", user_id: USER.id, status: STATUS }));
      });
      const presenceEvent = async (ev) => {
        for (const ws of live.sockets) ws.send(JSON.stringify({ type: "presence", ...ev }));
        await page.waitForTimeout(150);
      };
      await page.route(/\/account\/privacy$/, route => {
        if (route.request().method() === "PUT") state.privacyPuts.push(JSON.parse(route.request().postData() || "{}"));
        return json(route, { hide_lists: false, hide_activity: false, hide_presence: false, show_watching: false });
      });

      await page.goto(SITE + "/", { waitUntil: "domcontentloaded" }); await settle(page, 1500);
      const menu = vp.phone
        ? await page.$('#avSheetPersonal a[href="/my/friends"]')
        : (await page.$('#avAccountDrop a[href="/my/friends"]')) && (await page.$('#avNavPersonal [data-nav="my-friends"]'));
      await check(page, `friends ${tag}: «Друзья» in the menu`, !!menu);
      await check(page, `friends ${tag}: the tab opens the live connection and says it is on screen`,
        live.frames.some(f => f.type === "visible" && f.value === true), JSON.stringify(live.frames.slice(0, 3)));

      const seenFrom = await page.evaluate(() => Date.now());          // часы страницы до того, как нарисован список
      await page.goto(SITE + "/my/friends", { waitUntil: "domcontentloaded" }); await settle(page, 1200);
      const friendRows = async () => page.$$eval(".friends__item", els => els.map(el => ({
        name: el.querySelector(".ed-user__text")?.textContent.trim(),
        status: el.querySelector(".ed-user__meta")?.textContent.trim(),
        dot: !!el.querySelector(".ed-ava__dot"),
        href: el.querySelector(".ed-user__name")?.getAttribute("href"),
        watch: el.querySelector(".ed-user__meta a")?.getAttribute("href") || null,
        write: !!el.querySelector("[data-friend-write]"),
      })));
      const rows = await friendRows();
      const seenTo = await page.evaluate(() => Date.now());            // и после того, как прочитаны подписи
      await check(page, `friends ${tag}: five friends in server order`, rows.length === 5 && rows[0].href === "/@friend1", JSON.stringify(rows.map(r => r.name)));
      await check(page, `friends ${tag}: «смотрит «…»» links to the title`, /^смотрит «Фрирен/.test(rows[0]?.status || "") && rows[0]?.watch === "/anime/52991", rows[0]?.status);
      await check(page, `friends ${tag}: AsoPlay and AnimeSocial presence in the social network's words`, rows[1]?.status === "в сети и на AsoPlay"
        && rows[2]?.status === "в сети с телефона", `${rows[1]?.status} | ${rows[2]?.status}`);
      const dots = await page.$$eval(".friends__item", els => els.map(el => {
        const dot = el.querySelector(".ed-ava__dot");
        return !dot ? "" : dot.classList.contains("is-both") ? "both" : dot.classList.contains("is-client") ? "asoplay" : "social";
      }));
      const purple = await page.$$eval(".friends__item .ed-user__meta", els => els.map(el =>
        el.classList.contains("is-client") ? "line" : el.querySelector(".presence-also") ? "also" : ""));
      await check(page, `friends ${tag}: dots and lines in AnimeSocial colours — purple on AsoPlay, green in the social network, split for both`,
        dots.join(",") === "asoplay,both,social,," && purple.join(",") === "line,also,,,", `${dots.join(",")} | ${purple.join(",")}`);
      const kakashi = seenTexts(sec - 300, "был", seenFrom, seenTo), ino = seenTexts(sec - 3 * 86400, "была", seenFrom, seenTo);
      await check(page, `friends ${tag}: last seen like on AnimeSocial`, kakashi.has(rows[3]?.status) && ino.has(rows[4]?.status),
        `${rows[3]?.status} | ${rows[4]?.status} (верно: ${[...kakashi].join(" / ")} | ${[...ino].join(" / ")})`);
      await check(page, `friends ${tag}: a green dot only for those online`, rows.map(r => r.dot).join() === "true,true,true,false,false");
      await check(page, `friends ${tag}: every friend has «Написать»`, rows.every(r => r.write));
      const look = await page.evaluate(() => {
        const r = s => document.querySelector(s)?.getBoundingClientRect();
        const name = document.querySelector(".friends__item .ed-user__text").getBoundingClientRect();
        const badge = document.querySelector(".friends__item .ed-user__verify").getBoundingClientRect();
        const tabs = document.querySelector(".friends__tabs");
        return { gap: Math.round(badge.left - name.right), tabsInCard: !!tabs?.closest(".ed-panel.friends"),
          main: Math.round(r(".page__main").width), side: Math.round(r("#avFriendsSide")?.width || 0),
          watch: [...document.querySelectorAll(".friends-watch__item")].map(a => a.textContent.replace(/\s+/g, " ").trim()),
          label: getComputedStyle(document.querySelector(".friends__label")).display };
      });
      await check(page, `friends ${tag}: the badge keeps its distance from the name`, look.gap >= 4, `${look.gap}px`);
      await check(page, `friends ${tag}: tabs and search sit inside the card, as on AnimeSocial`, look.tabsInCard && !!(await page.$(".friends .friends__search input")));
      await check(page, `friends ${tag}: «Смотрят сейчас» shows what friends watch`, look.watch.length === 1 && /Фрирен.*Сакура/.test(look.watch[0]), JSON.stringify(look.watch));
      if (vp.phone) await check(page, "friends phone: one column and «Написать» is just the icon", look.label === "none" && look.side >= vp.width - 48, JSON.stringify(look));
      else await check(page, "friends desktop: the list and a 300px side column share the width", look.side === 300 && look.main >= 600, JSON.stringify(look));
      if (!vp.phone) await check(page, "friends desktop: nav marks Друзья", await page.$eval('#avNavPersonal [data-nav="my-friends"]', el => el.classList.contains("is-active")));
      const o = await overflowing(page);
      await check(page, `friends ${tag}: nothing sticks out`, !o.items.length && o.scrollWidth <= vp.width, JSON.stringify(o.items));
      await shot(page, `friends-${tag}`);
      await page.click('[data-filter="online"]'); await page.waitForTimeout(150);
      await check(page, `friends ${tag}: «В сети» shows the three online`, (await page.$$(".friends__item")).length === 3
        && await page.getAttribute('[data-filter="online"]', "aria-pressed") === "true");
      await page.click('[data-filter="all"]'); await page.waitForTimeout(150);
      await check(page, `friends ${tag}: «Все» brings everyone back`, (await page.$$(".friends__item")).length === 5);
      await page.fill(".friends__search input", "нарУ"); await page.waitForTimeout(150);
      await check(page, `friends ${tag}: the search finds by name`, (await friendRows()).map(r => r.name).join() === "Наруто Узумаки");
      await page.fill(".friends__search input", "Шикамару"); await page.waitForTimeout(150);
      await check(page, `friends ${tag}: nobody found says so`, /Никого не нашли/.test(await page.textContent(".friends__nothing").catch(() => "")));
      await page.fill(".friends__search input", ""); await page.waitForTimeout(150);

      await page.click('.friends__item[data-user-id="2"] [data-friend-menu]'); await page.waitForTimeout(150);
      const friendMenu = await page.$$eval('.friends__item[data-user-id="2"] .friends__menu:not([hidden]) .ed-menu__item', els => els.map(a => [a.textContent.trim(), a.getAttribute("href"), a.getAttribute("target")]));
      await check(page, `friends ${tag}: «⋯» opens the AsoPlay profile, the AnimeSocial page and «Удалить из друзей»`, friendMenu.length === 3 && friendMenu[0][1] === "/@friend2"
        && friendMenu[1][1] === "https://social.test/profile2" && friendMenu[1][2] === "_blank" && friendMenu[2][0] === "Удалить из друзей", JSON.stringify(friendMenu));
      await shot(page, `friends-${tag}-menu`);
      await page.keyboard.press("Escape"); await page.waitForTimeout(100);
      await check(page, `friends ${tag}: Escape closes «⋯»`, !(await page.$(".friends__menu:not([hidden])")));

      state.messages = [];
      state.messageStatus = 200;
      await page.route(/\/account\/messages\/\d+$/, route => {
        state.messages.push([route.request().url().split("/").pop(), JSON.parse(route.request().postData() || "{}").text]);
        return state.messageStatus === 200
          ? json(route, { ok: true, dialog_url: "https://social.test/im?uid=2" })
          : json(route, { detail: "privacy" }, state.messageStatus);
      });
      await page.click('.friends__item[data-user-id="2"] [data-friend-write]'); await page.waitForTimeout(250);
      await check(page, `friends ${tag}: «Написать» opens a message window to this friend`, await page.isVisible(".write-dialog")
        && /Наруто Узумаки/.test(await page.textContent(".write-dialog__to")) && await page.isDisabled('.write-dialog [type="submit"]'));
      await page.fill(".write-dialog__text", "Смотрим Фрирен вечером?");
      await page.waitForTimeout(300);                                   // кнопка «Отправить» проявляется плавно
      await shot(page, `friends-${tag}-write`);
      await page.click('.write-dialog [type="submit"]'); await page.waitForTimeout(300);
      await check(page, `friends ${tag}: the message is sent and the dialog can be opened in AnimeSocial`,
        JSON.stringify(state.messages) === '[["2","Смотрим Фрирен вечером?"]]' && /отправлено/.test(await page.textContent(".write-dialog"))
        && await page.getAttribute('.write-dialog a[href^="https://social.test/im"]', "target") === "_blank", JSON.stringify(state.messages));
      await page.click('.write-dialog [data-dialog-close][autofocus]'); await page.waitForTimeout(200);
      state.messageStatus = 403;
      await page.click('.friends__item[data-user-id="3"] [data-friend-write]'); await page.waitForTimeout(250);
      await page.fill(".write-dialog__text", "Привет");
      await page.keyboard.press("Control+Enter"); await page.waitForTimeout(300);
      await check(page, `friends ${tag}: a refusal is explained in the window`, /ограничил, кто может ему писать/.test(await page.textContent(".write-dialog__error").catch(() => "")));
      await page.keyboard.press("Escape"); await page.waitForTimeout(200);
      await check(page, `friends ${tag}: the window closes`, !(await page.$(".write-dialog")));

      await presenceEvent({ user: { id: 4, name: "Какаши Хатаке", avatar: null, profile_path: "/@friend4" }, on_asoplay: true,
        watching: { mal_id: 52991, title: "Фрирен, провожающая в последний путь", poster: "", path: "/anime/52991" } });
      let now = await friendRows();
      await check(page, `friends ${tag}: a friend who starts watching moves up at once`, now[0]?.href === "/@friend4" && now[0]?.dot
        && /^смотрит «Фрирен/.test(now[0]?.status || ""), JSON.stringify(now[0]));
      await check(page, `friends ${tag}: «Смотрят сейчас» counts both at once`,
        /Какаши и Сакура|Сакура и Какаши/.test(await page.textContent(".friends-watch").catch(() => "")), await page.textContent(".friends-watch").catch(() => ""));
      await presenceEvent({ user: { id: 1, name: "Сакура Харуно", avatar: null, profile_path: "/@friend1" }, on_asoplay: false, watching: null });
      now = await friendRows();
      await check(page, `friends ${tag}: a friend who leaves goes down as «только что»`,
        now.map(r => r.href).join() === "/@friend4,/@friend2,/@friend3,/@friend1,/@friend5" && now[3]?.status === "был в сети только что" && !now[3]?.dot,
        JSON.stringify(now.map(r => [r.href, r.status])));
      for (const ws of live.sockets) ws.send(JSON.stringify({ type: "social_presence", user_id: 5, online_where: "site", last_seen: sec }));
      await page.waitForTimeout(150);
      now = await friendRows();
      await check(page, `friends ${tag}: a friend who comes to AnimeSocial goes up at once`, now[2]?.href === "/@friend5" && now[2]?.dot
        && now[2]?.status === "в сети", JSON.stringify(now.map(r => [r.href, r.status])));
      const loadsBefore = state.calls.filter(c => c === "GET /account/friends").length;
      for (const ws of live.sockets) ws.send(JSON.stringify({ type: "friendship", user_id: 9, status: "friends" }));
      await page.waitForTimeout(400);
      await check(page, `friends ${tag}: a new friendship re-reads the list`, state.calls.filter(c => c === "GET /account/friends").length === loadsBefore + 1);


      const tabs = await page.$$eval(".friends__tabs .ed-tabs__item", els => els.map(b => b.textContent.replace(/\s+/g, " ").trim()));
      await check(page, `friends ${tag}: four tabs like AnimeSocial, requests counted`, tabs.join("|") === "Все5|В сети3|Заявки1|Исходящие2", tabs.join("|"));
      const suggest = await page.$$eval(".friends-suggest", els => els.map(el => [el.querySelector(".ed-user__text").textContent, el.querySelector(".friends-suggest__meta").textContent]));
      await check(page, `friends ${tag}: «Возможные друзья» with shared friends or the city`,
        JSON.stringify(suggest) === '[["Шикамару Нара","3 общих друга"],["Чоджи Акимичи","Коноха"]]', JSON.stringify(suggest));
      await page.click('[data-friend-add="50"]'); await page.waitForTimeout(250);
      await check(page, `friends ${tag}: «Добавить в друзья» sends the request`, state.actions.includes("POST /account/friends/50")
        && /Заявка отправлена/.test(await page.textContent('.friends-suggest[data-user-id="50"]')) && /Исходящие3/.test(await page.textContent(".friends__tabs")));
      await page.click('[data-filter="incoming"]'); await page.waitForTimeout(300);
      const incoming = await page.$$eval(".friends__item", els => els.map(el => [el.querySelector(".ed-user__text").textContent, el.querySelector(".ed-user__meta").textContent,
        [...el.querySelectorAll(".friends__actions .ed-btn")].map(b => b.textContent.trim()).join("+")]));
      await check(page, `friends ${tag}: «Заявки» lists who wants to be friends`, JSON.stringify(incoming) === '[["Киба Инузука","Коноха, Страна Огня","Принять+Отклонить"]]', JSON.stringify(incoming));
      await shot(page, `friends-${tag}-requests`);
      await page.click('[data-friend-accept="40"]'); await page.waitForTimeout(400);
      await check(page, `friends ${tag}: «Принять» clears the request and the counter`, state.actions.includes("POST /account/friends/40/accept")
        && !(await page.$('[data-friend-accept="40"]')) && await page.isHidden('[data-count="incoming"]'));
      await page.click('[data-filter="outgoing"]'); await page.waitForTimeout(300);
      await page.click('[data-friend-cancel="41"]'); await page.waitForTimeout(300);
      await check(page, `friends ${tag}: «Отменить заявку» takes it back`, state.actions.includes("DELETE /account/friends/41") && !(await page.$('[data-friend-cancel="41"]')));
      state.actionStatus = 429;
      await page.click('[data-friend-add="51"]'); await page.waitForTimeout(300);
      await check(page, `friends ${tag}: the daily limit is explained`, /не больше 50 в сутки/.test(await page.textContent(".friends__notice")) && await page.isVisible(".friends__notice"));
      state.actionStatus = 200;
      await page.click('[data-filter="all"]'); await page.waitForTimeout(200);
      await page.click('.friends__item[data-user-id="5"] [data-friend-menu]'); await page.waitForTimeout(150);
      await page.click('.friends__item[data-user-id="5"] [data-friend-remove]'); await page.waitForTimeout(2400);
      await check(page, `friends ${tag}: «Удалить из друзей» asks first`, /Удалить из друзей\?/.test(await page.textContent(".confirm-overlay")));
      await page.click("#avConfirmYes"); await page.waitForTimeout(400);
      await check(page, `friends ${tag}: and then removes the friend`, state.actions.includes("DELETE /account/friends/5") && !(await page.$('.friends__item[data-user-id="5"]')));

      state.friends = { connected: false, items: [], total: 0, online: 0 };
      await page.goto(SITE + "/my/friends", { waitUntil: "domcontentloaded" }); await settle(page, 1000);
      const connect = await page.getAttribute(".friends-page .ed-empty .ed-btn", "href").catch(() => "");
      await check(page, `friends ${tag}: not linked — sign in through AnimeSocial and come back`,
        /Друзья — из AnimeSocial/.test(await page.textContent(".friends-page .ed-empty__title").catch(() => "")) && /\/auth\/as\/start\?next=%2Fmy%2Ffriends$/.test(connect || ""), connect);
      await shot(page, `friends-${tag}-connect`);
      await page.goto(SITE + "/my/friends?as=other", { waitUntil: "domcontentloaded" }); await settle(page, 1000);
      await check(page, `friends ${tag}: «другой аккаунт» notice once`, /другой аккаунт/.test(await page.textContent(".friends-page .ed-note--danger").catch(() => ""))
        && await page.evaluate(() => location.search === ""));
      state.friends = { connected: true, items: [], total: 0, online: 0 };
      await page.goto(SITE + "/my/friends", { waitUntil: "domcontentloaded" }); await settle(page, 1000);
      await check(page, `friends ${tag}: no friends yet`, /пока нет друзей/.test(await page.textContent(".friends-page .ed-empty__title").catch(() => "")));
      state.friendsStatus = 503;
      await page.goto(SITE + "/my/friends", { waitUntil: "domcontentloaded" }); await settle(page, 1000);
      await check(page, `friends ${tag}: an outage says so`, /Не удалось загрузить друзей/.test(await page.textContent(".friends-page .ed-empty__title").catch(() => "")));
      state.friendsStatus = 200; state.friends = FRIENDS;

      await page.goto(SITE + "/my/settings/privacy", { waitUntil: "domcontentloaded" }); await settle(page, 1000);
      const sw = async () => page.evaluate(() => ({ presence: document.querySelector("#avShowPresenceToggle")?.checked,
        watching: document.querySelector("#avShowWatchingToggle")?.checked, disabled: document.querySelector("#avShowWatchingToggle")?.disabled }));
      await check(page, `friends ${tag}: presence on and «что смотрю» off by default`, JSON.stringify(await sw()) === '{"presence":true,"watching":false,"disabled":false}');
      await page.click("#avShowWatchingToggle"); await page.waitForTimeout(200);
      await page.click("#avShowPresenceToggle"); await page.waitForTimeout(200);
      const last = state.privacyPuts[state.privacyPuts.length - 1] || {};
      await check(page, `friends ${tag}: hiding presence disables «что смотрю» and is saved`, (await sw()).disabled === true
        && last.hide_presence === true && last.show_watching === true && state.privacyPuts.length === 2, JSON.stringify(state.privacyPuts));
      await page.click("#avShowPresenceToggle"); await page.waitForTimeout(200);
      await check(page, `friends ${tag}: presence back — «что смотрю» returns as it was`, JSON.stringify(await sw()) === '{"presence":true,"watching":true,"disabled":false}');
      await page.click("#avShowPresenceToggle"); await page.waitForTimeout(200);
      await check(page, `friends ${tag}: unavailable «что смотрю» looks off`, JSON.stringify(await sw()) === '{"presence":false,"watching":false,"disabled":true}');
      await shot(page, `friends-${tag}-settings`);

      state.title = { connected: true, total: 3, items: [
        { ...F(1, "Сакура Харуно"), now: true, status: null },
        { ...F(4, "Какаши Хатаке"), now: false, status: "completed" },
        { ...F(3, "Хината Хьюга с очень длинным именем"), now: false, status: "planned" },
      ] };
      await page.goto(SITE + "/anime/52991", { waitUntil: "domcontentloaded" }); await settle(page, 5000);
      const chips = await page.$$eval("#avTitleFriends .title-friend", els => els.map(el => el.querySelector(".title-friend__status").textContent.trim()));
      await check(page, `friends ${tag}: the title block shows who watches now and whose lists have it`,
        await page.isVisible("#avTitleFriends") && chips.join("|") === "Смотрит сейчас|Просмотрено|В планах", chips.join("|"));
      const o2 = await overflowing(page);
      await check(page, `friends ${tag}: title page — nothing sticks out`, !o2.items.length && o2.scrollWidth <= vp.width, JSON.stringify(o2.items));
      await page.$eval("#avTitleFriends", el => el.scrollIntoView({ block: "center" })); await page.waitForTimeout(200);
      await shot(page, `friends-${tag}-title`);
      const titleChips = async () => page.$$eval("#avTitleFriends .title-friend", els => els.map(el =>
        `${el.querySelector(".title-friend__name").textContent.trim()}:${el.querySelector(".title-friend__status").textContent.trim()}`));
      await presenceEvent({ user: { id: 2, name: "Наруто Узумаки", avatar: null, profile_path: "/@friend2" }, on_asoplay: true,
        watching: { mal_id: 52991, title: "Фрирен", path: "/anime/52991" } });
      await check(page, `friends ${tag}: a friend who starts this title appears in the block at once`,
        (await titleChips())[0] === "Наруто Узумаки:Смотрит сейчас", (await titleChips()).join(" | "));
      await presenceEvent({ user: { id: 2, name: "Наруто Узумаки", avatar: null, profile_path: "/@friend2" }, on_asoplay: true, watching: null });
      await presenceEvent({ user: { id: 1, name: "Сакура Харуно", avatar: null, profile_path: "/@friend1" }, on_asoplay: false, watching: null });
      await check(page, `friends ${tag}: stopping drops a friend without this title in their lists`,
        (await titleChips()).join("|") === "Какаши Хатаке:Просмотрено|Хината Хьюга с очень длинным именем:В планах", (await titleChips()).join(" | "));
      state.title = { connected: true, total: 12, items: Array.from({ length: 12 }, (_, i) => ({ ...F(20 + i, `Друг номер ${i + 1}`), now: i < 2, status: i < 2 ? null : "watching" })) };
      await page.goto(SITE + "/anime/52991", { waitUntil: "domcontentloaded" }); await settle(page, 5000);
      const strip = await page.$eval(".title-friends__list", el => ({ h: Math.round(el.getBoundingClientRect().height), sw: el.scrollWidth, cw: el.clientWidth }));
      await check(page, `friends ${tag}: many friends stay in one scrolling row`, strip.h < 70 && strip.sw > strip.cw, JSON.stringify(strip));
      if (!vp.phone) {
        await page.hover(".title-friends__list .title-friend"); await page.mouse.wheel(0, 300); await page.waitForTimeout(200);
        await check(page, "friends desktop: the wheel moves the friends row", await page.$eval(".title-friends__list", el => el.scrollLeft) > 0);
      }
      const o3 = await overflowing(page);
      await check(page, `friends ${tag}: a long row does not push the page sideways`, !o3.items.length && o3.scrollWidth <= vp.width, JSON.stringify(o3.items));
      state.title = { connected: true, total: 0, items: [] };
      await page.goto(SITE + "/anime/52991", { waitUntil: "domcontentloaded" }); await settle(page, 4000);
      await check(page, `friends ${tag}: no friends on the title — no block`, !(await page.isVisible("#avTitleFriends")));
      await check(page, `friends ${tag}: no JS errors`, !errors.length, errors.join(" | "));
      await ctx.close();
    }
  },
};
