/*
 * QA фронтенда EDDAS: проходит все экраны на компьютере и телефоне, собирает ошибки консоли, вылезающие за экран
 * элементы и проваленные проверки, снимает каждое состояние. Экраны аккаунта — на подменённых ответах: без базы
 * и без входа. Нужен запущенный сервер (SITE, по умолчанию http://127.0.0.1:8787); браузер — tools/guard/browser.cjs.
 *
 * Автор: Чепела Даниил Максимович.
 *
 *   node tools/qa/eddas-qa.cjs [сценарий,сценарий]     выход 1 — если хоть одна проверка не прошла
 */
const fs = require("fs");
const path = require("path");
const { launch } = require("../guard/browser.cjs");
const { OUT, SITE, STATUS, quietSocialSession, report } = require("./lib.cjs");

const ONLY = process.argv[2] ? new Set(process.argv[2].split(",")) : null;
// Сценарии по порядку: файл scenarios/<имя>.cjs отдаёт свой сценарий
const scenarios = Object.assign({}, ...["desktop-guest", "desktop-account", "phone", "as-login", "friends", "realtime",
  "admin", "admin-report", "landscape", "embedded", "narrow"]
  .map(name => require(`./scenarios/${name}.cjs`)));

(async () => {
  fs.rmSync(OUT, { recursive: true, force: true });
  fs.mkdirSync(OUT, { recursive: true });
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
  console.log(`\n${report.length - failed.length}/${report.length} checks passed`);
  for (const f of failed) console.log("FAIL:", f.name, f.detail);
  process.exitCode = failed.length ? 1 : 0;
})();
