const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const moduleUnderTest = { exports: {} };
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname, '../apps/boss-miniapp/native/core/ui.js'), 'utf8'), {
  module: moduleUnderTest, exports: moduleUnderTest.exports,
  require: name => name === './navigation' ? { tabs: [] } : {},
});
const { date } = moduleUnderTest.exports;

test('native explicit-offset timestamps use Shanghai time across day and year boundaries', () => {
  for (const [input, expected] of [
    ['2026-10-19T16:31:00.000Z', '2026-10-20 00:31'],
    ['2026-10-20T00:31:00+08:00', '2026-10-20 00:31'],
    ['2026-10-19T23:45:00+00:00', '2026-10-20 07:45'],
    ['2026-12-31T20:15:00Z', '2027-01-01 04:15'],
    ['2026-10-19T23:30:00-05:00', '2026-10-20 12:30'],
    ['2026-10-20T00:31:59.123456+0800', '2026-10-20 00:31'],
  ]) assert.equal(date(input), expected, input);
});

test('native naive and invalid legacy timestamps retain their safe original presentation', () => {
  for (const [input, expected] of [
    ['2026-10-19T16:31:59', '2026-10-19 16:31'],
    ['2026-10-19 16:31:59', '2026-10-19 16:31'],
    ['2026-10-19', '2026-10-19'],
    ['not-a-date', 'not-a-date'],
    ['2026-99-99T16:31:00Z', '2026-99-99 16:31'],
    ['2026-02-30T16:31:00Z', '2026-02-30 16:31'],
    [null, ''], [undefined, ''], ['', ''],
  ]) assert.equal(date(input), expected, String(input));
});
