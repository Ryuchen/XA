// Run native page controllers against the isolated Django live server.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map();
let loseResponse = true, committed = null;
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key),
  getStorageInfoSync: () => ({ keys: [...storage.keys()] }), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, showModal: o => o.success({ confirm: true }), navigateTo() {}, stopPullDownRefresh() {},
  request(o) {
    const url = new URL(o.url), endpoint = input.base + url.pathname + url.search;
    fetch(endpoint, { method: o.method, headers: o.header, ...(o.data === undefined ? {} : { body: JSON.stringify(o.data) }) })
      .then(async response => {
        const data = await response.json();
        if (loseResponse && endpoint.endsWith('/orders/gifts/send/') && data.code === 0) {
          loseResponse = false; committed = data.data; o.fail(new Error('response lost after committed transaction'));
        } else o.success({ statusCode: response.status, data });
      }).catch(o.fail);
  },
};
function page() {
  let definition; global.Page = value => { definition = value; };
  const file = path.join(root, 'pages/gifts/index.js'); delete require.cache[require.resolve(file)]; require(file);
  const instance = { ...definition, data: structuredClone(definition.data), setData(values) { Object.assign(this.data, values); } };
  for (const [key, value] of Object.entries(instance)) if (typeof value === 'function') instance[key] = value.bind(instance);
  instance.onLoad({ provider_id: input.providerId }); return instance;
}
async function main() {
  require(path.join(root, 'core/auth')).setSession(input.session);
  let gift = page(); await gift.load();
  assert.equal(gift.data.error, '');
  gift.select({ currentTarget: { dataset: { id: input.giftId } } });
  assert.equal(gift.data.enough, true);
  await gift.send();
  assert.ok(committed && committed.orders.length === 1, 'real transaction committed before response was lost');
  assert.equal(gift.data.pendingGift, true);
  gift.onUnload(); gift = page(); await gift.load();
  assert.equal(gift.data.pendingGift, true, 'pending transaction persists across page reload');
  const intentStore = require(path.join(root, 'core/gift-intent'));
  const pending = intentStore.load(input.session.user.id);
  const lookup = async (token, body) => {
    const response = await fetch(input.base + '/api/orders/gifts/receipt/', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + token }, body: JSON.stringify(body) });
    return response.json();
  };
  const credential = { path: pending.path, payload: pending.payload, client_request_id: pending.requestId };
  assert.equal((await lookup(input.session.token, credential)).data.status, 'COMMITTED', 'same account can verify with the original credential from another client');
  assert.equal((await lookup(input.otherToken, credential)).data.status, 'UNKNOWN', 'another account cannot read the receipt');
  assert.equal((await lookup(input.session.token, { ...credential, payload: { ...pending.payload, expected_amount: 1 } })).errorCode, 'REQUEST_ID_CONFLICT');
  assert.equal((await lookup(input.session.token, { ...credential, client_request_id: 'native-unknown-123' })).data.status, 'UNKNOWN');
  assert.equal(intentStore.load('other-device-without-local-credential'), null, 'a device without the original credential cannot infer it');
  const storageKey = 'xa:native:gift-intent:v1:' + input.session.user.id;
  wx.setStorageSync(storageKey, { ...pending, requestId: 'native-unknown-123' });
  await gift.checkPending();
  assert.equal(gift.data.pendingGift, true, 'an unknown lookup keeps the pending state');
  assert.equal(intentStore.load(input.session.user.id).requestId, 'native-unknown-123', 'an unknown lookup keeps the original identifier');
  wx.setStorageSync(storageKey, pending);
  gift.select({ currentTarget: { dataset: { id: input.giftId } } });
  gift.select({ currentTarget: { dataset: { id: input.giftId } } });
  await gift.send();
  assert.equal(gift.data.receipt, null, 'changed basket must not send a second transaction');
  await gift.checkPending();
  assert.equal(gift.data.pendingGift, false);
  assert.equal(gift.data.receipt.id, committed.orders[0].id, 'read the original committed gift without resending');
  // A request left by the previous client version also needs recovery after upgrade.
  loseResponse = true;
  const api = require(path.join(root, 'core/api'));
  await assert.rejects(api.fund('/orders/gifts/send/', { items: [{ gift_service_id: input.giftId, quantity: 1 }], provider_id: input.providerId, expected_amount: 2100, naming_title: '' }));
  assert.ok(committed && committed.orders.length === 1);
  const oldOrderId = committed.orders[0].id;
  gift.onUnload(); gift = page(); await gift.load();
  assert.equal(gift.data.pendingGift, true, 'upgrade discovers unresolved earlier request');
  await gift.recoverPending();
  assert.equal(gift.data.receipt.id, oldOrderId, 'upgrade recovers the original gift');
  assert.equal(gift.data.pendingGift, false);
  console.log('real Django HTTP gift recovery passed');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
