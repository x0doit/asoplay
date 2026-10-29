// App shell: the "Ещё" sheet, header search, theme and the active section.
// Markup and behaviour follow the EDDAS shell of AnimeSocial (index.tpl, core.js).

const THEME_KEY = "as-theme";  // the same key and values as AnimeSocial's theme switch
const THEME_LABELS = { system: "Как в системе", light: "Светлая", dark: "Тёмная" };
const THEME_COLORS = { light: "#ffffff", dark: "#1b2532" };

export function getTheme() {
  let theme = "system";
  try { theme = localStorage.getItem(THEME_KEY) || "system"; } catch (_) {}
  return THEME_LABELS[theme] ? theme : "system";
}

export function applyTheme(theme) {
  const root = document.documentElement;
  if (theme === "light" || theme === "dark") root.setAttribute("data-theme", theme);
  else root.removeAttribute("data-theme");
  try {
    if (theme === "light" || theme === "dark") localStorage.setItem(THEME_KEY, theme);
    else localStorage.removeItem(THEME_KEY);
  } catch (_) {}
  // The browser bar follows the forced theme; with "system" each meta keeps its media query.
  document.querySelectorAll('meta[name="theme-color"]').forEach(meta => {
    const own = meta.media.includes("dark") ? "dark" : "light";
    meta.content = THEME_COLORS[THEME_COLORS[theme] ? theme : own];
  });
  document.querySelectorAll("[data-theme-label]").forEach(el => { el.textContent = THEME_LABELS[getTheme()]; });
  document.dispatchEvent(new CustomEvent("av:theme", { detail: getTheme() }));
}

export function themeLabel(theme) {
  return THEME_LABELS[theme] || THEME_LABELS.system;
}

// Labels rendered after the shell (the account menu) pick up the current theme.
export function syncThemeLabels() {
  document.querySelectorAll("[data-theme-label]").forEach(el => { el.textContent = THEME_LABELS[getTheme()]; });
}

function cycleTheme() {
  const order = ["system", "light", "dark"];
  applyTheme(order[(order.indexOf(getTheme()) + 1) % order.length]);
}

// Marks every link of a section (side menu, tab bar, sheet) as current.
export function setActiveSection(view) {
  document.documentElement.dataset.view = view || "";
  document.querySelectorAll("[data-nav]").forEach(el => {
    const active = !!view && el.dataset.nav === view;
    el.classList.toggle("is-active", active);
    if (el.tagName === "A") {
      if (active) el.setAttribute("aria-current", "page");
      else el.removeAttribute("aria-current");
    }
  });
}

export function activeSection() {
  return document.documentElement.dataset.view || "";
}

// A bottom sheet follows the finger: drag it down from anywhere, or from its list when that list is at the top.
// Released below a third of its height or flicked down, it closes; otherwise it springs back. While a finger
// is on the sheet or the scrim the page underneath neither scrolls nor overscrolls (iPhone ignores
// body { overflow: hidden } for touches). Port of SheetDrag from AnimeSocial core.js.
export const SheetDrag = {
  SLOP: 6,
  bind(panel, o) {
    if (!panel || panel._sheetDrag) return;
    panel._sheetDrag = true;
    let s = null, closing = false, timer = 0, pending = null;
    const scrim = o.scrim || null;
    const ease = "var(--duration-base) var(--ease-out)";
    const put = (y, animate) => {
      panel.style.transition = animate ? `transform ${ease}` : "none";
      panel.style.transform = y > 0 ? `translateY(${Math.round(y)}px)` : "";
      if (!scrim) return;
      scrim.style.transition = animate ? `opacity ${ease}` : "none";
      scrim.style.opacity = y > 0 ? String(Math.max(0, 1 - y / (panel.offsetHeight || 1))) : "";
    };
    const clear = () => {
      panel.style.transition = panel.style.transform = "";
      if (scrim) scrim.style.transition = scrim.style.opacity = "";
    };
    const stopPending = () => {
      if (!pending) return;
      panel.removeEventListener("transitionend", pending);
      clearTimeout(timer);
      pending = null;
    };
    const settle = (close) => {
      stopPending();
      closing = close;
      put(close ? panel.offsetHeight : 0, true);
      pending = (e) => {
        if (e && (e.target !== panel || e.propertyName !== "transform")) return;
        stopPending();
        if (closing) {
          closing = false;
          o.close();
        }
        clear();
      };
      panel.addEventListener("transitionend", pending);
      timer = setTimeout(pending, 450);
    };
    panel.addEventListener("touchstart", (e) => {
      s = null;
      if (closing || e.touches.length !== 1 || (o.active && !o.active())) return;
      if (o.skip && e.target.closest?.(o.skip)) return;
      stopPending();
      const t = e.touches[0];
      const sc = o.scroll ? e.target.closest?.(o.scroll) : null;
      s = { x: t.clientX, y: t.clientY, ly: t.clientY, pts: [[e.timeStamp, t.clientY]], dy: 0, mode: "", sc: sc && panel.contains(sc) ? sc : null };
    }, { passive: true });
    panel.addEventListener("touchmove", (e) => {
      if (!s) return;
      const t = e.touches[0], dx = t.clientX - s.x, dy = t.clientY - s.y;
      if (e.touches.length !== 1) {
        if (s.mode === "drag") settle(false);
        s = null;
        return;
      }
      if (!s.mode) {
        if (Math.abs(dx) < SheetDrag.SLOP && Math.abs(dy) < SheetDrag.SLOP) return;
        if (dy > 0 && Math.abs(dy) > Math.abs(dx) && (!s.sc || s.sc.scrollTop <= 0)) s.mode = "drag";
        else s.mode = s.sc ? "scroll" : "hold";
      }
      if (s.mode === "drag") {
        if (e.cancelable) e.preventDefault();
        // Speed over the last 100 ms: a finger often slows down right before it lifts.
        s.pts.push([e.timeStamp, t.clientY]);
        while (s.pts.length > 2 && e.timeStamp - s.pts[0][0] > 100) s.pts.shift();
        s.dy = Math.max(0, dy);
        put(s.dy, false);
        return;
      }
      if (s.mode === "scroll") {
        // The sheet's list reached its edge: neither it nor the page moves further.
        const sc = s.sc, up = t.clientY < s.ly;
        s.ly = t.clientY;
        if (!(up ? sc.scrollTop + sc.clientHeight >= sc.scrollHeight - 1 : sc.scrollTop <= 0)) return;
      }
      if (e.cancelable) e.preventDefault();
    }, { passive: false });
    const end = (e) => {
      const st = s;
      s = null;
      if (!st || st.mode !== "drag") return;
      const h = panel.offsetHeight, a = st.pts[0], z = st.pts[st.pts.length - 1];
      // The finger stopped before lifting: that is not a flick.
      const v = (z[0] > a[0] && e.timeStamp - z[0] < 80) ? (z[1] - a[1]) / (z[0] - a[0]) : 0;
      settle(e.type === "touchend" && st.dy > 0 && (st.dy > Math.min(h / 3, 160) || (v > 0.45 && st.dy > 20)));
    };
    panel.addEventListener("touchend", end);
    panel.addEventListener("touchcancel", end);
    if (scrim) {
      let sx = 0, sy = 0;
      scrim.addEventListener("touchstart", (e) => { sx = e.touches[0].clientX; sy = e.touches[0].clientY; }, { passive: true });
      scrim.addEventListener("touchmove", (e) => {
        const t = e.touches[0];
        if (e.cancelable && (Math.abs(t.clientX - sx) >= SheetDrag.SLOP || Math.abs(t.clientY - sy) >= SheetDrag.SLOP)) e.preventDefault();
      }, { passive: false });
    }
  },
};

// Rows wider than their column (seasons, tabs, the activity graph, home rows on a phone) scroll sideways.
// A finger swipes them; a mouse gets the same: the wheel moves the row while it has room to move, and the row
// can be dragged. A drag that ends on a link does not open it.
const STRIPS = ".seasons-bar, .tabs-scroll, .gh-scroll, .home-grid";

function scrollableStrip(target) {
  const strip = target?.closest?.(STRIPS);
  return strip && strip.scrollWidth > strip.clientWidth + 1 ? strip : null;
}

function setupStripScrolling() {
  document.addEventListener("wheel", (e) => {
    if (e.ctrlKey || e.shiftKey || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return;
    const strip = scrollableStrip(e.target);
    if (!strip) return;
    const step = e.deltaMode === 1 ? e.deltaY * 16 : e.deltaMode === 2 ? e.deltaY * strip.clientWidth : e.deltaY;
    const max = strip.scrollWidth - strip.clientWidth;
    const next = Math.max(0, Math.min(max, strip.scrollLeft + step));
    // At either end the wheel goes back to scrolling the page.
    if (Math.abs(next - strip.scrollLeft) < 1) return;
    e.preventDefault();
    strip.scrollLeft = next;
  }, { passive: false });

  let drag = null;
  let swallowClick = false;
  document.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "mouse" || e.button !== 0) return;
    const strip = scrollableStrip(e.target);
    if (strip) drag = { strip, x: e.clientX, left: strip.scrollLeft, moved: false };
  });
  document.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.x;
    if (!drag.moved && Math.abs(dx) < 6) return;
    drag.moved = true;
    drag.strip.classList.add("is-strip-dragging");
    drag.strip.scrollLeft = drag.left - dx;
  });
  const endDrag = () => {
    if (!drag) return;
    if (drag.moved) {
      swallowClick = true;
      setTimeout(() => { swallowClick = false; }, 0);
    }
    drag.strip.classList.remove("is-strip-dragging");
    drag = null;
  };
  document.addEventListener("pointerup", endDrag);
  document.addEventListener("pointercancel", endDrag);
  // Links and images inside a row would otherwise start the browser's own drag-and-drop.
  document.addEventListener("dragstart", (e) => { if (scrollableStrip(e.target)) e.preventDefault(); });
  document.addEventListener("click", (e) => {
    if (!swallowClick) return;
    swallowClick = false;
    e.preventDefault();
    e.stopPropagation();
  }, true);
}

export function setupNavigation({ nav } = {}) {
  setupStripScrolling();
  const sheet = document.querySelector("#appSheet");
  const panel = sheet?.querySelector(".app-sheet__panel");
  const search = document.querySelector("#appSearch");
  const searchInput = document.querySelector("#appSearchInput");
  const searchToggle = document.querySelector("#appSearchToggle");
  let sheetReturn = null;
  let previousOverflow = "";

  const openers = () => document.querySelectorAll("[data-sheet-open]");
  const closeSheet = (restoreFocus = true) => {
    if (!sheet?.classList.contains("is-open")) return;
    // Focus leaves the sheet before aria-hidden: Chrome refuses aria-hidden on an ancestor of the focused element.
    if (sheet.contains(document.activeElement)) {
      const back = restoreFocus && sheetReturn?.isConnected && sheetReturn.getClientRects().length ? sheetReturn : null;
      if (back) back.focus({ preventScroll: true });
      else document.activeElement.blur();
    }
    sheet.classList.remove("is-open");
    sheet.setAttribute("aria-hidden", "true");
    openers().forEach(b => b.setAttribute("aria-expanded", "false"));
    document.body.style.overflow = previousOverflow;
  };
  const openSheet = (opener) => {
    if (!sheet || sheet.classList.contains("is-open")) return;
    sheetReturn = opener || document.activeElement;
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    sheet.classList.add("is-open");
    sheet.setAttribute("aria-hidden", "false");
    openers().forEach(b => b.setAttribute("aria-expanded", "true"));
    panel.scrollTop = 0;
    panel.querySelector(".ed-menu__item")?.focus({ preventScroll: true });
  };

  document.addEventListener("click", (e) => {
    const opener = e.target.closest("[data-sheet-open]");
    if (opener) { openSheet(opener); return; }
    if (e.target.closest("[data-sheet-close]")) { closeSheet(); return; }
    if (sheet?.contains(e.target) && e.target.closest("a[href]")) closeSheet(false);
    const themeButton = e.target.closest("[data-theme-cycle]");
    if (themeButton) cycleTheme();
  });
  if (panel) SheetDrag.bind(panel, { scrim: sheet.querySelector(".app-sheet__scrim"), scroll: ".app-sheet__panel", close: () => closeSheet() });

  const closeSearch = () => {
    if (!search?.classList.contains("is-open")) return;
    search.classList.remove("is-open");
    searchToggle?.setAttribute("aria-expanded", "false");
  };
  searchToggle?.addEventListener("click", () => {
    const open = !search.classList.contains("is-open");
    search.classList.toggle("is-open", open);
    searchToggle.setAttribute("aria-expanded", String(open));
    if (open) searchInput.focus();
  });
  search?.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = searchInput.value.trim();
    searchInput.blur();
    closeSearch();
    nav?.(q ? `/catalog?q=${encodeURIComponent(q)}` : "/catalog");
  });

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (sheet?.classList.contains("is-open")) { e.preventDefault(); closeSheet(); }
    else if (search?.classList.contains("is-open") && search.contains(document.activeElement)) { closeSearch(); searchToggle?.focus(); }
  });

  // The header search shows the query of the catalog page that is open.
  const syncSearch = () => {
    if (!searchInput || document.activeElement === searchInput) return;
    searchInput.value = location.pathname === "/catalog" || location.pathname === "/search"
      ? new URLSearchParams(location.search).get("q") || "" : "";
  };

  applyTheme(getTheme());
  return () => {
    closeSheet(false);
    closeSearch();
    syncSearch();
  };
}
