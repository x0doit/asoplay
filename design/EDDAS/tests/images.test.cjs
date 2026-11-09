/*
 * Картинки чужих хостов (js/images.js): какой адрес идёт в src — напрямую или через /proxy/img, и память о хостах,
 * которые у этого человека открываются только через прокси.
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

const source = readFileSync(join(__dirname, '..', '..', '..', 'js', 'images.js'), 'utf8');
let copies = 0;
// Свой экземпляр модуля на проверку: у модуля своё состояние (правила и память)
const fresh = () => import(`data:text/javascript;base64,${Buffer.from(`${source}\n// ${++copies}`).toString('base64')}`);

const MAL = 'https://cdn.myanimelist.net/images/anime/1/1.jpg';
const IMG_HOSTS = ['cdn.myanimelist.net', 'myanimelist.net', 's4.anilist.co', 'shikimori.io'];
const proxied = (u, backend = '') => `${backend}/proxy/img?url=${encodeURIComponent(u)}`;

function storage({ broken = false } = {}) {
  const data = new Map();
  const fail = () => { throw new Error('storage is off'); };
  const store = {
    data,
    getItem: broken ? fail : key => (data.has(key) ? data.get(key) : null),
    setItem: broken ? fail : (key, value) => data.set(key, String(value)),
  };
  Object.defineProperty(globalThis, 'localStorage', { value: store, configurable: true, writable: true });
  return store;
}

function clock(start = 1_793_440_000_000) {
  const real = Date.now;
  const time = { now: start, restore: () => { Date.now = real; } };
  Date.now = () => time.now;
  return time;
}

test('nothing is rewritten until the server is up and names its image hosts', async () => {
  storage();
  const images = await fresh();
  assert.equal(images.url(MAL), MAL);
  images.configure({ backend: '', available: false, proxyHosts: ['cdn.myanimelist.net'], imgHosts: IMG_HOSTS });
  assert.equal(images.url(MAL), MAL);
  assert.equal(images.url(''), '');
  assert.equal(images.url(undefined), '');
});

test('the server bridge does not send images through the proxy: catalog hosts load directly first', async () => {
  storage();
  const images = await fresh();
  images.configure({ backend: 'http://localhost:8787/', available: true, bridge: true, vpn: true, imgHosts: IMG_HOSTS });
  assert.equal(images.url(MAL), MAL);
  assert.equal(images.url('https://x.s4.anilist.co/a.png'), 'https://x.s4.anilist.co/a.png');
});

test('a listed host goes through the proxy with the backend address, other hosts and addresses stay as they are', async () => {
  storage();
  const images = await fresh();
  images.configure({ backend: 'http://localhost:8787/', available: true, proxyHosts: ['s4.anilist.co'], imgHosts: IMG_HOSTS });
  assert.equal(images.url('https://x.s4.anilist.co/a.png'), proxied('https://x.s4.anilist.co/a.png', 'http://localhost:8787'));
  for (const u of ['https://evil.test/a.jpg', 'https://s4.anilist.co.evil.test/a.jpg', 'https://evils4.anilist.co/a.jpg',
                   '/assets/eddas/img/empty.svg', 'data:image/png;base64,AAAA', 'blob:http://localhost/1']) {
    assert.equal(images.url(u), u, u);
  }
});

test('panel hosts go through the proxy at once, the rest load directly first', async () => {
  storage();
  const images = await fresh();
  images.configure({ backend: '', available: true, proxyHosts: ['img.example.com'],
                     imgHosts: [...IMG_HOSTS, 'img.example.com'] });
  assert.equal(images.url('https://img.example.com/a.png'), proxied('https://img.example.com/a.png'));
  assert.equal(images.url('https://a.img.example.com/a.png'), proxied('https://a.img.example.com/a.png'));
  assert.equal(images.url(MAL), MAL);
});

test('a status frame without the lists keeps the lists it had', async () => {
  storage();
  const images = await fresh();
  images.configure({ backend: '', available: true, proxyHosts: ['cdn.myanimelist.net'], imgHosts: IMG_HOSTS });
  images.configure({ available: true });
  assert.equal(images.url(MAL), proxied(MAL));
  images.configure({ available: true, proxyHosts: [], imgHosts: IMG_HOSTS });
  assert.equal(images.url(MAL), MAL);
});

test('a host that opened only through the proxy goes there at once for a day, then is tried directly again', async () => {
  const store = storage();
  const time = clock();
  try {
    const images = await fresh();
    images.configure({ backend: '', available: true, imgHosts: IMG_HOSTS });
    images.remember('cdn.myanimelist.net');
    assert.equal(images.url(MAL), proxied(MAL));
    assert.equal(images.url('https://s4.anilist.co/a.png'), 'https://s4.anilist.co/a.png');
    assert.deepEqual(JSON.parse(store.data.get('av_img_proxy')), { 'cdn.myanimelist.net': time.now + 24 * 3600 * 1000 });
    time.now += 24 * 3600 * 1000 - 1;
    assert.equal(images.url(MAL), proxied(MAL));
    time.now += 2;
    assert.equal(images.url(MAL), MAL);
    assert.deepEqual(JSON.parse(store.data.get('av_img_proxy')), {});
  } finally {
    time.restore();
  }
});

test('the memory is shared with other tabs through localStorage and expired hosts are dropped', async () => {
  const store = storage();
  const time = clock();
  try {
    store.setItem('av_img_proxy', JSON.stringify({ 'cdn.myanimelist.net': time.now + 1000, 's4.anilist.co': time.now - 1 }));
    const images = await fresh();
    images.configure({ backend: '', available: true, imgHosts: IMG_HOSTS });
    assert.equal(images.url(MAL), proxied(MAL));
    assert.equal(images.url('https://s4.anilist.co/a.png'), 'https://s4.anilist.co/a.png');
  } finally {
    time.restore();
  }
});

test('without localStorage the memory lives in the tab', async () => {
  storage({ broken: true });
  const images = await fresh();
  images.configure({ backend: '', available: true, imgHosts: IMG_HOSTS });
  images.remember('cdn.myanimelist.net');
  assert.equal(images.url(MAL), proxied(MAL));
  assert.equal(images.remembered('s4.anilist.co'), false);
});

test('only addresses the proxy would accept are sent to it, the rest stay direct', async () => {
  storage();
  const images = await fresh();
  images.configure({ backend: '', available: true, proxyHosts: ['cdn.myanimelist.net'], imgHosts: IMG_HOSTS });
  assert.equal(images.url(MAL), proxied(MAL));
  assert.equal(images.url('https://cdn.myanimelist.net:443/a.jpg'), proxied('https://cdn.myanimelist.net:443/a.jpg'));
  assert.equal(images.url('http://CDN.MyAnimeList.net./a.jpg'), proxied('http://CDN.MyAnimeList.net./a.jpg'));
  for (const u of ['https://cdn.myanimelist.net:8443/a.jpg', 'https://user:pw@cdn.myanimelist.net/a.jpg',
                   'https://@cdn.myanimelist.net/a.jpg', 'https://x%2ey.cdn.myanimelist.net/a.jpg',
                   'https://evil.test\@cdn.myanimelist.net/a.jpg', 'https://cdn.myanimelist.net:+443/a.jpg']) {
    assert.equal(images.url(u), u, u);
  }
});
