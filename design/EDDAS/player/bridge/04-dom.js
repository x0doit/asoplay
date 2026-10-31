/*
 * Мост плеера, часть 4: HTML-свойства, наблюдатель за новыми узлами и чистка рекламы.
 *
 * Автор: Чепела Даниил Максимович.
 */
  if (window.HTMLIFrameElement) patchHtmlProp(HTMLIFrameElement.prototype, "srcdoc", rewriteSrcdoc);
  const nativeInsertAdjacentHTML = Element.prototype.insertAdjacentHTML;
  if (nativeInsertAdjacentHTML) {
    Element.prototype.insertAdjacentHTML = function(position, html) {
      return nativeInsertAdjacentHTML.call(this, position, rewriteHtml(html));
    };
  }
  const nativeWrite = document.write.bind(document);
  const nativeWriteln = document.writeln.bind(document);
  document.write = (...items) => nativeWrite(...items.map(rewriteHtml));
  document.writeln = (...items) => nativeWriteln(...items.map(rewriteHtml));

  const prepareScreenshotVideo = (video) => {
    if (!video || String(video.tagName || "").toUpperCase() !== "VIDEO") return;
    if (video.currentSrc || video.readyState > 0) return;
    prepareVideoCors(video);
  };
  const postScreenshot = (payload) => {
    try { window.parent.postMessage(payload, "*"); } catch (_) {}
  };
  const postScreenshotError = (message) => {
    postScreenshot({ type: "asoplay:screenshot-error", message: String(message || "Скриншот недоступен") });
  };
  const setScreenshotBusy = (busy) => {
    try {
      document.querySelectorAll(".asoplay-screenshot-button").forEach((button) => {
        button.classList.toggle("asoplay-screenshot-busy", !!busy);
      });
    } catch (_) {}
  };
  const waitForDrawableFrame = (video) => new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      try { video.removeEventListener("loadeddata", finish); } catch (_) {}
      resolve();
    };
    try {
      if (video.readyState < 2) video.addEventListener("loadeddata", finish, { once: true });
      if (typeof video.requestVideoFrameCallback === "function") video.requestVideoFrameCallback(finish);
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
  const drawViaImageCapture = async (video) => {
    try {
      if (!video.captureStream || !window.ImageCapture) return null;
      const stream = video.captureStream();
      const track = stream && stream.getVideoTracks ? stream.getVideoTracks()[0] : null;
      if (!track) return null;
      try {
        const bitmap = await new ImageCapture(track).grabFrame();
        const canvas = document.createElement("canvas");
        canvas.width = bitmap.width || video.videoWidth;
        canvas.height = bitmap.height || video.videoHeight;
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
  const makeFrameCanvas = async (video) => {
    await waitForDrawableFrame(video);
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    if (!ctx) throw new Error("canvas unavailable");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    if (canvasLooksEmpty(canvas)) {
      const fallback = await drawViaImageCapture(video);
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
    const video = Array.from(document.querySelectorAll("video")).find((item) =>
      item && item.videoWidth > 0 && item.videoHeight > 0
    );
    if (!video) {
      postScreenshotError("Кадр еще не готов. Запустите видео и попробуйте снова.");
      return;
    }
    try {
      setScreenshotBusy(true);
      const canvas = await makeFrameCanvas(video);
      const payload = {
        type: "asoplay:screenshot",
        width: canvas.width,
        height: canvas.height,
        time: Number(video.currentTime) || 0,
        title: document.title || ""
      };
      payload.dataUrl = await canvasToDataUrl(canvas);
      setScreenshotBusy(false);
      postScreenshot(payload);
    } catch (err) {
      setScreenshotBusy(false);
      const name = err && err.name ? String(err.name) : "";
      postScreenshotError(name === "SecurityError"
        ? "Источник не разрешил снять кадр из видео."
        : "Кадр еще не отрисовался. Запустите видео и попробуйте снова.");
    }
  };
  const screenshotIcon =
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">' +
    '<path class="fill" d="M9.2 4.5h5.6l1.2 2h3A2.5 2.5 0 0 1 21.5 9v8A2.5 2.5 0 0 1 19 19.5H5A2.5 2.5 0 0 1 2.5 17V9A2.5 2.5 0 0 1 5 6.5h3l1.2-2Zm2.8 4A4.3 4.3 0 1 0 12 17a4.3 4.3 0 0 0 0-8.5Zm0 2A2.3 2.3 0 1 1 12 15a2.3 2.3 0 0 1 0-4.5Z"/></svg>';
  const markScreenshotUi = (root) => {
    if (!IS_KODIK) return;
    const scope = root && root.querySelectorAll ? root : document;
    const buttons = [];
    try {
      if (scope.matches && (scope.matches(".share_button") || scope.matches(".fp-share_button"))) buttons.push(scope);
      buttons.push(...scope.querySelectorAll(".share_button, .fp-share_button"));
    } catch (_) {}
    buttons.forEach((button) => {
      if (!button || button.__asoplayScreenshotButton) return;
      button.__asoplayScreenshotButton = true;
      button.classList.add("asoplay-screenshot-button");
      button.setAttribute("title", "Сделать скриншот");
      button.setAttribute("aria-label", "Сделать скриншот");
      try { button.innerHTML = screenshotIcon; } catch (_) {}
    });
    try {
      scope.querySelectorAll(".get_code_heading").forEach((item) => {
        item.textContent = "Сделать скриншот";
      });
      scope.querySelectorAll("video").forEach(prepareScreenshotVideo);
    } catch (_) {}
  };
  if (IS_KODIK) {
    document.addEventListener("click", (event) => {
      const button = event.target && event.target.closest
        ? event.target.closest(".asoplay-screenshot-button, .share_button, .fp-share_button")
        : null;
      if (!button) return;
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      captureScreenshot();
    }, true);
  }
  document.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    try { window.parent.postMessage({ type: "asoplay:player-pointerdown" }, "*"); } catch (_) {}
  }, true);
  document.addEventListener("contextmenu", (event) => {
    try {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      window.parent.postMessage({
        type: "asoplay:player-context-menu",
        x: event.clientX,
        y: event.clientY
      }, "*");
    } catch (_) {}
  }, true);
  window.addEventListener("message", (event) => {
    const data = event && event.data;
    if (!data || typeof data !== "object") return;
    if (data.type === "asoplay:capture-screenshot") captureScreenshot();
  });

  const clean = (root) => {
    if (!root || !root.querySelectorAll) return;
    const selector = "iframe, script, img, video, source, track, a, link, embed, object";
    const nodes = root.matches && root.matches(selector)
      ? [root, ...root.querySelectorAll(selector)]
      : [...root.querySelectorAll(selector)];
    nodes.forEach((el) => {
      for (const attr of ATTRS) {
        if (!el.hasAttribute || !el.hasAttribute(attr)) continue;
        const current = el.getAttribute(attr);
        const rewritten = attr === "src" && el.tagName === "IFRAME" ? proxiedFrame(current) : proxied(current);
        // Reassigning even an identical media src restarts loading and aborts play().
        if (rewritten !== current) el.setAttribute(attr, rewritten);
      }
      prepareScreenshotVideo(el);
      // Script text is code: Alloha's player config mentions "preroll", its referrer check "casino".
      const text = el.tagName === "SCRIPT" ? "" : el.textContent;
      const marker = [el.id, el.className, el.title, el.alt, text].join(" ");
      if (AD_RE.test(marker) && !/video|player/i.test(marker)) el.remove();
    });
  };
  new MutationObserver((items) => {
    for (const item of items) for (const node of item.addedNodes) {
      if (node.nodeType !== 1) continue;
      clean(node);
      markScreenshotUi(node);
    }
  }).observe(document.documentElement, { childList: true, subtree: true });
  clean(document);
  markScreenshotUi(document);
})();
