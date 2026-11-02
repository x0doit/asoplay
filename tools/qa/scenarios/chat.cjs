/*
 * Сценарий QA «chat» — чат серии на странице «Фрирен» с настоящим плеером (наш кадр, CVH) и подменёнными /comments
 * и /ws, компьютер 1600 px: значок у «Эпизод N», панель вместо «Эпизодов» вровень с колонкой плеера, минуты-кнопки
 * перематывают видео, полоски по правилу (самые отмеченные, жирное «…»), размытые спойлеры с переключателем,
 * «К текущей минуте» поверх низа списка, ветка с ответом, «Нравится», новый комментарий на минуте, живые события,
 * перечитывание после обрыва, во весь экран вместе с полосками (кнопкой и двойным щелчком), чат выключен.
 *
 * Автор: Чепела Даниил Максимович.
 *
 * Время видео — настоящее: видео в кадре играет без звука и перематывается, как его перематывает человек.
 */
const { USER, check, shot } = require("../lib.cjs");
const { OBJECT, chatDb } = require("../chat-mock.cjs");
const { playAt, pause, videoTime, until, openTitle, frameColumns, stripTexts, fullscreenOf } = require("../chat-page.cjs");

const SPOILER = "Спойлер, нажмите, чтобы показать";
const textOf = id => `.chat-list .comments__item[data-id="${id}"] > .comment__info > .comment__text`;
const blurred = (page, id) => page.$eval(textOf(id), t => t.classList.contains("is-spoiler") && /blur/.test(getComputedStyle(t).filter))
  .catch(() => null);

module.exports = {
  async chatDesktop(browser) {
    const db = chatDb({ me: USER });
    const { ctx, page, live, errors, frame } = await openTitle(browser, { db });
    const place = await page.evaluate(() => {
      const ep = document.querySelector("#avEpInfo").getBoundingClientRect(), chat = document.querySelector(".sp-chat").getBoundingClientRect();
      return { gap: Math.round(chat.left - ep.right), row: Math.abs((chat.top + chat.bottom) / 2 - (ep.top + ep.bottom) / 2) < 6 };
    });
    await check(page, "chat: the icon sits right after «Эпизод N», with the episode's count",
      place.row && place.gap >= 0 && place.gap <= 24 && (await page.textContent(".sp-chat__count")) === String(db.all().length), JSON.stringify(place));
    await check(page, "chat: the tab watches the episode thread", live.frames.some(f => f.type === "comments.watch" && f.objects?.[0] === OBJECT));
    await page.click(".sp-chat__btn");
    await until(page, () => document.querySelectorAll(".chat-list .comments__item").length > 0);
    await check(page, "chat: the panel replaces «Эпизоды», the icon is pressed",
      await page.isVisible(".chat-panel") && !(await page.isVisible(".ep-panel"))
      && (await page.getAttribute(".sp-chat__btn", "aria-pressed")) === "true");
    const even = await page.evaluate(() => {
      const main = document.querySelector(".watch-main").getBoundingClientRect(), card = document.querySelector("#avPlayerCard").getBoundingClientRect();
      const panel = document.querySelector(".chat-panel").getBoundingClientRect();
      return { top: Math.round(panel.top - main.top), bottom: Math.round(panel.bottom - card.bottom) };
    });
    await check(page, "chat: the chat column is even with the player column (top with the season tabs, bottom with the card)",
      Math.abs(even.top) <= 2 && Math.abs(even.bottom) <= 2, JSON.stringify(even));

    await playAt(frame, 575);
    const flew = await until(page, () => document.querySelectorAll(".chat-strip.is-in").length >= 3, null, 20000);
    await pause(frame);
    const texts = await stripTexts(page);
    const cut = await page.$$eval(".chat-strip.is-in", list => list.filter(s => s.querySelector("b")?.textContent === "…")
      .map(s => ({ chars: Array.from(s.textContent).length, weight: getComputedStyle(s.querySelector("b")).fontWeight })));
    await check(page, "chat: strips fly at their minutes; the long one is cut to 80 characters with a bold «…»",
      flew && texts.length === 3 && cut.length === 1 && cut[0].chars <= 81 && Number(cut[0].weight) >= 700, JSON.stringify({ texts, cut }));
    await check(page, "chat: minutes are seek buttons for our player; «Сейчас» follows the video",
      await page.$$eval(".comment__moment", b => b.length > 3 && b.every(x => !x.disabled))
      && /^Сейчас 09:[34]\d$/.test((await page.textContent(".chat-now")).trim()));
    const spoiler = await page.$eval(textOf(8), t => ({ role: t.getAttribute("role"), label: t.getAttribute("aria-label"), tab: t.tabIndex }));
    await check(page, "chat spoilers: a comment past «Сейчас» is blurred, read as «Спойлер, нажмите, чтобы показать»; watched ones are clear",
      await blurred(page, 8) && !(await blurred(page, 6)) && spoiler.role === "button" && spoiler.label === SPOILER && spoiler.tab === 0, JSON.stringify(spoiler));
    await frameColumns(page);
    await page.waitForTimeout(800);  // кадр после перемотки дорисовался, полоски проявились
    await shot(page, "chat-1600-light");
    await page.emulateMedia({ colorScheme: "dark" });
    await page.waitForTimeout(400);
    await shot(page, "chat-1600-dark");
    await page.emulateMedia({ colorScheme: "light" });

    await page.click(".chat-spoilers input");
    const shown = (await blurred(page, 8)) === false && (await blurred(page, 17)) === false;
    await page.click(".chat-spoilers input");
    await check(page, "chat spoilers: the «Спойлеры» switch shows everything and is remembered in this browser",
      shown && await blurred(page, 17) && (await page.evaluate(() => localStorage.getItem("av_chat_spoilers"))) === "hide");
    await page.click(textOf(8));
    await check(page, "chat spoilers: a tap opens just that one", !(await blurred(page, 8)) && await blurred(page, 9));

    await page.click('.chat-list .comments__item[data-id="6"] .comment__like');
    await until(page, () => document.querySelector('.comments__item[data-id="6"] .comment__like span')?.textContent === "9");
    await check(page, "chat: a like shows at once and goes to the server",
      (await page.getAttribute('.comments__item[data-id="6"] .comment__like', "aria-pressed")) === "true" && db.calls.includes("PUT /comments/6/like"));
    await page.dblclick('.chat-list .comments__item[data-id="7"] .comment__like');
    await page.waitForTimeout(800);
    await check(page, "chat: a double tap on the heart sends one request, not two",
      db.calls.filter(c => c === "PUT /comments/7/like").length === 1 && !db.calls.includes("DELETE /comments/7/like"));

    const at = Math.floor(await videoTime(frame));
    await page.fill(".chat-compose textarea", "Тут я заплакал");
    await page.press(".chat-compose textarea", "Enter");
    await until(page, () => [...document.querySelectorAll(".chat-strip")].some(s => s.textContent.includes("Тут я заплакал")));
    const sent = db.posted.at(-1) || {};
    await check(page, "chat: a new comment carries the minute where typing began and flies as a strip",
      sent.object === OBJECT && Math.abs(sent.moment - at) <= 1 && await page.isVisible(`.comments__item[data-id="${db.nextId}"]`), JSON.stringify(sent));

    await page.locator('.comments__item[data-id="2"] .comments__more').scrollIntoViewIfNeeded();
    await page.click('.comments__item[data-id="2"] .comments__more');
    await page.waitForSelector('.comments__item[data-id="22"]', { timeout: 8000 }).catch(() => {});
    await check(page, "chat: «2 ответа» opens the thread inside the comment, a reply to a reply says whom it answers",
      (await page.textContent('.comments__item[data-id="22"] .comment__replyto').catch(() => "")).includes("в ответ Сакура")
      && (await page.textContent('.comments__item[data-id="2"] .comments__more')).includes("Скрыть ответы"));
    await page.click('.comments__item[data-id="22"] .comment__reply');
    await page.fill(".chat-replybox textarea", "Тоже так думаю");
    await page.press(".chat-replybox textarea", "Enter");
    await until(page, () => [...document.querySelectorAll('.comments__item[data-id="2"] .comment__replies .comment__text')].some(t => t.textContent === "Тоже так думаю"));
    const reply = db.posted.at(-1) || {};
    await check(page, "chat: a reply goes into the thread with the root's minute and whom it answers",
      reply.thread === 2 && reply.reply_to === 22 && reply.moment === 275, JSON.stringify(reply));
    await page.click('.comments__item[data-id="21"] .comment__reply');
    const gap = await page.evaluate(() => {
      const b = document.querySelector(".chat-tonow"), line = document.querySelector(".chat-compose"), list = document.querySelector(".chat-list");
      if (!b || b.hidden) return null;
      const r = b.getBoundingClientRect(), l = list.getBoundingClientRect();
      return { height: Math.round(r.height), toLine: Math.round(line.getBoundingClientRect().top - r.bottom), overList: r.top >= l.top && r.bottom <= l.bottom };
    });
    await check(page, "chat: scrolled away — «К текущей минуте» floats over the list's bottom, full height, off the line above the field",
      gap && gap.height >= 30 && gap.toLine >= 12 && gap.overList, JSON.stringify(gap));
    await page.locator(".chat-panel").screenshot({ path: await shot(page, "chat-thread") });

    db.roots.push({ id: 600, object: OBJECT, author: { id: 9120, alias: "qa_9120", first_name: "Тору", last_name: "", avatar: null },
      text: "Только что написал", date: Math.floor(Date.now() / 1000), thread: null, moment: 590, reply_to: null,
      replies_count: 0, likes_count: 0, liked: false });
    const send = f => live.sockets.forEach(ws => ws.send(JSON.stringify({ type: "comment", object: OBJECT, thread: null, ...f })));
    send({ comment_id: 600, moment: 590, action: "added" });
    const added = await until(page, () => !!document.querySelector('.comments__item[data-id="600"]'));
    send({ comment_id: 6, moment: 579, action: "liked", likes_count: 15 });
    const liked = await until(page, () => document.querySelector('.comments__item[data-id="6"] .comment__like span')?.textContent === "15");
    send({ comment_id: 600, moment: 590, action: "deleted" });
    const gone = await until(page, () => !document.querySelector('.comments__item[data-id="600"]'));
    await check(page, "chat: live events add, like and delete without a reload", added && liked && gone, JSON.stringify({ added, liked, gone }));
    send({ comment_id: 81, thread: 8, moment: null, action: "deleted" });  // ответ в свёрнутой ветке Ино
    await check(page, "chat: a reply deleted in a folded thread takes «1 ответ» off its comment",
      await until(page, () => !document.querySelector('.comments__item[data-id="8"] .comments__more')));

    const before = db.calls.length, watches = live.frames.filter(f => f.type === "comments.watch").length;
    live.sockets.forEach(ws => ws.close());
    await page.waitForTimeout(4000);
    await check(page, "chat: after the link drops and returns, the thread is watched again and the window re-read",
      db.calls.slice(before).some(c => c.startsWith("GET /comments?") && c.includes("sort=moment") && c.includes("from="))
      && live.frames.filter(f => f.type === "comments.watch").length > watches);

    await page.evaluate(() => { document.querySelector(".chat-list").scrollTop = 0; });
    await check(page, "chat: scrolling the list up loads the earlier minutes",
      await until(page, () => !!document.querySelector('.chat-list .comments__item[data-id="1"]')));
    await page.locator('.comments__item[data-id="1"] .comment__moment').scrollIntoViewIfNeeded();
    await page.click('.comments__item[data-id="1"] .comment__moment');
    await page.waitForTimeout(800);
    const jumped = await videoTime(frame);
    await check(page, "chat: a minute button seeks our player there", Math.abs(jumped - 72) < 2, `video at ${jumped}`);
    // Другой кадр на той же странице (не родитель плеера) шлёт asoplay:seek — кадр плеера его не слушает
    await page.evaluate(() => {
      const spy = document.createElement("iframe");
      spy.setAttribute("sandbox", "allow-scripts allow-same-origin");
      spy.hidden = true;
      spy.srcdoc = '<script>parent.document.querySelector(".player-frame").contentWindow.postMessage({ type: "asoplay:seek", time: 300 }, "*");<\/script>';
      document.body.append(spy);
    });
    await page.waitForTimeout(1000);
    const stayed = await videoTime(frame);
    await check(page, "chat: another frame on the page cannot seek our player", Math.abs(stayed - 72) < 3, `video at ${stayed}`);

    await playAt(frame, 754);
    await until(page, () => document.querySelectorAll(".chat-strip.is-in").length >= 4, null, 20000);
    await pause(frame);
    const dense = await stripTexts(page);
    await check(page, "chat: a busy minute shows the four most liked; the rest stay in the list",
      dense.length === 4 && ["ВОТ ОНО", "Штарк красавчик", "Ферн просто огонь в этой сцене"].every(t => dense.includes(t))
      && dense.some(t => t.startsWith("Я ждал эту сцену")) && !dense.includes("мурашки по коже"), JSON.stringify(dense));
    await check(page, "chat spoilers: the episode reached 12:20 — its blur came off by itself; 13:02 is still blurred",
      !(await blurred(page, 9)) && await blurred(page, 17));
    await page.evaluate(() => {
      const list = document.querySelector(".chat-list"), first = list.querySelector('.comments__item[data-id="10"]');
      list.scrollTop = first.offsetTop;
    });
    await page.waitForTimeout(300);
    await frameColumns(page);
    await shot(page, "chat-dense");
    await page.locator(".sp-video").screenshot({ path: await shot(page, "chat-strip-cut") });

    await frame.click("#av-fullscreen");
    await page.waitForTimeout(1200);
    const full = await fullscreenOf(page);
    await shot(page, "chat-fullscreen");
    await frame.click("#av-fullscreen");
    await page.waitForTimeout(1000);
    const left = await fullscreenOf(page);
    // Двойной щелчок — по видео, в стороне от кнопки «Смотреть» и панели
    const vbox = await page.locator(".player-frame").boundingBox();
    await page.mouse.dblclick(vbox.x + 120, vbox.y + 120);
    await page.waitForTimeout(1200);
    const dbl = await fullscreenOf(page);
    await page.keyboard.press("Escape");
    await page.waitForTimeout(800);
    if (await fullscreenOf(page)) await page.evaluate(() => document.exitFullscreen());
    await check(page, "chat: our player goes full screen together with the strips (button, double click) and back",
      full?.cls === "sp-video" && full.strips >= 3 && left === null && dbl?.cls === "sp-video", JSON.stringify({ full, left, dbl }));

    await page.click(".sp-chat__btn");
    await check(page, "chat: closed — «Эпизоды» are back and nothing is drawn over the video",
      await page.isVisible(".ep-panel") && (await page.$$(".chat-strips")).length === 0);
    await frameColumns(page);
    await shot(page, "chat-off");
    await check(page, "chat: no page errors", errors.length === 0, errors.join(" | "));
    await ctx.close();
  },
};
