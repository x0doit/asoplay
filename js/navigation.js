export function isCompactNavigation() {
  const trigger = document.querySelector("#mobileMenuBtn");
  return !!trigger && getComputedStyle(trigger).display !== "none";
}

export function setupNavigation() {
  const root = document.documentElement;
  const menu = document.querySelector("#topMenu");
  const trigger = document.querySelector("#mobileMenuBtn");
  const dismiss = document.querySelector("#mobileMenuClose");
  const overlay = document.querySelector("#darkOverlay");
  let compact = false;
  let open = false;
  let previousOverflow = "";

  const close = (restoreFocus = true) => {
    if (!open) return;
    open = false;
    menu.classList.remove("show");
    overlay.classList.remove("show");
    trigger.setAttribute("aria-expanded", "false");
    document.body.style.overflow = previousOverflow;
    if (restoreFocus && isCompactNavigation()) trigger.focus({ preventScroll: true });
    menu.inert = compact;
  };
  const syncMode = () => {
    const next = isCompactNavigation();
    if (next !== compact) close(false);
    compact = next;
    root.classList.toggle("is-mobile-nav", compact);
    menu.inert = compact && !open;
    document.dispatchEvent(new Event("av:navigation-mode"));
  };
  trigger.addEventListener("click", () => {
    if (open) { close(); return; }
    if (!compact) return;
    open = true;
    previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    menu.inert = false;
    menu.scrollTop = 0;
    menu.classList.add("show");
    overlay.classList.add("show");
    trigger.setAttribute("aria-expanded", "true");
    dismiss.focus({ preventScroll: true });
  });
  dismiss.addEventListener("click", () => close());
  overlay.addEventListener("click", () => close());
  document.addEventListener("keydown", (event) => {
    if (!open) return;
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    } else if (event.key === "Tab") {
      const items = [...menu.querySelectorAll('a[href], button:not(:disabled)')]
        .filter(el => el.getClientRects().length);
      const first = items[0];
      const last = items[items.length - 1];
      if (!menu.contains(document.activeElement) ||
          (event.shiftKey && document.activeElement === first) ||
          (!event.shiftKey && document.activeElement === last)) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus();
      }
    }
  });
  window.addEventListener("resize", () => {
    if (isCompactNavigation() !== compact) syncMode();
  }, { passive: true });
  syncMode();
  return close;
}
