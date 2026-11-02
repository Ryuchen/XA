// Exercise the actual native checkout against an isolated Django LiveServer.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map(), modals = [];
let redirect = '', transport = Promise.resolve();
async function changePrice() {
  const response = await fetch(`${input.base}/api/admin/boss-types/${input.bossTypeId}/`, {
    method: 'PATCH', headers: { Authorization: `Bearer ${input.adminToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({discount_rate: 90}),
  });
  const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
}
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  getStorageInfoSync: () => ({ keys: [...storage.keys()] }), removeStorageSync: key => storage.delete(key),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, showModal: options => {
    modals.push(options);
    if (input.race && modals.length === 1) changePrice().then(() => options.success?.({ confirm: true })).catch(error => { console.error(error); process.exitCode = 1; options.fail?.(); });
    else options.success?.({ confirm: true });
  },
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
async function main() {
  const auth = require(path.join(root, 'core/auth')); auth.setSession(input.session);
  let definition; global.Page = value => { definition = value; };
  require(path.join(root, 'pages/checkout/index.js'));
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({ service_id: input.serviceId }); page._visible = true; await page.load();
  assert.equal(page.data.error, '');
  assert.equal(page.data.service.unit_price, 10000);
  const couponIndex = page.data.coupons.findIndex(item => item.id === input.voucherId); assert(couponIndex > 0);
  page.coupon({ detail: { value: String(couponIndex) } });
  while (page.data.quoting) await new Promise(resolve => setTimeout(resolve, 10));
  assert.equal(page.data.quote.amount, 0); assert.equal(page.data.quote.original_amount, 10000);
  assert.equal(page.data.quote.boss_discount, 0); assert.equal(page.data.quote.coupon_discount, 10000);
  page.input({ currentTarget: { dataset: { field: 'region' } }, detail: { value: '本地大区' } });
  page.input({ currentTarget: { dataset: { field: 'nickname' } }, detail: { value: '本地昵称' } });
  if (!input.race) await changePrice();
  await page.submit();
  assert.equal(modals.length, input.race ? 1 : 0, 'changed displayed price requires review before confirmation');
  assert.equal(redirect, ''); assert.equal(page.data.pendingOrder, false);
  assert.match(page.data.quoteError, /价格或优惠已变化/);
  assert.equal(page.data.quote, null);
  await page.refreshAvailability();
  assert.equal(page.data.service.unit_price, 10000);
  assert.equal(page.data.quote.original_amount, 10000); assert.equal(page.data.quote.amount, 0);
  assert.equal(page.data.quote.boss_discount, 1000); assert.equal(page.data.quote.coupon_discount, 9000);
  await page.submit();
  assert.match(redirect, /^\/pages\/order-detail\/index\?id=\d+$/);
  page.onUnload();
  console.log('price change with zero payable required reconfirmation over real HTTP');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
