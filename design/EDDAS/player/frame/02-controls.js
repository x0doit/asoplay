/*
 * Кадр плеера, часть 2: время и прогресс, управление и клавиши, громкость, качество, события.
 *
 * Автор: Чепела Даниил Максимович.
 */
  const sendTime = () => {
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    const time = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    syncProgress();
    if (duration > 0) send("kodik_player_duration_update", duration);
    if (time >= 0) send("kodik_player_time_update", time);
  };
  const fmt = (value) => {
    value = Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
    const h = Math.floor(value / 3600);
    const m = Math.floor((value % 3600) / 60);
    const s = value % 60;
    return h ? h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0") : m + ":" + String(s).padStart(2, "0");
  };
  const syncProgress = () => {
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    const time = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    if (!seeking) seek.value = duration > 0 ? String(Math.min(1000, Math.max(0, Math.round(time / duration * 1000)))) : "0";
    timeText.textContent = fmt(time) + " / " + fmt(duration);
  };
  const showStart = () => { if (video.paused && !video.ended) start.classList.remove("hidden"); };
  const hideStart = () => start.classList.add("hidden");
  const syncToggle = () => {
    // Text glyphs like U+25B6 render as colour emoji on phones; the icons are SVG.
    document.body.classList.toggle("playing", !video.paused && !video.ended);
    if (video.paused || video.ended) {
      document.body.classList.remove("controls-hidden");
      clearTimeout(controlsTimer);
    }
  };
  const scheduleControlsHide = (delay = 4000) => {
    clearTimeout(controlsTimer);
    if (!video.paused && !video.ended) {
      controlsTimer = setTimeout(() => {
        if (!video.paused && !video.ended && !qualityWrap.classList.contains("open")) {
          document.body.classList.add("controls-hidden");
        }
      }, delay);
    }
  };
  const showControls = () => {
    document.body.classList.remove("controls-hidden");
    scheduleControlsHide();
  };
  const tryPlay = () => {
    hideStart();
    const p = video.play();
    if (p && typeof p.catch === "function") p.catch(showStart);
    scheduleControlsHide();
  };
  const togglePlay = () => {
    if (video.paused || video.ended) tryPlay();
    else video.pause();
    showControls();
  };
  const syncVolume = () => {
    volumeRange.value = String(Math.round((video.muted ? 0 : video.volume) * 100));
    document.body.classList.toggle("muted", video.muted || video.volume <= 0);
    mute.setAttribute("aria-label", video.muted || video.volume <= 0 ? "Включить звук" : "Выключить звук");
  };
  // The frame is sandboxed without storage: the page passes the saved volume
  // in the fragment and keeps every change reported below.
  const handoff = (() => {
    const match = /(?:^#|&)asoplay=([^&]*)/.exec(location.hash);
    if (!match) return null;
    try { return JSON.parse(decodeURIComponent(match[1])); } catch (_) { return null; }
  })();
  if (handoff && typeof handoff.volume === "number" && handoff.volume >= 0 && handoff.volume <= 1) {
    video.volume = handoff.volume;
    video.muted = handoff.muted === true;
  }
  syncVolume();
  mute.addEventListener("click", () => {
    video.muted = !video.muted;
    if (!video.muted && video.volume <= 0) video.volume = 0.75;
    syncVolume();
    showControls();
  });
  volumeRange.addEventListener("input", () => {
    const value = Math.min(100, Math.max(0, Number(volumeRange.value) || 0)) / 100;
    video.volume = value;
    video.muted = value <= 0;
    syncVolume();
    showControls();
  });
  const closeQuality = () => {
    if (!qualityWrap.classList.contains("open")) return;
    qualityWrap.classList.remove("open");
    scheduleControlsHide();
  };
  const renderQuality = () => {
    qualityMenu.innerHTML = "";
    const list = Array.isArray(sources) ? sources : [];
    if (!list.length) {
      qualityWrap.classList.add("disabled");
      return;
    }
    qualityBtn.textContent = list[currentQuality] && list[currentQuality].label ? list[currentQuality].label : "Авто";
    if (list.length <= 1) return;
    list.forEach((item, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = item.label || "Авто";
      button.className = index === currentQuality ? "active" : "";
      button.addEventListener("click", () => selectQuality(index));
      qualityMenu.appendChild(button);
    });
  };
  const selectQuality = (index) => {
    const item = sources[index];
    if (!item || !item.url) return;
    const time = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    const wasPaused = video.paused;
    currentQuality = index;
    renderQuality();
    closeQuality();
    const resume = () => {
      if (time > 0) {
        try { video.currentTime = time; } catch (_) {}
      }
      if (!wasPaused) tryPlay();
      video.removeEventListener("loadedmetadata", resume);
    };
    video.addEventListener("loadedmetadata", resume);
    video.src = item.url;
    video.load();
    showControls();
  };
  renderQuality();
  qualityBtn.addEventListener("click", () => {
    if (!Array.isArray(sources) || sources.length <= 1) return;
    qualityWrap.classList.toggle("open");
    document.body.classList.remove("controls-hidden");
    if (qualityWrap.classList.contains("open")) clearTimeout(controlsTimer);
    else scheduleControlsHide();
  });
  document.addEventListener("pointerdown", (event) => {
    if (!qualityWrap.contains(event.target)) closeQuality();
  }, true);
  start.addEventListener("click", tryPlay);
  shot.addEventListener("click", () => { captureScreenshot(); showControls(); });
  toggle.addEventListener("click", togglePlay);
  video.addEventListener("click", togglePlay);
  seek.addEventListener("input", () => {
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    if (duration > 0) video.currentTime = duration * (Number(seek.value) || 0) / 1000;
    showControls();
  });
  seek.addEventListener("pointerdown", () => { seeking = true; });
  seek.addEventListener("pointerup", () => { seeking = false; syncProgress(); });
  seek.addEventListener("change", () => { seeking = false; syncProgress(); });
  // Во весь экран раскрывает страница — свой контейнер вместе с полосками чата серии. Не смогла (браузер не передал ей
  // нажатие) или молчит 600 мс (вкладка со старым кодом) — кадр раскрывается сам; на iPhone — родной режим видео.
  // Раскрыта ли страница, видно и по размеру кадра: ответ страницы может не пройти проверку адреса
  let pageFullscreen = false;
  let askTimer = 0;
  const screenSized = () => Math.abs(innerWidth - screen.width) < 8 && Math.abs(innerHeight - screen.height) < 8;
  const ownFullscreen = () => {
    const root = document.documentElement;
    if (!document.fullscreenElement && root.requestFullscreen) root.requestFullscreen().catch(() => {});
  };
  const toggleFullscreen = () => {
    const root = document.documentElement;
    if (document.fullscreenElement) {
      document.exitFullscreen && document.exitFullscreen();
    } else if (!root.requestFullscreen && video.webkitEnterFullscreen) {
      video.webkitEnterFullscreen();
    } else {
      const leaving = pageFullscreen || screenSized();
      try { parent.postMessage({ type: "asoplay:fullscreen" }, "*"); } catch (_) {}
      clearTimeout(askTimer);
      if (!leaving) askTimer = setTimeout(() => { if (!screenSized()) ownFullscreen(); }, 600);
    }
    showControls();
  };
  fullscreen.addEventListener("click", toggleFullscreen);
  video.addEventListener("dblclick", toggleFullscreen);
  document.addEventListener("mousemove", showControls, true);
  document.addEventListener("pointermove", showControls, true);
  document.addEventListener("focusin", showControls, true);
  document.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    showControls();
    try { parent.postMessage({ type: "asoplay:player-pointerdown" }, "*"); } catch (_) {}
  }, true);
  document.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    try { parent.postMessage({ type: "asoplay:player-context-menu", x: event.clientX, y: event.clientY }, "*"); } catch (_) {}
  }, true);
  // Клавиши, как в привычных плеерах: ← и → — на 5 с назад и вперёд (панель видна миг), пробел — пауза и пуск.
  // Фокус в кадре — ловит он сам; фокус на странице — клавишу присылает она (asoplay:key, ниже)
  const KEYS = { ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight", " ": "Space" };
  const pressKey = (key) => {
    if (key === "Space") { togglePlay(); return; }
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    const time = (Number.isFinite(video.currentTime) ? video.currentTime : 0) + (key === "ArrowRight" ? 5 : -5);
    try { video.currentTime = Math.max(0, duration > 0 ? Math.min(time, duration - 1) : time); } catch (_) {}
    syncProgress();
    document.body.classList.remove("controls-hidden");
    scheduleControlsHide(1500);
  };
  document.addEventListener("keydown", (event) => {
    const key = KEYS[event.key];
    if (!key || event.ctrlKey || event.metaKey || event.altKey || qualityWrap.classList.contains("open")) return;
    // Ползунок громкости стрелками меняет громкость — его родное дело
    if (key !== "Space" && event.target === volumeRange) return;
    event.preventDefault();
    pressKey(key);
  });
  window.addEventListener("message", (event) => {
    const data = event && event.data;
    if (!data || typeof data !== "object") return;
    if (data.type === "asoplay:capture-screenshot") { captureScreenshot(); return; }
    if (event.source !== window.parent || !Array.isArray(pageOrigins) || !pageOrigins.includes(event.origin)) return;
    if (data.type === "asoplay:seek" && Number.isFinite(data.time) && data.time >= 0) {
      const duration = Number.isFinite(video.duration) ? video.duration : 0;
      try { video.currentTime = duration > 0 ? Math.min(data.time, Math.max(0, duration - 1)) : data.time; } catch (_) {}
      syncProgress();
      showControls();
    } else if (data.type === "asoplay:fullscreen-state") {
      pageFullscreen = data.on === true;
      clearTimeout(askTimer);
      if (data.failed === true) ownFullscreen();
    } else if (data.type === "asoplay:key" && Object.values(KEYS).includes(data.key)) {
      pressKey(data.key);
    }
  });
  video.addEventListener("loadedmetadata", sendTime);
  video.addEventListener("durationchange", sendTime);
  video.addEventListener("canplay", () => { sendTime(); if (video.paused) showStart(); });
  video.addEventListener("timeupdate", sendTime);
  video.addEventListener("volumechange", () => {
    syncVolume();
    send("kodik_player_volume_change", { volume: video.volume, muted: video.muted });
  });
  video.addEventListener("playing", () => { hideStart(); syncToggle(); scheduleControlsHide(); });
  video.addEventListener("play", () => { syncToggle(); scheduleControlsHide(); send("kodik_player_play"); });
  video.addEventListener("pause", () => { syncToggle(); send("kodik_player_pause"); showStart(); });
  video.addEventListener("ended", () => send("kodik_player_video_ended"));
  video.addEventListener("error", () => { showStart(); document.body.classList.remove("controls-hidden"); });
  setInterval(sendTime, 1000);
  // Странице: этот плеер умеет перематывать — минуты в чате серии становятся кнопками — и принимает клавиши
  try { parent.postMessage({ type: "asoplay:frame-ready", seek: true, keys: true }, "*"); } catch (_) {}
  try { video.load(); } catch (_) {}
  syncToggle();
  syncProgress();
  setTimeout(() => { if (video.paused) showStart(); }, 500);
})();
