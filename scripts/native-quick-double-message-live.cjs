// The boss message destination and order display, driven by real HTTP data.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map(); let navigation = '';
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
  getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), showToast() {},
  navigateTo: ({ url }) => { navigation = url; },
  request(options) {
    const url = new URL(options.url);
    fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header })
      .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
  },
};
async function main() {
  require(path.join(root, 'core/auth')).setSession(input.session);
  let definition; global.Page = value => { definition = value; };
  function instantiate() {
    const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
    for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
    return page;
  }
  require(path.join(root, 'pages/message-detail/index.js'));
  const message = instantiate(); message.onLoad({ id: String(input.messageId) }); await message.load();
  assert.equal(message.data.item.title, '已为你指派双陪');
  for (const name of input.names) assert.ok(message.data.item.preview.includes(name));
  message.action(); assert.equal(navigation, `/pages/order-detail/index?id=${input.orderId}`); message.onUnload();
  require(path.join(root, 'pages/order-detail/index.js'));
  const order = instantiate(); order.onLoad({ id: String(input.orderId) }); await order.load();
  assert.equal(order.data.order.providerDisplay, input.names.join('、'));
  assert.equal(order.data.order.status, 'GRABBED'); order.onUnload();
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
