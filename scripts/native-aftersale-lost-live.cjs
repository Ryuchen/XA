// Real Django HTTP and business operations; only the first response is discarded.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map(), posts = [], toasts = [];
let transport = Promise.resolve(), drop = true, reason = '首次申请售后';
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast: value => toasts.push(value.title), showModal: value => value.success({ confirm: true, content: reason }),
  stopPullDownRefresh() {},
  request(options) {
    transport = transport.then(async () => {
      try {
        const url = new URL(options.url);
        const response = await fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
          ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) });
        const data = await response.json();
        if (url.pathname.endsWith('/aftersale/') && options.method === 'POST') {
          posts.push(options.data);
          if (drop && data.code === 0) { drop = false; options.fail({ errMsg: 'request:fail response lost' }); return; }
        }
        options.success({ statusCode: response.status, data });
      } catch (error) { options.fail(error); }
    });
  },
};
let definition;
global.Page = value => { definition = value; };
require(path.join(root, 'core/auth')).setSession(input.session);
require(path.join(root, 'pages/order-detail/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
page.onLoad({ id: input.orderId }); page._visible = true;
(async () => {
  await page.load(); assert.equal(page.data.order.canAfterSale, true);
  await page.aftersale();
  assert.equal(page.data.aftersale.status, 'OPEN', 'refresh must recover committed first case');
  const ticket = page.data.aftersale.id;
  const response = await fetch(input.base + `/api/admin/aftersales/${ticket}/resolve/`, { method: 'POST',
    headers: { Authorization: 'Bearer ' + input.staffToken, 'Content-Type': 'application/json' },
    body: JSON.stringify({ resolution: '已处理首次申请' }) });
  const resolved = await response.json(); assert.equal(resolved.code, 0, JSON.stringify(resolved));
  await page.load(); assert.equal(page.data.aftersale.status, 'RESOLVED');
  reason = '第二次新的售后问题';
  await page.aftersale();
  assert.equal(posts.length, 2);
  assert.notEqual(posts[0].client_request_id, posts[1].client_request_id, 'new case needs a new request id');
  assert.equal(page.data.aftersale.status, 'OPEN');
  assert.equal(page.data.aftersale.reason, reason);
  assert.ok(toasts.includes('售后已提交'));
  page.onUnload(); console.log('Real HTTP: committed response lost, resolved, and a new case reopened');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
