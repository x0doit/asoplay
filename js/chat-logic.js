/*
 * Чат серии, чистая логика без DOM и без запросов: минута серии, обрезка полоски, слова и даты, окна списка,
 * порядок комментариев и расписание полосок поверх видео. Без импортов — её проверяют node-тесты
 * (design/EDDAS/tests/episode-chat.test.cjs).
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * Правило полосок (владелец, 02.11.2026): только корневые комментарии с минутой, пока включён чат и идёт видео.
 * Дорожек до 4 на компьютере и 2 на телефоне; полоска живёт 6 с времени видео (на паузе замирает). На минуту
 * серии — до 12 полосок (телефон — 6), и из комментариев ближе 10 с друг к другу — не больше 4 (телефон — 2): в обоих
 * случаях идут самые отмеченные «Нравится», при равных — написанный раньше. Свободной дорожки полоска ждёт до 3 с,
 * потом остаётся только в списке. После перемотки — с нового места, без залпа пропущенного, как в повторах чата
 * Twitch и YouTube: назад — всё дальше новой позиции снова ждёт своей минуты. Комментарий в самой точке
 * приземления — уже с нового места (landing): кнопка минуты ведёт ровно на него, Kodik шлёт время целыми секундами.
 * Текст — до 80 знаков (телефон — 40) по границе слова, обрезанный кончается жирным «…».
 */

export const STRIP = {
  life: 6,
  wait: 3,
  rateWindow: 10,
  seekJump: 2.5,
  landing: 2,
  desktop: { lanes: 4, perMinute: 12, perWindow: 4, chars: 80 },
  phone: { lanes: 2, perMinute: 6, perWindow: 2, chars: 40 },
};

// Список — окнами по минуте серии: первое — 5 минут до и после текущей, дальше — по 10 минут; в запросе до 100
export const WINDOW = { around: 300, step: 600, narrow: 60, page: 100, maxPages: 10, end: 86400 };

const MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
  "ноября", "декабря"];
const two = n => String(n).padStart(2, "0");

/** Минута серии: «04:35», с часами — «1:02:09». */
export function fmtMoment(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${two(m)}:${two(s % 60)}` : `${two(m)}:${two(s % 60)}`;
}

/** Текст полоски: до max знаков по границе слова. [текст, обрезан ли] — к обрезанному страница ставит жирное «…». */
export function cutStrip(text, max) {
  const flat = String(text || "").replace(/\s+/g, " ").trim();
  const chars = Array.from(flat);
  if (chars.length <= max) return [flat, false];
  let part = chars.slice(0, max).join("");
  const space = part.lastIndexOf(" ");
  if (space >= Math.floor(part.length * 0.6)) part = part.slice(0, space);
  return [part.replace(/[\s.,;:!?…\-–—]+$/u, ""), true];
}

/** Форма слова по числу: plural(2, ["ответ", "ответа", "ответов"]) — «ответа». */
export function plural(n, forms) {
  const a = Math.abs(n) % 100, b = a % 10;
  return forms[a > 10 && a < 20 ? 2 : b === 1 ? 0 : b >= 2 && b <= 4 ? 1 : 2];
}

/** Полная дата, как на сайте соцсети: «2 ноября 2026 в 15:44» (время зрителя). */
export function fullDate(unix) {
  const d = new Date(Number(unix) * 1000);
  if (Number.isNaN(d.getTime())) return "";
  return `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()} в ${two(d.getHours())}:${two(d.getMinutes())}`;
}

/** Имя автора: «Имя Фамилия», без них — адрес страницы. */
export function authorName(author) {
  const name = `${author?.first_name || ""} ${author?.last_name || ""}`.trim();
  return name || author?.alias || "Пользователь";
}

/** Спойлер ли комментарий на минуте moment (у ответа — минута корневого): серия до неё ещё не дошла, размытие
 * включено и человек не открыл его сам. Полоски на видео спойлерами не бывают — они идут, когда серия дошла. */
export function isSpoiler(moment, t, hide = true, revealed = false) {
  return hide && !revealed && moment !== null && moment !== undefined && moment !== "" && Number(moment) > t;
}

/** Корневой ли комментарий: у ответа thread — id корневого. */
export function isRoot(c) {
  return !c.thread || c.thread === c.id;
}

/** Порядок чата: по минуте, затем по id — как sort=moment у соцсети. */
export function byMoment(a, b) {
  return (a.moment - b.moment) || (a.id - b.id);
}

/** Индекс первого элемента с минутой больше t (там стоит отметка «Сейчас»). */
export function firstAfter(roots, t) {
  let lo = 0, hi = roots.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (roots[mid].moment > t) hi = mid; else lo = mid + 1;
  }
  return lo;
}

/** Вставить корневой в отсортированный список без повторов; true — вставлен. */
export function insertSorted(roots, c) {
  if (roots.some(x => x.id === c.id)) return false;
  let i = firstAfter(roots, c.moment);
  while (i > 0 && roots[i - 1].moment === c.moment && roots[i - 1].id > c.id) i -= 1;
  roots.splice(i, 0, c);
  return true;
}

/** Первое окно списка: 5 минут до и после минуты t. */
export function windowAround(t) {
  const at = Math.max(0, Math.floor(Number(t) || 0));
  return [Math.max(0, at - WINDOW.around), Math.min(WINDOW.end, at + WINDOW.around)];
}

/** Окно раньше загруженного: wide — 10 минут, иначе минута (для очень плотного куска). */
export function windowBefore(lo, wide = true) {
  return [Math.max(0, lo - (wide ? WINDOW.step : WINDOW.narrow)), lo - 1];
}

/** Окно позже загруженного: 10 минут. */
export function windowAfter(hi) {
  return [hi + 1, Math.min(WINDOW.end, hi + WINDOW.step)];
}

/** Сколько дорожек полосок влезает: от 64 px под верхом кадра до 64 px над низом, не больше cap. */
export function lanesFor(height, step, cap) {
  return Math.max(1, Math.min(cap, Math.floor((Number(height) - 128) / Math.max(1, step))));
}

/** Пауза после отказа (время в мс): 5 с, дальше вдвое до 60 с; Retry-After сервера важнее своей; успех сбрасывает. */
export class Backoff {
  /** first и max — первая и самая долгая пауза. */
  constructor(first = 5000, max = 60000) {
    this.first = first;
    this.max = max;
    this.wait = 0;
    this.until = 0;
  }

  /** Можно ли уже просить снова. */
  ready(now) {
    return now >= this.until;
  }

  /** Отказ: следующая пауза; retryAfter — секунды из Retry-After. Возвращает паузу в мс. */
  fail(now, retryAfter = 0) {
    this.wait = this.wait ? Math.min(this.max, this.wait * 2) : this.first;
    const pause = retryAfter > 0 ? retryAfter * 1000 : this.wait;
    this.until = now + pause;
    return pause;
  }

  /** Успех: пауз нет. */
  ok() {
    this.wait = 0;
    this.until = 0;
  }
}

/** Расписание полосок по времени видео: tick(t) отдаёт видимые полоски [{id, lane, comment}]. */
export class StripScheduler {
  /** Пустое расписание для компьютера. */
  constructor() {
    this.mode = STRIP.desktop;
    this.lanes = STRIP.desktop.lanes;
    this.roots = [];
    this._picked = new Map();
    this.reset(null);
  }

  /** Телефон или компьютер и сколько дорожек влезло в кадр. */
  configure({ phone = false, lanes = 4 } = {}) {
    this.mode = phone ? STRIP.phone : STRIP.desktop;
    this.lanes = Math.max(1, Math.min(this.mode.lanes, lanes));
    this.active = this.active.filter(s => s.lane < this.lanes);
    this._picked.clear();
  }

  /** Корневые комментарии чата по порядку минут; отбор по «Нравится» считается заново. */
  setRoots(roots) {
    this.roots = roots;
    this._picked.clear();
  }

  /** С нового места: видимых и ждущих нет, пропущенное не догоняется. last — где видео (по нему видны прыжки),
   * passed — докуда комментарии пройдены: точка приземления и STRIP.landing секунд до неё — ещё впереди. */
  reset(t) {
    this.active = [];
    this.queue = [];
    this.started = [];
    this.shown = new Set();
    this.last = t;
    this.passed = t === null ? null : t - STRIP.landing;
  }

  /** Живой комментарий у текущей минуты (свой или только что пришедший) — в очередь сразу. */
  push(c, t) {
    if (this.shown.has(c.id)) return;
    this.shown.add(c.id);
    this.queue.push({ c, deadline: t + STRIP.wait });
  }

  /** Отобранные в минуте m: от самых отмеченных «Нравится» — пока норма минуты не набрана и рядом (ближе 10 с)
   * отобрано меньше нормы на 10 с. */
  _pick(m) {
    let set = this._picked.get(m);
    if (set) return set;
    const lo = m * 60;
    const ranked = this.roots.slice(firstAfter(this.roots, lo - 1), firstAfter(this.roots, lo + 59))
      .sort((a, b) => ((b.likes_count || 0) - (a.likes_count || 0)) || ((a.date || 0) - (b.date || 0)) || (a.id - b.id));
    const chosen = [];
    for (const c of ranked) {
      if (chosen.length >= this.mode.perMinute) break;
      if (chosen.filter(x => Math.abs(x.moment - c.moment) < STRIP.rateWindow).length < this.mode.perWindow) chosen.push(c);
    }
    set = new Set(chosen.map(c => c.id));
    this._picked.set(m, set);
    return set;
  }

  /** Время видео t: снять отжившие, поставить в очередь пройденные, раздать свободные дорожки. */
  tick(t) {
    if (this.last === null || t < this.last - 0.5 || t - this.last > STRIP.seekJump) this.reset(t);
    if (t > this.passed) {
      for (let i = firstAfter(this.roots, Math.floor(this.passed)); i < this.roots.length; i += 1) {
        const c = this.roots[i];
        if (c.moment > t) break;
        if (c.moment <= this.passed || this.shown.has(c.id) || !this._pick(Math.floor(c.moment / 60)).has(c.id)) continue;
        this.shown.add(c.id);
        this.queue.push({ c, deadline: c.moment + STRIP.wait });
      }
      this.passed = t;
    }
    this.last = Math.max(this.last, t);
    this.active = this.active.filter(s => t - s.start < STRIP.life && t >= s.start - 0.5);
    this.queue = this.queue.filter(q => t <= q.deadline);
    this.started = this.started.filter(at => t - at < STRIP.rateWindow);
    this.queue.sort((a, b) => ((b.c.likes_count || 0) - (a.c.likes_count || 0)) || (a.c.moment - b.c.moment) || (a.c.id - b.c.id));
    while (this.queue.length && this.started.length < this.mode.perWindow) {
      const used = new Set(this.active.map(s => s.lane));
      let lane = 0;
      while (lane < this.lanes && used.has(lane)) lane += 1;
      if (lane >= this.lanes) break;
      const { c } = this.queue.shift();
      this.active.push({ id: c.id, lane, start: t, comment: c });
      this.started.push(t);
    }
    return this.active;
  }
}
