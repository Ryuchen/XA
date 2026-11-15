// Execute real native page controllers against isolated HTTP and Redis/WebSocket.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const x = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native'), storage = new Map();
let opened; const connected = new Promise(resolve => { opened = resolve; });
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  removeStorageSync: key => storage.delete(key), getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, stopPullDownRefresh() {},
  request(options) { const url = new URL(options.url); fetch(x.base + url.pathname + url.search, {
    method: options.method, headers: options.header,
    ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }),
  }).then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail); },
  connectSocket({ url }) { const u = new URL(url), target = new URL(x.wsBase), socket = new WebSocket(`ws://${target.host}${u.pathname}${u.search}`); return {
    onOpen(fn) { socket.addEventListener('open', () => { fn(); opened(); }); },
    onMessage(fn) { socket.addEventListener('message', e => fn({ data: e.data })); },
    onClose(fn) { socket.addEventListener('close', fn); }, onError(fn) { socket.addEventListener('error', fn); },
    send({ data }) { socket.send(data); }, close() { socket.close(); },
  }; },
};
async function wait(predicate, message) { for (let i = 0; i < 500; i++) { if (predicate()) return; await new Promise(r => setTimeout(r, 25)); } throw Error(message); }
let definition; global.Page = value => { definition = value; };
require(path.join(root, 'core/auth')).setSession({ token: x.token, user: { id: x.account, role: 'customer' } });
function makePage(file) { require(path.join(root, file)); const page = { ...definition, data: structuredClone(definition.data), setData(p) { Object.assign(this.data, p); } };
  for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page); return page; }
(async () => {
  const mine = makePage('pages/mine/index.js'), membership = makePage('pages/membership/index.js'), checkout = makePage('pages/checkout/index.js');
  mine.onLoad({}); membership.onLoad({}); checkout.onLoad({ service_id: x.serviceId });
  mine._visible = membership._visible = checkout._visible = true;
  await Promise.all([mine.load(), membership.load(), checkout.load()]);
  assert.equal(membership.data.member.vip, null);
  assert.equal(checkout.data.quote.amount, 10000);
  checkout.input({ currentTarget: { dataset: { field: 'region' } }, detail: { value: '未提交的大区' } });
  const key = membership.data.member.tags.find(tag => tag.title === '体验标签').key;
  membership.tag({ currentTarget: { dataset: { key } } });
  assert.deepEqual(membership.data.selected, [key]);
  const realtime = require(path.join(root, 'core/realtime'));
  try {
    realtime.show(); await connected; await new Promise(resolve => setTimeout(resolve, 650));
    const task = spawnSync(x.python, ['-c', `import django; django.setup(); from engagement.confirmation import process_order; assert process_order(${x.orderId})`],
      { cwd: x.backend, env: process.env, encoding: 'utf8', timeout: 30000 });
    assert.equal(task.status, 0, task.stderr || task.stdout);
    await wait(() => mine.data.member?.vip?.level === 6, 'mine did not refresh VIP');
    await wait(() => !checkout.data.quoting && checkout.data.quote?.amount === 9000, 'checkout did not refresh VIP discount');
    assert.equal(checkout.data.region, '未提交的大区');
    await wait(() => membership._membershipRefreshPending, 'membership did not defer refresh while editing');
    assert.equal(membership.data.member.vip, null);
    assert.deepEqual(membership.data.selected, [key]);
    assert.equal(membership.data.refreshPending, true);
    await membership.update({ birthday_public: false }, 'birthday');
    assert.deepEqual(membership.data.selected, [key], 'saving birthday must preserve unsaved tags');
    assert.equal(membership.data.member?.vip?.level, 6);
    assert.equal(membership.data.member.birthday_public, false);
    assert.equal(membership.data.member.consumption_amount, 10000);
    membership.discardTagEdits();
    await wait(() => !membership._membershipTimer && !membership.data.loading && membership.data.selected.length === 0, 'discard did not clear tag edits');
    membership.tag({ currentTarget: { dataset: { key } } });
    const response = await fetch(`${x.base}/api/admin/vip-tiers/${x.tierId}/`, { method: 'PATCH',
      headers: { Authorization: `Bearer ${x.staffToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ benefits: { '专属说明': '配置更新' } }) });
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    await wait(() => mine.data.member?.vip?.benefits?.['专属说明'] === '配置更新', 'mine did not refresh tier configuration');
    await wait(() => membership._membershipRefreshPending, 'configuration refresh did not defer');
    assert.deepEqual(membership.data.selected, [key]);
    assert.equal(membership.data.refreshPending, true);
    membership.discardTagEdits();
    await wait(() => membership.data.member?.vip?.benefits?.['专属说明'] === '配置更新', 'membership did not refresh tier configuration');
    assert.equal(membership.data.refreshPending, false);
  } finally { realtime.hide(); mine.onUnload(); membership.onUnload(); checkout.onUnload(); }
  console.log('Actual VIP confirmation refreshed native pages while preserving unsaved tag edits');
})().then(() => process.exit(0)).catch(error => { console.error(error.stack || error); process.exit(1); });
