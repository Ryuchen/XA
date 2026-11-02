// Native owner page and native realtime adapter, wired to isolated HTTP/WebSocket.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const storage = new Map();
let connected;
const opened = new Promise(resolve => { connected = resolve; });
const peerSockets = [], peerEvents = [[], []];
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), showToast() {}, stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.api + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
  },
  connectSocket({ url }) {
    const parsed = new URL(url), base = new URL(input.api);
    const socket = new WebSocket(`ws://${base.host}${parsed.pathname}${parsed.search}`);
    return {
      onOpen(fn) { socket.addEventListener('open', () => { fn(); connected(); }); },
      onMessage(fn) { socket.addEventListener('message', event => fn({ data: event.data })); },
      onClose(fn) { socket.addEventListener('close', fn); }, onError(fn) { socket.addEventListener('error', fn); },
      send({ data }) { socket.send(data); }, close() { socket.close(); },
    };
  },
};
let definition;
global.Page = value => { definition = value; };
require(path.join(root, 'core/auth')).setSession({ token: input.bossToken, user: { id: input.bossId, role: 'customer' } });
const realtime = require(path.join(root, 'core/realtime'));
require(path.join(root, 'pages/order-detail/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
page.onLoad({ id: input.orderId }); page._visible = true;
async function waitFor(predicate, message) {
  for (let i = 0; i < 160; i++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 25)); }
  throw Error(message + ': ' + JSON.stringify(page.data.order));
}
(async () => {
  await page.load();
  assert.equal(page.data.order.providerDisplay, input.oldName);
  realtime.show(); await opened;
  await waitFor(() => !page.data.loading && page.data.order?.providerDisplay === input.oldName, 'initial order not loaded');
  for (const [index, token] of [input.oldToken, input.newToken].entries()) {
    const socket = new WebSocket(`ws://${new URL(input.api).host}/ws/orders/?token=${encodeURIComponent(token)}`);
    peerSockets.push(socket);
    socket.addEventListener('message', event => peerEvents[index].push(JSON.parse(event.data)));
    await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  }
  const response = await fetch(`${input.api}/api/admin/orders/${input.orderId}/transfer/`, {
    method: 'POST', headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ transfers: [{ old_provider_id: input.oldId, new_provider_id: input.newId, split_type: 'PERCENT', split_value: 40, penalty_amount: 0 }] }),
  });
  const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
  await waitFor(() => !page.data.loading && page.data.order?.providerDisplay === input.newName, 'owner did not refresh after transfer');
  assert.equal(page.data.order.status, 'IN_SERVICE');
  assert.deepEqual(page.data.order.participants.map(person => person.id), [input.newId]);
  assert.equal(page.data.order.my_assignment_settled, null);
  for (const [index, expected] of [true, false].entries()) {
    await waitFor(() => peerEvents[index].some(event => event.type === 'order_status_update' && event.data?.id === input.orderId), 'provider did not receive transfer');
    const event = peerEvents[index].find(item => item.type === 'order_status_update' && item.data?.id === input.orderId);
    assert.equal(event.data.my_assignment_settled, expected);
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; }).finally(() => { peerSockets.forEach(socket => socket.close()); realtime.hide(); page.onUnload(); });
