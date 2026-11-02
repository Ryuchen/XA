// Real native page and API against an isolated Django LiveServer; only wx host APIs are substituted.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({keys: [...storage.keys()]}),
  getAccountInfoSync: () => ({miniProgram: {envVersion: 'develop'}}),
  showToast() {}, stopPullDownRefresh() {},
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, {method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : {body: JSON.stringify(options.data)})})
      .then(async response => options.success({statusCode: response.status, data: await response.json()}))
      .catch(error => options.fail(error));
  },
};
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(test) { for (let i=0; i<300; i++) { if (test()) return; await sleep(20); } throw Error('native page did not settle'); }
async function push(title) {
  const response = await fetch(input.base + '/api/admin/messages/push/', {method:'POST',
    headers:{Authorization:'Bearer '+input.staffToken,'Content-Type':'application/json'},
    body:JSON.stringify({audience:'CUSTOM',recipient_account_ids:[input.firstAccount],title})});
  assert.equal((await response.json()).code, 0);
}
async function main() {
  const auth = require(path.join(root, 'core/auth'));
  const events = require(path.join(root, 'core/events'));
  auth.setSession(input.first);
  let definition; global.Page = value => { definition = value; };
  require(path.join(root, 'pages/messages/index.js'));
  const page = {...definition, data:structuredClone(definition.data), setData(patch) {Object.assign(this.data, patch);}};
  for (const [key,value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad(); page._visible=true; await page.load();
  assert.equal(page.data.items.length, 20);
  // A notice arriving before the next scroll must not shift the old page.
  await push('翻页期间的新消息');
  await page.loadMore(); assert.equal(page.data.items.length, 40);
  await page.loadMore(); assert.ok(page.data.items.some(item => item.id === input.oldest));
  assert.equal(new Set(page.data.items.map(item => item.id)).size, 55);
  await push('退款后新消息');
  events.emit('site-message.changed'); await waitFor(() => !page.data.loading && page.data.items.some(item => item.title === '退款后新消息'));
  assert.ok(page.data.items.some(item => item.id === input.oldest), 'new notice discarded already loaded history');
  assert.ok(page.data.items.some(item => item.title === '翻页期间的新消息'));
  assert.equal(new Set(page.data.items.map(item => item.id)).size, 57);
  assert.equal(page.data.unread, 57);
  page.onHide(); await push('离线期间的新消息'); page.onShow();
  await waitFor(() => !page.data.loading && page.data.items.some(item => item.title === '离线期间的新消息'));
  assert.ok(page.data.items.some(item => item.id === input.oldest), 'return discarded loaded history');
  assert.equal(page.data.unread, 58);
  await page.markRead(); await waitFor(() => !page.data.loading && page.data.unread === 0);
  assert.ok(page.data.items.some(item => item.id === input.oldest), 'read all discarded loaded history');
  auth.setSession(input.second); await waitFor(() => !page.data.loading && page.data.items.some(item => item.title === '另一账号消息'));
  assert.equal(page.data.items.length, 1); page.onUnload();
}
main().catch(error => { console.error(error); process.exitCode = 1; });
