/*
 * Окна перед плеером в app.js: клипы, скрытые панелью тайтлы, 18+, найденный после «не найдено»
 * источник.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * До шага 6 фронтенд лежит в корне проекта (app.js, js/), поэтому пути идут от корня.
 *   node --test design/EDDAS/tests/*.test.cjs
 */
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const vm = require('node:vm');
const source = readFileSync(join(__dirname, '..', '..', '..', 'app.js'), 'utf8');
const playerSource = source.slice(source.indexOf('class Player {'), source.indexOf('// ---------- router ----------'));

function harness(anime, nodes = {}, site = {}) {
  const context = vm.createContext({
    console: { warn() {} }, Set, Map, esc: s => s, uniq: list => [...new Set(list)],
    state: { backendAvailable: true, backendSources: ['oldyummy'] },
    site: { isHidden: () => false, isAdult: () => false, adultConfirmed: () => true, confirmAdult() {}, sourceOrder: () => [], ...site },
    NON_ANIME_TYPES: new Set(['Music', 'CM', 'PV']),
    ruEpisodeTitle: (name, num) => name || `Episode ${num}`,
    $: selector => nodes[selector] || null,
    $$: () => [],
  });
  vm.runInContext(playerSource + '\nglobalThis.Player = Player;', context);
  const p = Object.create(context.Player.prototype);
  const calls = { unavailable: [], discovered: 0 };
  Object.assign(p, {
    anime, titles: ['Title'], sources: {}, episodes: [], currentEp: 0, _reqId: 1, _dubsCache: new Map(),
    status() {}, renderUnavailable: (...args) => calls.unavailable.push(args),
    discoverWithFallback: async () => { calls.discovered += 1; },
  });
  return { p, calls };
}

const finished = { status: 'Finished Airing' };

test('only clip types are kept out of the player, not short real series', () => {
  for (const anime of [{ type: 'ONA', duration: '1 min per ep' }, { type: 'TV', duration: '3 min per ep' },
                       { type: 'Movie', duration: '20 min' }, {}]) {
    assert.equal(harness(anime).p._isNonAnime(), false, JSON.stringify(anime));
  }
  for (const type of ['Music', 'CM', 'PV']) assert.equal(harness({ type }).p._isNonAnime(), true, type);
});

test('a one-minute ONA goes straight to source discovery', async () => {
  const { p, calls } = harness({ ...finished, type: 'ONA', duration: '1 min per ep' });
  p._adoptAnyReadySource = () => true;
  await p.init();
  assert.equal(calls.discovered, 1);
  assert.equal(calls.unavailable.length, 0);
});

test('a clip gets an honest banner and late titles cannot search behind it', async () => {
  const { p, calls } = harness({ ...finished, type: 'PV' });
  await p.init();
  assert.equal(calls.unavailable.length, 1);
  assert.equal(calls.unavailable[0][0], 'Это клип или промо-ролик');
  assert.match(calls.unavailable[0][1], /сериалы, фильмы и спешлы/);
  await p.addTitles(['Late Russian title']);
  await p.rediscover();
  assert.equal(calls.discovered, 0);
  assert.deepEqual([...p.titles], ['Title']);
});

test('a title hidden from the AnimeSocial panel shows the takedown and never looks for video', async () => {
  const { p, calls } = harness({ ...finished, type: 'TV' }, {}, { isHidden: () => true });
  let takedown = 0;
  p.renderTakedown = () => { takedown += 1; };
  await p.init();
  assert.equal(takedown, 1);
  assert.equal(calls.discovered, 0);
});

test('an 18+ title looks for video only after «Мне есть 18»', async () => {
  let answer;
  const { p, calls } = harness({ ...finished, type: 'TV' }, {}, { isAdult: () => true, adultConfirmed: () => false });
  p._adoptAnyReadySource = () => true;
  p._adultGate = () => new Promise(resolve => { answer = resolve; });
  const started = p.init();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(calls.discovered, 0);
  answer(true);
  await started;
  assert.equal(calls.discovered, 1);
});

test('an 18+ title asks once per browser and never without an answer', async () => {
  const asked = harness({ ...finished, type: 'TV' }, {}, { isAdult: () => true, adultConfirmed: () => false });
  asked.p._adultGate = async () => false;
  await asked.p.init();
  assert.equal(asked.calls.discovered, 0);
  const confirmed = harness({ ...finished, type: 'TV' }, {}, { isAdult: () => true, adultConfirmed: () => true });
  confirmed.p._adoptAnyReadySource = () => true;
  confirmed.p._adultGate = () => { throw new Error('asked again'); };
  await confirmed.p.init();
  assert.equal(confirmed.calls.discovered, 1);
});

function overlay(html) {
  return { innerHTML: html, querySelector(selector) { return selector === '.unavail' && this.innerHTML.includes('unavail') ? {} : null; } };
}

function renderFound(overlayNode) {
  const { p } = harness(finished, { '#avPlayerOverlay': overlayNode, '#avEpCount': { textContent: '' } });
  Object.assign(p, {
    _activeSource: 'oldyummy', sources: { 'backend:oldyummy': { episodes: [{ num: 1, key: 'k' }] } },
    _bindPlayerControls() {}, _setupAutoNext() {}, _fetchEpisodeProgress: () => new Promise(() => {}),
  });
  return p._renderFoundSource();
}

test('a source found after the "not found" banner restores the status area', () => {
  const node = overlay('<div class="unavail">Видео пока не найдено</div>');
  assert.equal(renderFound(node), true);
  assert.match(node.innerHTML, /id="avPlayerStatus"/);
  assert.doesNotMatch(node.innerHTML, /unavail/);
});

test('a normal overlay is left untouched', () => {
  const html = '<div class="spinner"></div><div id="avPlayerStatus">Ищу источники…</div>';
  const node = overlay(html);
  assert.equal(renderFound(node), true);
  assert.equal(node.innerHTML, html);
});
