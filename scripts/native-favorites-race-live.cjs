// Real native page and HTTP backend; only network delivery order is controlled.
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const root = path.resolve('apps/boss-miniapp/native');
const storage = new Map();
let hold = '', held = null;
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => {
        const data = await response.json();
        const deliver = () => options.success({ statusCode: response.status, data });
        if (hold === options.method && url.pathname.includes('/orders/favorites/')) { hold = ''; held = deliver; }
        else deliver();
      }).catch(options.fail);
  },
};
async function waitFor(predicate) {
  for (let i = 0; i < 250; i++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
  throw new Error('Timed out waiting for real HTTP response');
}
async function main() {
  const auth = require(path.join(root, 'core/auth'));
  auth.setSession(input.first);
  let definition; global.Page = value => { definition = value; };
  require(path.join(root, 'pages/favorites/index.js'));
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({}); page._visible = true; await page.load();
  assert.deepEqual(page.data.items.map(row => row.id), [input.serviceId]);
  hold = 'GET'; const oldLoad = page.load(); await waitFor(() => held);
  await page.remove({ currentTarget: { dataset: { id: input.serviceId } } });
  assert.equal(page.data.items.length, 0);
  held(); held = null; await oldLoad;
  assert.equal(page.data.items.length, 0, 'older list response must not restore a removed favorite');
  // Re-add through the real API, then delay the committed delete's response across a login switch.
  const response = await fetch(input.base + '/api/orders/favorites/toggle/', { method: 'POST',
    headers: { Authorization: `Bearer ${input.first.token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ service_id: input.serviceId }) });
  assert.equal((await response.json()).code, 0);
  await page.load();
  hold = 'DELETE'; const removing = page.remove({ currentTarget: { dataset: { id: input.serviceId } } });
  await waitFor(() => held);
  auth.setSession(input.second);
  await waitFor(() => !page.data.loading && page.data.items.length === 1);
  held(); held = null; await removing;
  assert.deepEqual(page.data.items.map(row => row.id), [input.serviceId], 'previous account response must not change new account list');
  assert.equal(page.data.removing, 0);
  page.onUnload();
  console.log('Favorites real HTTP refresh and account switch passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
