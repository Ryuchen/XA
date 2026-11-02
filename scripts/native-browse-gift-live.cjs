// Real gift page controller and HTTP APIs; only the native confirmation UI is simulated.
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
  page.onLoad({}); await page.load();
  assert.equal(page.data.recipientIndex, -1);
  assert.equal(page.data.pickerRecipients[0].nickname, '请选择收礼陪玩');
  page.setData({ recipientSearch: '收礼陪玩甲' });
  await page.load();
  assert.equal(page.data.recipients.length, 1);
  assert.equal(page.data.pickerRecipients[1].id, input.providerId);
  page.select({ currentTarget: { dataset: { id: input.giftId } } });
  assert.equal(page.data.enough, false);
  page.recipient({ detail: { value: 1 } });
  assert.equal(page.data.recipientName, '收礼陪玩甲');
  assert.equal(page.data.enough, true);
  await page.load();
  assert.equal(page.data.recipientIndex, 0, 'selected recipient survives refresh');
  await page.send();
  assert.equal(page.data.receipt.recipient, '收礼陪玩甲');
  page.onUnload();
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
