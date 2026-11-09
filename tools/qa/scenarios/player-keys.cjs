/*
 * Сценарий QA «player-keys» — клавиши нашего плеера, как в привычных плеерах: ← и → — на 5 с назад и вперёд, пробел —
 * пауза и пуск. Фокус на странице (клавишу передаёт страница) и в кадре (ловит сам кадр); набор текста в чате серии
 * и в поиске плеер не трогает; во весь экран — и контейнер видео страницы, и собственный экран кадра.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { USER, check } = require("../lib.cjs");
const { chatDb } = require("../chat-mock.cjs");
const { playAt, videoTime, openTitle } = require("../chat-page.cjs");

const isPaused = frame => frame.evaluate(() => document.querySelector("video").paused);
const toPage = page => page.evaluate(() => document.activeElement?.blur());
const toFrame = page => page.locator(".player-frame").focus();

// Видео на паузе: на сколько секунд его сдвинула клавиша
async function shift(page, frame, key) {
  const before = await videoTime(frame);
  await page.keyboard.press(key);
  await page.waitForTimeout(400);
  return Math.round((await videoTime(frame)) - before);
}

// → и ← на паузе, затем пробел дважды: пуск и снова пауза; страница при этом не листается
async function keys(page, frame) {
  await playAt(frame, 100, false);
  const right = await shift(page, frame, "ArrowRight");
  const left = await shift(page, frame, "ArrowLeft");
  const scroll = await page.evaluate(() => scrollY);
  await page.keyboard.press(" ");
  await page.waitForTimeout(600);
  const played = !(await isPaused(frame));
  await page.keyboard.press(" ");
  await page.waitForTimeout(500);
  const pausedAgain = await isPaused(frame);
  const scrolled = Math.round(await page.evaluate(() => scrollY)) !== Math.round(scroll);
  return { right, left, played, pausedAgain, scrolled, ok: right === 5 && left === -5 && played && pausedAgain && !scrolled };
}

// Набор текста в поле: клавиши остаются полю, видео не двигается и не запускается
async function typing(page, frame, field) {
  await playAt(frame, 100, false);
  await page.click(field);
  await page.keyboard.type("фр");
  for (const key of ["ArrowLeft", "ArrowRight", " "]) await page.keyboard.press(key);
  await page.waitForTimeout(500);
  const time = Math.round(await videoTime(frame)), paused = await isPaused(frame);
  const value = await page.inputValue(field);
  return { time, paused, value, ok: time === 100 && paused && value === "фр " };
}

module.exports = {
  async playerKeys(browser) {
    const { ctx, page, frame, errors } = await openTitle(browser, { width: 1600, height: 900, db: chatDb({ me: USER }) });
    if (!frame) {
      await check(page, "keys: the title has our player frame with a video", false);
      await ctx.close();
      return;
    }
    await toPage(page);
    const onPage = await keys(page, frame);
    await check(page, "keys, focus on the page: → and ← move the video by 5 s, space plays and pauses, the page does not scroll",
      onPage.ok, JSON.stringify(onPage));
    await toFrame(page);
    const inFrame = await keys(page, frame);
    await check(page, "keys, focus in the player frame: → and ← by 5 s, space plays and pauses", inFrame.ok, JSON.stringify(inFrame));

    await page.click(".sp-chat__btn");
    await page.waitForSelector(".chat-compose textarea", { timeout: 15000 }).catch(() => {});
    const chat = await typing(page, frame, ".chat-compose textarea");
    const search = await typing(page, frame, "#appSearchInput");
    await check(page, "keys: typing in the episode chat and in the search moves nothing in the player",
      chat.ok && search.ok, JSON.stringify({ chat, search }));
    await page.fill("#appSearchInput", "");
    // Пробел на кнопке страницы нажимает её (чат закрывается), плеер не трогает
    await playAt(frame, 100, false);
    await page.locator(".sp-chat__btn").focus();
    await page.keyboard.press(" ");
    await page.waitForTimeout(600);
    const button = { chatClosed: await page.locator(".chat-panel").isHidden(), paused: await isPaused(frame) };
    // Открытое окно (жалоба на видео) — стрелки не двигают видео за ним
    await page.click("#avReportBtn");
    await page.waitForSelector("[aria-modal='true']", { timeout: 5000 }).catch(() => {});
    const modal = await page.evaluate(() => !!document.querySelector("[aria-modal='true']"));
    const behind = await shift(page, frame, "ArrowRight");
    await page.keyboard.press("Escape");
    await page.waitForTimeout(400);
    await check(page, "keys: space on a page button presses the button; with a dialog open the arrows leave the video alone",
      button.chatClosed && button.paused && modal && behind === 0, JSON.stringify({ ...button, modal, behind }));

    // Во весь экран страницы — контейнер видео; клавиши с фокусом на странице
    await frame.click("#av-fullscreen");
    await page.waitForTimeout(1200);
    const box = await page.evaluate(() => document.fullscreenElement?.className || null);
    await toPage(page);
    await playAt(frame, 100, false);
    const pageFull = await shift(page, frame, "ArrowRight");
    await page.evaluate(() => document.fullscreenElement && document.exitFullscreen());
    await page.waitForTimeout(800);
    // Собственный экран кадра — клавиши с фокусом в кадре
    await frame.evaluate(() => document.documentElement.requestFullscreen()).catch(() => {});
    await page.waitForTimeout(800);
    const own = await frame.evaluate(() => !!document.fullscreenElement);
    await toFrame(page);
    await playAt(frame, 100, false);
    const frameFull = await shift(page, frame, "ArrowRight");
    await frame.evaluate(() => document.fullscreenElement && document.exitFullscreen()).catch(() => {});
    await check(page, "keys in full screen: the page's video box and the frame's own screen both take →",
      box === "sp-video" && pageFull === 5 && own && frameFull === 5, JSON.stringify({ box, pageFull, own, frameFull }));
    await check(page, "keys: no page errors", errors.length === 0, errors.join(" | "));
    await ctx.close();
  },
};
