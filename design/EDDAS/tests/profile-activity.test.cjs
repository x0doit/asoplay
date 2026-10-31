/*
 * Активность в профиле (js/profile-activity.js): календарь, группировка ленты, периоды вкладок.
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
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../../../js/profile-activity.js'), 'utf8');
const helpers = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const dayRange = (start, count) => Array.from({ length: count }, (_, n) => {
  const d = new Date(`${start}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return { d: d.toISOString().slice(0, 10), n: n % 4 };
});

test('calendar uses Monday-first weeks and a stable six-week layout', async () => {
  const { calendarMonth } = await helpers;
  const result = calendarMonth(dayRange('2026-09-28', 30), '2026-10');
  assert.equal(result.cells.length, 42);
  assert.equal(result.cells[0].key, '2026-09-28');
  assert.equal(result.cells.find(c => c.key === '2026-10-01').col, 3);
  assert.equal(result.cells.find(c => c.key === '2026-10-26').col, 0);
  assert.equal(result.previous, '2026-09');
  assert.equal(result.next, null);
});

test('unavailable dates are not represented as zero-activity dates', async () => {
  const { calendarMonth } = await helpers;
  const days = dayRange('2026-09-28', 30);
  const october = calendarMonth(days, '2026-10');
  assert.equal(october.cells.find(c => c.key === '2026-10-28').day, undefined);
  assert.equal(october.cells.find(c => c.key === '2026-09-28').day, null);
  const september = calendarMonth(days, '2026-09');
  assert.equal(september.cells.find(c => c.key === '2026-09-28').day.n, 0);
  assert.equal(september.previous, null);
  assert.equal(september.next, '2026-10');
});

test('leap days and year boundaries remain navigable', async () => {
  const { calendarMonth } = await helpers;
  assert.ok(calendarMonth(dayRange('2024-02-01', 29), '2024-02').cells.find(c => c.key === '2024-02-29').day);
  const days = dayRange('2025-12-15', 30);
  assert.equal(calendarMonth(days, '2026-01').previous, '2025-12');
  assert.equal(calendarMonth(days, '2025-12').next, '2026-01');
});

test('calendar date coordinates are independent of local timezone', async () => {
  const { calendarMonth } = await helpers;
  const oldTZ = process.env.TZ;
  try {
    for (const timezone of ['Pacific/Honolulu', 'Asia/Tokyo', 'UTC']) {
      process.env.TZ = timezone;
      assert.equal(calendarMonth(dayRange('2026-10-01', 30), '2026-10').cells.find(c => c.key === '2026-10-01').col, 3);
    }
  } finally {
    if (oldTZ === undefined) delete process.env.TZ;
    else process.env.TZ = oldTZ;
  }
});

test('grouping preserves every event and its original order', async () => {
  const { groupActivity } = await helpers;
  const items = [
    { mal_id: 1, episode_num: 2, at: '2026-10-26T12:35:00', kind: 'watch_continue' },
    { mal_id: 1, episode_num: 2, at: '2026-10-26T12:30:00', kind: 'watch_start', poster_url: '/poster.jpg' },
    { mal_id: 1, episode_num: 1, at: '2026-10-26T11:00:00', kind: 'watch_start' },
    { mal_id: 2, at: '2026-10-26T10:00:00', kind: 'rate' },
    { mal_id: 1, episode_num: 2, at: '2026-10-25T12:30:00', kind: 'watch_start' },
    { mal_id: null, at: '2026-10-25T11:30:00', kind: 'list_add' },
    { mal_id: null, at: '2026-10-25T11:20:00', kind: 'list_move' },
  ];
  const groups = groupActivity(items);
  assert.equal(groups.length, 5);
  assert.equal(groups[0].poster_url, '/poster.jpg');
  assert.deepEqual(groups.map(group => group.episodes), [[2, 1], [], [2], [], []]);
  assert.deepEqual(groups.flatMap(group => group.events), items);
  assert.equal(items[0].poster_url, undefined);
  assert.equal(items[0].episodes, undefined);
  assert.deepEqual(groupActivity([]), []);
});

test('one title on one day is one card even when list actions interleave episodes', async () => {
  const { groupActivity } = await helpers;
  const groups = groupActivity([
    { mal_id: 5, episode_num: 1, at: '2026-10-26T12:34:10', kind: 'watch_continue' },
    { mal_id: 5, at: '2026-10-26T12:34:00', kind: 'list_add' },
    { mal_id: 5, episode_num: 1, at: '2026-10-26T12:29:00', kind: 'watch_start' },
    { mal_id: 6, at: '2026-10-26T12:00:00', kind: 'favorite' },
    { mal_id: 5, episode_num: 2, at: '2026-10-26T11:00:00', kind: 'watch_start' },
  ]);
  assert.deepEqual(groups.map(group => [group.mal_id, group.events.length, group.episodes]), [[5, 3, [1]], [6, 1, []], [5, 1, [2]]]);
});

test('grouping across pagination boundaries neither duplicates nor loses actions', async () => {
  const { groupActivity } = await helpers;
  const events = Array.from({ length: 23 }, (_, n) => ({ mal_id: 1, episode_num: 1, at: `2026-10-26T12:${String(59 - n).padStart(2, '0')}:00`, kind: 'watch_continue' }));
  for (const end of [10, 20, 23]) {
    const groups = groupActivity(events.slice(0, end));
    assert.equal(groups.length, 1);
    assert.equal(groups[0].events.length, end);
  }
});

test('feed opens the complete diary before painting without resetting filters or pages', () => {
  const account = readFileSync(join(__dirname, '../../../js/account.js'), 'utf8');
  const setter = account.slice(account.indexOf('function _setActivityView('), account.indexOf('// Главная точка входа.'));
  for (const storageAvailable of [true, false]) {
    const calls = [];
    const container = { _view: 'grid', _activity: { period_days: 90 }, _recentItems: [1, 2, 3],
      _recentGroup: 'watch', _dayFilter: '2026-10-26', _openDiary: () => calls.push('diary') };
    const env = {
      sessionStorage: { setItem: () => { if (!storageAvailable) throw new Error('Storage denied'); } },
      _paintActivity: (box, activity) => {
        assert.equal(box, container);
        assert.equal(activity, container._activity);
        calls.push('paint');
      },
    };
    vm.runInNewContext(setter, env);
    env._setActivityView(container, 'list');
    assert.deepEqual(calls, ['diary', 'paint']);
    assert.equal(container._view, 'list');
    assert.equal(container._recentGroup, 'watch');
    assert.equal(container._dayFilter, '2026-10-26');
    assert.deepEqual(container._recentItems, [1, 2, 3]);
    calls.length = 0;
    env._setActivityView(container, 'grid');
    assert.deepEqual(calls, ['paint']);
    assert.equal(container._view, 'grid');
  }
});

function periodHarness(fetch) {
  const account = readFileSync(join(__dirname, '../../../js/account.js'), 'utf8');
  const code = account.slice(account.indexOf('function _setActivityView('), account.indexOf('// Главная точка входа.'));
  const activity = days => ({ period_days: days, days: [] });
  const status = { textContent: '' };
  const container = {
    isConnected: true, _profileMode: 'overview', _view: 'grid',
    _periods: { overview: 365, diary: 30 }, _activity: activity(365),
    _cache: new Map([[365, activity(365)]]),
    _fetch: fetch || (async days => activity(days)),
    querySelector: () => status,
  };
  const env = {
    sessionStorage: { setItem() {} },
    _paintActivity: (box, fresh) => { box._activity = fresh; status.textContent = ''; },
    _reloadRecent: () => {},
    _hideActivity: () => { throw new Error('Unexpected privacy response'); },
  };
  vm.runInNewContext(code, env);
  container._openDiary = () => env._switchActivityMode(container, 'diary', { preservePeriod: true });
  return { container, status, env, activity };
}

test('overview restores its year after the diary opens the 30-day calendar', async () => {
  const { container, env } = periodHarness();
  await env._switchActivityMode(container, 'diary');
  assert.equal(container._activity.period_days, 30);
  await env._switchActivityMode(container, 'overview');
  assert.equal(container._activity.period_days, 365);
  assert.equal(container._view, 'grid');
  assert.deepEqual(container._periods, { overview: 365, diary: 30 });
});

test('each tab retains its explicitly selected period', async () => {
  const { container, env } = periodHarness();
  await env._loadActivityPeriod(container, 90);
  await env._switchActivityMode(container, 'diary');
  await env._loadActivityPeriod(container, 180);
  await env._switchActivityMode(container, 'overview');
  assert.equal(container._activity.period_days, 90);
  await env._switchActivityMode(container, 'diary');
  assert.equal(container._activity.period_days, 180);
});

test('a late diary response cannot replace the overview even when returning from cache', async () => {
  let resolve;
  const { container, env, activity } = periodHarness(() => new Promise(done => { resolve = done; }));
  const pending = env._switchActivityMode(container, 'diary');
  await env._switchActivityMode(container, 'overview');
  resolve(activity(30));
  await pending;
  assert.equal(container._profileMode, 'overview');
  assert.equal(container._activity.period_days, 365);
  assert.deepEqual(container._periods, { overview: 365, diary: 30 });
});

test('opening the full feed or selecting a day retains the current period and date', async () => {
  const { container, env } = periodHarness();
  await env._loadActivityPeriod(container, 90);
  container._dayFilter = '2026-10-26';
  env._setActivityView(container, 'list');
  assert.equal(container._profileMode, 'diary');
  assert.equal(container._periods.overview, 90);
  assert.equal(container._periods.diary, 90);
  assert.equal(container._dayFilter, '2026-10-26');
  await env._switchActivityMode(container, 'overview');
  assert.equal(container._dayFilter, '2026-10-26');
});

test('failed or mismatched period responses keep the displayed data and saved selection consistent', async () => {
  for (const fetch of [async () => null, async () => { throw new Error('Offline'); }, async () => ({ period_days: 30, days: [] })]) {
    const { container, status, env } = periodHarness(fetch);
    await env._loadActivityPeriod(container, 90);
    assert.equal(container._activity.period_days, 365);
    assert.equal(container._periods.overview, 365);
    assert.equal(container._cache.has(90), false);
    assert.match(status.textContent, /Не удалось/);
  }
});
