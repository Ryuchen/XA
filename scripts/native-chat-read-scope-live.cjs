const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const fixture = JSON.parse(input), storage = new Map(), batches = [];
  global.wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({keys:[...storage.keys()]}),
    getAccountInfoSync: () => ({miniProgram:{envVersion:'develop'}}), showToast() {},
    request(options) {
      const parsed = new URL(options.url);
      if (parsed.pathname === '/api/chat/read/') batches.push(options.data.message_ids);
      fetch(fixture.base + parsed.pathname + parsed.search, {
        method: options.method || 'GET', headers: {...options.header, 'Content-Type':'application/json'},
        body: options.method === 'POST' ? JSON.stringify(options.data || {}) : undefined,
      }).then(async response => options.success({statusCode: response.status, data: await response.json()})).catch(options.fail);
    },
  };
  const auth = require(path.join(root, 'core/auth')); auth.setSession(fixture.session);
  let definition; global.Page = value => { definition = value; };
  require(path.join(root, 'pages/chat/index.js'));
  const page = {...definition, data:structuredClone(definition.data), setData(patch){Object.assign(this.data,patch)}};
  for (const [key,value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({}); page._visible = true;
  const summary = async () => {
    const response = await fetch(`${fixture.base}/api/chat/session/`, {headers:{Authorization:`Bearer ${fixture.session.token}`}});
    return (await response.json()).data.session;
  };
  try {
    await page.load();
    assert.equal(page.data.error, '');
    assert.equal(page.data.items.length, 50);
    assert.ok(page.data.hasMore);
    assert.equal(page.data.items.some(item => item.id === fixture.backdated), false);
    assert.equal((await summary()).unread_user, fixture.total - 50, 'unloaded history must remain unread');
    await page.latest();
    assert.equal((await summary()).unread_user, fixture.total - 50, 'latest must not acknowledge unloaded history');
    while (page.data.hasMore) await page.loadOlder();
    assert.ok(page.data.items.some(item => item.id === fixture.backdated));
    await page.latest();
    assert.equal((await summary()).unread_user, 0, 'loaded historical message can be acknowledged');
    assert.ok(batches.every(ids => ids.length <= 200));
    if (fixture.total > 200) assert.ok(batches.some(ids => ids.length === 200), 'long loaded history must be split into batches');
    console.log('PASS real HTTP backdated chat read scope');
  } finally { page.onUnload(); }
});
