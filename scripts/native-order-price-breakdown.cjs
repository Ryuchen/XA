// Verify the native presenter against the real order detail returned by Django.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {present} = require('../apps/boss-miniapp/native/core/orders');
const order = JSON.parse(fs.readFileSync(0, 'utf8'));
const displayed = present(order);
assert.equal(displayed.originalText, '1000');
assert.equal(displayed.couponDiscountText, '100');
assert.equal(displayed.amountText, '900');
const markup = fs.readFileSync('apps/boss-miniapp/native/pages/order-detail/index.wxml', 'utf8');
assert.match(markup, /服务原价/);
assert.match(markup, /优惠券抵扣/);
assert.match(markup, /couponDiscountText/);
console.log('Native order price breakdown matches actual order amounts');
