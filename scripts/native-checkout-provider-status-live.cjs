// Real checkout controller and real APIs, with a transport adapter for wx.request.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const storage = new Map(); let navigation;
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, stopPullDownRefresh() {},
  navigateTo(options) { navigation = options; },
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
  },
};
require(path.join(root, 'core/auth')).setSession({ token: input.bossToken, user: { id: input.bossId, role: 'customer' } });
let definition; global.Page = value => { definition = value; };
require(path.join(root, 'pages/checkout/index.js'));
const page = { ...definition, _visible: true, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
async function status(value) {
  const response = await fetch(`${input.base}/api/users/escorts/status/`, { method: 'POST',
    headers: { Authorization: 'Bearer ' + input.providerToken, 'Content-Type': 'application/json' }, body: JSON.stringify({ status: value }) });
  const data = await response.json(); assert.equal(data.code, 0, JSON.stringify(data));
}
(async () => {
  page.onLoad({ service_id: input.serviceId, provider_id: input.providerId });
  await page.load(); assert.equal(page.data.error, ''); assert.ok(page.data.quote);
  page.setData({ region: '隔离区', nickname: '隔离老板', remark: '保留备注' });
  await status('OFFLINE'); await page.onPullDownRefresh();
  assert.equal(page.data.hasUnavailableProvider, true);
  assert.equal(page.data.providers[0].can_order, false);
  assert.ok(page.data.providers[0].order_unavailable_reason);
  assert.equal(page.data.quote, null); assert.ok(page.data.quoteError);
  assert.deepEqual([page.data.region, page.data.nickname, page.data.remark], ['隔离区', '隔离老板', '保留备注']);
  const offlinePage = { ...definition, _visible: true, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  offlinePage.onLoad({ service_id: input.serviceId, provider_id: input.providerId });
  await offlinePage.load();
  assert.equal(offlinePage.data.error, ''); assert.ok(offlinePage.data.service);
  assert.equal(offlinePage.data.hasUnavailableProvider, true); assert.equal(offlinePage.data.quote, null);
  offlinePage.setData({ region: '已填大区', nickname: '已填昵称' });
  offlinePage.chooseProvider();
  assert.match(navigation.url, /companion-picker/);
  navigation.events.providersSelected([]); await offlinePage.refreshQuote();
  assert.equal(offlinePage.data.hasUnavailableProvider, false); assert.ok(offlinePage.data.quote);
  assert.deepEqual([offlinePage.data.region, offlinePage.data.nickname], ['已填大区', '已填昵称']);
  offlinePage.onUnload();
  await status('AVAILABLE'); await page.onPullDownRefresh();
  assert.equal(page.data.hasUnavailableProvider, false);
  assert.equal(page.data.providers[0].can_order, true);
  assert.ok(page.data.quote); assert.equal(page.data.quoteError, '');
  page.onUnload(); console.log('Provider availability refreshed while preserving checkout input');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
