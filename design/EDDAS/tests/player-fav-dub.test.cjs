/*
 * Любимая озвучка в плеере app.js и js/dub-names.js: место в порядке выбора, совпадение имени, свой плеер первым.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * До шага 6 фронтенд лежит в корне проекта (app.js, js/), поэтому пути идут от корня.
 *   node --test design/EDDAS/tests/*.test.cjs
 */
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { browserModule, harness } = require('./player-harness.cjs');

const names = browserModule('js/dub-names.js');
const dub = label => ({ label, iframeUrl: 'https://site.yummyani.me/iframecvh.html?id=1' });
const with_ = (saved, favorite, extra = {}) => ({ prefs: { getDub: () => saved, getFavoriteDub: () => favorite }, ...extra });

test('the favourite plays when the title has no choice of its own', () => {
  const p = harness({}, with_(null, 'AniLibria'));
  assert.equal(p._pickDubIndex([dub('Озвучка SHIZA Project'), dub('Озвучка AniLibria.TV')]), 1);
  assert.equal(p._sessionDubKey, 'anilibria tv');  // следующие серии вкладки идут за ней
});

test('a dub chosen by hand on the title beats the favourite; when absent the favourite comes next', () => {
  const dubs = [dub('Озвучка SHIZA Project'), dub('Озвучка AniDub'), dub('Озвучка AniLibria')];
  assert.equal(harness({}, with_('anidub', 'AniLibria'))._pickDubIndex(dubs), 1);
  assert.equal(harness({}, with_('jam', 'AniLibria'))._pickDubIndex(dubs), 2);
});

test('without a favourite in the episode the first voice-over plays, never its subtitles', () => {
  assert.equal(harness({}, with_(null, 'AniLibria'))._pickDubIndex([dub('Субтитры AniLibria'), dub('Озвучка AniDub')]), 1);
  assert.equal(harness({}, with_(null, ''))._pickDubIndex([dub('Субтитры Crunchyroll'), dub('Озвучка JAM')]), 1);
});

test('an exact name wins; the start of words counts only when one dub fits', () => {
  assert.equal(names.favoriteIndex([dub('Озвучка Jam Club'), dub('Озвучка JAM')], 'JAM'), 1);
  assert.equal(names.favoriteIndex([dub('Озвучка AniDub'), dub('Озвучка Jam Club')], 'Jam'), 1);
  assert.equal(names.favoriteIndex([dub('Озвучка Jam Club'), dub('Озвучка Jam Studio')], 'Jam'), -1);
  assert.equal(names.favoriteIndex([dub('Озвучка AniLibria')], 'AniLibria.TV'), 0);
  assert.equal(names.favoriteIndex([dub('Озвучка AniLibria')], ''), -1);
  assert.equal(names.favoriteIndex([dub('Озвучка Anilibria Fan')], 'Ani'), -1);  // слово целиком, не буквы
});

test('the name for comparison did not change: titles keep the dubs saved before', () => {
  assert.equal(names.normDubName('Озвучка AniLibria.TV (RU)'), 'anilibria tv');
  assert.equal(names.normDubName('Субтитры [ТВ] Crunchyroll'), 'crunchyroll');
  assert.equal(names.normDubName('Дубляж SHIZA Project'), 'shiza project');
  assert.equal(harness()._dubKey(dub('Субтитры AniLibria')), 'sub:anilibria');
});

test('teams with a player of their own map to their sources', () => {
  assert.equal(names.ownSource('AniLibria.TV'), 'anilibria');
  assert.equal(names.ownSource('Dream Cast'), 'dreamcast');
  assert.equal(names.ownSource('Studio Band'), 'sameband');
  assert.equal(names.ownSource('SHIZA Project'), '');
  assert.equal(names.ownSource(''), '');
});

const order = ['yummy_anime', 'oldyummy', 'anilibria', 'sameband'];
const site = { isHidden: () => false, isAdult: () => false, sourceOrder: () => order };

test('the favourite own player is searched first only while nothing was chosen', () => {
  assert.deepEqual([...harness({}, with_(null, 'AniLibria', { site }))._backendSourcePriority()],
    ['anilibria', 'yummy_anime', 'oldyummy', 'sameband']);
  assert.deepEqual([...harness({}, with_('anidub', 'AniLibria', { site }))._backendSourcePriority()], order);
  assert.deepEqual([...harness({}, with_(null, 'SHIZA Project', { site }))._backendSourcePriority()], order);
  const tab = harness({}, with_(null, 'AniLibria', { site }));
  tab._sessionDubKey = 'shiza project';
  assert.deepEqual([...tab._backendSourcePriority()], order);
  assert.deepEqual([...harness({}, with_(null, 'Dream Cast', { site }))._backendSourcePriority()], order);  // нет в порядке
});

async function discovery(favorite, found) {
  const state = { backendSources: order };
  const p = harness({}, with_(null, favorite, { site, state, setTimeout }));
  const calls = [];
  p._renderFoundSource = () => true;
  p.discoverBackend = async src => {
    calls.push(src);
    if (found.includes(src)) p.sources[`backend:${src}`] = { episodes: [{ num: 1 }] };
  };
  await p.discoverWithFallback();
  return { calls, active: p._activeSource };
}

test('the own player goes alone before the others, and the search goes on without the title there', async () => {
  assert.deepEqual(await discovery('AniLibria', ['anilibria', 'yummy_anime']), { calls: ['anilibria'], active: 'anilibria' });
  assert.deepEqual(await discovery('AniLibria', ['yummy_anime']), { calls: ['anilibria', 'yummy_anime', 'oldyummy'], active: 'yummy_anime' });
  assert.deepEqual(await discovery('', ['yummy_anime']), { calls: ['yummy_anime', 'oldyummy'], active: 'yummy_anime' });
});
