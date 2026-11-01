// Check the native order presentation with the actual isolated API response.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { present } = require('../apps/boss-miniapp/native/core/orders');

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const result = present(input.order);
if (input.zeroPayment) {
  assert.equal(result.zeroPaymentCancelled, true);
  assert.ok(result.timeline.some(row => row.title === '订单已取消'));
  assert.ok(!result.timeline.some(row => row.title.includes('退款已入账')));
  const orderList = fs.readFileSync(path.join(__dirname, '../apps/boss-miniapp/native/pages/orders/index.wxml'), 'utf8');
  assert.match(orderList, /zeroPaymentCancelled/);
  assert.match(orderList, /无需退回余额/);
}
const recorded = result.refundRecords.flatMap(row => row.couponResults);
assert.equal(recorded.length, 1);
assert.equal(recorded[0].name, input.name);
assert.match(recorded[0].text, input.outcome === 'RETURNED' ? /已返还.*我的券/ : /已核销，不返还/);
const markup = fs.readFileSync(path.join(__dirname, '../apps/boss-miniapp/native/pages/order-detail/index.wxml'), 'utf8');
assert.match(markup, /bindtap="coupons"/);
console.log('Native refund coupon presentation agrees with the real API response');
