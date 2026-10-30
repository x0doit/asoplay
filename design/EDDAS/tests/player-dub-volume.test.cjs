/*
 * Озвучка и громкость плеера в app.js: какая озвучка выбирается и запоминается, громкость для следующего
 * фрейма и для чужих плееров.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * До шага 6 фронтенд лежит в корне проекта (app.js, js/), поэтому пути идут от корня.
 *   node --test design/EDDAS/tests/*.test.cjs
 */
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { harness, last } = require('./player-harness.cjs');

const kodik = 'https://kodikplayer.com/seria/1/abc/720p';
const cvh = 'https://site.yummyani.me/iframecvh.html?id=1';
const dub = (label, iframeUrl = cvh) => ({ label, iframeUrl });
const savedDub = saved => ({ prefs: { getDub: () => saved } });

test('first voice-over wins by default, not subtitles or the first Kodik entry', () => {
  const p = harness({}, savedDub(null));
  const dubs = [dub('Субтитры Crunchyroll', kodik), dub('Озвучка AniLibria'), dub('Озвучка SHIZA Project', kodik)];
  assert.equal(p._pickDubIndex(dubs), 1);
});

test('subtitles are chosen only when nothing is voiced', () => {
  const p = harness({}, savedDub(null));
  assert.equal(p._pickDubIndex([dub('Субтитры Crunchyroll'), dub('Субтитры Wakanim')]), 0);
});

test('a guest keeps the picked dub across episodes and retries it after a gap', () => {
  const p = harness({}, savedDub(null));
  p._pickDubIndex([dub('Озвучка AniLibria'), dub('Озвучка Studio Band')]);
  p._sessionDubKey = p._dubKey(dub('Озвучка Studio Band'));
  assert.equal(p._pickDubIndex([dub('Озвучка Studio Band'), dub('Озвучка AniLibria')]), 0);
  assert.equal(p._pickDubIndex([dub('Озвучка AniLibria')]), 0);
  assert.equal(p._pickDubIndex([dub('Озвучка AniLibria'), dub('Озвучка Studio Band')]), 1);
});

test('an automatic first choice also stays with the next episodes', () => {
  const p = harness({}, savedDub(null));
  assert.equal(p._pickDubIndex([dub('Озвучка AniLibria'), dub('Озвучка Studio Band')]), 0);
  assert.equal(p._pickDubIndex([dub('Озвучка Studio Band'), dub('Озвучка AniLibria')]), 1);
});

test('voice-over and subtitles of one team stay distinct; old saved names mean the voice-over', () => {
  const dubs = [dub('Субтитры AniLibria'), dub('Озвучка AniLibria')];
  assert.equal(harness({}, savedDub('anilibria'))._pickDubIndex(dubs), 1);
  assert.equal(harness({}, savedDub('sub:anilibria'))._pickDubIndex(dubs), 0);
});

const memoryStorage = () => {
  const data = new Map();
  return { getItem: k => (data.has(k) ? data.get(k) : null), setItem: (k, v) => { data.set(k, String(v)); } };
};
const handoffOf = url => JSON.parse(decodeURIComponent(url.split('#asoplay=')[1]));

test('the page keeps the volume for the next frame and ignores values it cannot trust', () => {
  const p = harness({}, { localStorage: memoryStorage() });
  const store = last().playerVolume;
  assert.equal(store.load(), null);
  assert.equal(p._handlePlayerVolumeMessage({ key: 'kodik_player_volume_change', value: { volume: 0.35, muted: false } }), true);
  assert.deepEqual({ ...store.load() }, { volume: 0.35, muted: false });
  for (const value of [1.5, -0.1, '0.2', null, { volume: NaN }, 40]) store.save(value);
  assert.deepEqual({ ...store.load() }, { volume: 0.35, muted: false });
  store.save(0.6);
  assert.deepEqual({ ...store.load() }, { volume: 0.6, muted: false });
  store.save({ volume: 0.2, muted: true });
  assert.deepEqual(handoffOf(store.fragment('ru.yummyani.me')), { volume: 0.2, muted: true, storage: {} });
});

test('third-party players get back only their own volume keys, within limits', () => {
  const localStorage = memoryStorage();
  const p = harness({}, { localStorage });
  const store = last().playerVolume;
  const plyr = '{"volume":0.4,"muted":false}';
  p._frameHost = 'alloha.yani.tv';
  assert.equal(p._handlePlayerVolumeMessage({ type: 'asoplay:storage', entries: { plyr } }), true);
  p._frameHost = 'kodikplayer.com';
  p._handlePlayerVolumeMessage({ type: 'asoplay:storage', entries: { pljsvolume: '0.7', huge: 'x'.repeat(3000) } });
  assert.deepEqual(handoffOf(store.fragment('alloha.yani.tv')).storage, { plyr });
  assert.deepEqual(handoffOf(store.fragment('kodikplayer.com')).storage, { pljsvolume: '0.7' });
  assert.deepEqual(handoffOf(store.fragment('other.example')).storage, {});
  p._handlePlayerVolumeMessage({ type: 'asoplay:storage', entries: {} });
  assert.deepEqual(handoffOf(store.fragment('kodikplayer.com')).storage, {});
  for (let i = 0; i < 20; i++) store.rememberStorage(`h${i}.test`, { volume: String(i) });
  const hosts = Object.keys(JSON.parse(localStorage.getItem('av_frame_storage')));
  assert.equal(hosts.length, 12);
  assert.ok(hosts.includes('h19.test') && !hosts.includes('alloha.yani.tv'));
});

test('Kodik gets the saved volume once it plays; other players never get Kodik commands', () => {
  const posted = [];
  const p = harness({}, { localStorage: memoryStorage() });
  last().playerVolume.save({ volume: 0.3, muted: true });
  p._iframeEl = { contentWindow: { postMessage: (msg, origin) => posted.push({ msg: JSON.parse(JSON.stringify(msg)), origin }) } };
  p._frameIsKodik = false;
  assert.equal(p._handlePlayerVolumeMessage({ key: 'kodik_player_play' }), false);
  assert.deepEqual(posted, []);
  p._frameIsKodik = true;
  p._kodikVolumeSent = false;
  assert.equal(p._handlePlayerVolumeMessage({ key: 'kodik_player_play' }), false);
  p._handlePlayerVolumeMessage({ key: 'kodik_player_video_started' });
  assert.deepEqual(posted, [
    { msg: { key: 'kodik_player_api', value: { method: 'volume', volume: 0.3 } }, origin: '*' },
    { msg: { key: 'kodik_player_api', value: { method: 'mute' } }, origin: '*' },
  ]);
});

test('each new frame carries the saved volume; a fragment-only change keeps the page', () => {
  const frames = [];
  const container = { addEventListener() {}, querySelector: () => frames[0] || null, appendChild: node => frames.push(node) };
  const makeFrame = () => ({ style: {}, src: '', setAttribute() {}, removeAttribute() {} });
  const p = harness({}, {
    localStorage: memoryStorage(), BACKEND: '', URL, document: { createElement: makeFrame },
    location: { origin: 'http://127.0.0.1:8787', protocol: 'http:', port: '8787', hostname: '127.0.0.1' },
    $: selector => (selector === '#artplayer' ? container : null),
  });
  const store = last().playerVolume;
  store.save({ volume: 0.25, muted: false });
  p.createIframe(cvh);
  assert.equal(frames.length, 1);
  const first = frames[0].src;
  assert.ok(first.startsWith('http://localhost:8787/player/frame?url='));
  assert.deepEqual(handoffOf(first), { volume: 0.25, muted: false, storage: {} });
  assert.equal(p._frameHost, 'site.yummyani.me');
  store.save({ volume: 0.5, muted: false });
  p.createIframe(cvh);
  assert.equal(frames[0].src, first);
  p.createIframe(kodik);
  assert.equal(frames.length, 1);
  assert.ok(frames[0].src.startsWith('http://localhost:8787/seria/1/abc/720p#asoplay='));
  assert.deepEqual(handoffOf(frames[0].src), { volume: 0.5, muted: false, storage: {} });
  assert.equal(p._frameIsKodik, true);
  assert.equal(p._kodikVolumeSent, false);
});
