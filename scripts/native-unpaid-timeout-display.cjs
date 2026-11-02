// Presentation of an order returned by the real cancellation API.
const fs = require('node:fs'), assert = require('node:assert/strict');
const { present } = require('../apps/boss-miniapp/native/core/orders');
const result = present(JSON.parse(fs.readFileSync(0, 'utf8')));
assert.equal(result.statusText, '已取消');
assert.equal(result.paymentUnconfirmed, true);
assert.equal(result.fulfillment, '订单已取消，付款情况请核对钱包流水或联系客服。');
assert.equal(result.canCancel, false);
assert.equal(result.refundRecords.length, 0);
