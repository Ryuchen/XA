// Actual native order-detail controller using isolated business HTTP endpoints.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native');
const storage = new Map();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  showToast() {}, stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
  },
};
require(path.join(root, 'core/auth')).setSession({ token: input.token, user: { id: input.bossId, role: 'customer' } });
let definition; global.Page = value => { definition = value; };
require(path.join(root, 'pages/order-detail/index.js'));
const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
(async () => {
  page.onLoad({ id: input.orderId }); page._visible = true; await page.load();
  assert.equal(page.data.order.canEvaluate, true);
  const targets = page.data.order.evaluationTargets;
  assert.deepEqual(targets.map(item => item.provider_account_id), input.providerIds);
  page.content({ detail: { value: '独立评价0' } });
  page.selectEvaluationTarget({ currentTarget: { dataset: { key: targets[1].key } } });
  page.score({ detail: { value: 4 } }); page.content({ detail: { value: '独立评价1' } });
  page.selectEvaluationTarget({ currentTarget: { dataset: { key: targets[0].key } } });
  assert.equal(page.data.content, '独立评价0');
  await page.evaluate();
  assert.equal(page.data.order.canEvaluate, true);
  assert.equal(page.data.order.evaluations.length, 1);
  assert.equal(page.data.order.evaluationProgress, '待评价 · 已评 1/2 人');
  assert.equal(page.data.evaluationTarget, targets[1].key);
  assert.equal(page.data.score, 4); assert.equal(page.data.content, '独立评价1');
  await page.evaluate();
  assert.equal(page.data.order.canEvaluate, false);
  assert.equal(page.data.order.evaluations.length, 2);
  page.onUnload();
  console.log('Both individual native evaluations submitted through real APIs');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
