// Real order APIs; only native modal completion order is controlled.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve('apps/boss-miniapp/native'), storage = new Map(), modals = [], toasts = [];
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast: value => toasts.push(value.title), showModal: value => modals.push(value), stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
  },
};
async function waitFor(predicate) {
  for (let i = 0; i < 250; i++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
  throw Error('Timed out waiting for order page');
}
async function main() {
  const auth = require(path.join(root, 'core/auth')); auth.setSession(input.first);
  let definition; global.Page = value => { definition = value; };
  require(path.join(root, 'pages/order-detail/index.js'));
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({ id: input.orderId }); page._visible = true; await page.load();
  assert.equal(page.data.order.canCancel, true);
  const old = page.cancel(); await waitFor(() => modals.length === 1);
  auth.setSession(input.second); await waitFor(() => !page.data.loading);
  assert.equal(page.data.busy, false);
  auth.setSession(input.first); await waitFor(() => !page.data.loading && page.data.order?.canCancel);
  const current = page.cancel(); await waitFor(() => modals.length === 2);
  modals[0].success({ confirm: false }); await old;
  assert.equal(page.data.busy, true, 'old modal must not unlock the current operation');
  modals[1].success({ confirm: true });
  await waitFor(() => modals.length === 3);
  modals[2].success({ confirm: true, content: '行程调整' }); await current;
  assert.equal(page.data.order.status, 'CANCELLED'); assert.equal(page.data.busy, false);
  assert.equal(toasts.filter(item => item.includes('订单已取消')).length, 1);
  page.onUnload(); console.log('Real order cancellation survives account switch and overlapping modal completions');
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
