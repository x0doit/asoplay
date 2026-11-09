/*
 * Кадр плеера, часть 1: элементы, источники, связь со страницей и снимок кадра.
 *
 * Автор: Чепела Даниил Максимович.
 */
(() => {
  const video = document.getElementById("@@safe_id@@");
  const start = document.getElementById("av-start");
  const shot = document.getElementById("av-shot");
  const controls = document.getElementById("av-controls");
  const toggle = document.getElementById("av-toggle");
  const seek = document.getElementById("av-seek");
  const timeText = document.getElementById("av-time");
  const mute = document.getElementById("av-mute");
  const volumeRange = document.getElementById("av-volume-range");
  const qualityWrap = document.getElementById("av-quality-wrap");
  const qualityBtn = document.getElementById("av-quality-btn");
  const qualityMenu = document.getElementById("av-quality-menu");
  const fullscreen = document.getElementById("av-fullscreen");
  const sources = @@sources_json@@;
  // Адреса страниц AsoPlay (их ставит сервер): перемотку и полный экран кадр принимает только от страницы с таким адресом
  const pageOrigins = @@page_origins@@;
  if (@@hls_only@@ && !video.canPlayType("application/vnd.apple.mpegurl")) {
    const note = document.getElementById("av-note");
    note.textContent = "Этот браузер не воспроизводит такой поток. Откройте серию в Chrome, Edge или Safari.";
    note.hidden = false;
    start.classList.add("hidden");
    controls.hidden = true;
    shot.hidden = true;
    video.removeAttribute("src");
    return;
  }
  let controlsTimer = 0;
  let seeking = false;
  let currentQuality = 0;
  const send = (key, value) => {
    try { parent.postMessage({ key, value }, "*"); } catch (_) {}
  };
  const sendShotError = (message) => {
    try { parent.postMessage({ type: "asoplay:screenshot-error", message }, "*"); } catch (_) {}
  };
  const waitForDrawableFrame = (item) => new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      try { item.removeEventListener("loadeddata", finish); } catch (_) {}
      resolve();
    };
    try {
      if (item.readyState < 2) item.addEventListener("loadeddata", finish, { once: true });
      if (typeof item.requestVideoFrameCallback === "function") item.requestVideoFrameCallback(finish);
    } catch (_) {}
    requestAnimationFrame(() => requestAnimationFrame(finish));
    setTimeout(finish, 450);
  });
  const canvasLooksEmpty = (canvas) => {
    const probe = document.createElement("canvas");
    const width = Math.min(24, Math.max(1, canvas.width));
    const height = Math.min(24, Math.max(1, canvas.height));
    probe.width = width;
    probe.height = height;
    const ctx = probe.getContext("2d", { willReadFrequently: true });
    if (!ctx) return false;
    ctx.drawImage(canvas, 0, 0, width, height);
    const data = ctx.getImageData(0, 0, width, height).data;
    let alpha = 0;
    for (let i = 3; i < data.length; i += 4) alpha += data[i];
    return alpha < width * height * 8;
  };
  const drawViaImageCapture = async (item) => {
    try {
      if (!item.captureStream || !window.ImageCapture) return null;
      const stream = item.captureStream();
      const track = stream && stream.getVideoTracks ? stream.getVideoTracks()[0] : null;
      if (!track) return null;
      try {
        const bitmap = await new ImageCapture(track).grabFrame();
        const canvas = document.createElement("canvas");
        canvas.width = bitmap.width || item.videoWidth;
        canvas.height = bitmap.height || item.videoHeight;
        const ctx = canvas.getContext("2d");
        if (!ctx) return null;
        ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        if (bitmap.close) bitmap.close();
        return canvasLooksEmpty(canvas) ? null : canvas;
      } finally {
        try { track.stop(); } catch (_) {}
      }
    } catch (_) {
      return null;
    }
  };
  const makeFrameCanvas = async (item) => {
    await waitForDrawableFrame(item);
    const canvas = document.createElement("canvas");
    canvas.width = item.videoWidth;
    canvas.height = item.videoHeight;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) throw new Error("canvas unavailable");
    ctx.drawImage(item, 0, 0, canvas.width, canvas.height);
    if (canvasLooksEmpty(canvas)) {
      const fallback = await drawViaImageCapture(item);
      if (fallback) return fallback;
      throw new Error("blank frame");
    }
    return canvas;
  };
  const canvasToDataUrl = (canvas) => new Promise((resolve, reject) => {
    if (canvas.toBlob && window.FileReader) {
      canvas.toBlob((blob) => {
        if (!blob) { reject(new Error("empty blob")); return; }
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result || ""));
        reader.onerror = () => reject(new Error("reader failed"));
        reader.readAsDataURL(blob);
      }, "image/png");
    } else {
      try { resolve(canvas.toDataURL("image/png")); } catch (err) { reject(err); }
    }
  });
  const captureScreenshot = async () => {
    if (!video || !video.videoWidth || !video.videoHeight) {
      sendShotError("Кадр еще не готов. Запустите видео и попробуйте снова.");
      return;
    }
    try {
      const canvas = await makeFrameCanvas(video);
      const dataUrl = await canvasToDataUrl(canvas);
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
    } catch (err) {
      const name = err && err.name ? String(err.name) : "";
      sendShotError(name === "SecurityError"
        ? "Источник не разрешил снять кадр из видео."
        : "Кадр еще не отрисовался. Запустите видео и попробуйте снова.");
    }
  };
