// AsoPlay — proprietary. (c) Chepela Daniel Maximovich (x0doit, https://crazydev.pro/).
// All rights reserved. See /COPYRIGHT for full terms.
//
// «Любимая озвучка» в настройках («Общие» → «Просмотр»): строка с выбранной озвучкой и выбор с поиском. Выбор —
// диалог EDDAS из account.js: на широком экране по центру, на телефоне нижняя шторка. В списке — все озвучки, что
// встречались в плеере (dub-names.js, GET /src/dub-names), сверху — те, что человек выбирал на тайтлах. Имена —
// чужой текст: на страницу только через textContent. Сохраняет account.js (PUT /account/settings, favorite_dub).

import { loadNames, normDubName } from "./dub-names.js?v=20261110-favdub1";

const SHOWN = 200;  // столько озвучек рисуется сразу; остальные находит поиск

export function favDubRow() {
  return `
    <div class="settings-option fav-dub">
      <span class="ed-field__label" id="avFavDubLabel">Любимая озвучка</span>
      <div class="fav-dub__row">
        <button type="button" class="ed-btn ed-btn--secondary fav-dub__pick" id="avFavDubPick" aria-haspopup="dialog" aria-labelledby="avFavDubLabel avFavDubValue"><span class="fav-dub__value" id="avFavDubValue"></span><i class="fas fa-chevron-down" aria-hidden="true"></i></button>
        <button type="button" class="ed-btn ed-btn--ghost" id="avFavDubReset" hidden>Сбросить</button>
      </div>
      <p class="settings-help">Плеер включит её сам, если она есть у серии. AniLibria, Dream Cast и Studio Band он сначала ищет в их собственных плеерах. Если для тайтла вы выбрали другую озвучку, останется ваш выбор.</p>
      <div class="ed-field__note is-error" id="avFavDubNote" role="alert"></div>
    </div>`;
}

// deps: get() — любимая сейчас ("" — нет); set(name) — сохранить, сбой — исключение и прежнее значение; chosen() —
// ключи озвучек, выбранных на тайтлах; fetchJson и openDialog — из account.js.
export function bindFavDub(root, deps) {
  if (!root) return;
  const value = root.querySelector("#avFavDubValue");
  const reset = root.querySelector("#avFavDubReset");
  const note = root.querySelector("#avFavDubNote");
  const paint = () => {
    const current = deps.get();
    value.textContent = current || "Не выбрана";
    reset.hidden = !current;
  };
  const save = async (name) => {
    note.textContent = "";
    try {
      await deps.set(name);
    } catch (_) {
      note.textContent = "Не удалось сохранить. Попробуйте ещё раз.";
    }
    paint();
  };
  paint();
  root.querySelector("#avFavDubPick").addEventListener("click", () => openPicker(deps, save));
  reset.addEventListener("click", () => save(""));
}

function openPicker(deps, save) {
  const dialog = deps.openDialog("fav-dub-overlay", `
    <button type="button" class="ed-iconbtn av-dialog__close" aria-label="Закрыть" title="Закрыть" data-dialog-close><i class="fas fa-xmark" aria-hidden="true"></i></button>
    <h2 class="ed-dialog__title" id="avFavDubTitle">Любимая озвучка</h2>
    <div class="ed-search fav-dub__search" role="search">
      <i class="ed-search__icon fas fa-search" aria-hidden="true"></i>
      <input class="ed-search__input" type="search" placeholder="Название озвучки" aria-label="Поиск озвучки" autocomplete="off" />
    </div>
    <div class="fav-dub__list" role="listbox" aria-labelledby="avFavDubTitle"></div>`, { label: "avFavDubTitle", scroll: ".fav-dub__list" });
  const list = dialog.el.querySelector(".fav-dub__list");
  const input = dialog.el.querySelector(".ed-search__input");
  // На телефоне клавиатура сразу закрыла бы шторку — поиск в фокусе только у мыши
  if (window.matchMedia("(pointer: fine)").matches) input.focus({ preventScroll: true });
  let entries = null;
  const choose = (name) => { dialog.close(); save(name); };
  const draw = () => list.replaceChildren(...(entries ? content(entries, input.value, deps, choose) : [stateBox("Загружаем список…")]));
  const load = () => {
    entries = null;
    draw();
    loadNames(deps.fetchJson)
      .then(names => { entries = names.map(name => ({ name, norm: normDubName(name), lower: name.toLowerCase() })); draw(); })
      .catch(() => list.replaceChildren(stateBox("Не удалось загрузить список", "", { label: "Повторить", onClick: load })));
  };
  input.addEventListener("input", () => { if (entries) draw(); });
  load();
}

// Что показать в списке для запроса: без запроса — «Вы выбирали» и «Все озвучки», с запросом — найденные.
function content(entries, query, deps, choose) {
  const current = normDubName(deps.get());
  const q = query.trim().toLowerCase();
  const qn = normDubName(query);
  const found = q ? entries.filter(e => e.lower.includes(q) || (qn && e.norm.includes(qn))) : entries;
  if (!found.length) {
    return [stateBox("Ничего не нашлось", "Такой озвучки пока нет. Она появится в списке, когда встретится в плеере.")];
  }
  const out = [];
  let rest = found;
  if (!q) {
    const mine = new Set(deps.chosen());
    const picked = found.filter(e => mine.has(e.norm));
    if (picked.length) out.push(group("Вы выбирали", picked, current, choose));
    rest = found.filter(e => !mine.has(e.norm));
    if (rest.length) out.push(group("Все озвучки", rest.slice(0, SHOWN), current, choose));
  } else {
    out.push(group("", rest.slice(0, SHOWN), current, choose));
  }
  if (rest.length > SHOWN) {
    const more = document.createElement("p");
    more.className = "fav-dub__more";
    more.textContent = `Показаны первые ${SHOWN} из ${rest.length}. Уточните поиск.`;
    out.push(more);
  }
  return out;
}

function group(title, items, current, choose) {
  const box = document.createElement("div");
  box.className = "fav-dub__group";
  box.setAttribute("role", "group");
  if (title) {
    const head = document.createElement("div");
    head.className = "fav-dub__group-title";
    head.textContent = title;
    box.setAttribute("aria-label", title);
    box.append(head);
  }
  for (const { name, norm } of items) {
    const active = !!current && norm === current;
    const item = document.createElement("button");
    item.type = "button";
    item.className = "ed-menu__item fav-dub__item" + (active ? " is-active" : "");
    item.setAttribute("role", "option");
    item.setAttribute("aria-selected", String(active));
    const text = document.createElement("span");
    text.className = "fav-dub__name";
    text.textContent = name;
    item.append(text);
    if (active) {
      const icon = document.createElement("i");
      icon.className = "fas fa-check";
      icon.setAttribute("aria-hidden", "true");
      item.append(icon);
    }
    item.addEventListener("click", () => choose(name));
    box.append(item);
  }
  return box;
}

// Пустое состояние списка: загрузка, ничего не нашлось, ошибка с повтором.
function stateBox(title, text = "", action = null) {
  const box = document.createElement("div");
  box.className = "ed-empty fav-dub__empty";
  const head = document.createElement("div");
  head.className = "ed-empty__title";
  head.textContent = title;
  box.append(head);
  if (text) {
    const p = document.createElement("div");
    p.className = "ed-empty__text";
    p.textContent = text;
    box.append(p);
  }
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "ed-btn ed-btn--secondary ed-empty__action";
    button.textContent = action.label;
    button.addEventListener("click", action.onClick);
    box.append(button);
  }
  return box;
}
