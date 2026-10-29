/*
 * Play and X icons from Lucide (https://lucide.dev).
 * ISC License
 * Copyright (c) 2026 Lucide Icons and Contributors
 * Permission to use, copy, modify, and/or distribute this software for any
 * purpose with or without fee is hereby granted, provided that the above
 * copyright notice and this permission notice appear in all copies.
 * THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
 * WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
 * MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
 * ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
 * WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
 * ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
 * OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
 *
 * X is derived from Feather, under the MIT License:
 * Copyright (c) 2013-present Cole Bemis
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */

export const trailerPlayIcon = `<svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 5a2 2 0 0 1 3.008-1.728l11.997 6.998a2 2 0 0 1 .003 3.458l-12 7A2 2 0 0 1 5 19z"/></svg>`;
const closeIcon = `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>`;

export function getTrailerId(trailer) {
  const id = String(trailer?.youtube_id || "").trim();
  return /^[a-zA-Z0-9_-]{11}$/.test(id) ? id : "";
}

export function bindTrailer(root, videoId, getTitle) {
  const id = getTrailerId({ youtube_id: videoId });
  if (!id) return () => {};
  const triggers = [...root.querySelectorAll("[data-open-trailer]")];
  const preview = root.querySelector(".cinema-trailer img");
  const hideBrokenPreview = () => { preview.hidden = true; };
  preview?.addEventListener("error", hideBrokenPreview);
  if (preview?.complete && !preview.naturalWidth) hideBrokenPreview();
  let dialog = null;
  let opener = null;
  let previousOverflow = "";

  const close = (restoreFocus = true) => {
    if (!dialog) return;
    // Remove the iframe immediately so closing or SPA navigation stops playback.
    dialog.querySelector("iframe")?.remove();
    dialog.close();
    dialog.remove();
    dialog = null;
    document.documentElement.style.overflow = previousOverflow;
    if (restoreFocus && opener?.isConnected) opener.focus({ preventScroll: true });
    opener = null;
  };

  const open = event => {
    if (dialog) return;
    opener = event.currentTarget;
    dialog = document.createElement("dialog");
    if (typeof dialog.showModal !== "function") {
      dialog = null;
      window.open(`https://www.youtube.com/watch?v=${id}`, "_blank", "noopener,noreferrer");
      return;
    }
    dialog.className = "trailer-dialog";
    dialog.setAttribute("aria-labelledby", "avTrailerTitle");
    dialog.innerHTML = `
      <div class="trailer-dialog-head">
        <h2 id="avTrailerTitle"></h2>
        <a class="trailer-external" href="https://www.youtube.com/watch?v=${id}" target="_blank" rel="noopener noreferrer">Открыть на YouTube <i class="fas fa-arrow-up-right-from-square" aria-hidden="true"></i></a>
        <button class="trailer-close" type="button" aria-label="Закрыть трейлер" title="Закрыть трейлер" autofocus>${closeIcon}</button>
      </div>
      <div class="trailer-video"></div>`;
    dialog.querySelector("h2").textContent = `Трейлер: ${getTitle()}`;
    const frame = document.createElement("iframe");
    frame.title = `Трейлер: ${getTitle()}`;
    frame.allow = "autoplay; encrypted-media; gyroscope; picture-in-picture; fullscreen";
    frame.referrerPolicy = "strict-origin-when-cross-origin";
    frame.src = `https://www.youtube-nocookie.com/embed/${id}?autoplay=1&rel=0`;
    dialog.querySelector(".trailer-video").appendChild(frame);
    dialog.querySelector(".trailer-close").addEventListener("click", () => close());
    dialog.addEventListener("cancel", event => { event.preventDefault(); close(); });
    const outside = event => {
      const r = dialog.getBoundingClientRect();
      return event.clientX < r.left || event.clientX > r.right
        || event.clientY < r.top || event.clientY > r.bottom;
    };
    let backdropPress = false;
    dialog.addEventListener("pointerdown", event => { backdropPress = event.target === dialog && outside(event); });
    dialog.addEventListener("click", event => {
      if (backdropPress && event.target === dialog && outside(event)) close();
      backdropPress = false;
    });
    document.body.appendChild(dialog);
    previousOverflow = document.documentElement.style.overflow;
    dialog.showModal();
    document.documentElement.style.overflow = "hidden";
  };

  triggers.forEach(button => button.addEventListener("click", open));
  return () => {
    close(false);
    preview?.removeEventListener("error", hideBrokenPreview);
    triggers.forEach(button => button.removeEventListener("click", open));
  };
}
