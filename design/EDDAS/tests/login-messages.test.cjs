/*
 * Сообщения формы входа (js/account.js, _loginErrorMessage): вход через соцсеть выключен на сервере — «Вход временно
 * недоступен.», а не «соцсеть не отвечает»; остальные ответы — как были.
 *
 * Автор: Чепела Даниил Максимович.
 * AsoPlay — proprietary, все права защищены (см. /COPYRIGHT).
 *
 * account.js — модуль с импортами, поэтому функция берётся из исходника и выполняется с заглушками.
 *   node --test design/EDDAS/tests/*.test.cjs
 */
const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const vm = require('node:vm');

const source = readFileSync(join(__dirname, '../../../js/account.js'), 'utf8');
const body = source.match(/function _loginErrorMessage\(exc\) \{[\s\S]*?\n\}\n/)[0];
const context = {
  authConfig: { as_login: true },
  socialSiteName: () => 'Anime Social',
  _plural: (n, forms) => forms[2],
};
vm.runInNewContext(`${body}\nthis.message = _loginErrorMessage;`, context);
const message = (status, text) => context.message(Object.assign(new Error(`${status} ${text}`), { status }));

test('login switched off on the server reads as temporarily unavailable', () => {
  assert.equal(message(503, '{"detail":"login_unavailable"}'), 'Вход временно недоступен.');
});

test('other answers keep their messages', () => {
  assert.equal(message(503, '{"detail":"unavailable"}'), 'Anime Social не отвечает. Попробуйте войти чуть позже.');
  assert.equal(message(403, '{"detail":"password_login_disabled"}'), 'Вход по почте сейчас выключен.');
  assert.equal(message(401, '{"detail":"invalid credentials"}'), 'Неправильная почта или пароль.');
});
