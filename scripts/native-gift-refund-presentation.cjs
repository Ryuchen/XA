// Verify the native presentation using real detail responses from a gift refund.
const assert = require('node:assert/strict'), fs = require('node:fs');
const { present } = require('../apps/boss-miniapp/native/core/orders');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const order = present(input.order);
assert.equal(order.statusText, input.partial ? '礼物已部分退款' : '礼物已退款');
assert.equal(order.refundAmountText, input.amount);
assert.equal(order.refundRemainingText, input.remaining);
assert.deepEqual(order.timeline.map(row => row.title), ['礼物已送达', input.partial ? '部分退款已入账' : '退款已入账']);
