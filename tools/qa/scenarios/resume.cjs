/*
 * Сценарий QA «resume» — «Продолжить с m:ss» у «Эпизод N». Вошедший видит кнопку в серии, где остановился (позиция
 * больше 30 с, серия не досмотрена); нажатие перематывает на эту секунду и пускает видео, кнопка уходит. Уходит она и
 * когда перемотал сам или сменил серию. Kodik: до его ▶ перемотка ждёт старта. Телефон — «▶ m:ss» с подписью.
 * Гостю, в досмотренной серии и на первых 30 с кнопки нет.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { check, shot } = require("../lib.cjs");
const { chatDb } = require("../chat-mock.cjs");
const { videoTime, until, openTitle } = require("../chat-page.cjs");

const ago = ms => new Date(Date.now() - ms).toISOString();
// Вошедший остановился в серии ep на last-й секунде; дальняя точка серии — far
function account(mal, ep, last, far = last, duration = 1440) {
  return {
    calls: [],
    history: [{ mal_id: mal, title: "QA", poster_url: "", last_episode: ep, episode_seconds: far, episode_duration: duration,
      episodes_total: 28, updated_at: ago(60e3) }],
    progress: { [mal]: [{ episode_num: ep, seconds: far, duration, last_seconds: last, updated_at: ago(60e3) }] },
  };
}
const resume = page => page.evaluate(() => {
  const b = document.querySelector(".sp-resume");
  if (!b || b.hidden || !b.getClientRects().length) return null;
  const shown = [...b.querySelectorAll("span")].filter(s => s.getClientRects().length).map(s => s.textContent.trim());
  return { text: shown.join(" "), aria: b.getAttribute("aria-label") };
});
const gone = page => until(page, () => !document.querySelector(".sp-resume")?.getClientRects().length, null, 8000);
const open = (browser, opts) => openTitle(browser, { width: 1440, height: 900, db: chatDb({ me: null }), ...opts });
const kodikFrame = page => page.frames().find(f => /^https?:\/\/[^/]+\/(serial|season|video|seria|uv)\//.test(f.url()));
const kodikTime = frame => frame.evaluate(() => document.querySelector("video")?.currentTime ?? -1).catch(() => -1);

module.exports = {
  async resume(browser) {
    // Наш кадр: кнопка, нажатие — 12:34 и пуск, кнопка уходит
    let t = await open(browser, { account: account(52991, 1, 754, 800) });
    const desktop = await resume(t.page);
    await shot(t.page, "resume-desktop");
    let pressed = null;
    if (desktop) {
      await t.page.click(".sp-resume");
      await t.page.waitForTimeout(2500);
      pressed = { time: Math.round(await videoTime(t.frame)), playing: await t.frame.evaluate(() => !document.querySelector("video").paused),
        gone: await gone(t.page) };
    }
    await check(t.page, "resume: a signed-in viewer sees «Продолжить с 12:34» at «Эпизод N»; the press seeks to 12:34, plays and the button goes",
      desktop?.text === "Продолжить с 12:34" && desktop.aria === "Продолжить с 12:34" && pressed && pressed.time >= 754 && pressed.time <= 760
        && pressed.playing && pressed.gone, JSON.stringify({ desktop, pressed }));
    await t.ctx.close();

    // Перемотал сам (→ на 5 с) — кнопка уходит; сменил серию — тоже
    t = await open(browser, { account: account(52991, 1, 754, 800) });
    const before = await resume(t.page);
    await t.page.evaluate(() => document.activeElement?.blur());
    await t.page.keyboard.press("ArrowRight");
    const bySeek = await gone(t.page);
    await t.ctx.close();
    t = await open(browser, { account: account(52991, 1, 754, 800) });
    const before2 = await resume(t.page);
    await t.page.click("#avNextEp");
    const byEpisode = await gone(t.page);
    await check(t.page, "resume: the button goes when the viewer seeks by himself and when the episode changes",
      !!before && bySeek && !!before2 && byEpisode, JSON.stringify({ before: !!before, bySeek, before2: !!before2, byEpisode }));
    await t.ctx.close();

    // Телефон и телефон боком: «▶ 12:34» с подписью «Продолжить с 12:34»; шапка плеера не выше, чем у гостя
    const headHeight = page => page.evaluate(() => Math.round(document.querySelector(".sp-head").getBoundingClientRect().height));
    const small = {};
    for (const [name, width, height] of [["phone", 375, 812], ["landscape", 844, 390]]) {
      t = await open(browser, { width, height, phone: true, account: account(52991, 1, 754, 800) });
      const button = await resume(t.page), head = await headHeight(t.page);
      await shot(t.page, `resume-${name}`);
      await t.ctx.close();
      const guest = await open(browser, { width, height, phone: true, signed: false });
      small[name] = { button, head, guestHead: await headHeight(guest.page) };
      await guest.ctx.close();
    }
    await check(null, "resume phone and landscape: «▶ 12:34» labelled «Продолжить с 12:34», the player head does not grow",
      Object.values(small).every(s => s.button?.text === "12:34" && s.button.aria === "Продолжить с 12:34" && s.head <= s.guestHead),
      JSON.stringify(small));

    // Гостю, в досмотренной серии и на первых 30 с кнопки нет
    const none = {};
    t = await open(browser, { signed: false });
    none.guest = await resume(t.page);
    await t.ctx.close();
    t = await open(browser, { account: account(52991, 1, 1400, 1400) });
    none.finished = await resume(t.page);
    await t.ctx.close();
    t = await open(browser, { account: account(52991, 1, 25, 25) });
    none.first30 = await resume(t.page);
    await t.ctx.close();
    await check(null, "resume: no button for a guest, after a finished episode or within the first 30 s",
      !none.guest && !none.finished && !none.first30, JSON.stringify(none));

    // Kodik: кнопка видна сразу; его пуск через API не прошёл — перемотка ждёт ▶ зрителя и встаёт на 0:40
    t = await open(browser, { path: "/anime/1535", account: account(1535, 1, 40, 40, 1420) });
    // Глушим в кадре Kodik команду пуска со страницы — как если бы браузер не дал запустить видео без нажатия.
    // Слушатель встаёт до скриптов Kodik, поэтому страница перезагружается
    await t.page.addInitScript(() => {
      if (!/\/(serial|season|video|seria|uv)\//.test(location.pathname)) return;
      window.addEventListener("message", e => {
        if (e.data?.key === "kodik_player_api" && e.data?.value?.method === "play") e.stopImmediatePropagation();
      }, true);
    });
    await t.page.reload({ waitUntil: "domcontentloaded" });
    await until(t.page, () => !!document.querySelector(".sp-resume"), null, 30000);
    let kodik = null;
    for (let i = 0; i < 30 && !kodik; i++) {
      kodik = kodikFrame(t.page);
      if (!kodik || !(await kodik.locator("a.play_button").count().catch(() => 0))) { kodik = null; await t.page.waitForTimeout(1000); }
    }
    const kButton = await resume(t.page);
    let kodikDone = null;
    if (kodik && kButton) {
      await t.page.click(".sp-resume");
      await t.page.waitForTimeout(3000);
      const startedByPage = (await kodikTime(kodik)) > 0;
      await kodik.locator("a.play_button").click({ timeout: 8000 }).catch(() => {});
      let time = -1;
      for (let i = 0; i < 20 && time < 39; i++) { await t.page.waitForTimeout(1000); time = await kodikTime(kodik); }
      kodikDone = { startedByPage, time: Math.round(time), gone: await gone(t.page) };
    }
    await check(t.page, "resume Kodik: the button shows; pressed before Kodik's ▶, the seek waits for the start and lands on 0:40",
      kButton?.text === "Продолжить с 0:40" && kodikDone && !kodikDone.startedByPage && kodikDone.time >= 39 && kodikDone.time <= 50
        && kodikDone.gone, JSON.stringify({ kodik: !!kodik, kButton, kodikDone }));
    await t.ctx.close();
  },
};
