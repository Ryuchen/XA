// Real native service and favorites pages against an isolated HTTP server.
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const root = path.resolve('apps/boss-miniapp/native');
const storage = new Map();
let holdDetail = false, held = null, holdToggle = false, sendToggle = null, loseToggle = false;
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    const send = () => fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => {
        const data = await response.json();
        const deliver = () => options.success({ statusCode: response.status, data });
        if (holdDetail && url.pathname === `/api/orders/services/${input.serviceId}/`) { holdDetail = false; held = deliver; }
        else if (loseToggle && url.pathname.endsWith('/orders/favorites/toggle/')) { loseToggle = false; options.fail(new Error('Response lost after server commit')); }
        else deliver();
      }).catch(options.fail);
    if (holdToggle && url.pathname.endsWith('/orders/favorites/toggle/')) { holdToggle = false; sendToggle = send; }
    else send();
  },
};
async function waitFor(predicate) {
  for (let i = 0; i < 250; i++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
  throw new Error('Timed out waiting for real HTTP response');
}
function page(name) {
  let definition; global.Page = value => { definition = value; };
  require(path.join(root, 'pages', name, 'index.js'));
  const instance = { ...definition, data: structuredClone(definition.data), setData(patch) {
    for (const [key, value] of Object.entries(patch)) {
      const parts = key.split('.'); let target = this.data;
      for (const part of parts.slice(0, -1)) target = target[part] ||= {};
      target[parts.at(-1)] = value;
    }
  } };
  for (const [key, value] of Object.entries(instance)) if (typeof value === 'function') instance[key] = value.bind(instance);
  return instance;
}
async function main() {
  const auth = require(path.join(root, 'core/auth')); auth.setSession(input.session);
  const service = page('service'); service.onLoad({ id: input.serviceId }); service._visible = true; await service.load();
  assert.equal(service.data.service.is_favorited, false);
  holdToggle = true; const adding = service.favorite(); await waitFor(() => sendToggle);
  holdDetail = true; const stale = service.load(); await waitFor(() => held);
  sendToggle(); sendToggle = null;
  await adding;
  held(); held = null; await stale;
  assert.equal(service.data.service.is_favorited, true, 'stale detail must not undo favorite');
  loseToggle = true;
  await service.favorite();
  assert.equal(service.data.service.is_favorited, false, 'lost response must be reconciled from server');
  assert.equal(service.data.favoriteBusy, false);
  await service.favorite();
  assert.equal(service.data.service.is_favorited, true);
  const favorites = page('favorites'); favorites.onLoad({}); favorites._visible = true; await favorites.load();
  assert.deepEqual(favorites.data.items.map(row => row.id), [input.serviceId]);
  await favorites.remove({ currentTarget: { dataset: { id: input.serviceId } } });
  service.onShow(); await waitFor(() => !service.data.refreshing);
  assert.equal(service.data.service.is_favorited, false, 'returning to detail reflects list removal');
  favorites.onUnload(); service.onUnload();
  console.log('Real service favorite and list reconciliation passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
