// Exercise the real native page logic and live API; wx supplies host APIs only.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map(), toasts = [];
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), stopPullDownRefresh() {},
  showToast: value => toasts.push(value.title),
  showModal: value => value.success({ confirm: true, content: '服务中希望协商处理' }),
  request(options) { (async () => { try {
    const url = new URL(options.url), response = await fetch(input.base + url.pathname + url.search, {
      method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }),
    });
    options.success({ statusCode: response.status, data: await response.json() });
  } catch (error) { options.fail(error); } })(); },
};
let definition; global.Page = value => { definition = value; };
require(path.join(root, 'core/auth')).setSession({ token: input.token, user: { id: input.bossId, role: 'customer' } });
require(path.join(root, 'pages/order-detail/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
page.onLoad({ id: input.orderId }); page._visible = true;
(async () => {
  await page.load();
  if (input.expectedReplenishment) {
    assert.equal(page.data.order.refundRecords[0].replenishmentText, input.expectedReplenishment);
    page.onUnload();
    console.log('Native order page loaded replenishment status from the real API');
    return;
  }
  assert.equal(page.data.order.status, 'IN_SERVICE');
  assert.equal(page.data.order.canAfterSale, true);
  await page.aftersale();
  assert.ok(toasts.includes('售后已提交'));
  const response = await fetch(`${input.base}/api/orders/orders/${input.orderId}/aftersale/`, {
    headers: { Authorization: `Bearer ${input.token}` },
  });
  const result = await response.json();
  assert.equal(result.code, 0); assert.equal(result.data.status, 'OPEN');
  assert.equal(result.data.reason, '服务中希望协商处理');
  require(path.join(root, 'pages/orders/index.js'));
  const list = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(list)) if (typeof value === 'function') list[key] = value.bind(list);
  list.onLoad();
  await list.onShow();
  const row = list.data.items.find(item => item.id === input.orderId);
  assert.ok(row, 'submitted order remains visible in the order list');
  assert.equal(row.aftersaleProgress, '售后处理中');
  assert.equal(row.statusText, '服务中');
  list.onUnload();
  page.onUnload();
  console.log('Native page submitted in-service aftersale to the real API');
})().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
