// The order input comes from the real customer API in the isolated workflow.
const fs = require('node:fs'), assert = require('node:assert/strict');
const { present } = require('../apps/boss-miniapp/native/core/orders');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const result = present(input);
assert.equal(result.teamProgressNeedsReview, true);
assert.equal(result.status, 'IN_SERVICE');
assert.equal(result.participants.length, 2);
const template = fs.readFileSync('apps/boss-miniapp/native/pages/order-detail/index.wxml', 'utf8');
assert.match(template, /order\.teamProgressNeedsReview/);
assert.match(template, /双陪服务进度可联系订单客服核对/);
