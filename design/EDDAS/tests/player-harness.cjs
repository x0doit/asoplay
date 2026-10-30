/*
 * Общее для проверок плеера из app.js: класс Player в песочнице vm с подменённым окружением.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * До шага 6 фронтенд лежит в корне проекта (app.js, js/), поэтому пути идут от корня.
 */
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');
const source = readFileSync(join(__dirname, '..', '..', '..', 'app.js'), 'utf8');
const playerSource = source.slice(source.indexOf('// ---------- player ----------'), source.indexOf('// ---------- router ----------'));
let lastContext = null;

function harness(api = {}, globals = {}) {
  const nodes = new Map();
  const context = vm.createContext({ api, console: { warn() {} }, Set, Map, esc: s => s,
    state: { backendSources: ['oldyummy', 'yummy_anime'] },
    site: { isHidden: () => false, isAdult: () => false, adultConfirmed: () => true, confirmAdult() {}, sourceOrder: () => [] },
    ruEpisodeTitle: (name, num) => name || `Episode ${num}`,
    titleSimilarity: () => 1,
    $: selector => {
      if (!nodes.has(selector)) nodes.set(selector, { classList: { toggle() {} }, querySelector: () => null });
      return nodes.get(selector);
    },
    $$: () => [],
    ...globals,
  });
  vm.runInContext(playerSource + '\nglobalThis.Player = Player;\nglobalThis.playerVolume = playerVolume;', context);
  lastContext = context;
  const p = Object.create(context.Player.prototype);
  Object.assign(p, { sources: {}, episodes: [], currentEp: 0, _reqId: 1,
    _dubsCache: new Map(), malId: 38000, anime: { year: 2019 }, titles: ['Demon Slayer'],
    _bindPlayerControls() {}, _setupAutoNext() {}, _fetchEpisodeProgress: async () => {},
    renderEpisodes() {}, playEpisode() {}, _resumeIndex: () => 0, _syncCurrentEpisodeTitle() {},
  });
  return p;
}

/** Песочница последнего вызова harness: в ней playerVolume и остальное окружение плеера. */
const last = () => lastContext;

module.exports = { harness, last };
