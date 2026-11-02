/*
 * Сценарий QA «images» — картинки чужих хостов (js/images.js). Хост открывается — обложки идут напрямую, даже при
 * VPN-мосте сервера; не открывается — приходят через /proxy/img, остальные его картинки на странице и дальше идут
 * через прокси сразу (память на сутки); зависший хост — повтор через 6 секунд, а медленный, по которому байты идут, —
 * нет; адрес, который сервер не примет, — только напрямую; хост из панели — через прокси сразу; кадр состояния по /ws
 * меняет правила без перезагрузки.
 *
 * Автор: Чепела Даниил Максимович.
 */
const { execFileSync } = require("child_process");
const fs = require("fs");
const http = require("http");
const https = require("https");
const os = require("os");
const path = require("path");
const { SITE, check, shot, settle } = require("../lib.cjs");

const MAL = /^https:\/\/cdn\.myanimelist\.net\//;
// Картинка 1×1 вместо обложки, когда сценарию нужен «доступный» MAL
const PIXEL = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

// Состояние сервера как есть, site — поверх решений панели; /ws на подмене: hello без статуса, кадры сценарий
// шлёт сам
async function liveStatus(page, site = {}) {
  await page.route(/\/health$/, async route => {
    const resp = await route.fetch();
    const json = await resp.json();
    await route.fulfill({ response: resp, json: { ...json, site: { ...json.site, ...site } } });
  });
  const live = { sockets: [], status: null };
  await page.routeWebSocket(/\/ws$/, ws => {
    live.sockets.push(ws);
    ws.onMessage(m => { if (JSON.parse(String(m)).type === "ping") ws.send(JSON.stringify({ type: "pong" })); });
    ws.send(JSON.stringify({ type: "hello", user_id: null }));
  });
  return live;
}

// Свой хост картинок на localhost: /drip.jpg отдаёт настоящую обложку по капле (начало — сразу, дальше по килобайту
// раз в полсекунды), /hang.jpg не отвечает вовсе. Страница видит его как drip.example на обычном порту (свой порт
// сервер не примет — такой адрес на прокси не идёт вовсе), а Playwright ведёт запросы сюда. Схема — как у страницы:
// http-картинку на https-странице Chrome сам переводит на https, и подмена её бы не поймала. Для https — одноразовый
// самоподписанный сертификат (openssl), контекст проверки принимает его (ignoreHTTPSErrors)
function dripCert() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "asoplay-qa-drip-"));
  try {
    execFileSync("openssl", ["req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1", "-subj", "/CN=drip.example",
      "-keyout", path.join(dir, "key.pem"), "-out", path.join(dir, "cert.pem")], { stdio: "ignore" });
    return { key: fs.readFileSync(path.join(dir, "key.pem")), cert: fs.readFileSync(path.join(dir, "cert.pem")) };
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

async function dripHost() {
  const cover = Buffer.from(await (await fetch(SITE + "/proxy/img?url="
    + encodeURIComponent("https://cdn.myanimelist.net/images/anime/1223/96541.jpg"))).arrayBuffer());
  const scheme = new URL(SITE).protocol;
  const sockets = new Set(), timers = new Set();
  const serve = (req, res) => {
    if (req.url !== "/drip.jpg") return;  // /hang.jpg — ни байта
    res.writeHead(200, { "content-type": "image/jpeg", "content-length": cover.length });
    let sentBytes = Math.min(4096, cover.length);
    res.write(cover.subarray(0, sentBytes));
    const timer = setInterval(() => {
      res.write(cover.subarray(sentBytes, sentBytes + 1024));
      sentBytes += 1024;
      if (sentBytes >= cover.length) { clearInterval(timer); res.end(); }
    }, 500);
    timers.add(timer);
  };
  const server = scheme === "https:" ? https.createServer(dripCert(), serve) : http.createServer(serve);
  server.on("connection", socket => { sockets.add(socket); socket.on("close", () => sockets.delete(socket)); });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  return {
    url: `${scheme}//drip.example`,
    real: `${scheme}//127.0.0.1:${server.address().port}`,
    stop: () => { timers.forEach(clearInterval); sockets.forEach(s => s.destroy()); server.close(); },
  };
}

// Сколько обложек каталога видно и откуда они пришли
const posters = page => page.$$eval(".av-card-img img", imgs => imgs.map(img => ({
  src: img.getAttribute("src") || "", loaded: img.complete && img.naturalWidth > 0,
  visible: img.getBoundingClientRect().top < innerHeight,
})));
const viaProxy = list => list.filter(p => p.loaded && p.src.startsWith("/proxy/img?url="));

module.exports = {
  async images(browser) {
    // MAL открывается: обложки идут напрямую, даже когда VPN-мост сервера работает
    const open = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: "ru-RU" });
    const first = await open.newPage();
    await liveStatus(first);
    const fetched = [];
    await first.route(MAL, route => {
      fetched.push(route.request().url());
      return route.fulfill({ contentType: "image/png", body: PIXEL });
    });
    await first.goto(SITE + "/catalog", { waitUntil: "domcontentloaded" }); await settle(first, 4000);
    let seen = await posters(first);
    const bridge = (await (await fetch(SITE + "/health")).json()).vpn;
    await check(first, "images: a host that opens loads directly, whatever the server's bridge",
      fetched.length >= 6 && !seen.some(p => p.src.startsWith("/proxy/img?url=")), `bridge ${bridge}, ${fetched.length} direct`);
    await open.close();

    // MAL закрыт для браузера: обложки — через прокси, хост запоминается, после перезагрузки — сразу через прокси
    const ctx = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: "ru-RU" });
    const page = await ctx.newPage();
    await liveStatus(page);
    const direct = [];
    await page.route(MAL, route => { direct.push(route.request().url()); return route.abort("connectionrefused"); });
    await page.goto(SITE + "/catalog", { waitUntil: "domcontentloaded" }); await settle(page, 4000);
    seen = await posters(page);
    const memory = await page.evaluate(() => JSON.parse(localStorage.getItem("av_img_proxy") || "{}"));
    await check(page, "images: a blocked host's covers come through /proxy/img",
      direct.length > 0 && viaProxy(seen).length >= 6, `${direct.length} direct tries, ${viaProxy(seen).length}/${seen.length} via proxy`);
    await check(page, "images: the host is remembered for a day",
      Math.abs((memory["cdn.myanimelist.net"] || 0) - Date.now() - 86_400_000) < 120_000, JSON.stringify(memory));
    await shot(page, "images-blocked-host");
    const before = direct.length;
    await page.reload({ waitUntil: "domcontentloaded" }); await settle(page, 4000);
    seen = await posters(page);
    await check(page, "images: after that its covers go to the proxy at once",
      direct.length === before && viaProxy(seen).length >= 6, `${direct.length - before} new direct tries`);
    await ctx.close();

    // Страница тайтла, хост ещё не известен: одна прямая попытка, а картинки, что ещё не грузились (персонажи ниже),
    // уходят на прокси сразу после первой удачи через него
    const fresh = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: "ru-RU" });
    const title = await fresh.newPage();
    await liveStatus(title);
    const tries = [];
    await title.route(MAL, route => { tries.push(route.request().url()); return route.abort("connectionrefused"); });
    await title.goto(SITE + "/anime/16498", { waitUntil: "domcontentloaded" }); await settle(title, 6000);
    const mal = await title.$$eval("img", imgs => imgs.map(img => decodeURIComponent(img.getAttribute("src") || ""))
      .filter(src => src.includes("cdn.myanimelist.net")));
    await check(title, "images: one direct try, then every cover of the host on the page goes to the proxy",
      tries.length === 1 && mal.length > 5 && mal.every(src => src.startsWith("/proxy/img?url=")),
      `${tries.length} direct, ${mal.filter(src => src.startsWith("/proxy/img?url=")).length}/${mal.length} on the proxy`);
    await fresh.close();

    // MAL висит: через 6 секунд — повтор через прокси
    const slow = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: "ru-RU" });
    const hang = await slow.newPage();
    await liveStatus(hang);
    await hang.route(MAL, () => new Promise(() => {}));
    await hang.goto(SITE + "/catalog", { waitUntil: "domcontentloaded" });
    await hang.waitForSelector(".av-card-img img", { timeout: 20000 }).catch(() => {});
    await hang.waitForTimeout(3000);
    const early = viaProxy(await posters(hang)).length;
    await hang.waitForTimeout(7000);
    const late = await posters(hang);
    await check(page, "images: a hanging host gets the proxy after 6 seconds",
      early === 0 && viaProxy(late.filter(p => p.visible)).length >= 4, `${early} at 3 s, ${viaProxy(late).length} at 10 s`);
    await slow.close();

    // Медленная, но живая связь: байты идут — картинку не трогают; без байтов — повтор через прокси; адрес, который
    // сервер не примет (свой порт), — только напрямую
    const health = await (await fetch(SITE + "/health")).json();
    const host = await dripHost();
    const thin = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: "ru-RU",
      ignoreHTTPSErrors: true });
    // Против прода страница публичная, а хост картинок — на 127.0.0.1: такие запросы Chrome без разрешения обрывает
    // (Local Network Access, net::ERR_FAILED). Локально страница сама на 127.0.0.1, разрешение не нужно
    await thin.grantPermissions(["local-network-access"]).catch(() => {});
    const line = await thin.newPage();
    await liveStatus(line, { img_hosts: [...health.site.img_hosts, "drip.example"] });
    await line.route(/^https?:\/\/drip\.example\//, route => route.continue({ url: route.request().url()
      .replace(host.url, host.real) }));
    const odd = "https://cdn.myanimelist.net:8443/images/anime/1/1.jpg";
    await line.route(/\/proxy\/jikan\//, async route => {
      const resp = await route.fetch();
      const json = await resp.json();
      [`${host.url}/drip.jpg`, `${host.url}/hang.jpg`, odd].forEach((url, n) => {
        if (json.data?.[n]) json.data[n].images = { jpg: { image_url: url, large_image_url: url } };
      });
      await route.fulfill({ response: resp, json });
    });
    await line.route(/^https:\/\/cdn\.myanimelist\.net:8443\//, route => route.abort("connectionrefused"));
    const sent = [];
    line.on("request", r => { if (r.url().includes("/proxy/img?url=")) sent.push(decodeURIComponent(r.url())); });
    await line.goto(SITE + "/catalog", { waitUntil: "domcontentloaded" });
    await line.waitForTimeout(9000);
    const firsts = await line.$$eval(".av-card-img img", imgs => imgs.slice(0, 2).map(img => ({
      src: img.getAttribute("src") || "", retried: img.dataset.avRetried || "", width: img.naturalWidth })));
    await check(line, "images: an image whose bytes still arrive is left alone after 6 seconds",
      firsts[0]?.src === `${host.url}/drip.jpg` && !firsts[0].retried && firsts[0].width > 0, JSON.stringify(firsts[0]));
    await check(line, "images: an image without a single byte gets the proxy", firsts[1]?.retried === `${host.url}/hang.jpg`,
      JSON.stringify(firsts[1]));
    await check(line, "images: an address the server would refuse is never sent to the proxy",
      !sent.some(url => url.includes(":8443")), sent.filter(url => url.includes(":8443")).join(" "));
    await thin.close();
    host.stop();

    // Хост из панели — через прокси сразу; кадр состояния по /ws убирает его — дальше снова напрямую
    const listed = await browser.newContext({ viewport: { width: 1280, height: 860 }, locale: "ru-RU" });
    const tab = await listed.newPage();
    const live = await liveStatus(tab, { proxy_hosts: ["cdn.myanimelist.net"] });
    const tried = [];
    await tab.route(MAL, route => { tried.push(route.request().url()); return route.fulfill({ contentType: "image/png", body: PIXEL }); });
    await tab.goto(SITE + "/catalog", { waitUntil: "domcontentloaded" }); await settle(tab, 4000);
    seen = await posters(tab);
    await check(tab, "images: a panel host goes through the proxy at once",
      tried.length === 0 && viaProxy(seen).length >= 6, `${tried.length} direct, ${viaProxy(seen).length} via proxy`);
    for (const ws of live.sockets) {
      ws.send(JSON.stringify({ type: "status", status: { ok: true, sources: health.sources, vpn: false,
        site: { ...health.site, proxy_hosts: [] } } }));
    }
    await tab.waitForTimeout(300);
    await tab.evaluate(() => { history.pushState(null, "", "/top"); dispatchEvent(new PopStateEvent("popstate")); });
    await settle(tab, 4000);
    seen = await posters(tab);
    await check(tab, "images: a status frame without the host sends its covers direct again",
      tried.length > 0 && seen.some(p => p.loaded && MAL.test(p.src)), `${tried.length} direct after the frame`);
    await listed.close();
  },
};
