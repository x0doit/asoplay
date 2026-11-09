/*
 * Сценарий QA «autonext» — автопереключение серий у гостя (оно у гостя включено): серия доигрывает до конца, и
 * страница сама включает следующую. Kodik (HLS, «Тетрадь смерти») и большой MP4 через /player/proxy («Фрирен»):
 * у MP4 ещё кусок у конца файла по Range должен прийти быстро — без этого видео не доигрывает. Серию перематывают
 * к концу через несколько секунд после начала, как проверяет человек: переход ждёт не часов, а позиции плеера в
 * конце серии. Часы страницы не трогаются — со сдвигом часов сценарий не видел, что перемотка к концу не работает.
 *
 * Кеш nginx перед приложением не пропускает к нему Range, и кусок у конца MP4 приходит минутами. Локально nginx
 * нет — запустите сценарий и против сайта: SITE=https://<сайт>.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, check, settle } = require("../lib.cjs");

const episode = page => page.evaluate(() => document.querySelector("#avEpInfo .sp-ep-num")?.textContent || "");

// Кадр, где есть <video>; у Kodik он появляется только после его кнопки «играть»
async function videoFrame(page) {
  for (let tries = 0; tries < 4; tries++) {
    for (const f of page.frames()) {
      if (await f.evaluate(() => !!document.querySelector("video")).catch(() => false)) return f;
    }
    const kodik = page.frames().find(f => /\/(serial|season|video)\//.test(new URL(f.url()).pathname));
    if (kodik) await kodik.locator("a.play_button").click({ timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(6000);
  }
  return null;
}

// Серия доиграла — включилась следующая
async function playsToTheEnd(browser, path, name) {
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: "ru-RU" });
  const page = await ctx.newPage();
  await page.goto(SITE + path, { waitUntil: "domcontentloaded" }); await settle(page, 8000);
  const frame = await videoFrame(page);
  const started = frame && await frame.evaluate(async () => {
    const v = document.querySelector("video");
    v.muted = true;
    await v.play().catch(() => {});
    await new Promise(r => setTimeout(r, 5000));
    return { duration: v.duration, src: v.currentSrc };
  }).catch(() => null);
  let tail = null;
  if (started && started.src.includes("/player/proxy")) {
    // Кусок у конца файла — как его попросит браузер при перемотке и под конец серии. Просит Playwright, а не страница:
    // локально видео идёт с другого origin (localhost против 127.0.0.1), и Content-Range странице без CORS не виден —
    // размер файла был бы 0, а запрос «с нуля до конца» — весь файл
    const api = page.context().request;
    tail = await (async () => {
      const head = await api.get(started.src, { headers: { Range: "bytes=0-0" }, timeout: 10000 });
      const total = Number((head.headers()["content-range"] || "").split("/")[1] || 0);
      const begun = Date.now();
      try {
        const resp = await api.get(started.src, { headers: { Range: `bytes=${Math.max(0, total - 4096)}-` }, timeout: 10000 });
        await resp.body();
        return { status: resp.status(), ms: Date.now() - begun, total };
      } catch (e) {
        return { status: 0, ms: Date.now() - begun, total, error: e.message.split("\n")[0] };
      }
    })().catch(e => ({ status: 0, error: e.message.split("\n")[0] }));
    await check(page, `autonext ${name}: the end of the file comes by Range at once`,
      tail.status === 206 && tail.total > 0 && tail.ms < 3000, JSON.stringify(tail));
  }
  const before = await episode(page);
  await frame?.evaluate(() => {
    const v = document.querySelector("video");
    v.currentTime = v.duration - 5;
    return v.play().catch(() => {});
  }).catch(() => {});
  // Сколько прошло от перемотки за 5 секунд до конца до смены серии — до 20 секунд
  const seeked = Date.now();
  let after = before, switchMs = null;
  while (Date.now() - seeked < 20000) {
    await page.waitForTimeout(250);
    after = await episode(page).catch(() => "");
    if (after && after !== before) { switchMs = Date.now() - seeked; break; }
  }
  await check(page, `autonext ${name}: the next episode starts by itself`, !!started && before === "1" && after === "2",
    `${JSON.stringify(started && { duration: started.duration, switch_ms: switchMs })} ${before} → ${after}`);
  await ctx.close();
}

module.exports = {
  async autonext(browser) {
    await playsToTheEnd(browser, "/anime/1535", "Kodik");
    await playsToTheEnd(browser, "/anime/52991", "MP4 via /player/proxy");
  },
};
