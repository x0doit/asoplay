/*
 * Сценарий QA «chat-replay» — полоски чата серии после перемотки, как в повторах чата Twitch и YouTube. Назад — всё,
 * что дальше новой позиции, снова ждёт своего момента; вперёд — пропущенное пачкой не высыпается. Кнопка минуты
 * комментария ведёт ровно к нему, и его полоска взлетает. Свой и чужой живой комментарии после перемотки назад —
 * снова. Пауза, перемотка, пуск. Другая озвучка: новый плеер с перемоткой — полоски с новой позиции. Наш кадр и Kodik.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { USER, check } = require("../lib.cjs");
const { chatDb, OBJECT } = require("../chat-mock.cjs");
const { playAt, pause, videoTime, until, openTitle, videoFrame } = require("../chat-page.cjs");

const strip = (page, text, ms = 15000) => until(page, t => [...document.querySelectorAll(".chat-strip")].some(s => s.textContent.includes(t)), text, ms);
const anyOf = (page, texts) => page.evaluate(list => [...document.querySelectorAll(".chat-strip")].map(s => s.textContent)
  .filter(s => list.some(t => s.includes(t))), texts);
const minute = async (page, id) => {
  const b = page.locator(`.comments__item[data-id="${id}"] .comment__moment`);
  await b.scrollIntoViewIfNeeded();
  await b.click();
};
const kodikFrame = page => page.frames().find(f => /^https?:\/\/[^/]+\/(serial|season|video|seria|uv)\//.test(f.url()));
const kodikSeek = (page, s) => page.evaluate(t => document.querySelector(".player-frame").contentWindow
  .postMessage({ key: "kodik_player_api", value: { method: "seek", seconds: t } }, "*"), s);

module.exports = {
  async chatReplay(browser) {
    const db = chatDb({ me: USER });
    const { ctx, page, frame, live } = await openTitle(browser, { width: 1600, height: 900, db });
    await page.click(".sp-chat__btn");
    await until(page, () => document.querySelectorAll(".chat-list .comments__item").length > 0);

    // Назад: полоска на 4:35 была, перемотка на 4:15 и пуск — снова на 4:35
    await playAt(frame, 268);
    const first = await strip(page, "Вот это поворот!");
    await page.waitForTimeout(8000);
    await playAt(frame, 255);
    const back = await strip(page, "Вот это поворот!", 30000);
    // Кнопка минуты комментария: видео ушло дальше, «▶ 4:35» — к нему, и его полоска взлетает
    await playAt(frame, 300);
    await page.waitForTimeout(2000);
    await minute(page, 2);
    const byMinute = await strip(page, "Вот это поворот!", 8000);
    await check(page, "chat replay: after seeking back the strip comes again at its minute; its «▶ 4:35» lands on it and it flies",
      first && back && byMinute, JSON.stringify({ first, back, byMinute }));

    // Вперёд через несколько комментариев — пачки нет, дальше идёт то, что с новой позиции
    await playAt(frame, 250);
    await page.waitForTimeout(3000);
    await playAt(frame, 590);
    await page.waitForTimeout(2500);
    const burst = await anyOf(page, ["Вот это поворот!", "Химмель лучший", "Этот взгляд Фрирен"]);
    const next = await strip(page, "Сейчас будет моя любимая часть", 25000);
    await check(page, "chat replay: a seek forward over several comments shows no burst, then what comes after the new place",
      burst.length === 0 && next, JSON.stringify({ burst, next }));

    // Свой и чужой живой — после перемотки назад снова
    await playAt(frame, 100);
    await page.waitForTimeout(1500);
    await page.fill(".chat-compose textarea", "Своя полоска");
    await page.press(".chat-compose textarea", "Enter");
    const own = await strip(page, "Своя полоска", 8000);
    const liveAt = Math.floor(await videoTime(frame)) + 1;
    db.roots.push({ id: 777, object: OBJECT, author: { id: 9300, alias: "qa_live", first_name: "Живой", last_name: "", avatar: null },
      text: "Живая полоска", date: Math.floor(Date.now() / 1000), thread: null, moment: liveAt, reply_to: null, replies_count: 0,
      likes_count: 0, liked: false });
    for (const ws of live.sockets) ws.send(JSON.stringify({ type: "comment", object: OBJECT, comment_id: 777, thread: null, moment: liveAt, action: "added" }));
    const liveFirst = await strip(page, "Живая полоска", 8000);
    await page.waitForTimeout(8000);
    await playAt(frame, (db.posted.at(-1)?.moment || 100) - 12);
    const ownAgain = await strip(page, "Своя полоска", 25000);
    const liveAgain = await strip(page, "Живая полоска", 15000);
    await check(page, "chat replay: my fresh comment and someone's live one both fly again after a seek back",
      own && liveFirst && ownAgain && liveAgain, JSON.stringify({ own, liveFirst, ownAgain, liveAgain }));

    // Пауза, перемотка, пуск
    await pause(frame);
    await playAt(frame, 200, false);
    await page.waitForTimeout(2000);
    await playAt(frame, 268, false);
    await page.waitForTimeout(2000);
    await frame.evaluate(() => document.querySelector("video").play());
    const paused = await strip(page, "Вот это поворот!", 20000);
    // Другая озвучка: новый плеер; перемотка на 4:28 — полоска на 4:35
    let dub = { other: false };
    await page.click("#avDubTrigger");
    const items = page.locator(".sp-dub-item");
    if (await items.count().catch(() => 0) > 1) {
      const before = await page.evaluate(() => document.querySelector(".player-frame")?.src || "");
      await items.nth(1).click();
      await until(page, s => (document.querySelector(".player-frame")?.src || "") !== s, before, 20000);
      const fresh = await videoFrame(page);
      dub = { other: true, frame: !!fresh };
      if (fresh) {
        await playAt(fresh, 268);
        dub.strip = await strip(page, "Вот это поворот!", 25000);
      }
    } else {
      await page.keyboard.press("Escape");
    }
    await check(page, "chat replay: pause, seek, play — the strip comes; on another dub the new player seeked back shows it too",
      paused && (!dub.other || dub.strip === true), JSON.stringify({ paused, dub }));
    await ctx.close();

    // Kodik: назад его перемоткой — полоска снова; «▶ 0:21» — его полоска взлетает
    const kdb = chatDb({ me: null });
    kdb.roots = [6, 10, 15, 21, 28, 40].map((m, i) => ({ id: 900 + i, object: "asoplay:episode:1535.1",
      author: { id: 9200 + i, alias: `qa_k${i}`, first_name: ["Лайт", "Миса", "Эл", "Рюк", "Мелло", "Ниа"][i], last_name: "", avatar: null },
      text: `Комментарий на ${m} секунде`, date: Math.floor(Date.now() / 1000) - 600 + i, thread: null, moment: m,
      reply_to: null, replies_count: 0, likes_count: i, liked: false }));
    const k = await openTitle(browser, { width: 1600, height: 900, db: kdb, signed: false, path: "/anime/1535" });
    await k.page.click(".sp-chat__btn");
    await kodikFrame(k.page)?.locator("a.play_button").click({ timeout: 8000 }).catch(() => {});
    const kFirst = await strip(k.page, "Комментарий на 15 секунде", 40000);
    await k.page.waitForTimeout(10000);
    await kodikSeek(k.page, 3);
    const kBack = await strip(k.page, "Комментарий на 6 секунде", 20000);
    await k.page.waitForTimeout(25000);
    await minute(k.page, 903);
    const kMinute = await strip(k.page, "Комментарий на 21 секунде", 10000);
    await check(k.page, "chat replay Kodik: a seek back brings the strips again; «▶ 0:21» lands on its comment and it flies",
      kFirst && kBack && kMinute, JSON.stringify({ kFirst, kBack, kMinute }));
    await k.ctx.close();
  },
};
