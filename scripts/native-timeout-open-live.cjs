// Native controller with real isolated HTTP and WebSocket; only wx transport is adapted.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const storage = new Map(); let pongResolve, updatedResolve;
const pong = new Promise(resolve => { pongResolve = resolve; });
const updated = new Promise(resolve => { updatedResolve = resolve; });
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, showModal() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async res => options.success({ statusCode: res.status, data: await res.json() })).catch(options.fail);
  },
  connectSocket(options) {
    const target = new URL(options.url);
    const ws = new WebSocket(input.base.replace(/^http/, 'ws') + target.pathname + target.search);
    return {
      onOpen: handler => ws.addEventListener('open', handler),
      onMessage: handler => ws.addEventListener('message', event => { if (String(event.data).includes('pong')) pongResolve(); handler({ data: event.data }); }),
      onClose: handler => ws.addEventListener('close', handler), onError: handler => ws.addEventListener('error', handler),
      send: value => ws.send(value.data), close: () => ws.close(),
    };
  },
};
const auth = require(path.join(root, 'core/auth'));
auth.setSession({ token: input.token, user: { id: String(input.bossId), role: 'customer' } });
let definition; global.Page = value => { definition = value; };
require(path.join(root, 'pages/order-detail/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(patch) {
  Object.assign(this.data, patch);
  if (this.data.order?.status === 'CANCELLED' && this.data.order.payment_status === 'REFUNDED') updatedResolve();
} };
const realtime = require(path.join(root, 'core/realtime'));
async function main() {
  page.onLoad({ id: input.orderId }); page.onShow(); await page.load();
  assert.equal(page.data.order.status, 'PENDING');
  assert.equal(page.data.order.canCancel, true);
  realtime.show();
  await Promise.race([pong, new Promise((_, reject) => setTimeout(() => reject(Error('native websocket did not connect')), 10000))]);
  fs.writeFileSync(input.ready, 'ready');
  await Promise.race([updated, new Promise((_, reject) => { const timer = setTimeout(() => reject(Error('native order did not refresh')), 60000); timer.unref(); })]);
  assert.equal(page.data.order.statusText, '已取消');
  assert.equal(page.data.order.canCancel, false);
  assert.ok(page.data.order.timeline.some(row => row.title === '退款已入账'));
  const messages = await require(path.join(root, 'core/api')).request('/messages/?type=ORDER');
  assert.ok(messages.messages.some(row => row.related_order_id === input.orderId && row.title === '订单超时已取消'));
  console.log('Open native controller refreshed cancellation, refund and message');
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; }).finally(() => { realtime.hide(); page.onUnload(); });
