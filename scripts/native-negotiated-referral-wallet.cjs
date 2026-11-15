const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const rows=JSON.parse(fs.readFileSync(0,'utf8'));let page;global.Page=value=>page=value;
require(path.resolve('apps/boss-miniapp/native/pages/wallet/index.js'));
const shown=page.format(rows);assert.equal(shown.length,1);assert.equal(shown[0].title,'推荐收益');assert.equal(shown[0].displayAmount,'+50');
console.log('Real retained referral income is labeled in the native wallet');
