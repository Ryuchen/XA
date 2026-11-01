const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const fixture = JSON.parse(input), storage = new Map();
  const adminReply = async content => {
    const response = await fetch(`${fixture.base}/api/admin/chat-sessions/${fixture.sessionId}/reply/`, {
      method: 'POST', headers: {'Authorization': `Bearer ${fixture.adminToken}`, 'Content-Type': 'application/json'},
      body: JSON.stringify({content}),
    });
    const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body)); return body.data;
  };
  const first = await adminReply('首次咨询回复');
  let delayed = false, secondId = 0;
  global.wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({keys:[...storage.keys()]}),
    getAccountInfoSync: () => ({miniProgram:{envVersion:'develop'}}), showToast() {},
    request(options) {
      (async () => {
        const parsed = new URL(options.url);
        if (parsed.pathname === '/api/chat/read/' && !delayed) {
          delayed = true;
          assert.deepEqual(options.data.message_ids, [first.id]);
          secondId = (await adminReply('页面读取后到达')).id;
        }
        const response = await fetch(fixture.base + parsed.pathname + parsed.search, {
          method: options.method || 'GET', headers: {...options.header, 'Content-Type':'application/json'},
          body: options.method === 'POST' ? JSON.stringify(options.data || {}) : undefined,
        });
        options.success({statusCode:response.status, data:await response.json()});
      })().catch(options.fail);
    },
  };
  const auth = require(path.join(root, 'core/auth'));
  auth.setSession(fixture.session);
  let definition; global.Page = value => {definition = value};
  require(path.join(root, 'pages/chat/index.js'));
  const page = {...definition, data:structuredClone(definition.data), setData(patch){Object.assign(this.data,patch)}};
  for (const [key,value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({}); page._visible = true;
  try {
    await page.load();
    assert.equal(page.data.error, ''); assert.equal(page.data.items.at(-1).id, first.id);
    assert.ok(delayed && secondId > first.id);
    const response = await fetch(`${fixture.base}/api/chat/session/`, {headers:{Authorization:`Bearer ${fixture.session.token}`}});
    const body = await response.json(); assert.equal(body.code,0);
    assert.equal(body.data.session.unread_user,1);
    assert.equal(body.data.messages.find(item => item.id === secondId).is_read,false);
    console.log('PASS real HTTP bounded chat read');
  } finally {page.onUnload();}
});
