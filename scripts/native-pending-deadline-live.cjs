// Exercise the native page with the real isolated Django order API.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  getStorageInfoSync: () => ({keys: [...storage.keys()]}), removeStorageSync: key => storage.delete(key),
  getAccountInfoSync: () => ({miniProgram: {envVersion: 'develop'}}),
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, {method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : {body: JSON.stringify(options.data)})})
      .then(async response => options.success({statusCode: response.status, data: await response.json()}))
      .catch(error => options.fail(error));
  },
};
async function main() {
  require(path.join(root, 'core/auth')).setSession(input.session);
  let definition;
  global.Page = page => { definition = page; };
  require(path.join(root, 'pages/order-detail/index.js'));
  const page = {...definition, data: structuredClone(definition.data), setData(patch) {Object.assign(this.data, patch);}};
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({id: input.orderId}); page._visible = true;
  require(path.join(root, 'pages/orders/index.js'));
  const list = {...definition, data: structuredClone(definition.data), setData(patch) {Object.assign(this.data, patch);}};
  for (const [key, value] of Object.entries(list)) if (typeof value === 'function') list[key] = value.bind(list);
  list.onLoad(); list._visible = true;
  try {
    await page.load(); await list.load();
    assert.equal(page.data.error, '');
    assert.equal(page.data.order.pending_deadline_expired, false);
    assert.equal(page.data.order.canSelectProvider, true);
    assert.equal(list.data.items.find(item => item.id === input.orderId).pending_deadline_expired, false);
    const limit = Date.now() + 10000;
    while ((!page.data.order?.pending_deadline_expired || !list.data.items.find(item => item.id === input.orderId)?.pending_deadline_expired) && Date.now() < limit)
      await new Promise(resolve => setTimeout(resolve, 100));
    assert.equal(page.data.order.pending_deadline_expired, true, 'the open page must reread server state after the deadline');
    assert.equal(page.data.order.statusText, '已到接单期限，等待处理');
    assert.equal(page.data.order.canSelectProvider, false);
    assert.equal(page.data.order.status, 'PENDING', 'the page must not cancel the order itself');
    const card = list.data.items.find(item => item.id === input.orderId);
    assert.equal(card.pending_deadline_expired, true, 'the open order list must reread server state after the deadline');
    assert.equal(card.statusText, '已到接单期限，等待处理');
    assert.equal(card.status, 'PENDING');
  } finally {page.onUnload(); list.onUnload();}
  console.log('native pending deadline rereads real order state');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
