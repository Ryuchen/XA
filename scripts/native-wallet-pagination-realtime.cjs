// Native page controller, real isolated API, and Redis-backed WebSocket.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const storage = new Map(); let pongResolve, holdNextPage = false, releaseHeldPage;
const pong = new Promise(resolve => { pongResolve = resolve; });
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), showToast() {},
  request(options) { const url = new URL(options.url); fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
    ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) }).then(async response => {
      const payload = { statusCode: response.status, data: await response.json() };
      if (holdNextPage && url.pathname.endsWith('/wallet/transactions/') && url.searchParams.get('page') === '2') {
        holdNextPage = false; releaseHeldPage = () => options.success(payload); return;
      }
      options.success(payload);
    }).catch(options.fail); },
  connectSocket(options) { const target = new URL(options.url), ws = new WebSocket(input.wsBase.replace(/^http/, 'ws') + target.pathname + target.search);
    return { onOpen: handler => ws.addEventListener('open', handler), onMessage: handler => ws.addEventListener('message', event => { if (String(event.data).includes('pong')) pongResolve(); handler({ data: event.data }); }),
      onClose: handler => ws.addEventListener('close', handler), onError: handler => ws.addEventListener('error', handler), send: value => ws.send(value.data), close: () => ws.close() }; },
};
require(path.join(root, 'core/auth')).setSession({ token: input.bossToken, user: { id: String(input.bossId), role: 'customer' } });
let definition; global.Page = value => { definition = value; }; require(path.join(root, 'pages/wallet/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
const realtime = require(path.join(root, 'core/realtime'));
async function waitFor(predicate, description) { const deadline = Date.now() + 15000; while (Date.now() < deadline) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 50)); } throw Error(description + ': ' + JSON.stringify(page.data)); }
async function recharge(id) {
  const form = new FormData();
  for (const [key, value] of Object.entries({ account_id: input.bossId, amount: 100, gift_amount: 0, trade_no: id, client_request_id: id })) form.append(key, String(value));
  form.append('proof_image', new Blob([Buffer.from(input.image, 'base64')], { type: 'image/png' }), 'proof.png');
  const response = await fetch(input.base + '/api/admin/wallets/recharge/', { method: 'POST', headers: { Authorization: 'Bearer ' + input.staffToken }, body: form });
  const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
}
async function main() {
  page.onLoad(); page.onShow(); realtime.show();
  await Promise.race([pong, new Promise((_, reject) => setTimeout(() => reject(Error('WebSocket timeout')), 10000))]);
  page.filter({ currentTarget: { dataset: { value: 'TOPUP' } } });
  await waitFor(() => !page.data.loading && page.data.items.length === 20, 'initial wallet');
  await page.more(); assert.equal(page.data.items.length, 21);
  const oldest = page.data.items[20].id;
  await recharge('wallet-page-live');
  await waitFor(() => page.data.balance === '220' && !page.data.loading, 'updated balance');
  assert.equal(page.data.filter, 'TOPUP');
  assert.equal(page.data.items.length, 22, 'expanded history must remain visible after recharge');
  assert.ok(page.data.items.some(item => item.id === oldest), 'oldest visible transaction remains visible');
  page.filter({ currentTarget: { dataset: { value: 'TOPUP' } } });
  await waitFor(() => !page.data.loading && page.data.items.length === 20, 'reset to first page');
  // Delay delivery of a genuine page-two HTTP response to reproduce an in-flight request.
  holdNextPage = true;
  const pendingMore = page.more();
  await waitFor(() => !!releaseHeldPage, 'page-two response held');
  await recharge('wallet-page-live-during-more');
  await waitFor(() => page.data.balance === '230' && !page.data.loading && page.data.items.length === 23, 'refresh during load more');
  releaseHeldPage(); await pendingMore;
  assert.equal(page.data.balance, '230');
  assert.equal(page.data.filter, 'TOPUP');
  assert.equal(page.data.items.length, 23, 'late response must not replace the refreshed history');
  assert.equal(new Set(page.data.items.map(item => item.id)).size, 23);
  assert.ok(page.data.items.some(item => item.id === oldest));
  console.log('Real recharge preserved expanded history, including during an in-flight page request');
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; }).finally(() => { realtime.hide(); page.onUnload(); });
