/*
 * «Продолжить с m:ss» у «Эпизод N»: вошедший зритель продолжает серию с места, где остановился. Кнопка — только в
 * серии, которую страница открыла как «где остановился», если позиция больше 30 с и серия не досмотрена, и только у
 * плеера, который умеет перематывать (наш кадр, Kodik). Нажатие — перемотка и пуск (их делает плеер, app.js); сама
 * страница ничего не перематывает. Кнопка уходит, когда её нажали, видео дошло до этой точки, зритель перемотал сам
 * или сменил серию; смена озвучки в той же серии её оставляет. Без импортов — логику проверяют node-тесты
 * (design/EDDAS/tests/resume.test.cjs).
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 */

/** Секунда «продолжить» по позиции серии ({ last, seconds, duration }: last — где остановился, seconds — дальняя
 *  точка): null, если она не больше 30 с или серия досмотрена (92 % или последние 90 с по дальней точке). */
export function resumePoint(entry) {
  if (!entry) return null;
  const far = Number(entry.seconds) || 0;
  const duration = Number(entry.duration) || 0;
  const last = Math.floor(Number(entry.last ?? far) || 0);
  if (last <= 30) return null;
  if (duration > 0 && (far >= duration * 0.92 || far >= Math.max(0, duration - 90))) return null;
  return last;
}

/** Время для кнопки: m:ss, от часа — h:mm:ss. */
export function clock(seconds) {
  const s = Math.max(0, Math.floor(Number(seconds) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), rest = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${rest}` : `${m}:${rest}`;
}

/** Время плеера прыгнуло так, как видео само не идёт: перемотка. prev — { t, at } прошлого сигнала (at — мс). */
export function jumped(prev, t, now) {
  if (!prev) return false;
  const passed = Math.max(0, (now - prev.at) / 1000);
  return t > prev.t + passed + 2 || t < prev.t - 2;
}

const span = (cls, text) => Object.assign(document.createElement("span"), { className: cls, textContent: text });

/** Кнопка в шапке плеера (#avEpInfo); act(seconds) — перемотать и пустить. */
export class ResumeButton {
  constructor(act) {
    this.act = act;
    this.offer = null;  // { ep, at }
    this.can = false;
    this.prev = null;
  }

  /** Предложить продолжить серию ep с секунды at; null — не предлагать. */
  set(ep, at) {
    this.offer = at ? { ep, at } : null;
    this.prev = null;
    this.render();
  }

  /** Плеер умеет перематывать — кнопку можно показать. Новый плеер считает время заново. */
  setCan(can) {
    this.can = !!can;
    this.prev = null;
    this.render();
  }

  /** Открыта серия ep (шапка перерисована): другая серия — предложение уходит. */
  episode(ep) {
    if (this.offer && this.offer.ep !== ep) this.offer = null;
    this.render();
  }

  /** Время плеера: дошло до точки или зритель перемотал сам — кнопка уходит. */
  time(t, now = Date.now()) {
    if (!this.offer) return;
    if (t >= this.offer.at - 1 || jumped(this.prev, t, now)) {
      this.offer = null;
      this.render();
      return;
    }
    this.prev = { t, at: now };
  }

  render() {
    const head = document.getElementById("avEpInfo");
    let button = head?.querySelector(".sp-resume");
    if (!head || !this.offer || !this.can) {
      button?.remove();
      return;
    }
    if (!button) {
      button = document.createElement("button");
      button.type = "button";
      button.className = "ed-btn ed-btn--secondary ed-btn--sm sp-resume";
      button.addEventListener("click", () => {
        const at = this.offer?.at;
        this.offer = null;
        this.render();
        if (at) this.act(at);
      });
      head.append(button);
    }
    const time = clock(this.offer.at);
    const icon = Object.assign(document.createElement("i"), { className: "fas fa-play" });
    icon.setAttribute("aria-hidden", "true");
    button.setAttribute("aria-label", `Продолжить с ${time}`);
    button.replaceChildren(icon, span("sp-resume__full", `Продолжить с ${time}`), span("sp-resume__short", time));
  }

  destroy() {
    this.offer = null;
    this.render();
  }
}
