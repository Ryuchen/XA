// Feed the real boss order-detail response through the native presentation.
const fs = require('node:fs');
const assert = require('node:assert/strict');
const { present } = require('../apps/boss-miniapp/native/core/orders');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const order = present(input.order);
assert.equal(order.statusText, input.statusText);
assert.equal(order.refundAmountText, String(input.refunded / 10));
assert.equal(order.refundRemainingText, String(input.remaining / 10));
assert.equal(order.refundRecords.length, input.count);
