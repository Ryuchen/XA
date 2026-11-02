// Exercise the native message detail and its real HTTP data, substituting only the wx host.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map();
let navigated = '';
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({keys: [...storage.keys()]}),
  getAccountInfoSync: () => ({miniProgram: {envVersion: 'develop'}}), showToast() {},
  navigateTo: ({url}) => { navigated = url; },
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, {method: options.method, headers: options.header})
      .then(async response => options.success({statusCode: response.status, data: await response.json()}))
      .catch(options.fail);
  },
};
async function main() {
  require(path.join(root, 'core/auth')).setSession({token: input.token, refresh: input.refresh, user: {id: String(input.account), role: 'customer'}});
  let definition; global.Page = value => { definition = value; };
  require(path.join(root, 'pages/message-detail/index.js'));
  const page = {...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); }};
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({id: String(input.message)}); await page.load();
  assert.equal(page.data.item.title, input.expectedTitle || '预约已确认');
  if (input.expectedPreview) assert.ok(page.data.item.preview.includes(input.expectedPreview));
  assert.equal(page.data.target, input.expectedTarget || `/pages/reservation-detail/index?id=${input.reservation}`);
  page.action(); assert.equal(navigated, page.data.target);
  page.onUnload();
  if (input.expectedStatus) {
    require(path.join(root, 'pages/reservation-detail/index.js'));
    const detail = {...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); }};
    for (const [key, value] of Object.entries(detail)) if (typeof value === 'function') detail[key] = value.bind(detail);
    detail.onLoad({id: String(input.reservation)}); await detail.load();
    assert.equal(detail.data.reservation.status, input.expectedStatus);
    assert.equal(detail.data.reservation.reject_reason || detail.data.reservation.cancel_reason, input.expectedReason);
    detail.onUnload();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
