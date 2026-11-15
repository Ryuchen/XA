// Render real wallet API rows produced by the isolated referral/refund flow.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const rows = JSON.parse(fs.readFileSync(0, 'utf8'));
let page;
global.Page = definition => { page = definition; };
require(path.resolve('apps/boss-miniapp/native/pages/wallet/index.js'));
const rendered = page.format(rows);
const credit = rendered.find(row => row.is_referral_income && !row.is_reversal);
const reversal = rendered.find(row => row.is_referral_income && row.is_reversal);
assert.ok(credit && reversal, 'real referral credit and refund must both be present');
assert.equal(credit.title, '推荐收益');
assert.equal(reversal.title, '推荐收益退回');
assert.ok(credit.amount > 0 && reversal.amount < 0);
assert.equal(credit.amount + reversal.amount, 0);
assert.ok(page.data.filters.some(filter => filter.value === 'INCOME'));
console.log('Real referral credit and refund render clearly in the native wallet');
