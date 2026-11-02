// Exercise the actual checkout page over an isolated Django LiveServer.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map(), toasts = [];
let redirect = '', transport = Promise.resolve();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  getStorageInfoSync: () => ({ keys: [...storage.keys()] }), removeStorageSync: key => storage.delete(key),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast: options => toasts.push(options.title), showModal: options => options.success?.({ confirm: true }),
  navigateTo: options => options.events?.providersSelected?.([]),
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
  page.onLoad({ service_id: input.serviceId, provider_id: input.providerId }); page._visible = true; await page.load();
  assert.equal(page.data.error, '');
  assert.equal(page.data.provider.id, input.providerId);
  page.input({ currentTarget: { dataset: { field: 'region' } }, detail: { value: '本地大区' } });
  page.input({ currentTarget: { dataset: { field: 'nickname' } }, detail: { value: '本地昵称' } });
  page.input({ currentTarget: { dataset: { field: 'rounds' } }, detail: { value: '2' } });
  await new Promise(resolve => setTimeout(resolve, 400)); await waitFor(() => !page.data.quoting);
  assert.equal(page.data.quote.amount, 20000);
  page.onHide();
  const trafficUrl = `${input.base}/api/admin/order-traffic/`;
  const headers = { Authorization: `Bearer ${input.adminToken}`, 'Content-Type': 'application/json' };
  const configResponse = await fetch(trafficUrl, { headers });
  const config = (await configResponse.json()).data;
  const fields = ['revision', 'enabled', 'self_order_enabled', 'peak_start', 'peak_end', 'valley_start', 'valley_end', 'limits'];
  const traffic = Object.fromEntries(fields.map(key => [key, config[key]]));
  traffic.self_order_enabled = false;
  const disabled = await fetch(trafficUrl, { method: 'PUT', headers, body: JSON.stringify(traffic) });
  const disabledBody = await disabled.json(); assert.equal(disabledBody.code, 0, JSON.stringify(disabledBody));
  page.onShow(); await waitFor(() => !page.data.quoting);
  assert.equal(page.data.selfOrderEnabled, false);
  await page.submit();
  assert.equal(redirect, '');
  assert.ok(toasts.some(value => value.includes('自主下单暂时关闭')));
  page.onHide();
  traffic.revision = disabledBody.data.revision;
  traffic.self_order_enabled = true;
  const enabled = await fetch(trafficUrl, { method: 'PUT', headers, body: JSON.stringify(traffic) });
  const enabledBody = await enabled.json(); assert.equal(enabledBody.code, 0, JSON.stringify(enabledBody));
  const response = await fetch(`${input.base}/api/admin/service-items/${input.serviceId}/`, {
    method: 'PATCH', headers,
    body: JSON.stringify({ price: 12000, billing_provider_count: 2, price_unit: '小时' }),
  });
  const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
  page.onShow(); await waitFor(() => !page.data.quoting);
  assert.equal(page.data.selfOrderEnabled, true);
  assert.equal(page.data.service.price, 24000, 'visible service price must include both providers');
  assert.equal(page.data.selectionLimit, 2, 'package participant count must be current');
  assert.equal(page.data.service.fixedRounds, true);
  assert.equal(page.data.rounds, 1);
  assert.equal(page.data.provider.id, input.providerId, 'the selected provider is not silently discarded');
  assert.equal(page.data.selectionComplete, false);
  assert.match(page.data.quoteError, /请选齐2位陪玩/);
  page.chooseProvider(); await waitFor(() => !page.data.quoting);
  assert.equal(page.data.providers.length, 0);
  assert.equal(page.data.selectionComplete, true);
  assert.equal(page.data.quote.amount, 24000);
  assert.deepEqual([page.data.region, page.data.nickname], ['本地大区', '本地昵称']);
  await page.submit();
  assert.match(redirect, /^\/pages\/order-detail\/index\?id=\d+$/, JSON.stringify({ toasts, quoteError: page.data.quoteError }));
  page.onUnload();
  console.log('Native checkout refreshed price and attendance over real HTTP');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
