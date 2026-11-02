/*
 * Чат серии, чистая логика (js/chat-logic.js): минута серии, обрезка полоски, слова и даты, порядок и окна списка,
 * расписание полосок по правилу владельца — дорожки, норма на минуту по «Нравится», не больше новых за 10 с,
 * ожидание дорожки до 3 с, перемотка без залпа, пауза замораживает.
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

const source = readFileSync(join(__dirname, '../../../js/chat-logic.js'), 'utf8');
const logic = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);
const root = (id, moment, likes = 0, date = id) => ({ id, moment, likes_count: likes, date, text: `#${id}` });
// Прогнать время видео от a до b шагом step; вернуть id, которые хоть раз были на экране
function run(sched, a, b, step = 0.25) {
  const seen = new Set();
  for (let t = a; t <= b + 1e-9; t += step) for (const s of sched.tick(Number(t.toFixed(3)))) seen.add(s.id);
  return seen;
}

test('episode minute is mm:ss, with hours h:mm:ss', async () => {
  const { fmtMoment } = await logic;
  assert.equal(fmtMoment(0), '00:00');
  assert.equal(fmtMoment(275.9), '04:35');
  assert.equal(fmtMoment(3729), '1:02:09');
  assert.equal(fmtMoment(-5), '00:00');
});

test('a long strip is cut at a word boundary without trailing punctuation; short text stays whole', async () => {
  const { cutStrip } = await logic;
  assert.deepEqual(cutStrip('  Вот   это поворот! ', 80), ['Вот это поворот!', false]);
  const long = 'Лучшая сцена серии, пересматриваю второй раз. Как они показали воспоминания Химмеля — через мелочи';
  const [text, cut] = cutStrip(long, 80);
  assert.equal(cut, true);
  assert.ok(Array.from(text).length <= 80);
  assert.ok(long.startsWith(text));
  assert.match(text, /[^\s.,;:!?…—-]$/u);
  assert.equal(cutStrip('😀'.repeat(50), 40)[0], '😀'.repeat(40), 'emoji are not split in half');
});

test('reply counts take the right word form', async () => {
  const { plural } = await logic;
  const forms = ['ответ', 'ответа', 'ответов'];
  assert.deepEqual([1, 2, 5, 11, 21, 112].map(n => plural(n, forms)), ['ответ', 'ответа', 'ответов', 'ответов', 'ответ', 'ответов']);
});

test('full date reads like the social network: «2 ноября 2026 в 15:44»', async () => {
  const { fullDate } = await logic;
  assert.equal(fullDate(new Date(2026, 10, 2, 15, 44).getTime() / 1000), '2 ноября 2026 в 15:44');
  assert.equal(fullDate('nope'), '');
});

test('after a refusal the chat waits 5 s, then twice as long up to 60 s; Retry-After wins; success resets', async () => {
  const { Backoff } = await logic;
  const b = new Backoff();
  assert.equal(b.ready(0), true);
  assert.deepEqual([1, 2, 3, 4, 5, 6].map(() => b.fail(0)), [5000, 10000, 20000, 40000, 60000, 60000]);
  b.ok();
  assert.equal(b.fail(1000), 5000, 'success starts over from 5 s');
  assert.equal(b.ready(5999), false);
  assert.equal(b.ready(6000), true);
  assert.equal(b.fail(6000, 30), 30000, 'Retry-After from the server wins over its own pause');
  assert.equal(b.ready(35999), false);
  assert.equal(b.ready(36000), true);
});

test('a spoiler is a comment past the current minute, unless blurring is off or the viewer opened it', async () => {
  const { isSpoiler } = await logic;
  assert.equal(isSpoiler(605, 581.4), true, 'below «Сейчас» — blurred');
  assert.equal(isSpoiler(577, 581.4), false, 'already watched');
  assert.equal(isSpoiler(581, 581.4), false, 'the episode reached its minute — the blur comes off by itself');
  assert.equal(isSpoiler('605', 581.4), true, 'a minute kept in data-moment counts the same');
  assert.equal(isSpoiler(605, 581.4, false), false, 'the «Спойлеры» switch shows everything');
  assert.equal(isSpoiler(605, 581.4, true, true), false, 'opened by a tap stays open');
  assert.equal(isSpoiler(null, 10), false);
  assert.equal(isSpoiler('', 10), false, 'a comment without a minute is never a spoiler');
});

test('the chat order is by minute, then id; no duplicates; the now mark goes before the first later comment', async () => {
  const { insertSorted, firstAfter } = await logic;
  const roots = [];
  for (const c of [root(5, 60), root(2, 30), root(9, 60), root(3, 60), root(1, 120)]) insertSorted(roots, c);
  assert.equal(insertSorted(roots, root(3, 60)), false);
  assert.deepEqual(roots.map(c => c.id), [2, 3, 5, 9, 1]);
  assert.equal(firstAfter(roots, 59.5), 1);
  assert.equal(firstAfter(roots, 60), 4);
  assert.equal(firstAfter(roots, 500), 5);
});

test('list windows: 5 minutes around now, 10-minute neighbours, a one-minute step back when dense', async () => {
  const { windowAround, windowBefore, windowAfter } = await logic;
  assert.deepEqual(windowAround(30), [0, 330]);
  assert.deepEqual(windowAround(600), [300, 900]);
  assert.deepEqual(windowBefore(300), [0, 299]);
  assert.deepEqual(windowBefore(900, false), [840, 899]);
  assert.deepEqual(windowAfter(900), [901, 1500]);
});

test('lanes fit between the camera button and the player controls, capped at 4 or 2', async () => {
  const { lanesFor } = await logic;
  assert.equal(lanesFor(450, 39, 4), 4);
  assert.equal(lanesFor(197, 32, 2), 2);
  assert.equal(lanesFor(150, 39, 4), 1);
});

test('a strip appears when the video passes its minute and lives 6 seconds of video time', async () => {
  const { StripScheduler } = await logic;
  const s = new StripScheduler();
  s.setRoots([root(1, 10), root(2, 11)]);
  s.tick(9);
  assert.deepEqual(s.tick(10.2).map(x => [x.id, x.lane]), [[1, 0]]);
  assert.deepEqual(s.tick(11.1).map(x => [x.id, x.lane]), [[1, 0], [2, 1]]);
  for (const t of [12, 13, 14, 15]) s.tick(t);
  assert.deepEqual(s.tick(16.3).map(x => x.id), [2]);
  assert.deepEqual(s.tick(16.3).map(x => x.id), [2], 'a paused video keeps its strips');
  assert.deepEqual(s.tick(17.2).map(x => x.id), []);
});

test('a busy minute keeps only its most liked comments (phone: 6), ties go to the earlier one', async () => {
  const { StripScheduler } = await logic;
  const s = new StripScheduler();
  s.configure({ phone: true, lanes: 2 });
  // Десять комментариев за минуту, по одному в 5 секунд: дорожки и норма за 10 с их не режут
  const roots = Array.from({ length: 10 }, (_, i) => root(i + 1, i * 5 + 1, [9, 1, 8, 2, 7, 3, 6, 4, 5, 5][i], i));
  s.setRoots(roots);
  const seen = run(s, 0, 59);
  assert.deepEqual([...seen].sort((a, b) => a - b), [1, 3, 5, 7, 9, 10].sort((a, b) => a - b));
});

test('no more than 4 new strips in 10 seconds; a strip waits for a lane at most 3 seconds', async () => {
  const { StripScheduler } = await logic;
  const s = new StripScheduler();
  s.setRoots([1, 2, 3, 4, 5, 6].map(id => root(id, 5)));
  const seen = run(s, 4, 20);
  assert.deepEqual([...seen].sort(), [1, 2, 3, 4]);
  const two = new StripScheduler();
  two.configure({ phone: false, lanes: 2 });
  two.setRoots([1, 2, 3].map(id => root(id, 5)));
  assert.deepEqual([...run(two, 4, 20)].sort(), [1, 2], 'the third never got a free lane within 3 s');
});

test('when strips compete for lanes, the most liked go first', async () => {
  const { StripScheduler } = await logic;
  const s = new StripScheduler();
  s.setRoots([[1, 2], [2, 54], [3, 0], [4, 41], [5, 33], [6, 20]].map(([id, likes]) => root(id, 5, likes)));
  assert.deepEqual([...run(s, 4, 20)].sort((a, b) => a - b), [2, 4, 5, 6]);
});

test('in a busy few seconds the four most liked win, even if a weaker one came a moment earlier', async () => {
  const { StripScheduler } = await logic;
  const s = new StripScheduler();
  s.setRoots([[10, 756, 54], [11, 757, 41], [12, 757, 2], [13, 758, 33], [14, 759, 0], [15, 760, 20], [16, 760, 1]]
    .map(([id, moment, likes]) => root(id, moment, likes)));
  assert.deepEqual([...run(s, 754, 775)].sort((a, b) => a - b), [10, 11, 13, 15]);
});

test('after a seek strips start from the new place, the skipped part does not burst', async () => {
  const { StripScheduler } = await logic;
  const s = new StripScheduler();
  s.setRoots([root(1, 50), root(2, 100), root(3, 101)]);
  s.tick(10);
  assert.deepEqual(s.tick(100.4).map(x => x.id), [], 'the jump itself shows nothing');
  assert.deepEqual(s.tick(101.2).map(x => x.id), [3]);
  assert.equal(run(s, 101.2, 110).has(1), false);
});

test('a live comment at the current minute is queued at once, even if its minute has just passed', async () => {
  const { StripScheduler } = await logic;
  const s = new StripScheduler();
  s.setRoots([]);
  s.tick(30);
  s.push(root(7, 29), 30);
  assert.deepEqual(s.tick(30.3).map(x => x.id), [7]);
});
