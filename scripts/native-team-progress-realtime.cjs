// Native page controller uses real HTTP, WebSocket and isolated business state.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const storage = new Map(); let pongResolve;
const pong = new Promise(resolve => { pongResolve = resolve; });
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), showToast() {}, showModal() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async res => options.success({ statusCode: res.status, data: await res.json() })).catch(options.fail);
  },
  connectSocket(options) {
    const target = new URL(options.url), ws = new WebSocket(input.base.replace(/^http/, 'ws') + target.pathname + target.search);
    return { onOpen: handler => ws.addEventListener('open', handler),
      onMessage: handler => ws.addEventListener('message', event => { if (String(event.data).includes('pong')) pongResolve(); handler({ data: event.data }); }),
      onClose: handler => ws.addEventListener('close', handler), onError: handler => ws.addEventListener('error', handler),
      send: value => ws.send(value.data), close: () => ws.close() };
  },
};
const auth = require(path.join(root, 'core/auth'));
auth.setSession({ token: input.token, user: { id: String(input.bossId), role: 'customer' } });
let definition; global.Page = value => { definition = value; };
require(path.join(root, 'pages/order-detail/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
const realtime = require(path.join(root, 'core/realtime'));
async function waitProgress(stage, count, status) {
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline) {
    const order = page.data.order, progress = order?.team_progress;
    if (order?.status === status && progress?.stage === stage && progress.submitted_count === count && progress.total_count === 2) return progress;
    await new Promise(resolve => setTimeout(resolve, 50));
  }
  throw Error(`Native page did not refresh ${stage} ${count}/2: ${JSON.stringify(page.data.order?.team_progress)}`);
}
async function main() {
  page.onLoad({ id: input.orderId }); page.onShow(); await page.load();
  await waitProgress('ENTRY', 0, 'GRABBED'); realtime.show();
  await Promise.race([pong, new Promise((_, reject) => setTimeout(() => reject(Error('native websocket did not connect')), 10000))]);
  fs.writeFileSync(input.ready, 'ready');
  let progress = await waitProgress('ENTRY', 1, 'GRABBED');
  assert.ok(progress.members.some(member => member.nickname === '陪玩甲' && member.submitted));
  fs.writeFileSync(input.entrySeen, 'ready');
  await waitProgress('COMPLETION', 0, 'IN_SERVICE'); fs.writeFileSync(input.startedSeen, 'ready');
  progress = await waitProgress('COMPLETION', 1, 'IN_SERVICE');
  assert.ok(progress.members.some(member => member.nickname === '陪玩甲' && member.submitted));
  fs.writeFileSync(input.completionSeen, 'ready');
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline && page.data.order?.status !== 'COMPLETED') await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(page.data.order?.status, 'COMPLETED');
  assert.equal(page.data.order.statusText, '已完成');
  assert.equal(page.data.order.team_progress, null);
  assert.ok(page.data.order.timeline.some(item => item.title === '服务已完成'));
  console.log('Native order detail refreshed team evidence and completed order via actual WebSocket');
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; }).finally(() => { realtime.hide(); page.onUnload(); });
