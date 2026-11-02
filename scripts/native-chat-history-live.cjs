const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const fixture = JSON.parse(input), storage = new Map();
  let releaseOld, markHeld, failNextHistoryPage = false;
  const oldHeld = new Promise(resolve => { markHeld = resolve; });
  const releaseGate = new Promise(resolve => { releaseOld = resolve; });
  global.wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({keys:[...storage.keys()]}),
    getAccountInfoSync: () => ({miniProgram:{envVersion:'develop'}}), showToast() {},
    request(options) {
      (async () => {
        const parsed = new URL(options.url);
        if (failNextHistoryPage && parsed.pathname === '/api/chat/session/' && parsed.searchParams.has('before_id')) {
          failNextHistoryPage = false;
          options.fail({errMsg:'request:fail offline'});
          return;
        }
        const response = await fetch(fixture.base + parsed.pathname + parsed.search, {
          method: options.method || 'GET', headers: {...options.header, 'Content-Type':'application/json'},
          body: options.method === 'POST' ? JSON.stringify(options.data || {}) : undefined,
        });
        const body = await response.json();
        if (parsed.pathname === '/api/chat/send/' && options.data?.content === '旧账号消息') {
          assert.equal(body.code, 0); markHeld(); await releaseGate;
        }
        options.success({statusCode:response.status, data:body});
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
    assert.equal(page.data.items.length, 50); assert.equal(page.data.hasMore, true);
    const firstVisible = page.data.items[0].id;
    await page.loadOlder();
    assert.equal(page.data.items.length, 100); assert.equal(page.data.scrollTo, `message-${firstVisible}`);
    assert.equal(page.data.historyBrowsing, true); assert.equal(page.data.hasMore, true);
    await page.loadOlder();
    assert.equal(page.data.items.length, 105); assert.equal(page.data.hasMore, false);
    assert.equal(page.data.items[0].content, '历史消息 000');
    const oldScroll = page.data.scrollTo;
    const added = await fetch(`${fixture.base}/api/admin/chat-sessions/${fixture.sessionId}/reply/`, {
      method:'POST', headers:{Authorization:`Bearer ${fixture.adminToken}`, 'Content-Type':'application/json'},
      body:JSON.stringify({content:'查看历史期间收到的新消息'}),
    });
    const newMessage = await added.json(); assert.equal(newMessage.code, 0);
    // While the customer is reading older history, more than one page may
    // arrive during a disconnect. Reconnect must fill the gap without moving
    // the reader or prematurely marking the new replies read.
    page.onHide();
    for (let index = 0; index < 60; index++) {
      const response = await fetch(`${fixture.base}/api/admin/chat-sessions/${fixture.sessionId}/reply/`, {
        method:'POST', headers:{Authorization:`Bearer ${fixture.adminToken}`, 'Content-Type':'application/json'},
        body:JSON.stringify({content:`阅读历史期间回复 ${String(index).padStart(2, '0')}`}),
      });
      assert.equal((await response.json()).code, 0);
    }
    page._visible = true;
    await page.load(true);
    assert.equal(page.data.items.length, 166); assert.equal(page.data.scrollTo, oldScroll);
    assert.equal(page.data.hasMore, false);
    let current = await (await fetch(`${fixture.base}/api/chat/session/`, {headers:{Authorization:`Bearer ${fixture.session.token}`}})).json();
    // The newest page was acknowledged on entry; the 55 older replies
    // remain unread until the customer returns to the loaded messages.
    assert.equal(current.data.session.unread_user, 116);
    await page.latest();
    assert.equal(page.data.historyBrowsing, false);
    assert.equal(page.data.items.at(-1).content, '阅读历史期间回复 59');
    assert.equal(page.data.scrollTo, `message-${page.data.items.at(-1).id}`);
    current = await (await fetch(`${fixture.base}/api/chat/session/`, {headers:{Authorization:`Bearer ${fixture.session.token}`}})).json();
    assert.equal(current.data.session.unread_user, 0);
    assert.equal(new Set(page.data.items.map(item=>item.id)).size, 166);
    // More than one page arrives while the customer is away. On return the
    // newest page alone does not overlap the existing history.
    page.onHide();
    for (let index = 0; index < 60; index++) {
      const response = await fetch(`${fixture.base}/api/admin/chat-sessions/${fixture.sessionId}/reply/`, {
        method:'POST', headers:{Authorization:`Bearer ${fixture.adminToken}`, 'Content-Type':'application/json'},
        body:JSON.stringify({content:`离线期间回复 ${String(index).padStart(2, '0')}`}),
      });
      assert.equal((await response.json()).code, 0);
    }
    page._visible = true;
    failNextHistoryPage = true;
    await page.load(true);
    assert.equal(page.data.items.length, 166, 'a failed gap read must not display a partial timeline');
    assert.ok(page.data.error, 'a failed gap read must be visible and retryable');
    current = await (await fetch(`${fixture.base}/api/chat/session/`, {headers:{Authorization:`Bearer ${fixture.session.token}`}})).json();
    assert.equal(current.data.session.unread_user, 60, 'failed catch-up must not acknowledge unseen replies');
    await page.load(true);
    assert.equal(page.data.items.length, 226, 'reconnect must fill the gap between old history and the latest page');
    assert.ok(page.data.items.some(item => item.content === '离线期间回复 00'));
    assert.ok(page.data.items.some(item => item.content === '离线期间回复 59'));
    current = await (await fetch(`${fixture.base}/api/chat/session/`, {headers:{Authorization:`Bearer ${fixture.session.token}`}})).json();
    assert.equal(current.data.session.unread_user, 0, 'all newly loaded replies should be acknowledged');
    // The customer can scroll up within the already loaded page without
    // touching "load older". New replies must not drag them to the bottom.
    assert.equal(typeof page.onHistoryScroll, 'function', 'manual history scrolling must be tracked');
    page.onHistoryScroll({detail:{scrollTop:500}});
    page.onHistoryScroll({detail:{scrollTop:1000}});
    assert.equal(page.data.historyBrowsing, false, 'scrolling toward the latest message is not history browsing');
    page.onHistoryScroll({detail:{scrollTop:600}});
    assert.equal(page.data.historyBrowsing, true);
    const whileReading = await fetch(`${fixture.base}/api/admin/chat-sessions/${fixture.sessionId}/reply/`, {
      method:'POST', headers:{Authorization:`Bearer ${fixture.adminToken}`, 'Content-Type':'application/json'},
      body:JSON.stringify({content:'手动上滚时收到的回复'}),
    });
    const whileReadingMessage = await whileReading.json(); assert.equal(whileReadingMessage.code, 0);
    const manualScroll = page.data.scrollTo;
    await page.load(true);
    assert.equal(page.data.scrollTo, manualScroll, 'new replies must not move a customer reading history');
    current = await (await fetch(`${fixture.base}/api/chat/session/`, {headers:{Authorization:`Bearer ${fixture.session.token}`}})).json();
    assert.equal(current.data.session.unread_user, 1, 'new reply stays unread while reading history');
    await page.latest();
    current = await (await fetch(`${fixture.base}/api/chat/session/`, {headers:{Authorization:`Bearer ${fixture.session.token}`}})).json();
    assert.equal(current.data.session.unread_user, 0);
    // A touch alone does not claim that history moved; the first real scroll
    // after a downward swipe still enters history mode without an earlier event.
    page._historyScrollTop = null;
    page.onHistoryTouchStart({touches:[{clientY:100}]});
    page.onHistoryTouchMove({touches:[{clientY:130}]});
    assert.equal(page.data.historyBrowsing, false);
    page.onHistoryScroll({detail:{scrollTop:350}});
    assert.equal(page.data.historyBrowsing, true);
    page.onHistoryTouchEnd();
    await page.latest();
    page.setData({content:'旧账号消息'});
    const pending = page.send(); await oldHeld;
    page._visible = false;
    auth.setSession(fixture.otherSession);
    page.setData({content:'新账号草稿'});
    assert.equal(page.data.items.length, 0); assert.equal(page.data.sending, false);
    releaseOld(); await pending;
    assert.equal(page.data.content, '新账号草稿'); assert.equal(page.data.items.length, 0);
    console.log('PASS real HTTP chat history');
  } finally {page.onUnload();}
});
