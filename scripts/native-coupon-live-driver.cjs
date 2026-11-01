// Native page controller with wx transport adapted to the isolated Django server.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map();
let navigation = '';
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, stopPullDownRefresh() {}, navigateTo: options => { navigation = options.url; }, switchTab: options => { navigation = options.url; },
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
  },
};
async function adminPatch(active) {
  const response = await fetch(`${input.base}/api/admin/coupons/${input.couponId}/`, {
    method: 'PATCH', headers: { Authorization: `Bearer ${input.adminToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: active }),
  });
  const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body));
}
async function main() {
  const auth = require(path.join(root, 'core/auth'));
  auth.setSession(input.session);
  let definition;
  global.Page = value => { definition = value; };
  require(path.join(root, 'pages/coupons/index.js'));
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({ tab: 'mine' }); page._visible = true; await page.load();
  assert.equal(page.data.items[0].status, 'UNUSED');
  await adminPatch(false); await page.load();
  assert.equal(page.data.items.length, 0, '停用券不能留在待使用');
  page.filter({ currentTarget: { dataset: { status: 'DISABLED' } } });
  assert.equal(page.data.items[0].status, 'DISABLED');
  page.use({ currentTarget: { dataset: { id: input.voucherId } } });
  assert.equal(navigation, '', '停用券不能跳转选择服务');
  await adminPatch(true); await page.load();
  page.filter({ currentTarget: { dataset: { status: 'UNUSED' } } });
  assert.equal(page.data.items[0].status, 'UNUSED');
  page.use({ currentTarget: { dataset: { id: input.voucherId } } });
  assert.match(navigation, /catalog/);
  page.onUnload();
  console.log('Native coupon page -> real Django HTTP: disabled and reenabled availability passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
