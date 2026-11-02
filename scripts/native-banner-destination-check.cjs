// Exercise the native click handler with responses from real banner APIs.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const actions = [];
let responseRows = input.available;
global.wx = { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
  getStorageSync: () => null,
  request: options => options.success({ statusCode: 200, data: { code: 0, data: responseRows } }),
  navigateTo: value => actions.push({ type: 'navigate', url: value.url }),
  showToast: value => actions.push({ type: 'toast', title: value.title }) };
let definition;
global.Page = value => { definition = value; };
require(path.resolve('apps/boss-miniapp/native/pages/home/index.js'));
const page = { ...definition, _alive: true, _visible: true, data: { banners: input.available } };
(async () => {
  await page.banner({ currentTarget: { dataset: { id: input.bannerId } } });
  assert.deepEqual(actions.pop(), { type: 'navigate', url: `/pages/service/index?id=${input.serviceId}` });
  // The page is still showing the old response, but its next read is current.
  responseRows = input.unavailable;
  await page.banner({ currentTarget: { dataset: { id: input.bannerId } } });
  assert.deepEqual(actions.pop(), { type: 'toast', title: '活动暂不可用，请浏览其他服务' });
  assert.equal(actions.length, 0);
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
