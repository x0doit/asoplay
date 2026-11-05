/*
 * «Продолжить с m:ss» (js/resume.js): какая секунда предлагается, как она пишется на кнопке и когда кнопка уходит.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 *   node --test design/EDDAS/tests/*.test.cjs
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');

const source = readFileSync(join(__dirname, '../../../js/resume.js'), 'utf8');
const resume = import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`);

test('the offered second is where the viewer stopped, past 30 s, in an episode not watched to the end', async () => {
  const { resumePoint } = await resume;
  assert.equal(resumePoint({ last: 754, seconds: 800, duration: 1440 }), 754);
  assert.equal(resumePoint({ seconds: 800, duration: 1440 }), 800);  // строка до миграции — дальняя точка
  assert.equal(resumePoint({ last: 30, seconds: 30, duration: 1440 }), null);  // первые 30 с — с начала
  assert.equal(resumePoint({ last: 600, seconds: 1330, duration: 1440 }), null);  // досмотрена: 92 % дальней точки
  assert.equal(resumePoint({ last: 300, seconds: 515, duration: 600 }), null);  // досмотрена: последние 90 с
  assert.equal(resumePoint({ last: 120, seconds: 120, duration: 0 }), 120);  // длительность ещё неизвестна
  assert.equal(resumePoint(null), null);
});

test('the time reads as m:ss, from an hour as h:mm:ss', async () => {
  const { clock } = await resume;
  assert.equal(clock(754), '12:34');
  assert.equal(clock(40), '0:40');
  assert.equal(clock(3725), '1:02:05');
});

test('a jump of the player time that playback cannot explain is a seek', async () => {
  const { jumped } = await resume;
  assert.equal(jumped(null, 100, 0), false);
  assert.equal(jumped({ t: 100, at: 0 }, 101, 1000), false);  // играет
  assert.equal(jumped({ t: 100, at: 0 }, 100, 5000), false);  // пауза
  assert.equal(jumped({ t: 100, at: 0 }, 105, 400), true);  // → на 5 с
  assert.equal(jumped({ t: 100, at: 0 }, 95, 400), true);  // ← на 5 с
});

test('the offer goes when the playhead reaches it, on a seek and on another episode; a new player keeps it', async () => {
  const { ResumeButton } = await resume;
  const quiet = () => Object.assign(new ResumeButton(() => {}), { render() {} });
  const playing = quiet();
  playing.set(1, 754);
  playing.setCan(true);
  playing.time(0, 0);
  playing.time(1, 1000);
  playing.setCan(false);  // смена озвучки: новый плеер снова с 0 — это не перемотка
  playing.setCan(true);
  playing.time(0, 2000);
  assert.deepEqual(playing.offer, { ep: 1, at: 754 });
  playing.time(10, 2200);  // перемотал сам
  assert.equal(playing.offer, null);

  const reached = quiet();
  reached.set(1, 40);
  reached.time(38, 0);
  reached.time(39.2, 1200);
  assert.equal(reached.offer, null);

  const other = quiet();
  other.set(1, 754);
  other.episode(1);
  assert.ok(other.offer);
  other.episode(2);
  assert.equal(other.offer, null);
  const none = quiet();
  none.set(1, null);
  assert.equal(none.offer, null);
});
