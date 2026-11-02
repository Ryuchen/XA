// The native checkout controller uses the actual isolated order APIs.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve('apps/boss-miniapp/native'), storage = new Map(), modals = [];
let confirm = false, redirect = '', transport = Promise.resolve();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  getStorageInfoSync: () => ({ keys: [...storage.keys()] }), removeStorageSync: key => storage.delete(key),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), showToast() {},
  showModal: options => { modals.push(options); options.success?.({ confirm }); },
  redirectTo: options => { redirect = options.url; }, stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    transport = transport.then(async () => {
      try {
        const response = await fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
          ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) });
        options.success({ statusCode: response.status, data: await response.json() });
      } catch (error) { options.fail(error); }
    });
  },
};
async function main() {
  const auth = require(path.join(root, 'core/auth')); auth.setSession(input.session);
  let definition; global.Page = value => { definition = value; };
  require(path.join(root, 'pages/checkout/index.js'));
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({ service_id: input.serviceId, provider_ids: input.providers.join(',') }); page._visible = true; await page.load();
  assert.equal(page.data.error, ''); assert.equal(page.data.selectionComplete, true);
  page.input({ currentTarget: { dataset: { field: 'region' } }, detail: { value: '隔离大区' } });
  page.input({ currentTarget: { dataset: { field: 'nickname' } }, detail: { value: '隔离老板' } });
  await page.submit();
  assert.equal(redirect, ''); assert.equal(modals.length, 1);
  for (const name of input.names) assert.ok(modals[0].content.includes(name), modals[0].content);
  assert.match(modals[0].content, /支付/);
  confirm = true; await page.submit();
  assert.equal(modals.length, 2); assert.match(redirect, /^\/pages\/order-detail\/index\?id=\d+$/);
  page.onUnload();
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
