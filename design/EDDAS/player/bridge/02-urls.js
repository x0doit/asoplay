/*
 * Мост плеера, часть 2: загрузчик Alloha, адреса через прокси, window.open и origin сообщений.
 *
 * Автор: Чепела Даниил Максимович.
 */
  const setupAllohaBootstrap = () => {
    if (!IS_ALLOHA) return;
    try {
      const params = new URL(BASE).searchParams;
      const toInt = (value, fallback) => {
        const num = Number.parseInt(String(value || ""), 10);
        return Number.isFinite(num) && num > 0 ? num : fallback;
      };
      const season = toInt(params.get("season"), 1);
      const episode = toInt(params.get("episode"), 1);
      const translation = toInt(params.get("translation"), 0);
      const movieToken = params.get("token_movie") || params.get("token") || "";
      const ensureObject = (value) => value && typeof value === "object" && !Array.isArray(value) ? value : {};
      const ensureArray = (value) => Array.isArray(value) ? value : [];
      const cacheKey = "__asoplayAllohaFileList:" + BASE;
      let cached = null;
      try { cached = JSON.parse(sessionStorage.getItem(cacheKey) || "null"); } catch (_) {}
      const list = ensureObject(window.fileList || cached);
      const active = ensureObject(list.active);
      active.season = active.season || season;
      active.episode = active.episode || episode;
      active.translation = active.translation || translation;
      active.token_movie = active.token_movie || movieToken;
      active.token = active.token || movieToken;
      active.typeList = ensureObject(active.typeList);
      active.typeList.active = active.typeList.active || active.type || "iframe";
      active.types = ensureObject(active.types);
      active.files = ensureArray(active.files);
      list.active = active;
      list.typeList = ensureObject(list.typeList);
      list.typeList.active = list.typeList.active || active.typeList.active;
      list.typeList.list = ensureArray(list.typeList.list);
      list.season = ensureObject(list.season);
      list.season.active = list.season.active || season;
      list.season.list = ensureArray(list.season.list);
      list.seasons = ensureArray(list.seasons);
      list.episode = ensureObject(list.episode);
      list.episode.active = list.episode.active || episode;
      list.episode.list = ensureArray(list.episode.list);
      list.episodes = ensureArray(list.episodes);
      list.translation = ensureObject(list.translation);
      list.translation.active = list.translation.active || translation;
      list.translation.list = ensureArray(list.translation.list);
      list.translations = ensureArray(list.translations);
      list.movie = ensureObject(list.movie);
      list.movie.active = ensureObject(list.movie.active || active);
      list.serial = ensureObject(list.serial);
      list.serial.active = ensureObject(list.serial.active || active);
      list.serial.season = list.serial.season || list.season;
      list.serial.seasons = ensureArray(list.serial.seasons || list.seasons);
      list.serial.episode = list.serial.episode || list.episode;
      list.serial.episodes = ensureArray(list.serial.episodes || list.episodes);
      list.serial.translation = list.serial.translation || list.translation;
      list.serial.translations = ensureArray(list.serial.translations || list.translations);
      window.fileList = list;
      window.config = ensureObject(window.config);
      window.config.player = ensureObject(window.config.player);
      window.__asoplayAllohaFileList = list;
      try { sessionStorage.setItem(cacheKey, JSON.stringify(list)); } catch (_) {}
    } catch (_) {}
  };
  setupAllohaBootstrap();
  const passthrough = (value) => {
    const s = String(value || "").trim();
    return !s || s[0] === "#" || /^(about|blob|data|javascript|mailto|tel|chrome-extension|moz-extension|safari-web-extension|ms-browser-extension):/i.test(s) || /^[a-z][a-z0-9+.-]*-extension:/i.test(s);
  };
  const isPlayerInternalPath = (value) =>
    /^\/player\/(?:frame|proxy|modules|documents|cvh-api|cdn-check)(?:[/?#]|$)|^\/player\/(?:asoplay-shield\.js|kodik-skin\.css)(?:[?#]|$)/i
      .test(String(value || "").trim());
  const isLocalProxyPath = (value) => isPlayerInternalPath(value);
  const isLocalProxyUrl = (value) => {
    try {
      const u = new URL(String(value || ""), LOCAL_ORIGIN);
      return u.origin === LOCAL_ORIGIN && isLocalProxyPath(u.pathname);
    } catch (_) {
      return false;
    }
  };
  const unwrapProxyTarget = (value) => {
    const raw = String(value || "");
    try {
      const u = new URL(raw, LOCAL_ORIGIN);
      if (u.origin === LOCAL_ORIGIN && isLocalProxyPath(u.pathname)) {
        const path = u.pathname.match(/^\/player\/(?:documents|modules)\/(https?)\/([^/]+)(\/.*)$/);
        if (path) return path[1] + "://" + path[2] + path[3] + u.search + u.hash;
        return u.searchParams.get("url") || raw;
      }
    } catch (_) {}
    return raw;
  };
  const isAdTarget = (value) => {
    const raw = String(value || "");
    const unwrapped = unwrapProxyTarget(raw);
    return AD_RE.test(raw) || AD_EXTRA_RE.test(raw) || AD_RE.test(unwrapped) || AD_EXTRA_RE.test(unwrapped);
  };
  const absolute = (value) => {
    if (isLocalProxyPath(value)) return LOCAL_ORIGIN + String(value || "").trim();
    try { return new URL(String(value || ""), BASE).href; } catch (_) { return String(value || ""); }
  };
  const normalizeUpstream = (url) => {
    try {
      const u = new URL(url);
      if (u.origin === LOCAL_ORIGIN && !isPlayerInternalPath(u.pathname)) {
        return UPSTREAM_ORIGIN + u.pathname + u.search + u.hash;
      }
      return u.href;
    } catch (_) {
      return url;
    }
  };
  const proxied = (value) => {
    if (isAdTarget(value)) shieldLog();
    if (passthrough(value)) return value;
    if (isLocalProxyUrl(value)) return String(value || "");
    const u = normalizeUpstream(absolute(value));
    if (isLocalProxyUrl(u)) {
      if (isAdTarget(u)) shieldLog();
      return u;
    }
    if (isAdTarget(u)) shieldLog();
    return "/player/proxy?url=" + encodeURIComponent(u) + "&base=" + encodeURIComponent(BASE) + "&pv=@@proxy_version@@";
  };
  const proxiedFrame = (value) => {
    if (passthrough(value)) return value;
    const mapped = proxied(value);
    try {
      const proxy = new URL(mapped, LOCAL_ORIGIN);
      if (proxy.pathname !== "/player/proxy") return mapped;
      const target = new URL(proxy.searchParams.get("url"));
      if (!/^https?:$/.test(target.protocol) || target.username || target.password) return mapped;
      return "/player/documents/" + target.protocol.slice(0, -1) + "/" + target.host
        + target.pathname + target.search + target.hash;
    } catch (_) { return mapped; }
  };
  const rewriteHtml = (html) => String(html || "").replace(
    /\b(src|href|poster|data|action)\s*=\s*(['"])(.*?)\2/gi,
    (_, attr, q, val) => attr + "=" + q + proxied(val).replace(/"/g, "&quot;") + q
  );
  const rewriteSrcdoc = (value) => {
    const text = rewriteHtml(value);
    if (!bridgeMarkup || text.includes("data-asoplay-bridge")) return text;
    return /<head\b[^>]*>/i.test(text)
      ? text.replace(/<head\b[^>]*>/i, head => head + bridgeMarkup) : bridgeMarkup + text;
  };
  const sanitizeMessage = (message) => message;

  window.open = () => null;
  try { Object.defineProperty(window, "opener", { value: null, configurable: false }); } catch (_) {}
  const normalizeTargetOrigin = (origin) => {
    if (!origin || origin === "*") return origin;
    try {
      const wanted = new URL(String(origin)).origin;
      if (wanted !== LOCAL_ORIGIN) return "*";
    } catch (_) {}
    return origin;
  };
  // Never install a sender wrapper into another window: that changes event.source.
