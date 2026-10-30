/*
 * Сценарий QA «as-login» — «Войти через AnimeSocial»: выключено, вошедший в соцсеть, форма почты, «это вы?», окно для гостя.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { SITE, check, shot, settle } = require("../lib.cjs");

module.exports = {
  // «Войти через AnimeSocial»: hidden while the server switch is off. With it on (mocked /auth/config and
  // the social network's «who is signed in» answer), the login page opens on the button for someone
  // signed in to AnimeSocial and on the mail form for everyone else; guests get «Имя Фамилия, это вы?».
  async asLogin(browser) {
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, colorScheme: "light", locale: "ru-RU" });
    const page = await ctx.newPage();
    const errors = []; page.on("pageerror", e => errors.push(String(e.message).slice(0, 200)));
    // The local server may run with the switch on (.env): force each state through /auth/config
    let asLogin = false;
    await page.route("**/auth/config", async route => {
      const resp = await route.fetch();
      await route.fulfill({ response: resp, json: { ...(await resp.json()), as_login: asLogin, as_api_url: "https://social.test/api/v2" } });
    });
    await page.goto(SITE + "/login", { waitUntil: "domcontentloaded" }); await settle(page, 1000);
    await check(page, "as-login: switched off — mail form only", !(await page.$(".auth-as"))
      && await page.isVisible("#avLoginForm") && !(await page.$("[data-auth-switch]")));
    const field = async () => page.evaluate(() => [document.querySelector('label[for="avLoginInput"]')?.textContent.trim(), document.querySelector("#avLoginInput")?.type].join(" / "));
    await check(page, "as-login: switched off — login or email, checked here", await field() === "Логин или электронная почта / text", await field());

    let socialUser = { id: 7, first_name: "Сакура", last_name: "Харуно", avatar: null };
    asLogin = true;
    await page.route("https://social.test/api/v2/auth/web/session", route => route.fulfill({
      status: 200, contentType: "application/json",
      headers: { "Access-Control-Allow-Origin": SITE, "Access-Control-Allow-Credentials": "true" },
      body: JSON.stringify({ ok: true, data: socialUser ? { logged_in: true, user: socialUser } : { logged_in: false } }),
    }));

    await page.goto(SITE + "/login?next=%2Fanime%2F52991", { waitUntil: "domcontentloaded" }); await settle(page, 1200);
    const label = (await page.textContent(".auth-as").catch(() => "")).trim();
    const href = await page.getAttribute(".auth-as", "href").catch(() => "");
    await check(page, "as-login: signed in to AnimeSocial — «Продолжить как Имя Фамилия»", /Продолжить как Сакура Харуно$/.test(label), label);
    await check(page, "as-login: the button starts the flow with next", /\/auth\/as\/start\?next=%2Fanime%2F52991$/.test(href || ""), href);
    await check(page, "as-login: the mail form waits behind «Войти через почту»", !(await page.isVisible("#avLoginForm")));
    await shot(page, "as-login-known");
    await page.click('[data-auth-switch="mail"]'); await page.waitForTimeout(200);
    await check(page, "as-login: «Войти через почту» shows the form", await page.isVisible("#avLoginForm") && !(await page.isVisible(".auth-as"))
      && (await page.evaluate(() => document.activeElement?.id)) === "avLoginInput");
    await check(page, "as-login: with AnimeSocial the form asks for the email, as on its site", await field() === "Электронная почта / email", await field());
    await shot(page, "as-login-mail");
    await page.click('[data-auth-switch="as"]'); await page.waitForTimeout(200);
    await check(page, "as-login: and back to the button", await page.isVisible(".auth-as") && !(await page.isVisible("#avLoginForm")));

    socialUser = null;
    await page.goto(SITE + "/login", { waitUntil: "domcontentloaded" }); await settle(page, 1200);
    await check(page, "as-login: not signed in to AnimeSocial — the mail form first",
      await page.isVisible("#avLoginForm") && !(await page.isVisible(".auth-as")) && await page.isVisible('[data-auth-switch="as"]'));
    await page.goto(SITE + "/login?as=denied", { waitUntil: "domcontentloaded" }); await settle(page, 1200);
    await check(page, "as-login: a refusal opens the button view with the message", await page.isVisible(".auth-as") && await page.isVisible(".auth-as-error"));

    socialUser = { id: 7, first_name: "Сакура", last_name: "Харуно", avatar: null };
    await page.goto(SITE + "/", { waitUntil: "domcontentloaded" }); await settle(page, 2000);
    const hintTitle = (await page.textContent(".as-hint__title").catch(() => "")).trim();
    await check(page, "as-login: a guest is asked «Имя Фамилия, это вы?»", hintTitle === "Сакура Харуно, это вы?", hintTitle);
    await check(page, "as-login: «Продолжить» signs in and comes back here",
      /\/auth\/as\/start\?next=%2F$/.test(await page.getAttribute(".as-hint [data-hint-yes]", "href").catch(() => "") || ""));
    await shot(page, "as-login-hint");
    await page.click(".as-hint [data-hint-no]"); await page.waitForTimeout(200);
    await page.reload({ waitUntil: "domcontentloaded" }); await settle(page, 2000);
    await check(page, "as-login: «Нет» is remembered for this account", !(await page.$(".as-hint")));
    await page.evaluate(() => localStorage.removeItem("av_as_hint_dismissed"));

    await page.goto(SITE + "/anime/52991", { waitUntil: "domcontentloaded" }); await settle(page, 5000);
    await page.evaluate(() => document.querySelector(".as-hint")?.remove());
    await page.click("#avFavBtn"); await page.waitForTimeout(500);
    const gate = await page.$$eval(".gate-overlay .ed-dialog__actions a", els => els.map(a => ({ text: a.textContent.trim(), top: a.getBoundingClientRect().top, href: a.getAttribute("href") })));
    const login = gate.find(a => /AnimeSocial/.test(a.text)), signup = gate.find(a => /Создать/.test(a.text));
    await check(page, "as-login: the gate signs in through AnimeSocial, button on top",
      !!login && !!signup && login.top < signup.top && /\/auth\/as\/start\?next=/.test(login.href), JSON.stringify(gate));
    await shot(page, "as-login-gate");
    await check(page, "as-login: no JS errors", !errors.length, errors.join(" | "));
    await ctx.close();
  },
};
