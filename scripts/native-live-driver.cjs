// Called by the Django LiveServer test. wx transport is adapted to real HTTP;
// Page methods and the entire native request/auth layer execute unchanged.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const storage = new Map();
let navigated = '';
const pages = [];
let transportQueue = Promise.resolve();
let loseNextCreate = false, lostOrderId = null;
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, stopPullDownRefresh() {}, setTabBarBadge() {}, removeTabBarBadge() {},
  showModal: options => options.success({ confirm: true, content: '联动验收取消' }),
  login: options => options.success({ code: 'fresh-native-http-code' }),
  redirectTo: options => { navigated = options.url; }, navigateTo(options) { navigated = options.url; }, navigateBack() {}, switchTab() {},
  request(options) {
    const target = new URL(options.url);
    const endpoint = input.base + target.pathname + target.search;
    const send = () => fetch(endpoint, { method: options.method, headers: options.header,
      ...(options.data !== undefined ? { body: JSON.stringify(options.data) } : {}) })
      .then(async response => {
        const data = await response.json();
        if (loseNextCreate && endpoint.endsWith('/orders/create/') && data.code === 0) {
          loseNextCreate = false; lostOrderId = data.data.id;
          options.fail(new Error('Simulated loss after committed order'));
        } else options.success({ statusCode: response.status, data });
      })
      .catch(options.fail);
    if (input.serializeRequests) transportQueue = transportQueue.then(send, send);
    else send();
  },
};
function page(name, options = {}) {
  let definition; global.Page = value => { definition = value; };
  const file = path.join(root, 'pages', name, 'index.js');
  delete require.cache[require.resolve(file)]; require(file);
  const instance = { ...definition, data: structuredClone(definition.data), setData(patch) {
    for (const [key, value] of Object.entries(patch)) {
      const pieces = key.replace(/\[(\d+)\]/g, '.$1').split('.'); let target = this.data;
      for (const piece of pieces.slice(0, -1)) target = target[piece] || (target[piece] = {});
      target[pieces.at(-1)] = value;
    }
  } };
  for (const [key, value] of Object.entries(instance)) if (typeof value === 'function') instance[key] = value.bind(instance);
  if (instance.onLoad) instance._initialLoad = instance.onLoad(options);
  pages.push(instance);
  return instance;
}
async function load(name, options) {
  const instance = page(name, options);
  if (instance._initialLoad && typeof instance._initialLoad.then === 'function') await instance._initialLoad;
  else await instance.load();
  assert.equal(instance.data.error, '', name + ': ' + instance.data.error);
  assert.equal(instance.data.loading, false, name + ': loading stuck');
  return instance;
}
async function main() {
  const auth = require(path.join(root, 'core/auth'));
  auth.setSession(input.session);
  for (const name of ['home', 'catalog', 'companions', 'orders', 'messages', 'mine', 'wallet', 'profile', 'security', 'achievements', 'rankings', 'coupons', 'favorites', 'checkin', 'announcements', 'support']) await load(name);
  const message = await load('message-detail', { id: String(input.messageId) });
  assert.equal(message.data.item.is_read, true);
  assert.equal(message.data.target, '/pages/service/index?id=' + input.serviceId);
  const announcement = await load('announcement-detail', { id: String(input.announcementId) });
  assert.equal(announcement.data.item.title, '联动公告');
  const service = await load('service', { id: input.serviceId });
  assert.equal(service.data.service.displayPrice, '125');
  let checkout = await load('checkout', { service_id: input.serviceId });
  assert.ok(checkout.data.quote, checkout.data.quoteError);
  assert.equal(checkout.data.quote.amountText, '125');
  checkout.setData({ region: '微信区', nickname: '联动测试', uid: '10001' });
  loseNextCreate = true;
  await checkout.submit();
  assert.equal(navigated, '', 'lost response must not invent a success redirect');
  assert.ok(lostOrderId, 'the first request must actually commit at the backend');
  assert.match(checkout.data.quoteError, /网络/);
  await checkout.submit();
  assert.match(navigated, /^\/pages\/order-detail\/index\?id=\d+$/);
  const id = navigated.split('id=')[1];
  assert.equal(id, String(lostOrderId), 'retry must recover the original order');
  const detail = await load('order-detail', { id });
  assert.equal(detail.data.order.canCancel, true);
  assert.equal(detail.data.order.amountText, '125');
  const api = require(path.join(root, 'core/api'));
  const paidWallet = await api.request('/wallet/info/'); assert.equal(paidWallet.balance, 8750);
  await detail.cancel();
  assert.equal(detail.data.order.status, 'CANCELLED');
  const refundedWallet = await api.request('/wallet/info/'); assert.equal(refundedWallet.balance, 10000);
  // A second purchase proceeds through the actual provider/admin HTTP contracts.
  const actorRequest = async (url, token, method = 'GET', data, imageField) => {
    const headers = { Authorization: 'Bearer ' + token };
    let body;
    if (imageField) {
      body = new FormData();
      for (const [key, value] of Object.entries(data || {})) body.append(key, String(value));
      body.append(imageField, new Blob([Buffer.from(input.evidence, 'base64')], { type: 'image/png' }), 'evidence.png');
    } else if (data !== undefined) { headers['Content-Type'] = 'application/json'; body = JSON.stringify(data); }
    const response = await fetch(input.base + '/api' + url, { method, headers, body });
    const result = await response.json();
    assert.equal(result.code, 0, url + ': ' + result.msg);
    return result.data;
  };
  if (input.autoGrab) {
    checkout = await load('checkout', { service_id: input.serviceId });
    checkout.dispatch({ detail: { value: 1 } });
    assert.equal(checkout.data.dispatchIndex, 1);
    checkout.setData({ region: '微信区', nickname: '直接抢单老板', uid: '10002' });
  }
  await checkout.refreshQuote(); await checkout.submit();
  const fulfillmentId = Number(navigated.split('id=')[1]); assert.notEqual(fulfillmentId, Number(id));
  const pool = await actorRequest('/orders/orders/?status=pending', input.providerToken);
  assert.ok(pool.some(row => row.id === fulfillmentId && row.allowed_actions.includes('GRAB')));
  await actorRequest(`/orders/orders/${fulfillmentId}/grab/`, input.providerToken, 'POST', {});
  const fulfilled = await load('order-detail', { id: fulfillmentId });
  if (input.autoGrab) {
    assert.equal(fulfilled.data.order.requires_boss_selection, false);
    assert.equal(fulfilled.data.order.status, 'GRABBED');
  } else {
    assert.equal(fulfilled.data.order.status, 'PENDING');
    assert.equal(fulfilled.data.order.canSelectProvider, true);
    assert.equal(fulfilled.data.order.applications.length, 1);
    await fulfilled.selectProvider({ currentTarget: { dataset: { id: fulfilled.data.order.applications[0].id } } });
  }
  await actorRequest('/orders/network-report/', input.providerToken, 'POST', { samples: 3, failures: 0, average_ms: 120, jitter_ms: 30 });
  const monitoring = await actorRequest('/admin/order-traffic/', input.adminToken);
  assert.equal(monitoring.metrics.network_reports, 1);
  assert.ok(monitoring.metrics.create_admitted >= 2);
  assert.ok(monitoring.metrics.grab_admitted >= 1);
  assert.equal(fulfilled.data.order.status, 'GRABBED'); assert.equal(fulfilled.data.order.canCancel, false);
  await actorRequest(`/orders/orders/${fulfillmentId}/start/`, input.providerToken, 'POST', {}, 'entry_image');
  await fulfilled.load(); assert.equal(fulfilled.data.order.status, 'IN_SERVICE');
  await actorRequest(`/orders/orders/${fulfillmentId}/complete/`, input.providerToken, 'POST', {}, 'completion_image');
  assert.equal((await actorRequest('/wallet/info/', input.providerToken)).balance, 0, 'completion must not credit before review');
  const report = (await actorRequest('/wallet/reports/', input.providerToken)).find(row => row.order_id === fulfillmentId);
  await actorRequest(`/wallet/reports/${report.id}/images/`, input.providerToken, 'POST', { kind: 'RESULT' }, 'image');
  await actorRequest(`/wallet/reports/${report.id}/submit/`, input.providerToken, 'POST', {});
  await actorRequest(`/admin/reports/${report.id}/approve/`, input.adminToken, 'POST', {});
  assert.equal((await actorRequest('/wallet/info/', input.providerToken)).balance, 0, 'approval waits for the aftersale window');
  assert.equal((await api.request('/wallet/info/')).balance, 8750, 'report approval must not debit boss a second time');
  await fulfilled.load(); assert.equal(fulfilled.data.order.status, 'COMPLETED'); assert.equal(fulfilled.data.order.canEvaluate, true);
  fulfilled.setData({ content: '真实联动验收评价', score: 5 }); await fulfilled.evaluate();
  assert.equal(fulfilled.data.order.canEvaluate, false);
  const chat = await load('chat'); chat.setData({ content: '原生页面真实 HTTP 联动测试' }); await chat.send();
  assert.ok(chat.data.items.some(item => item.content === '原生页面真实 HTTP 联动测试'));
  assert.equal(chat.data.content, '');
  // A standalone Node driver cannot obtain a real wx.login session code.
  // The LiveServer contract test keeps exercising its mocked code exchange.
  if (!input.skipWechatExchange) {
    const login = page('login'); login.setData({ agreed: true }); await login.quickLogin();
    assert.equal(login.data.error, ''); assert.equal(auth.getSession().user.id, input.session.user.id);
    assert.equal(auth.getSession().user.isWechatBound, true);
  }
  const audition = await load('audition', { token: input.auditionToken, role: 'boss' });
  assert.equal(audition.data.info.title, '原生试音活动');
  assert.equal(auth.getSession().user.id, input.session.user.id, 'reading an invite must not switch identity');
  await audition.enter();
  assert.equal(auth.getSession().user.id, input.auditionAccountId);
  assert.equal(message.data.item, null, 'private page data is cleared on explicit account switch');
  for (const instance of pages) if (instance.onUnload) instance.onUnload();
  console.log('Native JS → real Django HTTP: page controllers, purchase, grab, start, complete, evidence, report approval, pending settlement, evaluation, refund, chat and invitation passed; '
    + (input.skipWechatExchange ? 'WeChat platform exchange excluded (real wx.login code unavailable)' : 'login exchange passed'));
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
