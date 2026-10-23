// Exercise the actual H5 input/quote boundary with server-shaped payloads.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const appRoot = path.resolve(__dirname, '../apps/provider-miniapp');
const ts = createRequire(path.join(appRoot, 'package.json'))('typescript');
function compile(filename, requireModule) {
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, require: requireModule });
  return module.exports;
}
const money = compile(path.resolve(__dirname, '../packages/money/src/index.js'));
const { parseWithdrawAmount, validWithdrawQuote } = compile(path.join(appRoot, 'src/utils/withdraw.ts'), () => money);

test('withdraw input preserves tenths of a coin without truncating or rounding invalid text', () => {
  assert.equal(parseWithdrawAmount('12.5'), 125);
  assert.equal(parseWithdrawAmount(' 1000 '), 10000);
  assert.equal(parseWithdrawAmount('0.1'), 1);
  for (const value of ['', '12.55', '100abc', '1e3', 'Infinity', '-1', '0', '214748364.8']) {
    assert.equal(parseWithdrawAmount(value), 0, value);
  }
});

test('only a complete matching quote can enable a withdrawal, including legitimate zero tax', () => {
  const quote = { amount: 125, tax_amount: 3, actual_amount: 122, tax_rate: 2, min_amount: 10, balance: 5000, frozen_amount: 0, requests: [] };
  assert.equal(validWithdrawQuote(125, quote), true);
  assert.equal(validWithdrawQuote(125, { ...quote, tax_rate: 0, tax_amount: 0, actual_amount: 125 }), true);
  for (const data of [null, undefined, {}, { ...quote, tax_amount: undefined }, { ...quote, actual_amount: 125 }, { ...quote, tax_rate: 101 }, { ...quote, balance: '5000' }, { ...quote, min_amount: NaN }]) {
    assert.equal(validWithdrawQuote(125, data), false);
  }
  assert.equal(validWithdrawQuote(250, quote), false, 'the previous amount cannot remain actionable');
});
