// Native owner controller against real isolated Django HTTP and business state.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), showToast() {}, stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() }))
      .catch(options.fail);
  },
};
let definition;
global.Page = value => { definition = value; };
require(path.join(root, 'core/auth')).setSession(input.session);
require(path.join(root, 'pages/order-detail/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
page.onLoad({ id: input.orderId }); page._visible = true;
async function post(token, url, body) {
  const response = await fetch(input.base + url, { method: 'POST', headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const data = await response.json(); assert.equal(data.code, 0, JSON.stringify(data)); return data.data;
}
(async () => {
  await page.load();
  assert.equal(page.data.order.canAfterSale, false);
  assert.equal(page.data.order.aftersaleUnavailableNote, '暂不可在线申请售后，可联系订单客服核对。');
  await post(input.providerToken, `/api/wallet/reports/${input.reportId}/submit/`, {});
  await post(input.staffToken, `/api/admin/reports/${input.reportId}/approve/`, {});
  await page.load();
  assert.equal(page.data.order.canAfterSale, true, 'settled order can reopen an aftersale under existing rules');
  assert.equal(page.data.order.aftersaleUnavailableNote, '');
  page.onUnload();
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
