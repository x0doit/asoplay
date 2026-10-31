/*
 * Общее для сценариев QA: подменённые ответы аккаунта, проверки и отчёт, снимки, вылезающие за экран
 * элементы, тишина для «кто вошёл в соцсеть».
 *
 * Автор: Чепела Даниил Максимович.
 *
 * Аккаунт — подменённый ответ /auth/me (qa-viewer, id 900001): в базу ничего не пишется, вход не нужен.
 * Снимки — в docs/internal/eddas/qa (вне git) или в QA_OUT.
 */
const path = require("path");

const SITE = process.env.SITE || "http://127.0.0.1:8787";
const OUT = path.resolve(process.env.QA_OUT || path.join(__dirname, "../../docs/internal/eddas/qa"));
const now = Date.now();
const iso = (ms) => new Date(ms).toISOString();
const TITLES = [
  [16498, "Атака титанов", "https://cdn.myanimelist.net/images/anime/10/47347l.jpg", 25],
  [1535, "Тетрадь смерти", "https://cdn.myanimelist.net/images/anime/1079/138100l.jpg", 37],
  [5114, "Стальной алхимик: Братство", "https://cdn.myanimelist.net/images/anime/1208/94745l.jpg", 64],
  [30276, "Ванпанчмен", "https://cdn.myanimelist.net/images/anime/12/76049l.jpg", 12],
  [38000, "Клинок, рассекающий демонов", "https://cdn.myanimelist.net/images/anime/1286/99889l.jpg", 26],
  [31964, "Моя геройская академия", "https://cdn.myanimelist.net/images/anime/10/78745l.jpg", 13],
];
const STATUSES = ["watching", "planned", "completed", "postponed", "dropped"];
const USER = { id: 900001, login: "qa-viewer", email: "qa-viewer@example.test", name: "Тестовый зритель с очень длинным именем для проверки",
  alias: "qa-viewer", handle: "qa-viewer", profile_path: "/@qa-viewer", avatar: "", avatar_small: "", profile_url: "" };

function accountMocks(state) {
  const lists = TITLES.map(([mal_id, title, poster_url], i) => ({ mal_id, title, poster_url, status: STATUSES[i % 5],
    status_source: i === 1 ? "auto" : "user", is_favorite: i % 2 === 0, updated_at: iso(now - i * 3600e3) }));
  state.lists = lists;
  state.history = TITLES.slice(0, 4).map(([mal_id, title, poster_url, total], i) => ({ mal_id, title, poster_url,
    last_episode: i + 2, episode_seconds: 500 + i * 100, episode_duration: 1420, episodes_total: total, updated_at: iso(now - i * 5400e3) }));
  const days = (n) => Array.from({ length: n }, (_, k) => {
    const d = new Date(now - (n - 1 - k) * 86400e3);
    return { d: d.toISOString().slice(0, 10), n: (k * 7) % 11 > 6 ? (k % 9) + 1 : 0 };
  });
  const activity = (n) => ({ period_days: n, from: days(n)[0].d, to: days(n)[n - 1].d, days: days(n),
    totals: { events: 120, active_days: 40, max_on_day: 9, streak_current: 2, streak_best: 6, by_kind: {} },
    recent: TITLES.map(([mal_id, title, poster_url], i) => ({ kind: ["watch_continue", "list_add", "favorite", "rate", "complete", "watch_start"][i],
      mal_id, title, poster_url, meta: "", at: iso(now - i * 7200e3), episode_num: i % 2 ? i + 1 : null })) });
  return async (page) => {
    const json = (route, body, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
    await page.route(/\/auth\/me(\?|$)/, r => json(r, { authenticated: true, user: USER }));
    await page.route(/\/auth\/logout/, r => { state.loggedOut = true; return json(r, { ok: true }); });
    await page.route(/\/account\//, (route) => {
      const url = new URL(route.request().url());
      const method = route.request().method();
      const p = url.pathname;
      state.calls.push(`${method} ${p}${url.search}`);
      if (method === "DELETE" && p === "/account/history") { state.history = []; return json(route, { ok: true }); }
      if (method === "DELETE" && p.startsWith("/account/history/")) {
        const id = Number(p.split("/").pop()); state.history = state.history.filter(h => h.mal_id !== id); return json(route, { ok: true });
      }
      if (method === "PUT" && /\/account\/lists\/\d+\/status/.test(p)) {
        const id = Number(p.split("/")[3]); const body = JSON.parse(route.request().postData() || "{}");
        const l = state.lists.find(x => x.mal_id === id); if (l) l.status = body.status || null;
        return json(route, { ok: true, status: body.status || null, status_source: "user" });
      }
      if (method !== "GET") return json(route, { ok: true });
      if (p === "/account/lists/counts") {
        const c = Object.fromEntries(STATUSES.map(s => [s, state.lists.filter(l => l.status === s).length]));
        c.favorite = state.lists.filter(l => l.is_favorite).length; return json(route, c);
      }
      if (p === "/account/lists") {
        const s = url.searchParams.get("status");
        if (s === "favorite") return json(route, state.lists.filter(l => l.is_favorite));
        return json(route, s ? state.lists.filter(l => l.status === s) : state.lists);
      }
      if (p === "/account/history") return json(route, state.history);
      if (p === "/account/settings") return json(route, { autonext: true, auto_add_lists: true });
      if (p === "/account/privacy") return json(route, { hide_lists: false, hide_activity: false });
      return json(route, {});
    });
    await page.route(/\/profile\/qa-viewer\//, (route) => {
      const url = new URL(route.request().url());
      const n = Number(url.searchParams.get("days") || 365);
      const counts = Object.fromEntries(STATUSES.map(s => [s, state.lists.filter(l => l.status === s).length]));
      counts.favorite = state.lists.filter(l => l.is_favorite).length;
      if (url.pathname.endsWith("/summary")) return json(route, { user: USER, is_owner: true, privacy: { hide_lists: false, hide_activity: false }, counts, activity: activity(n) });
      if (url.pathname.endsWith("/lists")) {
        const s = url.searchParams.get("status");
        return json(route, { hidden: false, is_owner: true, items: s === "favorite" ? state.lists.filter(l => l.is_favorite) : state.lists.filter(l => l.status === s) });
      }
      if (url.pathname.endsWith("/activity/recent")) return json(route, { items: activity(30).recent, has_more: true, offset: 0, limit: 10, group: "all", day: null });
      if (url.pathname.endsWith("/activity")) return json(route, activity(n));
      return json(route, {});
    });
  };
}


const report = [];
let shotN = 0;
const STATUS = { ok: true, sources: [], vpn: false };  // запуск дописывает его из /health

// Известные нестабильные проверки: их падение помечается задачей, чтобы его не приняли за новое, но провалом остаётся.
// QA-FLAKY-1 (после шага 5 рефакторинга): плеер под нагрузкой ждёт по времени, а не признака готовности (CODEX)
const KNOWN_FLAKY = new Map([
  ["dub choice changes the label and closes the menu", "QA-FLAKY-1"],
  ["landscape 844: player head and video fit between the bars", "QA-FLAKY-1"],
  ["landscape 740: player head and video fit between the bars", "QA-FLAKY-1"],
]);

async function check(page, name, cond, detail = "") {
  const ok = !!cond;
  const flaky = ok ? null : KNOWN_FLAKY.get(name) || null;
  report.push({ name, ok, detail, ...(flaky ? { flaky } : {}) });
  console.log(ok ? "  ok  " : flaky ? `  FAIL [известная нестабильность ${flaky}]` : "  FAIL", name, detail ? `(${detail})` : "");
}

async function shot(page, name, full = false) {
  const file = path.join(OUT, `${String(++shotN).padStart(3, "0")}-${name}.png`);
  await page.screenshot({ path: file, fullPage: full });
  return file;
}

// Elements that stick out of the viewport horizontally (ignoring scrollable strips and their children).
async function overflowing(page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const out = [];
    for (const el of document.querySelectorAll("body *")) {
      const cs = getComputedStyle(el);
      if (cs.display === "none" || cs.visibility === "hidden" || cs.position === "fixed") continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      let p = el.parentElement, scrolled = false;
      while (p && p !== document.body) {
        const ps = getComputedStyle(p);
        if (/(auto|scroll|hidden|clip)/.test(ps.overflowX) || ps.position === "fixed") { scrolled = true; break; }
        p = p.parentElement;
      }
      if (scrolled) continue;
      if (r.right > vw + 1 || r.left < -1) out.push(`${el.tagName.toLowerCase()}.${[...el.classList].join(".")} ${Math.round(r.left)}..${Math.round(r.right)}`);
    }
    return { scrollWidth: document.documentElement.scrollWidth, vw, items: out.slice(0, 8) };
  });
}

// The list menu opens right under its button (or right above it) and stays inside the screen.
async function pickerPlacement(page) {
  return page.evaluate(() => {
    const b = document.querySelector("#avListPickerBtn").getBoundingClientRect();
    const p = document.querySelector("#avListPickerPop").getBoundingClientRect();
    const below = Math.abs(p.top - b.bottom - 6) < 2, above = Math.abs(b.top - p.bottom - 6) < 2;
    return { ok: (below || above) && p.left >= 0 && p.right <= innerWidth && p.top >= 0 && p.bottom <= innerHeight,
      button: [Math.round(b.left), Math.round(b.top), Math.round(b.bottom)], menu: [Math.round(p.left), Math.round(p.top), Math.round(p.bottom)] };
  });
}

// Console noise that is not a page error
const CONSOLE_NOISE = /Failed to load resource/;

// Ошибки консоли считаются только у нашей страницы — те, чей адрес на нашем origin. Сообщения чужих iframe
// (трейлер, плеер) в проверки не идут, а копятся здесь со страницей и текстом: отчёт покажет их отдельной строкой,
// чтобы такое можно было повторить.
const foreign = [];

function watchConsole(page, errors, scenario) {
  page.on("console", m => {
    if (m.type() !== "error" || CONSOLE_NOISE.test(m.text())) return;
    const source = (m.location() || {}).url || "";
    if (source === SITE || source.startsWith(SITE + "/")) errors.push(m.text().slice(0, 200));
    else foreign.push({ scenario, page: page.url(), source, text: m.text().slice(0, 300) });
  });
}

// «Who is signed in to AnimeSocial» never reaches the real social network: every page of every run would spend its
// per-address limit (60 a minute), which the engine's own tests on this machine share. Unknown → «not signed in»;
// the asLogin and friends scenarios answer it themselves (page routes win over this context route).
async function quietSocialSession(ctx) {
  await ctx.route(/\/api\/v2\/auth\/web\/session(\?|$)/, route => route.fulfill({
    status: 200, contentType: "application/json",
    headers: { "Access-Control-Allow-Origin": route.request().headers()["origin"] || SITE, "Access-Control-Allow-Credentials": "true", "Vary": "Origin" },
    body: JSON.stringify({ ok: true, data: { logged_in: false, user: null } }),
  }));
  return ctx;
}

async function settle(page, ms = 800) {
  await page.waitForLoadState("networkidle", { timeout: 12000 }).catch(() => {});
  await page.waitForTimeout(ms);
}

// Живое соединение /ws на подмене: hello со статусом сервера, ответ на ping; кадры вкладки копятся в frames,
// открытые соединения — в sockets (через них сценарий шлёт свои кадры)
async function liveSocket(page, userId) {
  const live = { sockets: [], frames: [] };
  await page.routeWebSocket(/\/ws$/, ws => {
    live.sockets.push(ws);
    ws.onMessage(m => {
      const frame = JSON.parse(String(m));
      live.frames.push(frame);
      if (frame.type === "ping") ws.send(JSON.stringify({ type: "pong" }));
    });
    ws.onClose(() => { live.sockets = live.sockets.filter(x => x !== ws); });
    ws.send(JSON.stringify({ type: "hello", user_id: userId, status: STATUS }));
  });
  return live;
}

// «был в сети 5 минут назад» — как пишет AnimeSocial: единицы от года до минуты и русские формы числа
const AGO_UNITS = [[31_570_560, ["год", "года", "лет"]], [2_630_880, ["месяц", "месяца", "месяцев"]],
  [604_800, ["неделю", "недели", "недель"]], [86_400, ["день", "дня", "дней"]], [3_600, ["час", "часа", "часов"]],
  [60, ["минуту", "минуты", "минут"]]];
const ruForm = (n, forms) => forms[n % 100 > 10 && n % 100 < 20 ? 2 : n % 10 === 1 ? 0 : n % 10 >= 2 && n % 10 <= 4 ? 1 : 2];
const agoText = diff => {
  for (const [len, forms] of AGO_UNITS) if (diff >= len) { const n = Math.floor(diff / len); return `${n} ${ruForm(n, forms)} назад`; }
  return "только что";
};
// Подписи «был в сети …», верные хоть для одного мгновения между from и to (мс по часам страницы). Страница пишет подпись
// по своим часам в миг отрисовки, а когда именно она рисовала, снаружи известно только с точностью до этого промежутка:
// ни запаса на медленный прогон, ни застывших часов, от которых встал бы и остальной код страницы со временем
const seenTexts = (lastSeen, word, from, to) => {
  const out = new Set();
  for (let s = Math.floor(from / 1000); s <= Math.floor(to / 1000); s++) out.add(`${word} в сети ${agoText(Math.max(0, s - lastSeen))}`);
  return out;
};

module.exports = { SITE, OUT, USER, STATUS, report, foreign, accountMocks, check, shot, overflowing,
  pickerPlacement, CONSOLE_NOISE, watchConsole, quietSocialSession, settle, liveSocket, seenTexts };
