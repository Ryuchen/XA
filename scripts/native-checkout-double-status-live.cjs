// Real native checkout and picker controllers against isolated HTTP endpoints.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const storage = new Map(); let navigation, redirect;
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, stopPullDownRefresh() {}, showModal: value => value.success?.({ confirm: true }),
  navigateTo: value => { navigation = value; }, navigateBack() {}, redirectTo: value => { redirect = value.url; },
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
  },
};
require(path.join(root, 'core/auth')).setSession({ token: input.bossToken, user: { id: input.bossId, role: 'customer' } });
let definition; global.Page = value => { definition = value; };
function instance() { return { ...definition, _visible: true, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } }; }
require(path.join(root, 'pages/checkout/index.js'));
(async () => {
  const checkout = instance();
  checkout.onLoad({ service_id: input.serviceId, provider_ids: input.providerIds.slice(0, 2).join(',') });
  await checkout.load(); assert.ok(checkout.data.quote); assert.equal(checkout.data.selectionComplete, true);
  checkout.setData({ region: '隔离区', nickname: '隔离老板', remark: '保留备注' });
  const response = await fetch(`${input.base}/api/users/escorts/status/`, { method: 'POST',
    headers: { Authorization: 'Bearer ' + input.providerTokens[1], 'Content-Type': 'application/json' }, body: JSON.stringify({ status: 'OFFLINE' }) });
  assert.equal((await response.json()).code, 0);
  await checkout.onPullDownRefresh();
  assert.equal(checkout.data.hasUnavailableProvider, true);
  assert.deepEqual(checkout.data.providers.map(item => item.can_order), [true, false]);
  assert.equal(checkout.data.quote, null);
  checkout.chooseProvider();
  const params = Object.fromEntries(new URL(navigation.url, 'http://native.test').searchParams.entries());
  require(path.join(root, 'pages/companion-picker/index.js'));
  const picker = instance(); picker.onLoad(params);
  picker.getOpenerEventChannel = () => ({ emit: (event, value) => navigation.events[event](value) });
  await picker.load();
  assert.equal(picker.data.selectionReady, false);
  picker.removeSelected({ currentTarget: { dataset: { id: input.providerIds[1] } } });
  assert.equal(picker.data.selectionReady, false, 'one person does not complete a two-person package');
  picker.chooseProvider({ currentTarget: { dataset: { id: input.providerIds[2] } } });
  assert.equal(picker.data.selectionReady, true);
  await picker.confirmSelection(); await checkout.refreshQuote();
  assert.deepEqual(checkout.data.selectedProviderIds, [input.providerIds[0], input.providerIds[2]]);
  assert.equal(checkout.data.hasUnavailableProvider, false); assert.ok(checkout.data.quote);
  assert.deepEqual([checkout.data.region, checkout.data.nickname, checkout.data.remark], ['隔离区', '隔离老板', '保留备注']);
  await checkout.submit(); assert.match(redirect, /^\/pages\/order-detail\/index\?id=\d+$/);
  picker.onUnload(); checkout.onUnload();
  console.log('Offline teammate replaced, double package quoted and ordered through real APIs');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
