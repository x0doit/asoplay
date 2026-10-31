/*
 * QA фронтенда EDDAS: проходит все экраны на компьютере и телефоне, собирает ошибки консоли, вылезающие за экран
 * элементы и проваленные проверки, снимает каждое состояние. Экраны аккаунта — на подменённых ответах: без базы
 * и без входа. Нужен запущенный сервер (SITE, по умолчанию http://127.0.0.1:8787); браузер — tools/guard/browser.cjs.
 *
 * Автор: Чепела Даниил Максимович.
 *
 *   node tools/qa/eddas-qa.cjs [сценарий,сценарий]     выход 1 — если хоть одна проверка не прошла
 * Сценарий называют именем файла (lists-live) или его ключом (listsLive). Неизвестное имя — выход 2 со списком
 * сценариев: опечатка не должна выглядеть зелёным прогоном.
 */
const fs = require("fs");
const path = require("path");
const { OUT, SITE, STATUS, foreign, quietSocialSession, report } = require("./lib.cjs");

// Сценарии по порядку: файл scenarios/<имя>.cjs отдаёт свой сценарий под ключом (lists-live.cjs → listsLive)
const FILES = ["desktop-guest", "desktop-account", "phone", "as-login", "friends", "realtime", "lists-live", "admin",
  "admin-report", "landscape", "embedded", "narrow"];
const scenarios = {}, byName = {};
for (const file of FILES) {
  const found = require(`./scenarios/${file}.cjs`);
  for (const key of Object.keys(found)) {
    scenarios[key] = found[key];
    byName[key] = [key];
  }
  byName[file] = Object.keys(found);  // имя файла — все его сценарии
}
const asked = (process.argv[2] || "").split(",").map(name => name.trim()).filter(Boolean);
const unknown = asked.filter(name => !byName[name]);
if (unknown.length) {
  console.error(`нет таких сценариев: ${unknown.join(", ")}`);
  console.error(`есть: ${FILES.map(file => `${file} (${byName[file].join(", ")})`).join(", ")}`);
  process.exit(2);
}
const ONLY = asked.length ? new Set(asked.flatMap(name => byName[name])) : null;

(async () => {
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });
  const { launch } = require("../guard/browser.cjs");  // после проверки имён: опечатка видна и без браузера
  const browser = await launch();
  try {
    const h = await (await fetch(SITE + "/health")).json();
    Object.assign(STATUS, { ok: true, sources: h.sources || [], vpn: !!h.vpn });
  } catch (_) { /* the server is down: scenarios will say so */ }
  const newContext = browser.newContext.bind(browser);
  browser.newContext = async (opts) => quietSocialSession(await newContext(opts));
  for (const [name, run] of Object.entries(scenarios)) {
    if (ONLY && !ONLY.has(name)) continue;
    console.log(`== ${name}`);
    try { await run(browser); } catch (e) { report.push({ name: `${name} crashed`, ok: false, detail: String(e).slice(0, 300) }); console.log("  CRASH", String(e).slice(0, 300)); }
  }
  await browser.close();
  const failed = report.filter(r => !r.ok);
  fs.writeFileSync(path.join(OUT, "_report.json"), JSON.stringify(report, null, 2));
  // Ошибки консоли чужих iframe (трейлер, плеер) проверку не валят, но видны: страница, источник и текст
  fs.writeFileSync(path.join(OUT, "_foreign_iframes.json"), JSON.stringify(foreign, null, 2));
  const noAddress = foreign.filter(f => !f.source).length;  // не из файлов страницы: вставка Playwright, сам браузер
  console.log(`\nчужие iframe: ${foreign.length - noAddress}, без адреса: ${noAddress}`);
  for (const f of foreign) console.log(`  ${f.scenario} | ${f.page} | ${f.source || "без адреса"} | ${f.text}`);
  console.log(`\n${report.length - failed.length}/${report.length} checks passed`);
  for (const f of failed) console.log("FAIL:", f.name, f.detail);
  if (!report.length) console.log("FAIL: ни одной проверки — нечего считать зелёным");
  process.exitCode = failed.length || !report.length ? 1 : 0;
})();
