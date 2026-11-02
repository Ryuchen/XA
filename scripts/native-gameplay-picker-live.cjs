// Run native page controllers with real HTTP; only the WeChat host is adapted.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map();
let navigation = '', queue = Promise.resolve();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({keys: [...storage.keys()]}),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), showToast() {},
  showModal: options => { if (options.success) options.success({ confirm: true }); },
  navigateTo: options => { navigation = options.url; }, redirectTo: options => { navigation = options.url; },
  request(options) {
    const url = new URL(options.url); const target = input.base + url.pathname + url.search;
    const send = () => fetch(target, { method: options.method, headers: options.header,
      ...(options.data !== undefined ? { body: JSON.stringify(options.data) } : {}) })
      .then(async res => options.success({statusCode: res.status, data: await res.json()})).catch(options.fail);
    // SQLite's in-memory test connection is shared with the live server.
    queue = queue.then(send, send);
  },
};
function page(name, options) {
  let definition; global.Page = value => { definition = value; };
  require(path.join(root, 'pages', name, 'index.js'));
  const result = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(result)) if (typeof value === 'function') result[key] = value.bind(result);
  result.onLoad(options); return result;
}
async function main() {
  require(path.join(root, 'core/auth')).setSession(input.session);
  if (input.legacy) {
    const picker = page('companion-picker', {service_id: input.service});
    picker.onShow(); await picker.load();
    assert.equal(picker.data.error, '');
    assert.ok(picker.data.visible.some(row => row.id === input.provider && row.can_order));
    picker.chooseProvider({currentTarget: {dataset: {id: input.provider}}});
    assert.equal(picker.data.selectionReady, true);
    await picker.confirmSelection();
    assert.match(navigation, /checkout\/index/);
    const checkout = page('checkout', {service_id: input.service, provider_id: input.provider});
    await checkout.load();
    assert.equal(checkout.data.error, ''); assert.equal(checkout.data.quote.amount, 10000);
    checkout.setData({ region: '测试区', nickname: '测试老板' });
    await checkout.submit(); assert.match(navigation, /order-detail\/index\?id=\d+/);
    console.log(JSON.stringify({order: Number(navigation.split('id=')[1])}));
    picker.onUnload(); checkout.onUnload();
    return;
  }
  const companion = page('companion-detail', {id: input.provider});
  await companion.load(); companion.select();
  assert.match(navigation, /pages\/catalog\/index/);
  const catalog = page('catalog', {});
  catalog.onShow(); await catalog.load();
  assert.equal(catalog.data.error, '');
  const family = catalog.data.displayed.find(row => row.id === input.service);
  assert.ok(family, JSON.stringify(catalog.data.displayed));
  assert.equal(family.variantCount, 2);
  assert.ok(!catalog._services.some(row => row.id === input.unsupported));
  catalog.open({currentTarget: {dataset: {id: input.service}}});
  assert.match(navigation, new RegExp('pages/service/index\\?id=' + input.service + '&provider_id=' + input.provider));
  const service = page('service', {id: input.service, provider_id: input.provider});
  await service.load();
  assert.equal(service.data.error, ''); assert.equal(service.data.service.id, input.service);
  assert.equal(service.data.variantTotal, 2);
  service.checkout(); assert.match(navigation, /checkout\/index/);
  const checkout = page('checkout', {service_id: input.service, provider_id: input.provider});
  await checkout.load();
  assert.equal(checkout.data.error, ''); assert.equal(checkout.data.quote.amount, 10000);
  checkout.setData({ region: '测试区', nickname: '测试老板' });
  await checkout.submit(); assert.match(navigation, /order-detail\/index\?id=\d+/);
  console.log(JSON.stringify({order: Number(navigation.split('id=')[1]), service: input.service, provider: input.provider}));
  companion.onUnload(); catalog.onUnload(); service.onUnload(); checkout.onUnload();
}
main().catch(error => { console.error(error); process.exitCode = 1; });
