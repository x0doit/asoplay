/*
 * Источники плеера в app.js: ключи эпизодов, повторный поиск по просроченным ключам, гонки запросов,
 * запасной источник и честные сбои.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * До шага 6 фронтенд лежит в корне проекта (app.js, js/), поэтому пути идут от корня.
 *   node --test design/EDDAS/tests/*.test.cjs
 */
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { harness } = require('./player-harness.cjs');

const episode = (src, key = `${src}-key`) => ({ num: 1, name: 'Episode 1', key, _src: src });

test('late higher-priority discovery cannot separate the active source from rendered episodes', async () => {
  const calls = [];
  const p = harness({ backendSources: async (key, src) => { calls.push({ key, src }); return []; } });
  p.sources['backend:oldyummy'] = { episodes: [episode('oldyummy')] };
  assert.equal(p._adoptAnyReadySource(), true);
  p.sources['backend:yummy_anime'] = { episodes: [episode('yummy_anime')] };
  assert.equal(p._adoptAnyReadySource(), true);
  await p._loadEpisodeDubs(p.episodes[0], 1);
  assert.equal(p._activeSource, 'oldyummy');
  assert.deepEqual(calls, [{ key: 'oldyummy-key', src: 'oldyummy' }]);
});

test('episode identity retains its source and is used for dub requests', async () => {
  const calls = [];
  const p = harness({ backendSources: async (key, src) => { calls.push({ key, src }); return []; } });
  p._activeSource = 'yummy_anime';
  const ep = p._buildEpisodeList([episode('oldyummy')])[0];
  assert.equal(ep._src, 'oldyummy');
  await p._loadEpisodeDubs(ep, 1);
  assert.deepEqual(calls, [{ key: 'oldyummy-key', src: 'oldyummy' }]);
});

test('expired keys trigger real rediscovery and exactly one retry', async () => {
  const calls = [];
  const p = harness({
    backendSearch: async () => [{ key: 'search-key', title: 'Demon Slayer', mal_id: 38000 }],
    backendEpisodes: async () => [episode('oldyummy', 'fresh-key')],
    backendSources: async (key, src) => {
      calls.push({ key, src });
      if (key !== 'fresh-key') throw Object.assign(new Error('410'), { status: 410 });
      return [{ name: 'Dub', key: 'dub-key' }];
    },
  });
  p._activeSource = 'oldyummy';
  p.sources['backend:oldyummy'] = { episodes: [episode('oldyummy')] };
  p.episodes = p._buildEpisodeList(p.sources['backend:oldyummy'].episodes);
  const dubs = await p._loadEpisodeDubs(p.episodes[0], 1);
  assert.equal(dubs.length, 1);
  assert.deepEqual(calls, [{ key: 'oldyummy-key', src: 'oldyummy' }, { key: 'fresh-key', src: 'oldyummy' }]);
});

test('a refresh cannot overwrite a source selected while the request was pending', async () => {
  const p = harness();
  p._activeSource = 'oldyummy';
  p.episodes = [episode('oldyummy')];
  p.discoverBackend = async () => {
    p._activeSource = 'yummy_anime';
    p.episodes = [episode('yummy_anime')];
    p.sources['backend:oldyummy'] = { episodes: [episode('oldyummy', 'fresh-key')] };
    return true;
  };
  assert.equal(await p._refreshActiveSourceKeys(1), false);
  assert.equal(p.episodes[0].key, 'yummy_anime-key');
});

test('failed rediscovery cannot claim that cached stale keys are fresh', async () => {
  const p = harness({ backendSearch: async () => [] });
  p._activeSource = 'oldyummy';
  p.sources['backend:oldyummy'] = { episodes: [episode('oldyummy')] };
  p.episodes = p._buildEpisodeList(p.sources['backend:oldyummy'].episodes);
  assert.equal(await p._refreshActiveSourceKeys(1), false);
});

test('cancelled and destroyed players do not apply a pending refresh', async () => {
  for (const cancel of [p => p._reqId++, p => { p.destroyed = true; }]) {
    const p = harness();
    p._activeSource = 'oldyummy';
    p.episodes = [episode('oldyummy')];
    p.discoverBackend = async () => {
      cancel(p);
      p.sources['backend:oldyummy'] = { episodes: [episode('oldyummy', 'new-key')] };
      return true;
    };
    assert.equal(await p._refreshActiveSourceKeys(1), false);
    assert.equal(p.episodes[0].key, 'oldyummy-key');
  }
});

test('refresh does not silently select a different episode if the current one disappeared', async () => {
  const p = harness();
  p._activeSource = 'oldyummy';
  p.episodes = [episode('oldyummy')];
  p.discoverBackend = async () => {
    p.sources['backend:oldyummy'] = { episodes: [{ ...episode('oldyummy'), num: 2 }] };
    return true;
  };
  assert.equal(await p._refreshActiveSourceKeys(1), false);
  assert.equal(p.episodes[0].num, 1);
});

test('repeated 410 is bounded instead of looping', async () => {
  let calls = 0;
  const p = harness({ backendSources: async () => {
    calls++;
    throw Object.assign(new Error('410'), { status: 410 });
  } });
  p._activeSource = 'oldyummy';
  p.episodes = [episode('oldyummy')];
  p._refreshActiveSourceKeys = async () => true;
  await assert.rejects(p._loadEpisodeDubs(p.episodes[0], 1), e => e.status === 410);
  assert.equal(calls, 2);
});

test('fallback with a matching episode switches source and keys together', async () => {
  const p = harness({ backendSources: async () => [{ name: 'Dub', key: 'dub-key' }] });
  p._activeSource = 'yummy_anime';
  p.sources['backend:oldyummy'] = { episodes: [episode('oldyummy')] };
  const fallback = await p._tryAlternativeSourceForEpisode(1, 1);
  assert.equal(p._activeSource, 'oldyummy');
  assert.equal(fallback.ep._src, 'oldyummy');
  assert.equal(fallback.dubs[0]._src, 'oldyummy');
});

test('upstream failure is not treated as a successful empty dub list', async () => {
  const p = harness({ backendSources: async () => {
    throw Object.assign(new Error('502'), { status: 502 });
  } });
  p._activeSource = 'yummy_anime';
  p.sources['backend:oldyummy'] = { episodes: [episode('oldyummy')] };
  await assert.rejects(p._tryAlternativeSourceForEpisode(1, 1), e => e.status === 502);
});

test('genuine empty fallback stays empty', async () => {
  const p = harness({ backendSources: async () => [] });
  p._activeSource = 'yummy_anime';
  p.sources['backend:oldyummy'] = { episodes: [episode('oldyummy')] };
  assert.equal(await p._tryAlternativeSourceForEpisode(1, 1), null);
});

test('playback load failure offers retry and never claims that dubs do not exist', async () => {
  const p = harness();
  const messages = [];
  Object.assign(p, {
    episodes: [episode('oldyummy')], closeEpisodeUnwatchConfirm() {}, _armFallback() {},
    _rememberTabEpisode() {}, renderDubs() {},
    status: (text, options) => messages.push({ text, options }),
    _loadEpisodeDubs: async () => { throw new Error('502'); },
    _tryAlternativeSourceForEpisode: async () => null,
  });
  await Object.getPrototypeOf(p).playEpisode.call(p, 0);
  assert.ok(messages.at(-1).text.includes('Не удалось загрузить'));
  assert.equal(typeof messages.at(-1).options.retry, 'function');
  assert.ok(!messages.some(m => m.text === 'Озвучки не найдены'));
});

test('a late rejected request cannot replace the state of the next episode', async () => {
  const p = harness();
  const messages = [];
  Object.assign(p, {
    episodes: [episode('oldyummy')], closeEpisodeUnwatchConfirm() {}, _armFallback() {},
    _rememberTabEpisode() {}, renderDubs() {}, status: m => messages.push(m),
    _loadEpisodeDubs: async () => { p._reqId++; throw new Error('502'); },
  });
  await Object.getPrototypeOf(p).playEpisode.call(p, 0);
  assert.equal(messages.length, 1);
});
