// Native page controllers against real order, application and staff APIs.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native'), storage = new Map();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, showModal: value => value.success({ confirm: true }), stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
  },
};
async function post(url, token, body) {
  const response = await fetch(input.base + url, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result)); return result.data;
}
async function main() {
  require(path.join(root, 'core/auth')).setSession(input.session);
  let definition; global.Page = value => { definition = value; };
  const makePage = file => {
    require(path.join(root, file));
    const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
    for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
    return page;
  };
  const detail = makePage('pages/order-detail/index.js'); detail.onLoad({ id: input.orderId }); detail._visible = true;
  const list = makePage('pages/orders/index.js'); list.onLoad(); list._visible = true;
  const check = async (listHint, detailHint) => {
    await list.load(); await detail.load();
    assert.equal(list.data.items.find(row => row.id === input.orderId).applicationHint, listHint);
    assert.equal(detail.data.order.providerDisplay, detailHint);
  };
  try {
    await check('等待陪玩报名', '等待陪玩报名');
    await post(`/api/orders/orders/${input.orderId}/grab/`, input.applicantToken, { team_size: 2, partner_id: input.partnerId });
    await check('已收到报名，查看详情', '报名暂不可选，请查看详情');
    const rejected = detail.data.order.applications[0];
    await post(`/api/orders/orders/${input.orderId}/applications/${rejected.id}/action/`, input.partnerToken, { action: 'WITHDRAW' });
    // Simulate the already-verified websocket invalidation; the pages reload the real HTTP state.
    require(path.join(root, 'core/events')).emit('order.changed', { id: input.orderId, status: 'PENDING' });
    for (let i = 0; i < 150 && (detail.data.order.applications.length || list.data.items.find(row => row.id === input.orderId).applicationHint !== '等待陪玩报名'); i++) {
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.equal(detail.data.order.applications.length, 0);
    assert.equal(detail.data.order.providerDisplay, '等待陪玩报名');
    assert.equal(list.data.items.find(row => row.id === input.orderId).applicationHint, '等待陪玩报名');
    await post(`/api/orders/orders/${input.orderId}/grab/`, input.applicantToken, { team_size: 2, partner_id: input.partnerId });
    await check('已收到报名，查看详情', '报名暂不可选，请查看详情');
    const application = detail.data.order.applications[0];
    assert.notEqual(application.id, rejected.id);
    await post(`/api/orders/orders/${input.orderId}/applications/${application.id}/action/`, input.partnerToken, { action: 'CONFIRM_TEAM' });
    await check('已收到报名，查看详情', '请选择陪玩');
    await post('/api/users/escorts/status/', input.partnerToken, { status: 'OFFLINE' });
    await check('已收到报名，查看详情', '报名暂不可选，请查看详情');
    await post('/api/users/escorts/status/', input.partnerToken, { status: 'AVAILABLE' });
    await check('已收到报名，查看详情', '请选择陪玩');
    await detail.selectProvider({ currentTarget: { dataset: { id: application.id } } });
    assert.equal(detail.data.order.status, 'GRABBED');
    const response = await fetch(`${input.base}/api/admin/orders/${input.orderId}/`, { headers: { Authorization: `Bearer ${input.staffToken}` } });
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    assert.equal(result.data.status, 'GRABBED'); assert.equal(result.data.providers.length, 2);
  } finally { detail.onUnload(); list.onUnload(); }
  console.log('Real team application progression, readiness and selection agree across roles');
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
