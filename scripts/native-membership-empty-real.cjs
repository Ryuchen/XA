// Exercise the native page against the real membership endpoint with no tier configuration.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const x = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native'), storage = new Map();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, stopPullDownRefresh() {},
  request(options) { const url = new URL(options.url); fetch(x.base + url.pathname + url.search, {
    method: options.method, headers: options.header,
    ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }),
  }).then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail); },
};
let definition; global.Page = value => { definition = value; };
require(path.join(root, 'core/auth')).setSession({ token: x.token, user: { id: x.account, role: 'customer' } });
require(path.join(root, 'pages/membership/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(patch) {
  for (const [key, value] of Object.entries(patch)) assert.notEqual(value, undefined, `undefined page data: ${key}`);
  Object.assign(this.data, patch);
} };
for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
(async () => {
  page.onLoad({}); page._visible = true; await page.load();
  assert.equal(page.data.error, ''); assert.deepEqual(page.data.member.tiers, []);
  assert.equal(page.data.member.vip, null); assert.equal(page.data.tier, null); assert.equal(page.data.progress, 0);
  page.onUnload(); console.log('Native membership page handles actual empty tier configuration');
})().then(() => process.exit(0)).catch(error => { console.error(error.stack || error); process.exit(1); });
