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
// Django LiveServer's in-memory SQLite connection is shared between threads;
// serialize the HTTP transport while leaving every endpoint and query real.
let transport = Promise.resolve();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast: options => toasts.push(options.title), showModal: options => { modals.push(options); options.success?.({ confirm: true }); },
  redirectTo: options => { redirect = options.url; }, stopPullDownRefresh() {},
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
async function main() {
  const auth = require(path.join(root, 'core/auth'));
  auth.setSession(input.session);
  let definition;
  global.Page = value => { definition = value; };
  require(path.join(root, 'pages/checkout/index.js'));
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({ service_id: input.serviceId }); page._visible = true; await page.load();
  assert.equal(page.data.error, '');
  const index = page.data.coupons.findIndex(row => row.id === input.voucherId);
  assert.ok(index > 0, 'claimed coupon is initially selectable');
  page.coupon({ detail: { value: index } }); await waitFor(() => !page.data.quoting);
  assert.equal(page.data.quote.amount, input.zero ? 0 : 9000);
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
  page.onHide();
  const response = await fetch(`${input.base}/api/admin/coupons/${input.couponId}/`, {
    method: 'PATCH', headers: { Authorization: `Bearer ${input.adminToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: false }),
  });
  assert.equal((await response.json()).code, 0);
  page.onShow(); await waitFor(() => !page.data.quoting);
  assert.ok(!page.data.coupons.some(row => row.id === input.voucherId));
  assert.equal(page.data.quote.amount, 10000);
  assert.deepEqual([page.data.region, page.data.nickname], ['本地大区', '本地昵称']);
  assert.ok(toasts.some(message => message.includes('优惠券已不可用')));
  await page.submit();
  assert.match(redirect, /^\/pages\/order-detail\/index\?id=\d+$/, JSON.stringify({ toasts, quoteError: page.data.quoteError, pending: page.data.pendingOrder }));
  page.onUnload();
  console.log('Native checkout -> real Django HTTP: disabled coupon, re-quote, preserved inputs and order passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
