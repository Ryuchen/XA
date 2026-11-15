// Native page controllers, real HTTP and Redis-backed WebSockets; wx provides host APIs.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const x = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const ws = new URL(x.wsBase), storage = new Map(), toasts = [];
let opened;
const connected = new Promise(resolve => { opened = resolve; });
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast: value => toasts.push(value.title), stopPullDownRefresh() {},
  request(options) { const url = new URL(options.url); fetch(x.base + url.pathname + url.search, {
    method: options.method, headers: options.header,
    ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }),
  }).then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail); },
  connectSocket({ url }) { const u = new URL(url), socket = new WebSocket(`ws://${ws.host}${u.pathname}${u.search}`); return {
    onOpen(fn) { socket.addEventListener('open', () => { fn(); opened(); }); },
    onMessage(fn) { socket.addEventListener('message', e => fn({ data: e.data })); },
    onClose(fn) { socket.addEventListener('close', fn); }, onError(fn) { socket.addEventListener('error', fn); },
    send({ data }) { socket.send(data); }, close() { socket.close(); },
  }; },
};
async function wait(predicate, message) { for (let i = 0; i < 400; i++) { if (predicate()) return; await new Promise(r => setTimeout(r, 25)); } throw Error(message); }
(async () => {
  require(path.join(root, 'core/auth')).setSession({ token: x.token, user: { id: x.account, role: 'customer' } });
  let definition; global.Page = value => { definition = value; }; require(path.join(root, 'pages/coupons/index.js'));
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({}); page._visible = true; await page.load();
  assert.ok(page.data.items.some(item => item.id === x.firstCoupon && item.claimable));
  await page.claim({ currentTarget: { dataset: { id: x.firstCoupon } } });
  assert.ok(toasts.includes('领取成功，可在我的券中查看'));
  assert.ok(page.data.items.find(item => item.id === x.firstCoupon).is_claimed);
  page.tab({ currentTarget: { dataset: { tab: 'mine' } } });
  await wait(() => page.data.items.some(item => item.name === '原生领取券'), 'claimed coupon not shown');
  assert.ok(page.data.items.some(item => item.scope === 'SAFE_BOX' && item.service_ids?.includes(x.serviceId)));
  const other = new WebSocket(`ws://${ws.host}/ws/orders/?token=${encodeURIComponent(x.otherToken)}`), otherEvents = [];
  other.addEventListener('message', e => { const event = JSON.parse(e.data); if (event.type === 'coupon_catalog_update') otherEvents.push(event); });
  const realtime = require(path.join(root, 'core/realtime'));
  try {
    await new Promise((resolve, reject) => { other.addEventListener('open', resolve, { once: true }); other.addEventListener('error', reject, { once: true }); });
    realtime.show(); await connected;
    await wait(() => !page._couponOrderTimer && !page.data.loading, 'initial refresh incomplete');
    const response = await fetch(`${x.base}/api/coupons/${x.secondCoupon}/claim/`, {
      method: 'POST', headers: { Authorization: `Bearer ${x.token}`, 'Content-Type': 'application/json' }, body: '{}',
    });
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    await wait(() => page.data.items.some(item => item.name === '另一设备领取券'), 'second device claim did not refresh page');
    await new Promise(resolve => setTimeout(resolve, 500));
    assert.equal(otherEvents.length, 0, 'another boss received private update');
  } finally { realtime.hide(); page.onUnload(); other.close(); }
  console.log('Account-only native coupon page follows real claims and private realtime update');
})().then(() => process.exit(0)).catch(error => { console.error(error.stack || error); process.exit(1); });
