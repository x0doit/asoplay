/*
 * Мост плеера, часть 3: postMessage и слушатели сообщений, сеть (fetch, XHR, WebSocket, Worker) и свойства элементов.
 *
 * Автор: Чепела Даниил Максимович.
 */
  const nativePostMessage = window.postMessage;
  const proxiedWindows = new WeakMap();
  if (window.HTMLIFrameElement && HTMLIFrameElement.prototype) {
    const contentWindowDesc = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, "contentWindow");
    if (contentWindowDesc && contentWindowDesc.get) {
      try {
        Object.defineProperty(HTMLIFrameElement.prototype, "contentWindow", {
          configurable: true,
          enumerable: contentWindowDesc.enumerable,
          get() {
            const win = contentWindowDesc.get.call(this);
            if (!win) return win;
            if (proxiedWindows.has(win)) return proxiedWindows.get(win);
            const proxy = new Proxy(win, {
              set(target, prop, value) {
                return Reflect.set(target, prop, value, target);
              },
              get(target, prop, receiver) {
                if (prop === "postMessage") {
                  return (message, targetOrigin, transfer) => {
                    message = sanitizeMessage(message);
                    targetOrigin = normalizeTargetOrigin(targetOrigin);
                    if (typeof transfer !== "undefined") return nativePostMessage.call(target, message, targetOrigin, transfer);
                    return nativePostMessage.call(target, message, targetOrigin);
                  };
                }
                if (prop === "fetch" && typeof target.fetch === "function") {
                  return (input, init) => {
                    const url = input && input.url ? input.url : input;
                    try {
                      if (input && input.url && typeof target.Request !== "undefined") {
                        return target.fetch(new target.Request(proxied(url), input), init);
                      }
                    } catch (_) {}
                    return target.fetch(proxied(url), init);
                  };
                }
                let value;
                try {
                  value = Reflect.get(target, prop, target);
                } catch (_) {
                  storageShieldLog();
                  return undefined;
                }
                return typeof value === "function" ? value.bind(target) : value;
              }
            });
            proxiedWindows.set(win, proxy);
            return proxy;
          }
        });
      } catch (_) {}
    }
  }
  const fallbackMessageOrigin = () => {
    try {
      return document.referrer ? new URL(document.referrer).origin : LOCAL_ORIGIN;
    } catch (_) {
      return LOCAL_ORIGIN;
    }
  };
  const wrapMessageEvent = (event) => {
    if (!event || event.type !== "message") return event;
    return new Proxy(event, {
      get(target, prop, receiver) {
        if (prop === "origin" && target.origin === "null") return fallbackMessageOrigin();
        if (prop === "source") {
          const source = target.source;
          // SDKs identify their iframe with event.source === iframe.contentWindow.
          if (source && !proxiedWindows.has(source)) {
            document.querySelectorAll("iframe").forEach(frame => {
              try { void frame.contentWindow; } catch (_) {}
            });
          }
          return proxiedWindows.get(source) || source;
        }
        let value;
        try {
          value = Reflect.get(target, prop, target);
        } catch (_) {
          storageShieldLog();
          return undefined;
        }
        return typeof value === "function" ? value.bind(target) : value;
      }
    });
  };
  const nativeAddEventListener = EventTarget.prototype.addEventListener;
  const nativeRemoveEventListener = EventTarget.prototype.removeEventListener;
  const messageListeners = new WeakMap();
  const wrapMessageListener = (listener) => {
    if (!listener || (typeof listener !== "function" && typeof listener.handleEvent !== "function")) return listener;
    if (messageListeners.has(listener)) return messageListeners.get(listener);
    const wrapped = typeof listener === "function"
      ? function(event) { return listener.call(this, wrapMessageEvent(event)); }
      : {
          handleEvent(event) { return listener.handleEvent(wrapMessageEvent(event)); }
        };
    messageListeners.set(listener, wrapped);
    return wrapped;
  };
  EventTarget.prototype.addEventListener = function(type, listener, options) {
    if (String(type).toLowerCase() === "message" && listener) {
      return nativeAddEventListener.call(this, type, wrapMessageListener(listener), options);
    }
    return nativeAddEventListener.call(this, type, listener, options);
  };
  EventTarget.prototype.removeEventListener = function(type, listener, options) {
    if (String(type).toLowerCase() === "message" && listener) {
      return nativeRemoveEventListener.call(this, type, messageListeners.get(listener) || listener, options);
    }
    return nativeRemoveEventListener.call(this, type, listener, options);
  };
  try {
    let assignedOnMessage = null;
    Object.defineProperty(window, "onmessage", {
      configurable: true,
      enumerable: true,
      get() { return assignedOnMessage; },
      set(listener) {
        if (assignedOnMessage) nativeRemoveEventListener.call(window, "message", wrapMessageListener(assignedOnMessage));
        assignedOnMessage = listener;
        if (listener && (typeof listener === "function" || typeof listener.handleEvent === "function")) {
          nativeAddEventListener.call(window, "message", wrapMessageListener(listener));
        }
      }
    });
  } catch (_) {}
  addEventListener("click", (event) => {
    const a = event.target && event.target.closest ? event.target.closest("a[target], a[href]") : null;
    if (!a) return;
    if (isAdTarget(a.href || "") || a.target === "_blank") {
      if (isAdTarget(a.href || "")) shieldLog();
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);

  const nativeFetch = window.fetch ? window.fetch.bind(window) : null;
  if (IS_KODIK && nativeFetch) {
    window.__asoplayCheckCdn = async (url, report) => {
      let available = false;
      try {
        const response = await nativeFetch("/player/cdn-check?url=" + encodeURIComponent(url)
          + "&base=" + encodeURIComponent(BASE), { credentials: "omit", cache: "no-store" });
        if (!response.ok) throw new Error("CDN check failed: " + response.status);
        available = (await response.json()).available === true;
      } catch (err) {
        nativeConsole.warn("AsoPlay CDN diagnostic failed", err);
      }
      if (!available) {
        diagnosticLog("cdn-unavailable", "Проверочный сервер Kodik вышел из чата. Сэмпай Даниэль записал ему прогул.");
      }
      report(available);
    };
  }
  if (nativeFetch) {
    window.fetch = (input, init) => {
      const url = input && input.url ? input.url : input;
      if (typeof Request !== "undefined" && input instanceof Request) {
        return nativeFetch(new Request(proxied(url), input), init);
      }
      return nativeFetch(proxied(url), init);
    };
  }
  const XHR = window.XMLHttpRequest;
  if (XHR) {
    const open = XHR.prototype.open;
    XHR.prototype.open = function(method, url, ...rest) {
      return open.call(this, method, proxied(url), ...rest);
    };
  }
  if (navigator.sendBeacon) {
    navigator.sendBeacon = () => false;
  }
  if (window.WebSocket) {
    const NativeWS = window.WebSocket;
    window.WebSocket = function(url, protocols) {
      if (isAdTarget(url)) {
        shieldLog();
        throw new Error("Blocked ad websocket");
      }
      return new NativeWS(url, protocols);
    };
  }
  if (window.Worker) {
    const NativeWorker = window.Worker;
    window.Worker = function(url, options) {
      return new NativeWorker(proxied(url), options);
    };
    window.Worker.prototype = NativeWorker.prototype;
  }
  if (window.SharedWorker) {
    const NativeSharedWorker = window.SharedWorker;
    window.SharedWorker = function(url, options) {
      return new NativeSharedWorker(proxied(url), options);
    };
    window.SharedWorker.prototype = NativeSharedWorker.prototype;
  }

  const setAttr = Element.prototype.setAttribute;
  const prepareVideoCors = (el) => {
    if (String(el.tagName || "").toUpperCase() !== "VIDEO"
        || el.getAttribute("crossorigin") === "anonymous") return;
    try { setAttr.call(el, "crossorigin", "anonymous"); } catch (_) {}
  };
  Element.prototype.setAttribute = function(name, value) {
    if (String(name).toLowerCase() === "srcdoc") value = rewriteSrcdoc(value);
    if (String(name).toLowerCase() === "src") prepareVideoCors(this);
    if (ATTRS.has(String(name).toLowerCase())) value = String(name).toLowerCase() === "src"
      && this.tagName === "IFRAME" ? proxiedFrame(value) : proxied(value);
    return setAttr.call(this, name, value);
  };
  const patchProp = (proto, prop) => {
    const desc = Object.getOwnPropertyDescriptor(proto, prop);
    if (!desc || !desc.set) return;
    Object.defineProperty(proto, prop, {
      configurable: true,
      enumerable: desc.enumerable,
      get: desc.get,
      set(value) {
        if (prop === "src") prepareVideoCors(this);
        return desc.set.call(this, prop === "src" && this.tagName === "IFRAME" ? proxiedFrame(value) : proxied(value));
      }
    });
  };
  const propTargets = [
    [window.HTMLScriptElement && HTMLScriptElement.prototype, "src"],
    [window.HTMLIFrameElement && HTMLIFrameElement.prototype, "src"],
    [window.HTMLImageElement && HTMLImageElement.prototype, "src"],
    [window.HTMLMediaElement && HTMLMediaElement.prototype, "src"],
    [window.HTMLSourceElement && HTMLSourceElement.prototype, "src"],
    [window.HTMLTrackElement && HTMLTrackElement.prototype, "src"],
    [window.HTMLLinkElement && HTMLLinkElement.prototype, "href"],
    [window.HTMLAnchorElement && HTMLAnchorElement.prototype, "href"],
    [window.HTMLVideoElement && HTMLVideoElement.prototype, "poster"],
  ];
  propTargets.forEach(([proto, prop]) => proto && patchProp(proto, prop));

  const patchHtmlProp = (proto, prop, rewrite = rewriteHtml) => {
    const desc = Object.getOwnPropertyDescriptor(proto, prop);
    if (!desc || !desc.set) return;
    Object.defineProperty(proto, prop, {
      configurable: true,
      enumerable: desc.enumerable,
      get: desc.get,
      set(value) { return desc.set.call(this, rewrite(value)); }
    });
  };
  patchHtmlProp(Element.prototype, "innerHTML");
  patchHtmlProp(Element.prototype, "outerHTML");
