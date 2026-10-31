/*
 * Трейлер на странице тайтла (js/trailer.js): какой id YouTube годится для плеера.
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

const source = readFileSync(join(__dirname, '..', '..', '..', 'js', 'trailer.js'), 'utf8');
const modulePromise = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('accepts a valid YouTube trailer ID, including hyphens and underscores', async () => {
  const { getTrailerId } = await modulePromise;
  assert.equal(getTrailerId({ youtube_id: 'JtqIas3bYhg' }), 'JtqIas3bYhg');
  assert.equal(getTrailerId({ youtube_id: 'Ab_123-cD45' }), 'Ab_123-cD45');
});

test('trims surrounding whitespace without changing the ID', async () => {
  const { getTrailerId } = await modulePromise;
  assert.equal(getTrailerId({ youtube_id: ' JtqIas3bYhg\n' }), 'JtqIas3bYhg');
});

test('missing trailers and placeholder IDs do not create a player', async () => {
  const { getTrailerId } = await modulePromise;
  for (const trailer of [undefined, null, {}, { youtube_id: null }, { youtube_id: '' }, { youtube_id: 'null' }]) {
    assert.equal(getTrailerId(trailer), '');
  }
});

test('rejects URLs, markup, query parameters and invalid ID lengths', async () => {
  const { getTrailerId } = await modulePromise;
  for (const youtube_id of ['https://youtu.be/JtqIas3bYhg', '<script>bad</script>', 'JtqIas3bYhg?autoplay=1', 'short', 'JtqIas3bYhg-extra', 'Ab 123-cD45']) {
    assert.equal(getTrailerId({ youtube_id }), '');
  }
});

test('absent or invalid trailers need no DOM and have safe repeatable cleanup', async () => {
  const { bindTrailer } = await modulePromise;
  for (const id of ['', undefined, 'invalid']) {
    const cleanup = bindTrailer(null, id, () => { throw new Error('Title must not be read'); });
    cleanup();
    cleanup();
  }
});
