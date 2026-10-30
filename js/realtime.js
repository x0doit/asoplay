// -*- coding: utf-8 -*-
/*
 * AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
 * All rights reserved. See /COPYRIGHT for full terms.
 *
 * Живое соединение вкладки с сервером AsoPlay (WebSocket /ws, server/realtime.py) вместо опроса по
 * таймеру. Вкладка сообщает, на экране ли она и что играет в плеере, — только когда это меняется;
 * сервер присылает состояние (источники, VPN) и события друзей. Страница слушает их на document:
 *   av:status          {ok, sources, vpn, site}           — при подключении и при изменении (site — решения панели)
 *   av:presence        {user, on_asoplay, watching}       — друг зашёл на AsoPlay, вышел, начал или перестал смотреть
 *   av:social-presence {user_id, online_where, last_seen} — друг зашёл в Anime Social или вышел оттуда
 *   av:friendship      {user_id, status}                  — дружба изменилась (заявка, принята, удалена)
 *   av:session-ended   {}                                 — вход закрыт: выход в другой вкладке, блокировка
 *   av:link            {up, failures}                     — соединение есть / его нет столько попыток подряд
 * Обрыв соединение переживает само: переподключается с растущей паузой, сразу — при возврате сети и
 * когда вкладка снова на экране. Как на сайте соцсети, вкладка без действий человека 5 минут перестаёт
 * держать его «в сети» — если только у неё не играет плеер.
 */

const PING_EVERY = 30_000;
const PONG_WAIT = 10_000;
const WATCH_CHECK_EVERY = 15_000;
const IDLE_AFTER = 5 * 60_000;

let backend = "";
let watchingNow = () => null;
let ws = null;
let failures = 0;
let retryTimer = 0;
let pingTimer = 0;
let pongTimer = 0;
let started = false;
let sentVisible = null;
let sentWatching = "null";
let lastActivity = Date.now();

function _url() {
  const u = new URL("/ws", backend || location.origin);
  u.protocol = u.protocol === "https:" ? "wss:" : "ws:";
  return u.href;
}

function _emit(name, detail) {
  document.dispatchEvent(new CustomEvent(name, { detail }));
}

function _send(frame) {
  if (ws?.readyState !== WebSocket.OPEN) return false;
  ws.send(JSON.stringify(frame));
  return true;
}

// «На экране» для сервера: вкладка видна и человек что-то делал последние 5 минут или у него играет плеер
function _here() {
  return document.visibilityState === "visible" && (Date.now() - lastActivity < IDLE_AFTER || !!watchingNow());
}

function _sendVisible() {
  const visible = _here();
  if (visible !== sentVisible && _send({ type: "visible", value: visible })) sentVisible = visible;
}

function _onActivity() {
  const was = Date.now() - lastActivity >= IDLE_AFTER;
  lastActivity = Date.now();
  if (was) _sendVisible();
}

// Что играет — сообщаем серверу только при изменении: заиграл другой тайтл, перестал играть
export function refreshWatching() {
  const w = watchingNow();
  const key = w ? JSON.stringify([w.mal_id, w.title]) : "null";
  if (key === sentWatching) return;
  if (_send(w ? { type: "watching", mal_id: w.mal_id, title: w.title } : { type: "watching", mal_id: null })) sentWatching = key;
}

function _ping() {
  if (document.visibilityState !== "visible" || !_send({ type: "ping" })) return;
  clearTimeout(pongTimer);
  // Ответа нет — соединение повисло (сон ноутбука, смена сети): закрываем, onclose переподключит
  pongTimer = setTimeout(() => ws?.close(), PONG_WAIT);
}

function _schedule(delay) {
  clearTimeout(retryTimer);
  retryTimer = setTimeout(_connect, delay);
}

function _connect() {
  clearTimeout(retryTimer);
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  let sock;
  try { sock = new WebSocket(_url()); } catch (_) { _scheduleRetry(); return; }
  ws = sock;
  sock.onopen = () => {
    failures = 0;
    sentVisible = null;
    sentWatching = "null";
    _emit("av:link", { up: true, failures: 0 });
    _sendVisible();
    refreshWatching();
    clearInterval(pingTimer);
    pingTimer = setInterval(_ping, PING_EVERY);
  };
  sock.onmessage = (e) => {
    let frame;
    try { frame = JSON.parse(e.data); } catch (_) { return; }
    if (frame.type === "pong") { clearTimeout(pongTimer); return; }
    if (frame.type === "hello" || frame.type === "status") { if (frame.status) _emit("av:status", frame.status); return; }
    if (frame.type === "presence") _emit("av:presence", frame);
    else if (frame.type === "social_presence") _emit("av:social-presence", frame);
    else if (frame.type === "friendship") _emit("av:friendship", frame);
  };
  sock.onclose = (e) => {
    if (ws !== sock) return;
    ws = null;
    clearInterval(pingTimer);
    clearTimeout(pongTimer);
    // 4001 — вход закрыт (выход в другой вкладке, блокировка): страница узнаёт об этом, а соединение
    // сразу открывается заново — уже гостем
    if (e.code === 4001) { _emit("av:session-ended", {}); _schedule(0); return; }
    _scheduleRetry();
  };
}

function _scheduleRetry() {
  failures += 1;
  _emit("av:link", { up: false, failures });
  // 1, 2, 4… до 30 секунд; если соединение не поднимается совсем (его режет прокси) — раз в 5 минут
  const delay = failures > 10 ? 300_000 : Math.min(30_000, 1000 * 2 ** (failures - 1));
  _schedule(delay);
}

export function start({ backendUrl = "", watching } = {}) {
  backend = backendUrl;
  if (typeof watching === "function") watchingNow = watching;
  if (started) return;
  started = true;
  document.addEventListener("visibilitychange", () => {
    _sendVisible();
    if (document.visibilityState === "visible" && !ws) _connect();
  });
  window.addEventListener("online", () => { if (!ws) _connect(); });
  for (const type of ["pointerdown", "pointermove", "keydown", "wheel", "touchstart", "scroll"]) {
    window.addEventListener(type, _onActivity, { passive: true, capture: true });
  }
  // Сигналы плеера и действия человека — локальные: раз в 15 секунд сверяем, не перестал ли играть плеер
  // и не ушёл ли человек от экрана, — без запросов к серверу
  setInterval(() => { refreshWatching(); _sendVisible(); }, WATCH_CHECK_EVERY);
  _connect();
}

// Вход или выход: у соединения меняется человек — подключаемся заново с новой cookie
export function restart() {
  if (!started) return;
  const old = ws;
  ws = null;
  clearInterval(pingTimer);
  clearTimeout(pongTimer);
  if (old) { old.onclose = null; old.close(); }
  failures = 0;
  _connect();
}
