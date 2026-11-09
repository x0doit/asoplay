/*
 * Мост плеера, часть 1: консоль и журналы щита, передача громкости и хранилища.
 *
 * Автор: Чепела Даниил Максимович.
 */
(() => {
  "use strict";
  const bridgeMarkup = document.currentScript
    ? '<script src="/player/asoplay-shield.js"><' + '/script>' + document.currentScript.outerHTML : "";
  const BASE = @@base_js@@;
  const IS_KODIK = @@is_kodik_js@@;
  const IS_ALLOHA = @@is_alloha_js@@;
  const UPSTREAM_ORIGIN = new URL(BASE).origin;
  const LOCAL_ORIGIN = location.protocol === "about:"
    ? new URL(document.baseURI).origin : location.origin;
  const AD_RE = /(casino|bookmaker|betting|vulkan|1xbet|pin[-\s]?up|av\s*casino|werbung|реклам|казино|ставк|букмекер|advert|preroll|vast|vpaid|popunder)/i;
  const ATTRS = new Set(["src", "href", "poster", "data", "action"]);
  const AD_EXTRA_RE = /(buzzoola|adfox|doubleclick|googlesyndication|googletag|\/ad(?:[/?#]|$)|\/ads(?:[/?#]|$))/i;
  const shieldStyleA = "background:#ff5c66;color:#fff;padding:3px 7px;border-radius:5px;font-weight:800";
  const shieldStyleB = "color:#dc2626;font-weight:800";
  const pauseStyleA = "background:#2563eb;color:#fff;padding:3px 7px;border-radius:5px;font-weight:800";
  const pauseStyleB = "color:#2563eb;font-weight:800";
  const extensionStyleA = "background:#f59e0b;color:#111827;padding:3px 7px;border-radius:5px;font-weight:900";
  const extensionStyleB = "color:#b45309;font-weight:800";
  const nativeConsole = {
    log: console.log ? console.log.bind(console) : () => {},
    warn: console.warn ? console.warn.bind(console) : () => {},
    error: console.error ? console.error.bind(console) : () => {},
    info: console.info ? console.info.bind(console) : () => {},
    debug: console.debug ? console.debug.bind(console) : () => {}
  };
  const shieldConsole = window.__asoplayShieldLog || ((method, args) => {
    const fn = nativeConsole[method] || nativeConsole.info;
    return fn(...args);
  });
  const shieldInfo = (...args) => shieldConsole("info", args);
  const diagnosticNotices = new Set();
  const diagnosticLog = (key, message) => {
    if (diagnosticNotices.has(key)) return;
    diagnosticNotices.add(key);
    shieldInfo("%cAsoPlay Shield:%c " + message, extensionStyleA, extensionStyleB);
  };
  const castQueues = new WeakSet();
  window.__asoplayPrepareCast = (urls) => {
    if (!Array.isArray(urls)) return;
    const modern = "//www.gstatic.com/eureka/clank/cast_sender.js";
    const legacy = value => /^chrome-extension:\/\/(?:pkedcjkdefgpdelpbcmbmeomcjbeemfm|enhhojjnijigcajfphajepfemndkmdlo)\/cast_sender\.js$/.test(value);
    if (!castQueues.has(urls)) {
      castQueues.add(urls);
      const firstLegacy = urls.findIndex(legacy);
      const modernIndex = urls.indexOf(modern);
      if (firstLegacy >= 0 && (modernIndex < 0 || modernIndex > firstLegacy)) {
        if (modernIndex >= 0) urls.splice(modernIndex, 1);
        urls.splice(firstLegacy, 0, modern);
      }
    }
    if (urls[0] === modern && urls.some(legacy)) {
      diagnosticLog("cast-modern", "Google Cast заглянул в архив расширений. Сэмпай Даниэль показал ему современный вход.");
    }
  };
  const shieldState = { windowStart: 0, hits: 0, mutedUntil: 0 };
  const shieldLog = () => {
    try {
      const now = Date.now();
      if (now < shieldState.mutedUntil) return;
      if (!shieldState.windowStart || now - shieldState.windowStart > 5000) {
        shieldState.windowStart = now;
        shieldState.hits = 0;
      }
      shieldState.hits += 1;
      if (shieldState.hits >= 3) {
        shieldInfo(
          "%cAsoPlay Shield:%c У Kodik настоящий понос, но у Даниэля Сэмпая есть волшебная \"Антиспам\" палочка, которая блокирует его запросы. ^-^",
          shieldStyleA,
          shieldStyleB
        );
        shieldState.mutedUntil = now + 60000;
        shieldState.windowStart = now;
        shieldState.hits = 0;
        return;
      }
      if (shieldState.hits === 1) {
        shieldInfo(
          "%cAsoPlay Shield:%c Kodik пытается засрать твое видео рекламой, но обосрался. Сэмпай Даниэль защитил твои глазки от мусора.",
          shieldStyleA,
          shieldStyleB
        );
      }
    } catch (_) {}
  };
  const storageShieldState = { windowStart: 0, hits: 0, mutedUntil: 0 };
  const storageShieldLog = () => {
    try {
      const now = Date.now();
      if (now < storageShieldState.mutedUntil) return;
      if (!storageShieldState.windowStart || now - storageShieldState.windowStart > 5000) {
        storageShieldState.windowStart = now;
        storageShieldState.hits = 0;
      }
      storageShieldState.hits += 1;
      if (storageShieldState.hits >= 3) {
        shieldInfo(
          "%cAsoPlay Shield:%c Kodik устроил storage-истерику, но антиспам Сэмпая Даниэля отправил ее в угол подумать. ^-^",
          shieldStyleA,
          shieldStyleB
        );
        storageShieldState.mutedUntil = now + 60000;
        storageShieldState.windowStart = now;
        storageShieldState.hits = 0;
        return;
      }
      if (storageShieldState.hits === 1) {
        shieldInfo(
          "%cAsoPlay Shield:%c Kodik полез в localStorage за мутной фигней, но Сэмпай Даниэль выдал ему безопасную пустышку.",
          shieldStyleA,
          shieldStyleB
        );
      }
    } catch (_) {}
  };
  const pauseShieldState = { mutedUntil: 0 };
  const pauseShieldLog = () => {
    try {
      const now = Date.now();
      if (now < pauseShieldState.mutedUntil) return;
      shieldInfo(
        "%cAsoPlay Shield:%c Пауза поставлена. Серия терпеливо ждёт, пока ты вернёшься к просмотру.",
        pauseStyleA,
        pauseStyleB
      );
      pauseShieldState.mutedUntil = now + 30000;
    } catch (_) {}
  };
  const sandboxCookieState = { shown: false };
  const sandboxCookieLog = () => {
    try {
      if (sandboxCookieState.shown) return;
      sandboxCookieState.shown = true;
      shieldInfo(
        "%cAsoPlay Shield:%c Cookies этого iframe под замком sandbox. Сэмпай Даниэль ключи не раздаёт. :З",
        extensionStyleA,
        extensionStyleB
      );
    } catch (_) {}
  };
  queueMicrotask(() => {
    let blocked = false;
    try {
      void document.cookie;
    } catch (err) {
      const text = String((err && err.name) || "") + " " + String((err && err.message) || "");
      blocked = /SecurityError|sandbox|cookie/i.test(text);
    }
    if (blocked) sandboxCookieLog();
  });
  const patchConsoleMethod = (name) => {
    const native = nativeConsole[name];
    try {
      console[name] = (...args) => {
        const text = args.map(x => {
          try {
            if (typeof x === "string") return x;
            if (x && typeof x.message === "string") return x.message;
            return JSON.stringify(x);
          } catch (_) {
            return String(x);
          }
        }).join(" ");
        if (/AUTO\s+PAUSED/i.test(text)) {
          pauseShieldLog();
          return;
        }
        if (/Unable to send stat|VAST ended|noad|original_manifest/i.test(text)) {
          shieldLog();
          return;
        }
        return shieldConsole(name, args);
      };
    } catch (_) {}
  };
  ["log", "warn", "error", "info", "debug"].forEach(patchConsoleMethod);
  // A sandboxed frame keeps nothing between episodes. The page passes the
  // volume keys this player saved last time in the URL fragment and stores
  // the ones it writes now: keys named after the volume or Plyr-style JSON.
  const handoff = (() => {
    const match = /(?:^#|&)asoplay=([^&]*)/.exec(location.hash);
    if (!match) return null;
    try { history.replaceState(history.state, "", location.href.split("#")[0]); } catch (_) {}
    try { return JSON.parse(decodeURIComponent(match[1])); } catch (_) { return null; }
  })();
  const keepsVolume = (key, value) => key.length <= 100 && value.length <= 2048
    && (/volume|mute/i.test(key) || /"(?:volume|muted)"\s*:/.test(value));
  const volumeRelay = (initial) => {
    let last = JSON.stringify(initial || {});
    return (data) => {
      const entries = {};
      for (const [key, value] of data) if (keepsVolume(key, value)) entries[key] = value;
      const next = JSON.stringify(entries);
      if (next === last) return;
      last = next;
      try { window.parent.postMessage({ type: "asoplay:storage", entries }, "*"); } catch (_) {}
    };
  };
  const makeStorage = (initial, onChange) => {
    const data = new Map();
    if (initial && typeof initial === "object") {
      for (const [key, value] of Object.entries(initial)) if (typeof value === "string") data.set(key, value);
    }
    const changed = () => { if (onChange) onChange(data); };
    return {
      get length() { return data.size; },
      clear() { data.clear(); changed(); },
      getItem(key) {
        key = String(key);
        return data.has(key) ? data.get(key) : null;
      },
      key(index) {
        return Array.from(data.keys())[Number(index)] ?? null;
      },
      removeItem(key) { data.delete(String(key)); changed(); },
      setItem(key, value) { data.set(String(key), String(value)); changed(); }
    };
  };
  const ensureStorage = (name, initial, onChange) => {
    try {
      const storage = window[name];
      const probe = "__av_probe__";
      storage.setItem(probe, "1");
      storage.removeItem(probe);
    } catch (_) {
      try {
        Object.defineProperty(window, name, {
          configurable: true,
          enumerable: true,
          value: makeStorage(initial, onChange)
        });
      } catch (_) {}
    }
  };
  ensureStorage("localStorage", handoff && handoff.storage, handoff ? volumeRelay(handoff.storage) : null);
  ensureStorage("sessionStorage");
