// AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
// All rights reserved. See /COPYRIGHT for full terms.
//
// Имена озвучек: приведение для сравнения, любимая озвучка среди озвучек серии, её собственный плеер и список всех
// озвучек для настроек (GET /src/dub-names).
//
// Любимая — имя, выбранное в настройках (/account/settings, favorite_dub). Среди озвучек серии она ищется так: сначала
// то же имя после приведения; по началу слов («AniLibria» и «AniLibria.TV») — только если так подходит ровно одна
// озвучка, иначе «Jam» нашёл бы и «Jam Club». Субтитры с тем же именем не подходят. У AniLibria, Dream Cast и Studio
// Band свой плеер: плеер ищет его первым (sourceFirst). Имена из списка — чужой текст: на страницу только textContent.

const PREFIXES = ["озвучка ", "субтитры ", "оригинал ", "original ", "voice ", "sub ", "дубляж "];
// Команды со своим источником: слова имени после приведения → id источника
const OWN_SOURCES = [
  [["anilibria"], "anilibria"],
  [["dream", "cast"], "dreamcast"], [["dreamcast"], "dreamcast"],
  [["studio", "band"], "sameband"], [["sameband"], "sameband"],
];

// Имя для сравнения: без пометок в скобках, «Озвучка»/«Субтитры» в начале, языка и знаков. Из него же ключ
// озвучки тайтла (Player._dubKey) — менять только вместе с сохранёнными выборами.
export function normDubName(name) {
  let s = (name || "").toLowerCase().trim();
  s = s.replace(/[\(\[]([^)\]]*)[\)\]]/g, " ");
  for (const p of PREFIXES) if (s.startsWith(p)) s = s.slice(p.length);
  s = s.replace(/\b(ru|en|jp|ja|рус|русский|англ\w*|яп\w*|дубляж|дубл|sub|subs|ost)\b/g, " ");
  s = s.replace(/[^\w\sа-яё]+/gi, " ");
  s = s.replace(/\s+/g, " ").trim();
  return s;
}

export const isSubtitles = label => /субтитры/i.test(label || "");

const words = s => (s ? s.split(" ") : []);
// Слова short — начало слов long
const leads = (long, short) => short.length > 0 && short.length <= long.length && short.every((w, i) => long[i] === w);

// Индекс любимой среди озвучек серии ({label}); нет или подходит не одна — -1.
export function favoriteIndex(dubs, favorite) {
  const want = normDubName(favorite);
  if (!want) return -1;
  const voices = dubs.map((d, i) => ({ i, norm: normDubName(d?.label), sub: isSubtitles(d?.label) }))
    .filter(v => !v.sub && v.norm);
  const exact = voices.find(v => v.norm === want);
  if (exact) return exact.i;
  const wanted = words(want);
  const close = voices.filter(v => leads(words(v.norm), wanted) || leads(wanted, words(v.norm)));
  return close.length === 1 ? close[0].i : -1;
}

// Свой источник любимой озвучки; нет — пусто.
export function ownSource(favorite) {
  const name = words(normDubName(favorite));
  return OWN_SOURCES.find(([team]) => leads(name, team))?.[1] || "";
}

// Порядок поиска источников со своим плеером любимой первым — если он в порядке есть.
export function sourceFirst(order, source) {
  return source && order.includes(source) ? [source, ...order.filter(s => s !== source)] : order;
}

// Список всех озвучек для выбора любимой: один запрос на вкладку, сбой — следующий вызов спросит снова.
let namesRequest = null;
export function loadNames(fetchJson) {
  if (!namesRequest) {
    namesRequest = fetchJson("/src/dub-names")
      .then(list => (Array.isArray(list) ? list.filter(n => typeof n === "string" && n) : []))
      .catch(err => { namesRequest = null; throw err; });
  }
  return namesRequest;
}
