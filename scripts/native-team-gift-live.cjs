// Exercise the actual gift page controller with a completed order and real APIs.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve('apps/boss-miniapp/native'), storage = new Map();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), showToast() {},
  showModal: options => options.success({ confirm: true }), stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
  },
};
require(path.join(root, 'core/auth')).setSession(input.session);
let definition; global.Page = value => { definition = value; };
require(path.join(root, 'pages/gifts/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(values) { Object.assign(this.data, values); } };
(async () => {
  page.onLoad({ order_id: input.orderId }); await page.load();
  assert.deepEqual(page.data.recipients.map(item => item.id), input.providerIds);
  assert.equal(page.data.recipientIndex, -1);
  page.select({ currentTarget: { dataset: { id: input.giftId } } });
  assert.equal(page.data.enough, false, 'multi-provider gift requires a deliberate selection');
  page.chooseRecipient({ currentTarget: { dataset: { index: 1 } } });
  assert.equal(page.data.recipientName, '收礼陪玩乙');
  await page.load();
  assert.equal(page.data.recipientName, '收礼陪玩乙', 'selection remains after refresh');
  assert.equal(page.data.enough, true);
  await page.send();
  assert.equal(page.data.receipt.recipient, '收礼陪玩乙');
  page.onUnload();
  console.log('Completed team order gift selected and sent to the second provider');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
