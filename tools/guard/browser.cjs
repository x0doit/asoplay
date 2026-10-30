/*
 * Браузер для сторожей и QA: Chrome без окна через playwright-core.
 *
 * Автор: Чепела Даниил Максимович.
 *
 * Пути берутся из окружения, а не из кода:
 *   PW_CORE — папка пакета playwright-core, если он не установлен рядом (иначе require("playwright-core"));
 *   CHROME  — путь к chrome.exe; без него — установленный Chrome (channel "chrome").
 */
const { chromium } = require(process.env.PW_CORE || "playwright-core");

/** Запустить Chrome без окна. */
function launch() {
  const chrome = process.env.CHROME;
  return chromium.launch(chrome ? { executablePath: chrome, headless: true } : { channel: "chrome", headless: true });
}

module.exports = { chromium, launch };
