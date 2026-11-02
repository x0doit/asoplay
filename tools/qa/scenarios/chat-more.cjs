/*
 * Сценарий QA «chat-more» — чат серии вне компьютера 1600 px: гость, лист на телефоне в двух темах, 1280 px (колонка
 * чата встаёт справа от плеера), пусто и «комментарии недоступны», плеер Kodik («Тетрадь смерти»): полоски, перемотка
 * по минуте через его API и его собственный «во весь экран».
 *
 * Автор: Чепела Даниил Максимович.
 */
const { USER, check, shot, overflowing } = require("../lib.cjs");
const { chatDb } = require("../chat-mock.cjs");
const { playAt, pause, until, openTitle, frameColumns, stripTexts } = require("../chat-page.cjs");

// Комментарии к 1-й серии «Тетради смерти» на 5–40 с
function kodikDb() {
  const db = chatDb({ me: null });
  db.roots = [6, 10, 15, 21, 28, 40].map((m, i) => ({ id: 900 + i, object: "asoplay:episode:1535.1",
    author: { id: 9200 + i, alias: `qa_k${i}`, first_name: ["Лайт", "Миса", "Эл", "Рюк", "Мелло", "Ниа"][i], last_name: "", avatar: null },
    text: `Комментарий на ${m} секунде`, date: Math.floor(Date.now() / 1000) - 600 + i, thread: null, moment: m,
    reply_to: null, replies_count: 0, likes_count: i, liked: false }));
  return db;
}

module.exports = {
  // Гость: читает, «Ответить» нет, вместо поля — «Войдите», у сердца — подсказка
  async chatGuest(browser) {
    const db = chatDb({ me: null });
    const { ctx, page } = await openTitle(browser, { db, signed: false });
    await page.click(".sp-chat__btn");
    await until(page, () => document.querySelectorAll(".chat-list .comments__item").length > 0);
    await page.click('.chat-list .comments__item[data-id="1"] .comment__like');
    await check(page, "chat guest: no «Ответить», a sign-in row instead of the field, the heart says «Войдите, чтобы отметить»",
      (await page.$$(".comment__reply")).length === 0 && await page.isVisible(".chat-guest")
      && await page.isVisible('.comments__item[data-id="1"] .ed-tip.is-open .ed-tip__bubble')
      && !db.calls.some(c => c.startsWith("PUT ")));
    await frameColumns(page);
    await shot(page, "chat-guest");
    await ctx.close();
  },

  // Телефон: лист под видео, видео на экране, ничего не вылезает, крестик закрывает; тёмная тема
  async chatPhone(browser) {
    const db = chatDb({ me: USER });
    const { ctx, page, frame } = await openTitle(browser, { width: 375, height: 812, phone: true, db });
    await page.click(".sp-chat__btn");
    await until(page, () => document.querySelectorAll(".chat-list .comments__item").length > 0);
    await playAt(frame, 575);
    await until(page, () => document.querySelectorAll(".chat-strip.is-in").length >= 2, null, 20000);
    await pause(frame);
    const sheet = await page.evaluate(() => {
      const panel = document.querySelector(".chat-panel"), video = document.querySelector(".sp-video");
      return { sheet: panel.classList.contains("is-sheet"), top: Math.round(panel.getBoundingClientRect().top),
        videoBottom: Math.round(video.getBoundingClientRect().bottom), locked: document.documentElement.classList.contains("chat-sheet-open"),
        strips: document.querySelectorAll(".chat-strip.is-in").length, spoilers: document.querySelectorAll(".comment__text.is-spoiler").length };
    });
    const over = await overflowing(page);
    await check(page, "chat phone: a sheet right under the video, page locked, at most 2 strips, spoilers blurred, nothing sticks out",
      sheet.sheet && Math.abs(sheet.top - sheet.videoBottom) <= 2 && sheet.locked && sheet.strips <= 2 && sheet.spoilers > 0
      && over.scrollWidth <= over.vw, JSON.stringify({ ...sheet, over: over.items }));
    await shot(page, "chat-phone");
    await page.emulateMedia({ colorScheme: "dark" });
    await page.waitForTimeout(400);
    await shot(page, "chat-phone-dark");
    await page.click(".chat-close");
    await check(page, "chat phone: × closes the sheet and unlocks the page",
      !(await page.isVisible(".chat-panel")) && !(await page.evaluate(() => document.documentElement.classList.contains("chat-sheet-open"))));
    await ctx.close();
  },

  // 1280 px: колонка чата встаёт справа от плеера, со спойлерами
  async chatWide(browser) {
    const db = chatDb({ me: USER });
    const { ctx, page, frame } = await openTitle(browser, { width: 1280, height: 860, db });
    await page.click(".sp-chat__btn");
    await until(page, () => document.querySelectorAll(".chat-list .comments__item").length > 0);
    await playAt(frame, 575);
    await until(page, () => document.querySelectorAll(".chat-strip.is-in").length >= 3, null, 20000);
    await pause(frame);
    const side = await page.evaluate(() => {
      const card = document.querySelector("#avPlayerCard").getBoundingClientRect(), panel = document.querySelector(".chat-panel").getBoundingClientRect();
      return { right: panel.left >= card.right, bottom: Math.round(panel.bottom - card.bottom) };
    });
    await check(page, "chat 1280: the chat stands to the right of the player, even with the card",
      side.right && Math.abs(side.bottom) <= 2 && (await page.$$(".comment__text.is-spoiler")).length > 0, JSON.stringify(side));
    await frameColumns(page);
    await page.waitForTimeout(600);
    await shot(page, "chat-1280");
    // «Наверх» не ложится на отправку: низ чата у нижнего края окна — кнопки не видно; чат закрыт — она вернулась
    await page.evaluate(() => window.scrollBy(0, document.querySelector(".chat-panel").getBoundingClientRect().bottom - innerHeight));
    await page.waitForTimeout(500);
    const totop = await page.evaluate(() => {
      const send = document.querySelector('.chat-compose button[aria-label="Отправить"]').getBoundingClientRect();
      const hit = document.elementFromPoint(send.right - 4, send.top + send.height / 2);
      return { scrollY: Math.round(scrollY), shown: document.querySelector("#toTop").classList.contains("show"), send: !!hit?.closest(".chat-compose") };
    });
    await shot(page, "chat-1280-bottom");
    await page.click(".sp-chat__btn");
    const back = await until(page, () => document.querySelector("#toTop").classList.contains("show"), null, 3000);
    await check(page, "chat 1280: «Наверх» hides while it would cover the chat (the send button stays reachable) and comes back when the chat closes",
      totop.scrollY > 400 && !totop.shown && totop.send && back, JSON.stringify({ ...totop, back }));
    await ctx.close();
  },

  // Пусто и «комментарии недоступны» (ошибка с «Повторить»)
  async chatStates(browser) {
    const db = chatDb({ me: USER, empty: true });
    const { ctx, page } = await openTitle(browser, { width: 1600, height: 900, db });
    await page.click(".sp-chat__btn");
    await until(page, () => !!document.querySelector(".chat-list .ed-empty, .chat-list .comments__item"));
    await check(page, "chat: an empty episode says how to start", (await page.textContent(".chat-list .ed-empty__title")) === "Комментариев пока нет");
    await page.locator(".chat-panel").screenshot({ path: await shot(page, "chat-empty") });
    await page.click(".sp-chat__btn");
    db.fail = true;
    await page.click(".sp-chat__btn");
    await until(page, () => !!document.querySelector('.chat-list [data-act="retry"]'));
    await check(page, "chat: comments unavailable — an error with «Повторить»", (await page.textContent(".chat-list .ed-empty__title")) === "Чат не загрузился");
    await page.locator(".chat-panel").screenshot({ path: await shot(page, "chat-unavailable") });
    db.fail = false;
    await page.click('.chat-list [data-act="retry"]');
    await check(page, "chat: «Повторить» loads again", await until(page, () => !!document.querySelector(".chat-list .ed-empty, .chat-list .comments__item")));
    await ctx.close();
  },

  // Соцсеть отказывает: окно вперёд просится снова только после паузы (5 с, потом 10 с), а с Retry-After — после него,
  // а не на каждом сообщении плеера о времени (до 4 в секунду на зрителя). Две страницы: без Retry-After и с ним
  async chatBackoff(browser) {
    const refusals = async (retryAfter, seconds) => {
      const db = chatDb({ me: USER });
      const { ctx, page, frame } = await openTitle(browser, { width: 1600, height: 900, db });
      await page.click(".sp-chat__btn");
      await until(page, () => document.querySelectorAll(".chat-list .comments__item").length > 0);
      const windows = () => db.calls.filter(c => c.startsWith("GET /comments?") && c.includes("sort=moment") && c.includes("from=")).length;
      Object.assign(db, { fail: true, retryAfter });
      const before = windows();
      await playAt(frame, 200);
      await page.waitForTimeout(seconds * 1000);
      const asked = windows() - before;
      await ctx.close();
      return asked;
    };
    const plain = await refusals(0, 12);
    const told = await refusals(30, 12);
    await check(null, "chat: a refused window is asked again only after a pause (5 s, then 10 s), and only after Retry-After when the server sets it",
      plain === 2 && told === 1, JSON.stringify({ plain, told }));
  },

  // Kodik: время приходит из его событий, полоски поверх его кадра, «▶ m:ss» перематывает через его API
  async chatKodik(browser) {
    const db = kodikDb();
    const { ctx, page } = await openTitle(browser, { width: 1600, height: 900, db, signed: false, path: "/anime/1535" });
    const kodik = page.frames().find(f => /\/(serial|season|video|seria|uv)\//.test(new URL(f.url()).pathname));
    await page.click(".sp-chat__btn");
    await until(page, () => document.querySelectorAll(".chat-list .comments__item").length > 0);
    await kodik?.locator("a.play_button").click({ timeout: 8000 }).catch(() => {});
    const flew = await until(page, () => document.querySelectorAll(".chat-strip.is-in").length >= 1, null, 30000);
    const strips = await stripTexts(page);
    const seekable = await page.$$eval(".comment__moment", b => b.length > 0 && b.every(x => !x.disabled));
    await check(page, "chat Kodik: strips fly over Kodik at their minutes, minutes are seek buttons",
      flew && strips.every(t => t.startsWith("Комментарий на")) && seekable, JSON.stringify({ strips, seekable }));
    await frameColumns(page);
    await shot(page, "chat-kodik");
    await page.locator('.comments__item[data-id="905"] .comment__moment').click().catch(() => {});
    const jumped = await until(page, () => /^Сейчас 00:4\d$/.test(document.querySelector(".chat-now")?.textContent.trim() || ""), null, 12000);
    await check(page, "chat Kodik: «▶ 00:40» seeks Kodik through its API", jumped, (await page.textContent(".chat-now").catch(() => "")).trim());
    const box = await page.locator(".player-frame").boundingBox();
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
    const target = await kodik?.evaluate(() => {
      const e = document.querySelector(".fp-x-fullscreen");
      const r = e?.getBoundingClientRect();
      return r && r.width ? { x: r.x + r.width / 2, y: r.y + r.height / 2 } : null;
    }).catch(() => null);
    if (target) await page.mouse.click(box.x + target.x, box.y + target.y);
    await page.waitForTimeout(1500);
    const full = await page.evaluate(() => document.fullscreenElement?.className || null);
    await check(page, "chat Kodik: its own «во весь экран» works (the strips stay on the page, not in Kodik's full screen)",
      full === "player-frame", String(full));
    if (full) await page.evaluate(() => document.exitFullscreen());
    await ctx.close();
  },
};
