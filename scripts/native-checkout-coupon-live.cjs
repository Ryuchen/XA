// Exercise the real checkout controller against an isolated Django HTTP server.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map();
const toasts = [];
const modals = [];
let redirect = '';
let activePage;
let connectedResolve;
const connected = new Promise(resolve => { connectedResolve = resolve; });
// Django LiveServer's in-memory SQLite connection is shared between threads;
// serialize the HTTP transport while leaving every endpoint and query real.
let transport = Promise.resolve();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast: options => toasts.push(options.title), showModal: options => {
    modals.push(options);
    if (input.race && modals.length === 1) {
      (async () => { await disableCoupon(); await waitFor(() => activePage._couponRefreshPending === true); options.success?.({confirm:true}); })()
        .catch(error => { console.error(error); process.exitCode = 1; options.fail?.(); });
    } else options.success?.({ confirm: true });
  },
  redirectTo: options => { redirect = options.url; }, stopPullDownRefresh() {},
  connectSocket({url}) {
    const source = new URL(url), upstream = new URL(input.wsBase);
    const socket = new WebSocket(`ws://${upstream.host}${source.pathname}${source.search}`);
    return { onOpen(fn) { socket.addEventListener('open', () => { fn(); connectedResolve(); }); },
      onMessage(fn) { socket.addEventListener('message', event => fn({data:event.data})); },
      onClose(fn) { socket.addEventListener('close', fn); }, onError(fn) { socket.addEventListener('error', fn); },
      send({data}) { socket.send(data); }, close() { socket.close(); } };
  },
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
async function waitFor(predicate) {
  const end = Date.now() + 5000;
  while (Date.now() < end) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
  throw new Error('checkout did not finish refreshing');
}
async function disableCoupon() {
  const route = input.vipTierId ? `/api/admin/vip-tiers/${input.vipTierId}/` : `/api/admin/coupons/${input.couponId}/`;
  const response = await fetch(`${input.base}${route}`, {
    method:'PATCH', headers:{Authorization:`Bearer ${input.adminToken}`, 'Content-Type':'application/json'},
    body:JSON.stringify(input.vipTierId ? {safe_box_service_ids:[input.replacementServiceId]} : input.changeAmount ? {amount:2000} : {is_active:false}),
  });
  assert.equal((await response.json()).code, 0);
}
async function main() {
  const auth = require(path.join(root, 'core/auth'));
  auth.setSession(input.session);
  let definition;
  global.Page = value => { definition = value; };
  require(path.join(root, 'pages/checkout/index.js'));
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  activePage = page;
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({ service_id: input.serviceId }); page._visible = true; await page.load();
  assert.equal(page.data.error, '');
  if (input.bossTypeId) {
    assert.equal(page.data.quote.amount, 9000);
    page.input({ currentTarget: { dataset: { field: 'region' } }, detail: { value: '本地大区' } });
    page.input({ currentTarget: { dataset: { field: 'nickname' } }, detail: { value: '本地昵称' } });
    const realtime = require(path.join(root, 'core/realtime')); realtime.show(); await connected;
    await waitFor(() => !page.data.quoting && !!page.data.quote && !page._couponTimer);
    const response = await fetch(`${input.base}/api/admin/boss-types/${input.bossTypeId}/`, {
      method: 'PATCH', headers: { Authorization: `Bearer ${input.adminToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ discount_rate: 80 }),
    });
    const changed = await response.json(); assert.equal(changed.code, 0, JSON.stringify(changed));
    await waitFor(() => !page.data.quoting && page.data.quote?.amount === 8000);
    assert.deepEqual([page.data.region, page.data.nickname], ['本地大区', '本地昵称']);
    await waitFor(() => toasts.some(message => message.includes('优惠或价格已更新')));
    await page.submit();
    assert.match(redirect, /^\/pages\/order-detail\/index\?id=\d+$/, JSON.stringify({ toasts, quoteError: page.data.quoteError }));
    const orderId = redirect.match(/id=(\d+)/)[1];
    const detail = await fetch(`${input.base}/api/orders/orders/${orderId}/`, { headers: { Authorization: `Bearer ${input.session.token}` } });
    const order = await detail.json(); assert.equal(order.code, 0); assert.equal(order.data.amount, 8000);
    realtime.hide(); page.onUnload();
    console.log('Native checkout follows actual staff discount change and places order at updated price');
    return;
  }
  const index = page.data.coupons.findIndex(row => row.id === input.voucherId);
  assert.ok(index > 0, 'claimed coupon is initially selectable');
  page.coupon({ detail: { value: index } }); await waitFor(() => !page.data.quoting);
  assert.equal(page.data.quote.amount, input.zero || input.vipTierId ? 0 : 9000);
  page.input({ currentTarget: { dataset: { field: 'region' } }, detail: { value: '本地大区' } });
  page.input({ currentTarget: { dataset: { field: 'nickname' } }, detail: { value: '本地昵称' } });
  if (input.zero) {
    await page.submit();
    assert.match(redirect, /^\/pages\/order-detail\/index\?id=\d+$/, JSON.stringify({ toasts, quoteError: page.data.quoteError }));
    const confirmation = modals.find(item => item.title === '确认下单');
    assert.ok(confirmation, 'zero-price checkout confirms an order');
    assert.match(confirmation.content, /实付 0/);
    assert.doesNotMatch(confirmation.content, /退回余额/);
    page.onUnload();
    console.log('Native checkout -> real Django HTTP: zero-price coupon order confirmed');
    return;
  }
  let realtime;
  if (input.wsBase) {
    realtime = require(path.join(root, 'core/realtime'));
    realtime.show(); await connected;
    await waitFor(() => !page.data.quoting && !!page.data.quote);
  } else page.onHide();
  if (input.race) {
    await page.submit(); assert.equal(redirect, '');
    const response = await fetch(`${input.base}/api/orders/orders/?status=all`, {headers:{Authorization:`Bearer ${input.session.token}`}});
    const result = await response.json(); assert.equal(result.code, 0); assert.equal(result.data.length, 0);
  }
  else await disableCoupon();
  if (!input.wsBase) page.onShow();
  await waitFor(() => !page.data.quoting && !!page.data.quote && (input.changeAmount ? page.data.quote.amount === 8000 : !page.data.coupons.some(row => row.id === input.voucherId)));
  if (input.changeAmount) assert.equal(page.data.coupons[page.data.couponIndex].id, input.voucherId);
  else assert.ok(!page.data.coupons.some(row => row.id === input.voucherId));
  assert.equal(page.data.quote.amount, input.vipTierId ? 9000 : input.changeAmount ? 8000 : 10000);
  assert.deepEqual([page.data.region, page.data.nickname], ['本地大区', '本地昵称']);
  await waitFor(() => toasts.some(message => message.includes(input.changeAmount ? '优惠或价格已更新' : '优惠券已不可用')));
  await page.submit();
  assert.match(redirect, /^\/pages\/order-detail\/index\?id=\d+$/, JSON.stringify({ toasts, quoteError: page.data.quoteError, pending: page.data.pendingOrder }));
  if (realtime) realtime.hide();
  page.onUnload();
  console.log('Native checkout -> real Django HTTP: disabled coupon, re-quote, preserved inputs and order passed');
}
main().catch(error => { console.error(error); process.exit(1); });
