// Exercise a fresh native client against a real isolated Django order API.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map();
let tabUrl = '';
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({keys: [...storage.keys()]}),
  getAccountInfoSync: () => ({miniProgram: {envVersion: 'develop'}}),
  switchTab: options => {tabUrl = options.url;}, navigateTo() {}, showToast() {}, showModal() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, {method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : {body: JSON.stringify(options.data)})})
      .then(async response => options.success({statusCode: response.status, data: await response.json()}))
      .catch(options.fail);
  },
};
async function main() {
  const auth = require(path.join(root, 'core/auth'));
  auth.setSession(input.session);
  const ui = require(path.join(root, 'core/ui'));
  ui.go('/pages/orders/index?type=gift');
  assert.equal(tabUrl, '/pages/orders/index');
  assert.deepEqual(storage.get('xa:native:orders-intent'), {status: '', type: 'gift'});
  let definition; global.Page = value => { definition = value; };
  require(path.join(root, 'pages/orders/index.js'));
  const page = {...definition, data: structuredClone(definition.data), setData(patch) {Object.assign(this.data, patch);}};
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad();
  await page.onShow();
  assert.equal(page.data.error, '');
  assert.equal(page.data.onlyGifts, true);
  assert.deepEqual(page.data.items.map(item => item.id), [input.giftId]);
  assert.equal(page.data.items[0].statusText, '礼物已送达');
  const detail = await fetch(input.base + '/api/orders/orders/' + input.giftId + '/', {headers: {Authorization: 'Bearer ' + input.session.token}});
  const detailBody = await detail.json();
  assert.equal(detailBody.data.id, input.giftId);
  assert.equal(detailBody.data.is_gift_snapshot, true);
  page.setData({filter: 'pending'}); await page.load();
  assert.deepEqual(page.data.items, [], 'gift type and pending status combine');
  page.setData({filter: 'completed'}); await page.load();
  assert.deepEqual(page.data.items.map(item => item.id), [input.giftId]);
  page.setData({onlyGifts: false}); await page.load();
  assert.ok(page.data.items.some(item => item.id === input.giftId));
  assert.ok(page.data.items.some(item => item.id === input.normalId));
  page._visible = false; auth.setSession(input.otherSession);
  page.setData({onlyGifts: true, filter: ''}); await page.load();
  assert.equal(page.data.error, '');
  assert.deepEqual(page.data.items, [], 'another account does not see the gift');
  const otherDetail = await fetch(input.base + '/api/orders/orders/' + input.giftId + '/', {headers: {Authorization: 'Bearer ' + input.otherSession.token}});
  assert.equal(otherDetail.status, 404);
  page.onUnload();
  console.log('fresh-device gift filter uses real account order API');
}
main().catch(error => {console.error(error); process.exitCode = 1;});
