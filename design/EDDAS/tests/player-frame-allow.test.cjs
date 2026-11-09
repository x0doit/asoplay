/*
 * Кадр плеера на странице: разрешения iframe (app.js, hardenFrame). Документ плеера за прокси лежит в CSP-песочнице
 * без allow-same-origin (с 1db5224), его адрес непрозрачный: разрешение без списка пускает только адрес из src, и
 * внутри плеера пропадал полный экран. Поэтому у каждого разрешения — список «*».
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * До шага 6 фронтенд лежит в корне проекта (app.js, js/), поэтому пути идут от корня.
 *   node --test design/EDDAS/tests/*.test.cjs
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const source = readFileSync(join(__dirname, '../../../app.js'), 'utf8');

test('the player iframe allows fullscreen and the rest to its opaque document, not only to the src origin', () => {
  const allow = source.match(/node\.allow = "([^"]+)"/)?.[1];
  assert.ok(allow, 'hardenFrame sets node.allow');
  const features = Object.fromEntries(allow.split(';').map(part => part.trim().split(/\s+/)).map(([name, ...list]) => [name, list]));
  for (const name of ['autoplay', 'fullscreen', 'picture-in-picture', 'encrypted-media']) {
    assert.deepEqual(features[name], ['*'], `${name} *`);
  }
});

test('hardenFrame still drops allowfullscreen and keeps the sandbox flags', () => {
  const body = source.slice(source.indexOf('const hardenFrame'), source.indexOf('const hardenFrame') + 900);
  assert.match(body, /removeAttribute\("allowfullscreen"\)/);
  assert.match(body, /setAttribute\("sandbox", `allow-scripts allow-presentation\$\{sameOrigin \? " allow-same-origin" : ""\}`\)/);
});
