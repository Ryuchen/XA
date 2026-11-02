// Real native wallet controller against isolated HTTP and WebSocket.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const storage = new Map(); let pongResolve, redirect, deferredModal; let pauseConfirmation = true;
const pong = new Promise(resolve => { pongResolve = resolve; });
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), showToast() {},
  showModal: options => { if (pauseConfirmation && options.title === '确认余额支付') { deferredModal = options; fs.writeFileSync(input.confirmationReady, 'ready'); } else options.success?.({ confirm: true }); }, redirectTo: options => { redirect = options.url; },
  request(options) { const url = new URL(options.url); fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
    ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) }).then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail); },
  connectSocket(options) { const target = new URL(options.url), ws = new WebSocket(input.base.replace(/^http/, 'ws') + target.pathname + target.search);
    return { onOpen: handler => ws.addEventListener('open', handler), onMessage: handler => ws.addEventListener('message', event => { if (String(event.data).includes('pong')) pongResolve(); handler({ data: event.data }); }),
      onClose: handler => ws.addEventListener('close', handler), onError: handler => ws.addEventListener('error', handler), send: value => ws.send(value.data), close: () => ws.close() }; },
};
require(path.join(root, 'core/auth')).setSession({ token: input.token, user: { id: String(input.bossId), role: 'customer' } });
let definition; global.Page = value => { definition = value; }; require(path.join(root, 'pages/wallet/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
const realtime = require(path.join(root, 'core/realtime'));
async function waitFor(balance, types) {
  const until = Date.now() + 20000;
  while (Date.now() < until) { if (page.data.balance === balance && types.every(type => page.data.items.some(row => row.tx_type === type))) return; await new Promise(resolve => setTimeout(resolve, 50)); }
  throw Error(`wallet did not update to ${balance}: ${JSON.stringify(page.data)}`);
}
async function main() {
  page.onLoad(); page.onShow(); await page.load(); assert.equal(page.data.balance, '0');
  require(path.join(root, 'pages/checkout/index.js'));
  const checkout = { ...definition, _visible: true, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  checkout.onLoad({ service_id: input.serviceId }); await checkout.load();
  assert.ok(checkout.data.quote, checkout.data.quoteError);
  assert.equal(checkout.data.quote.balanceText, '0');
  assert.equal(checkout.data.quote.balance_sufficient, false);
  checkout.setData({ region: '隔离大区', nickname: '隔离老板' });
  realtime.show(); await Promise.race([pong, new Promise((_, reject) => setTimeout(() => reject(Error('wallet websocket did not connect')), 10000))]);
  fs.writeFileSync(input.ready, 'ready');
  await waitFor('1100', ['TOPUP', 'GIFT']);
  const deadline = Date.now() + 20000;
  while (Date.now() < deadline && checkout.data.quote?.balanceText !== '1100') await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(checkout.data.quote?.balanceText, '1100');
  assert.equal(checkout.data.quote.balance_sufficient, true);
  assert.deepEqual([checkout.data.region, checkout.data.nickname], ['隔离大区', '隔离老板']);
  const cancelled = checkout.submit();
  await waitFor('2200', ['TOPUP', 'GIFT']);
  await new Promise(resolve => setTimeout(resolve, 500));
  assert.ok(deferredModal, 'payment confirmation was open during recharge');
  deferredModal.success({ confirm: false }); await cancelled;
  const updated = Date.now() + 20000;
  while (Date.now() < updated && checkout.data.quote?.balanceText !== '2200') await new Promise(resolve => setTimeout(resolve, 50));
  assert.equal(checkout.data.quote?.balanceText, '2200');
  assert.deepEqual([checkout.data.region, checkout.data.nickname], ['隔离大区', '隔离老板']);
  pauseConfirmation = false;
  await checkout.submit();
  assert.match(redirect || '', /^\/pages\/order-detail\/index\?id=\d+$/);
  checkout.onUnload();
  await waitFor('1700', ['TOPUP', 'GIFT', 'PAY']);
  assert.ok(page.data.items.some(row => row.tx_type === 'PAY' && row.displayAmount === '-500'));
  console.log('Native wallet updated after real recharge and order payment');
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; }).finally(() => { realtime.hide(); page.onUnload(); });
