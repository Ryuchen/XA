// Execute the native page against real HTTP; delay or lose responses after server commit.
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const root = path.resolve('apps/boss-miniapp/native');
const storage = new Map(), held = [], toasts = [];
let mode = '';
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast: options => toasts.push(options.title), stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => {
        const data = await response.json();
        if (options.method === 'POST' && /\/coupons\/\d+\/claim\/$/.test(url.pathname)) {
          if (mode === 'hold') { held.push(() => options.success({ statusCode: response.status, data })); return; }
          if (mode === 'lose') { options.fail(); return; }
        }
        options.success({ statusCode: response.status, data });
      }).catch(options.fail);
  },
};
async function waitFor(predicate) {
  for (let i = 0; i < 250; i++) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
  throw new Error('Timed out waiting for HTTP response');
}
async function main() {
  const auth = require(path.join(root, 'core/auth'));
  auth.setSession(input.first);
  let definition; global.Page = value => { definition = value; };
  require(path.join(root, 'pages/coupons/index.js'));
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({}); page._visible = true; await page.load();
  const event = { currentTarget: { dataset: { id: input.couponId } } };
  mode = 'hold'; const first = page.claim(event); await waitFor(() => held.length === 1);
  auth.setSession(input.second);
  await waitFor(() => !page.data.loading && page.data.items.some(item => item.id === input.couponId));
  const second = page.claim(event); await waitFor(() => held.length === 2);
  held[0](); await first;
  assert.equal(page.data.claiming, input.couponId, 'old account must not unlock the new claim');
  assert.deepEqual(toasts, [], 'old result must not notify the new account');
  held[1](); await second;
  assert.equal(page.data.claiming, 0);
  assert.equal(toasts.length, 1);
  assert.equal(page.data.items.find(item => item.id === input.couponId).is_claimed, true);

  // A committed response can disappear; the page reports uncertainty and rereads the actual claim.
  mode = 'lose';
  await page.claim({ currentTarget: { dataset: { id: input.otherCouponId } } });
  assert.equal(toasts.at(-1), '领取结果未确认，请查看我的券后再操作');
  assert.equal(page.data.items.find(item => item.id === input.otherCouponId).is_claimed, true);
  page.onUnload();
  console.log('Coupon claims: real commit, account race and lost response passed.');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
