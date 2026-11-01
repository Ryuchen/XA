// Actual native controller and isolated Django HTTP; no payment or order logic is stubbed.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map(), modals = [];
let redirect = '', transport = Promise.resolve();
async function changePackage() {
  const response = await fetch(`${input.base}/api/admin/service-items/${input.serviceId}/`, {
    method: 'PATCH', headers: { Authorization: `Bearer ${input.adminToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify(input.patch || { security_level: 'TOP_SECRET' }),
  });
  const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
}
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  getStorageInfoSync: () => ({ keys: [...storage.keys()] }), removeStorageSync: key => storage.delete(key),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, showModal: options => {
    modals.push(options);
    if (input.race && modals.length === 1) changePackage().then(() => options.success?.({ confirm: true })).catch(error => { console.error(error); process.exitCode = 1; options.fail?.(); });
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
  const auth = require(path.join(root, 'core/auth'));
  auth.setSession(input.session);
  let definition;
  global.Page = value => { definition = value; };
  require(path.join(root, 'pages/checkout/index.js'));
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({ service_id: input.serviceId }); page._visible = true; await page.load();
  assert.equal(page.data.error, '');
  assert.equal(page.data.service[input.key || 'security_level'], input.before || 'CONFIDENTIAL');
  assert.equal(page.data.quote.amount, 10000);
  page.input({ currentTarget: { dataset: { field: 'region' } }, detail: { value: '本地大区' } });
  page.input({ currentTarget: { dataset: { field: 'nickname' } }, detail: { value: '本地昵称' } });
  if (!input.race) await changePackage();
  await page.submit();
  assert.equal(modals.length, input.race ? 1 : 0, 'same total must not silently confirm the old single-provider arrangement');
  assert.equal(redirect, '');
  assert.equal(page.data.pendingOrder, false, 'the rejected first attempt must be safely retryable');
  assert.match(page.data.quoteError, /商品信息已变化/);
  assert.equal(page.data.quote, null, 'a rejected package change must invalidate the old displayed quote');
  const priorModalCount = modals.length;
  await page.submit();
  assert.equal(modals.length, priorModalCount, 'the customer cannot retry before refreshing the quote');
  await page.refreshAvailability();
  assert.equal(page.data.service[input.key || 'security_level'], input.after || 'TOP_SECRET');
  assert.equal(page.data.quote.amount, 10000);
  await page.submit();
  assert.match(modals.at(-1).content, /确认|公开|客服/);
  assert.match(redirect, /^\/pages\/order-detail\/index\?id=\d+$/);
  page.onUnload();
  console.log('same-price gameplay difficulty change requires review before actual order');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
