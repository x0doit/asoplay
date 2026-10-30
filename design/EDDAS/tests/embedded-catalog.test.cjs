/*
 * Каталог в app.js (api): только встроенный Jikan, фильтры и страницы как у MAL, сбой сети —
 * не пустой ответ.
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
const apiSource = source.slice(source.indexOf('async function fetchJson'), source.indexOf('// ---------- cards + grid'));
const json = value => JSON.parse(JSON.stringify(value));
const response = (value, status = 200) => ({ ok: status < 400, status,
  json: async () => value, text: async () => JSON.stringify(value) });
function harness(responder) {
  const calls = [];
  const context = vm.createContext({ URLSearchParams, AbortController, setTimeout, clearTimeout,
    JIKAN: () => '/jikan', ANILIST: () => '/anilist', BACKEND: '',
    fetch: async (url, options) => { calls.push(url); return responder(url, options); },
  });
  vm.runInContext(apiSource + '\nglobalThis.api = api;', context);
  return { api: context.api, calls };
}
test('filters and pagination keep the original MAL contract', async () => {
  const data = { data: [{ mal_id: 34382 }], pagination: { has_next_page: true, last_visible_page: 2 } };
  const h = harness(() => response(data));
  assert.deepEqual(json(await h.api.catalog({ genres: '26,46', type: 'tv', page: 2 })), data);
  const p = new URLSearchParams(h.calls[0].split('?')[1]);
  assert.equal(p.get('genres'), '26,46');
  assert.equal(p.get('page'), '2');
  assert.equal(p.get('type'), 'tv');
  assert.equal(p.get('sfw'), 'true');
});
test('catalog errors never switch to an unrelated taxonomy', async () => {
  for (const status of [400, 403, 404, 429, 500, 503]) {
    const h = harness(() => response({}, status));
    await assert.rejects(h.api.catalog({ genres: 26 }), e => e.status === status);
    assert.equal(h.calls.length, 1);
  }
});
test('network failure is not converted to empty results', async () => {
  const h = harness(() => { throw new TypeError('Failed to fetch'); });
  await assert.rejects(h.api.search('Naruto'));
  assert.equal(h.calls.length, 1);
});
test('a real empty search stays empty', async () => {
  const h = harness(() => response({ data: [], pagination: { has_next_page: false } }));
  assert.deepEqual(json(await h.api.search('doesnotexist')), []);
});
test('all primary entry points use the embedded API', async () => {
  const h = harness(() => response({ data: [], pagination: {} }));
  await h.api.top(); await h.api.season(); await h.api.genres();
  await h.api.movies(); await h.api.topPaged(2); await h.api.trending();
  await h.api.details(1535); await h.api.characters(1535); await h.api.recommendations(1535);
  await h.api.random();
  assert.equal(h.calls.length, 10);
  assert.ok(h.calls.every(url => url.startsWith('/jikan/')));
  assert.ok(h.calls.some(url => /seasons\/\d{4}\/(winter|spring|summer|fall)/.test(url)));
});
test('genre list comes from MAL, removing explicit genres only', async () => {
  const h = harness(() => response({ data: [
    { mal_id: 26, name: 'Girls Love' }, { mal_id: 46, name: 'Award Winning' }, { mal_id: 12, name: 'Hentai' }
  ] }));
  assert.deepEqual(json(await h.api.genres()).map(x => x.mal_id), [26, 46]);
});
test('frontend has no direct public Jikan URL or catalog fallback', () => {
  assert.ok(!source.includes('api.jikan.moe'));
  assert.ok(!source.includes('anilistCatalog'));
});
