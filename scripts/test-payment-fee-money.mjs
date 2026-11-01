import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { parseRmbFen, formatRmbFen } from '../packages/money/src/index.js';

test('RMB cents preserve exact decimal input and do not round fractional cents', () => {
  for (const [input, expected] of [['0.01', 1], ['0.29', 29], ['1.2', 120], ['12.34', 1234], ['21474836.47', 2147483647], ['90071992547409.91', Number.MAX_SAFE_INTEGER]]) {
    assert.equal(parseRmbFen(input), expected, input);
  }
  for (const invalid of ['', '01', '-1', '1.001', '1e2', 'NaN', 'Infinity', '90071992547409.92', null, undefined]) assert.equal(parseRmbFen(invalid), 0);
});

test('RMB cents display preserves cents at the integer limits without coin conversion', () => {
  for (const [fen, expected] of [[0, '0.00'], [1, '0.01'], [29, '0.29'], [1234, '12.34'], [2147483647, '21474836.47'], [Number.MAX_SAFE_INTEGER, '90071992547409.91'], [-1, '-0.01']]) assert.equal(formatRmbFen(fen), expected);
  for (const invalid of [NaN, Infinity, 1.1, Number.MAX_SAFE_INTEGER + 1]) assert.equal(formatRmbFen(invalid), '0.00');
});

test('payment fee form keeps its API cents range and rejects invalid preview input', () => {
  const source = readFileSync(new URL('../apps/admin-web/src/views/payment-fee/index.vue', import.meta.url), 'utf8');
  const body = source.match(/const previewFen = computed\(\(\) => \{([\s\S]*?)\n\}\)/)?.[1];
  assert.ok(body, 'preview amount computation must remain available');
  const parseForm = input => runInNewContext(`(() => {${body}})()`, { previewYuan: { value: input }, parseRmbFen });
  for (const [input, expected] of [['0.01', 1], ['0.29', 29], ['12.34', 1234], ['21474836.47', 2147483647]]) assert.equal(parseForm(input), expected);
  for (const input of ['0', '0.001', '01', '-1', '1e2', '21474836.48', '99999999.99', '100000000']) assert.equal(parseForm(input), 0, input);
});
