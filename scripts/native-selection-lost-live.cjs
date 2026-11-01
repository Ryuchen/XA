// Use real Django HTTP; discard only the committed selection response.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map(), toasts = [];
let transport = Promise.resolve(), selections = 0;
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast: value => toasts.push(value.title), showModal: value => value.success({ confirm: true }),
  stopPullDownRefresh() {},
  request(options) {
    transport = transport.then(async () => {
      try {
        const url = new URL(options.url);
        const response = await fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
          ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) });
        const data = await response.json();
        if (/\/applications\/\d+\/select\/$/.test(url.pathname) && options.method === 'POST') {
          selections++;
          if (data.code === 0) { options.fail({ errMsg: 'request:fail response lost' }); return; }
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
  await page.load();
  assert.equal(page.data.order.applications[0].ready, true);
  await page.selectProvider({ currentTarget: { dataset: { id: input.applicationId } } });
  assert.equal(selections, 1);
  assert.equal(page.data.order.status, 'GRABBED');
  assert.equal(page.data.order.canSelectProvider, false);
  assert.ok(toasts.includes('选择结果未确认，请核对订单状态'));
  page.onUnload();
  console.log('Real HTTP: selection committed, response lost, order state recovered');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
