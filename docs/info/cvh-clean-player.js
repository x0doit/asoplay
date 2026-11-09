(() => {
  const video = document.getElementById("av-cvh-video");
  const start = document.getElementById("av-start");
  const controls = document.getElementById("av-controls");
  const toggle = document.getElementById("av-toggle");
  const seek = document.getElementById("av-seek");
  const timeText = document.getElementById("av-time");
  const quality = document.getElementById("av-quality");
  const fullscreen = document.getElementById("av-fullscreen");
  const sources = window.__ASOPLAY_CVH_SOURCES__ || [];
  let controlsTimer = 0;

  const send = (key, value) => {
    try { parent.postMessage({ key, value }, "*"); } catch (_) {}
  };

  const sendShotError = (message) => {
    try { parent.postMessage({ type: "asoplay:screenshot-error", message }, "*"); } catch (_) {}
  };

  const captureScreenshot = () => {
    if (!video || !video.videoWidth || !video.videoHeight) {
      sendShotError("Кадр еще не готов. Запустите видео и попробуйте снова.");
      return;
    }
    try {
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new Error("canvas unavailable");
      ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
      const done = (dataUrl) => {
        try {
          parent.postMessage({
            type: "asoplay:screenshot",
            dataUrl,
            width: canvas.width,
            height: canvas.height,
            time: Number(video.currentTime) || 0,
            title: document.title || ""
          }, "*");
        } catch (_) {}
      };
      if (canvas.toBlob && window.FileReader) {
        canvas.toBlob((blob) => {
          if (!blob) {
            sendShotError("Не удалось сохранить кадр.");
            return;
          }
          const reader = new FileReader();
          reader.onload = () => done(String(reader.result || ""));
          reader.onerror = () => sendShotError("Не удалось прочитать кадр.");
          reader.readAsDataURL(blob);
        }, "image/png");
      } else {
        done(canvas.toDataURL("image/png"));
      }
    } catch (err) {
      const name = err && err.name ? String(err.name) : "";
      sendShotError(name === "SecurityError"
        ? "Источник не разрешил снять кадр из видео."
        : "Не удалось сделать скриншот.");
    }
  };

  const fmt = (value) => {
    value = Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
    const h = Math.floor(value / 3600);
    const m = Math.floor((value % 3600) / 60);
    const s = value % 60;
    return h
      ? h + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0")
      : m + ":" + String(s).padStart(2, "0");
  };

  const syncProgress = () => {
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    const time = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    seek.value = duration > 0
      ? String(Math.min(1000, Math.max(0, Math.round(time / duration * 1000))))
      : "0";
    timeText.textContent = fmt(time) + " / " + fmt(duration);
  };

  const sendTime = () => {
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    const time = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    syncProgress();
    if (duration > 0) send("kodik_player_duration_update", duration);
    if (time > 0) send("kodik_player_time_update", time);
  };

  const showStart = () => {
    if (video.paused && !video.ended) start.classList.remove("hidden");
  };

  const hideStart = () => start.classList.add("hidden");

  const syncToggle = () => {
    toggle.textContent = video.paused ? "▶" : "❚❚";
    document.body.classList.toggle("playing", !video.paused && !video.ended);
    if (video.paused || video.ended) {
      document.body.classList.remove("controls-hidden");
      clearTimeout(controlsTimer);
    }
  };

  const scheduleControlsHide = (delay = 10000) => {
    clearTimeout(controlsTimer);
    if (!video.paused && !video.ended) {
      controlsTimer = setTimeout(() => {
        controls.classList.remove("open");
        if (!video.paused && !video.ended && !controls.matches(":focus-within")) {
          document.body.classList.add("controls-hidden");
        }
      }, delay);
    }
  };

  const showControls = () => {
    document.body.classList.remove("controls-hidden");
    controls.classList.add("open");
    scheduleControlsHide(10000);
  };

  const tryPlay = () => {
    hideStart();
    const p = video.play();
    if (p && typeof p.catch === "function") p.catch(showStart);
    scheduleControlsHide(10000);
  };

  const togglePlay = () => {
    if (video.paused || video.ended) tryPlay();
    else video.pause();
    showControls();
  };

  if (Array.isArray(sources) && sources.length > 1) {
    sources.forEach((item, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = item.label;
      quality.appendChild(option);
    });
  } else {
    quality.disabled = true;
  }

  quality.addEventListener("change", () => {
    const item = sources[Number(quality.value)];
    if (!item || !item.url) return;
    const time = Number.isFinite(video.currentTime) ? video.currentTime : 0;
    const wasPaused = video.paused;
    video.src = item.url;
    video.load();
    const resume = () => {
      if (time > 0) {
        try { video.currentTime = time; } catch (_) {}
      }
      if (!wasPaused) tryPlay();
      video.removeEventListener("loadedmetadata", resume);
    };
    video.addEventListener("loadedmetadata", resume);
    showControls();
  });

  start.addEventListener("click", tryPlay);
  toggle.addEventListener("click", togglePlay);
  video.addEventListener("click", togglePlay);

  seek.addEventListener("input", () => {
    const duration = Number.isFinite(video.duration) ? video.duration : 0;
    if (duration > 0) video.currentTime = duration * (Number(seek.value) || 0) / 1000;
    showControls();
  });

  fullscreen.addEventListener("click", () => {
    const root = document.documentElement;
    if (document.fullscreenElement) {
      document.exitFullscreen && document.exitFullscreen();
    } else if (root.requestFullscreen) {
      root.requestFullscreen().catch(() => {});
    } else if (video.webkitEnterFullscreen) {
      video.webkitEnterFullscreen();
    }
    showControls();
  });

  document.addEventListener("mousemove", showControls, true);
  document.addEventListener("pointerdown", showControls, true);
  document.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    try { parent.postMessage({ type: "asoplay:player-pointerdown" }, "*"); } catch (_) {}
  }, true);
  document.addEventListener("contextmenu", (event) => {
    event.preventDefault();
    try {
      parent.postMessage({
        type: "asoplay:player-context-menu",
        x: event.clientX,
        y: event.clientY
      }, "*");
    } catch (_) {}
  }, true);
  window.addEventListener("message", (event) => {
    const data = event && event.data;
    if (data && typeof data === "object" && data.type === "asoplay:capture-screenshot") {
      captureScreenshot();
    }
  });

  video.addEventListener("loadedmetadata", sendTime);
  video.addEventListener("durationchange", sendTime);
  video.addEventListener("canplay", () => {
    sendTime();
    if (video.paused) showStart();
  });
  video.addEventListener("timeupdate", sendTime);
  video.addEventListener("playing", () => {
    hideStart();
    syncToggle();
    scheduleControlsHide(10000);
  });
  video.addEventListener("play", () => {
    syncToggle();
    scheduleControlsHide(10000);
    send("kodik_player_play");
  });
  video.addEventListener("pause", () => {
    syncToggle();
    send("kodik_player_pause");
    showStart();
  });
  video.addEventListener("ended", () => send("kodik_player_video_ended"));
  video.addEventListener("error", showStart);

  setInterval(sendTime, 1000);
  try { video.load(); } catch (_) {}
  syncToggle();
  setTimeout(() => { if (video.paused) showStart(); }, 500);
})();
