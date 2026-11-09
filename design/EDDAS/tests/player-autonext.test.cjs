/*
 * Автопереключение серий в app.js: следующая серия включается, когда позиция плеера дошла до конца серии от более
 * ранней (наименьшей за серию), — досмотрели её целиком, начали с середины, отмотали назад или перемотали к концу.
 * Голый сигнал конца без позиции ждёт часов: минута и половина серии с её открытия.
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

const DURATION = 1420;

// Плеер на 1-й серии из трёх, открытой openedAgo миллисекунд назад; send — сообщение из его кадра
function opened(openedAgo, { autoNext = true, episode = 0 } = {}) {
  const listeners = [];
  const frame = {};
  const p = harness({}, {
    state: { autoNext, user: null, backendSources: [] },
    window: { addEventListener: (type, fn) => { if (type === 'message') listeners.push(fn); } },
    realtime: { refreshWatching() {} },
    saveProgress() {}, getProgress: () => null, getWatch: () => null, ensureWatchEntry() {},
    parseEpDurationMin: () => 24,
  });
  last().Player.prototype._setupAutoNext.call(p);
  const switched = [];
  Object.assign(p, {
    episodes: [{ num: 1 }, { num: 2 }, { num: 3 }], currentEp: episode, anime: { episodes: 3, images: {} },
    _iframeEl: { contentWindow: frame, src: 'http://127.0.0.1:8787/player/frame?url=x' },
    _playStartedAt: Date.now() - openedAgo, playEpisode: i => switched.push(i),
  });
  const send = (key, value) => listeners.forEach(fn => fn({ source: frame, origin: 'null', data: { key, value } }));
  return { p, send, switched };
}

const playThrough = (send, times) => {
  send('kodik_player_duration_update', DURATION);
  for (const t of times) send('kodik_player_time_update', t);
  send('kodik_player_video_ended');
};

test('seeking to the end right after the start switches to the next episode', () => {
  const { send, switched } = opened(20_000);
  playThrough(send, [8, DURATION - 5, DURATION - 2, DURATION]);
  assert.deepEqual(switched, [1]);
});

test('an episode started from the middle switches when it ends, however soon', () => {
  const { send, switched } = opened(30_000);
  playThrough(send, [DURATION - 25, DURATION - 10, DURATION]);
  assert.deepEqual(switched, [1]);
});

test('a broad end signal before the real end does not skip the rest of the episode', () => {
  const { send, switched } = opened(20 * 60_000);
  playThrough(send, [600, 610]);
  assert.deepEqual(switched, []);
});

test('an episode opened right at its end does not jump on to the next one', () => {
  const { send, switched } = opened(20_000);
  playThrough(send, [DURATION - 0.5, DURATION]);
  assert.deepEqual(switched, []);
});

test('opened at its end, rewound a minute and watched to the end — switches', () => {
  const { send, switched } = opened(90_000);  // плеер сам продолжил с конца; меньше половины серии от открытия
  playThrough(send, [DURATION - 0.5, DURATION - 60, DURATION - 30, DURATION]);
  assert.deepEqual(switched, [1]);
});

test('a bare end signal without the playhead waits a minute and half the episode', () => {
  const early = opened(40_000);
  early.send('kodik_player_play');
  early.send('kodik_player_video_ended');
  assert.deepEqual(early.switched, []);

  const halfway = opened(DURATION * 400);  // больше минуты, меньше половины серии
  halfway.send('kodik_player_duration_update', DURATION);
  halfway.send('kodik_player_play');
  halfway.send('kodik_player_video_ended');
  assert.deepEqual(halfway.switched, []);

  const late = opened(DURATION * 600);
  late.send('kodik_player_duration_update', DURATION);
  late.send('kodik_player_play');
  late.send('kodik_player_video_ended');
  assert.deepEqual(late.switched, [1]);
});

test('no switch with the setting off or on the last episode, and only once per episode', () => {
  const off = opened(20_000, { autoNext: false });
  playThrough(off.send, [8, DURATION]);
  assert.deepEqual(off.switched, []);

  const lastEp = opened(20_000, { episode: 2 });
  playThrough(lastEp.send, [8, DURATION]);
  assert.deepEqual(lastEp.switched, []);

  const twice = opened(20_000);
  playThrough(twice.send, [8, DURATION]);
  twice.send('ended');
  assert.deepEqual(twice.switched, [1]);
});
