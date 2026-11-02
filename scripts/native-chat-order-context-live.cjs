const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', async () => {
  const fixture = JSON.parse(input), storage = new Map();
  const imagePath = path.join(root, 'assets/icons/chat.png');
  let gate = null, failOrderOnce = false, loseSendResponseOnce = false, confirmModal = true;
  // SQLite LiveServer shares a test connection across threads. Serialize
  // real HTTP responses, while allowing our response gate to release later.
  let transport = Promise.resolve();
  function realFetch(url, options) {
    const pending = transport.then(async () => {
      const response = await fetch(url, options);
      const data = await response.json();
      return {status:response.status, json:async () => data};
    });
    transport = pending.then(() => {}, () => {});
    return pending;
  }
  global.wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({keys:[...storage.keys()]}),
    getAccountInfoSync: () => ({miniProgram:{envVersion:'develop'}}), showToast() {},
    showModal: options => options.success({confirm:confirmModal, cancel:!confirmModal}),
    getFileSystemManager: () => ({getFileInfo: options => options.success({size:fs.statSync(options.filePath).size})}),
    uploadFile(options) {
      (async () => {
        const parsed = new URL(options.url), body = new FormData();
        body.append(options.name, new Blob([fs.readFileSync(options.filePath)], {type:'image/png'}), 'chat.png');
        for (const [key, value] of Object.entries(options.formData || {})) body.append(key, String(value));
        const response = await realFetch(fixture.base + parsed.pathname + parsed.search, {method:'POST', headers:options.header, body});
        options.success({statusCode:response.status, data:JSON.stringify(await response.json())});
      })().catch(options.fail);
    },
    request(options) {
      (async () => {
        const parsed = new URL(options.url);
        if (failOrderOnce && parsed.pathname === `/api/orders/orders/${fixture.orderId}/`) {
          failOrderOnce = false;
          return options.fail({errMsg:'request:fail network unavailable'});
        }
        const response = await realFetch(fixture.base + parsed.pathname + parsed.search, {
          method: options.method || 'GET', headers: {...options.header, 'Content-Type':'application/json'},
          body: options.method === 'POST' ? JSON.stringify(options.data || {}) : undefined,
        });
        const data = await response.json();
        if (loseSendResponseOnce && parsed.pathname === '/api/chat/send/' && data.code === 0) {
          loseSendResponseOnce = false;
          return options.fail({errMsg:'request:fail response lost after commit'});
        }
        if (gate && parsed.pathname === `/api/orders/orders/${fixture.orderId}/`) {
          const current = gate; gate = null; current.arrived(); await current.release;
        }
        options.success({statusCode:response.status, data});
      })().catch(options.fail);
    },
  };
  const auth = require(path.join(root, 'core/auth'));
  auth.setSession(fixture.sessions[0]);
  let definition; global.Page = value => { definition = value };
  require(path.join(root, 'pages/chat/index.js'));
  const makePage = (orderId = fixture.orderId) => {
    const page = {...definition, data:structuredClone(definition.data), setData(patch){Object.assign(this.data, patch)}};
    for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
    page.onLoad({order_id:String(orderId)}); page._visible = true; return page;
  };
  let page = makePage();
  try {
    await page.loadContext();
    assert.equal(page.data.contextError, '');
    assert.equal(page.data.content, `我想咨询订单 ${fixture.orderNo} 的服务情况。`);
    await page.send();
    const own = await realFetch(`${fixture.base}/api/chat/session/`, {headers:{Authorization:`Bearer ${fixture.sessions[0].token}`}}).then(r => r.json());
    assert.equal(own.code, 0); const sessionId = own.data.session.id;
    const staff = await realFetch(`${fixture.base}/api/admin/chat-sessions/${sessionId}/messages/`, {headers:{Authorization:`Bearer ${fixture.staffToken}`}}).then(r => r.json());
    assert.equal(staff.code, 0);
    assert.equal(staff.data.list.at(-1).content, `我想咨询订单 ${fixture.orderNo} 的服务情况。`);
    page.input({detail:{value:'这段草稿不应随图片发送'}});
    await page.sendImage({tempFilePath:imagePath, size:fs.statSync(imagePath).size});
    assert.equal(page.data.content, '这段草稿不应随图片发送');
    const images = await realFetch(`${fixture.base}/api/admin/chat-sessions/${sessionId}/messages/`, {headers:{Authorization:`Bearer ${fixture.staffToken}`}}).then(r => r.json());
    assert.equal(images.data.list.at(-1).content_type, 'IMAGE');
    assert.equal(images.data.list.at(-1).content, `订单号 ${fixture.orderNo}`);
    assert.ok(images.data.list.at(-1).image_url);
    page.clearContext();
    assert.equal(page.data.contextOrderNo, '');
    assert.equal(page.data.content, '这段草稿不应随图片发送');
    assert.equal(storage.get(`xa:native:chat-draft:${fixture.sessions[0].user.id}:general`), '这段草稿不应随图片发送');
    assert.equal(storage.get(`xa:native:chat-draft:${fixture.sessions[0].user.id}:${fixture.orderId}`), undefined);
    await page.sendImage({tempFilePath:imagePath, size:fs.statSync(imagePath).size});
    const unlinked = await realFetch(`${fixture.base}/api/admin/chat-sessions/${sessionId}/messages/`, {headers:{Authorization:`Bearer ${fixture.staffToken}`}}).then(r => r.json());
    assert.equal(unlinked.data.list.at(-1).content, '');
    page.onUnload();
    page = makePage(fixture.secondOrderId);
    await page.loadContext();
    assert.equal(page.data.contextOrderNo, fixture.secondOrderNo);
    await page.sendImage({tempFilePath:imagePath, size:fs.statSync(imagePath).size});
    const secondOrderMessages = await realFetch(`${fixture.base}/api/admin/chat-sessions/${sessionId}/messages/`, {headers:{Authorization:`Bearer ${fixture.staffToken}`}}).then(r => r.json());
    assert.equal(secondOrderMessages.data.list.at(-1).content, `订单号 ${fixture.secondOrderNo}`);
    page.onUnload();
    page = makePage(); failOrderOnce = true;
    await page.loadContext();
    assert.match(page.data.contextError, /订单号暂未读取/);
    page.input({detail:{value:'我想问服务时间'}});
    await page.retryContext();
    assert.equal(page.data.contextError, '');
    assert.equal(page.data.content, `我想咨询订单 ${fixture.orderNo} 的服务情况。\n我想问服务时间`);
    page.onUnload();
    page = makePage(fixture.secondOrderId);
    await page.loadContext();
    assert.equal(page.data.content, `我想咨询订单 ${fixture.secondOrderNo} 的服务情况。`, 'another order has its own draft');
    page.onUnload();
    auth.setSession(fixture.sessions[1]);
    page = makePage();
    await page.loadContext();
    assert.match(page.data.contextError, /订单号暂未读取/);
    assert.equal(page.data.content, '');
    assert.equal(page.data.contextOrderNo, '');
    await page.sendImage({tempFilePath:imagePath, size:fs.statSync(imagePath).size});
    const otherSession = await realFetch(`${fixture.base}/api/chat/session/`, {headers:{Authorization:`Bearer ${fixture.sessions[1].token}`}}).then(r => r.json());
    const otherMessages = await realFetch(`${fixture.base}/api/admin/chat-sessions/${otherSession.data.session.id}/messages/`, {headers:{Authorization:`Bearer ${fixture.staffToken}`}}).then(r => r.json());
    assert.equal(otherMessages.data.list.at(-1).content, '');
    page.onUnload();
    // A real rejected access/refresh token clears the session while an order
    // lookup is in flight. Logging back into the same account resumes the
    // consultation and its order number reaches the actual staff inbox.
    auth.setSession({...fixture.sessions[0], token:'expired-access', refresh:'expired-refresh'});
    page = makePage();
    await page.loadContext();
    assert.equal(auth.getSession(), null, 'expired credentials must require login');
    assert.equal(page.data.contextOrderNo, '');
    auth.setSession(fixture.sessions[0]);
    for (let attempt = 0; attempt < 100 && page.data.contextOrderNo !== fixture.orderNo; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.equal(page.data.contextOrderNo, fixture.orderNo, 'same-account login resumes the order consultation');
    assert.equal(page.data.content, `我想咨询订单 ${fixture.orderNo} 的服务情况。\n我想问服务时间`);
    await page.send();
    const recovered = await realFetch(`${fixture.base}/api/admin/chat-sessions/${sessionId}/messages/`, {headers:{Authorization:`Bearer ${fixture.staffToken}`}}).then(r => r.json());
    assert.equal(recovered.data.list.at(-1).content, `我想咨询订单 ${fixture.orderNo} 的服务情况。\n我想问服务时间`);
    page.input({detail:{value:'登录后保留的咨询文字'}});
    await page.sendImage({tempFilePath:imagePath, size:fs.statSync(imagePath).size});
    const recoveredImage = await realFetch(`${fixture.base}/api/admin/chat-sessions/${sessionId}/messages/`, {headers:{Authorization:`Bearer ${fixture.staffToken}`}}).then(r => r.json());
    assert.equal(recoveredImage.data.list.at(-1).content_type, 'IMAGE');
    assert.equal(recoveredImage.data.list.at(-1).content, `订单号 ${fixture.orderNo}`);
    assert.equal(page.data.content, '登录后保留的咨询文字');
    // The customer can replace the prefill; the active order still reaches
    // staff, including when a committed response is lost and later checked.
    loseSendResponseOnce = true;
    await page.send();
    assert.equal(page.data.pendingSend, true);
    page.onUnload();
    page = makePage(); await page.loadContext();
    await page.checkPending();
    assert.equal(page.data.content, '');
    const edited = await realFetch(`${fixture.base}/api/admin/chat-sessions/${sessionId}/messages/`, {headers:{Authorization:`Bearer ${fixture.staffToken}`}}).then(r => r.json());
    assert.equal(edited.data.list.filter(item => item.content === `订单号 ${fixture.orderNo}\n登录后保留的咨询文字`).length, 1);
    page.input({detail:{value:'长'.repeat(1000)}});
    await page.send();
    assert.equal(page.data.pendingSend, false, 'an overlong message stays editable without being sent');
    assert.equal(page.data.content.length, 1000);
    const afterLongText = await realFetch(`${fixture.base}/api/admin/chat-sessions/${sessionId}/messages/`, {headers:{Authorization:`Bearer ${fixture.staffToken}`}}).then(r => r.json());
    assert.equal(afterLongText.data.list.length, edited.data.list.length);
    page.input({detail:{value:''}});
    page.onUnload();
    // The access and refresh tokens can both expire after the customer has
    // already composed a message. Exercise real 401s and then send the
    // restored draft to the actual staff inbox.
    auth.setSession({...fixture.sessions[0], refresh:'expired-refresh'});
    page = makePage();
    await page.load();
    await page.loadContext();
    const draftBeforeExpiry = `我想咨询订单 ${fixture.orderNo} 的服务情况。\n想确认今晚的开始时间`;
    page.input({detail:{value:draftBeforeExpiry}});
    auth.updateToken('expired-access');
    await page.load(true);
    assert.equal(auth.getSession(), null);
    auth.setSession(fixture.sessions[0]);
    for (let attempt = 0; attempt < 100 && page.data.contextOrderNo !== fixture.orderNo; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.equal(page.data.content, draftBeforeExpiry, 'same-account login restores the unsent draft exactly once');
    await page.send();
    const restoredDraft = await realFetch(`${fixture.base}/api/admin/chat-sessions/${sessionId}/messages/`, {headers:{Authorization:`Bearer ${fixture.staffToken}`}}).then(r => r.json());
    assert.equal(restoredDraft.data.list.at(-1).content, draftBeforeExpiry);
    page.onUnload();
    auth.clearSession();
    page = makePage();
    await page.loadContext();
    assert.equal(page.data.contextOrderNo, '');
    auth.setSession(fixture.sessions[0]);
    for (let attempt = 0; attempt < 100 && page.data.contextOrderNo !== fixture.orderNo; attempt++) {
      await new Promise(resolve => setTimeout(resolve, 20));
    }
    assert.equal(page.data.contextOrderNo, fixture.orderNo, 'login from the consultation opens the requested order');
    page.clearContext();
    auth.clearSession();
    auth.setSession(fixture.sessions[0]);
    await transport;
    assert.equal(page.data.contextOrderNo, '', 'a deliberately cleared association must stay cleared');
    page.onUnload();
    // A different customer signing in after logout does not inherit the
    // previous customer's association or draft.
    auth.setSession(fixture.sessions[0]);
    page = makePage();
    await page.load();
    await page.loadContext();
    page.input({detail:{value:'上一位老板未发送的文字'}});
    storage.set(`xa:native:chat-draft:${fixture.sessions[1].user.id}:general`, '另一位老板的普通咨询');
    storage.set(`xa:native:chat-draft:${fixture.sessions[1].user.id}:${fixture.orderId}`, '不应恢复的旧订单草稿');
    auth.clearSession();
    auth.setSession(fixture.sessions[1]);
    await transport;
    assert.equal(page._contextOrderId, '');
    assert.equal(page.data.contextOrderNo, '');
    assert.equal(page.data.content, '另一位老板的普通咨询');
    assert.equal(page._draftScope, 'general');
    page.onUnload();
    // Leave and reopen the order consultation, then lose the response after
    // the real server commits. A receipt lookup clears only the sent draft.
    auth.setSession(fixture.sessions[0]);
    page = makePage(); await page.loadContext();
    const durableDraft = `我想咨询订单 ${fixture.orderNo} 的服务情况。\n离开页面后继续咨询`;
    page.input({detail:{value:durableDraft}});
    page.onUnload();
    page = makePage(); await page.loadContext();
    assert.equal(page.data.content, durableDraft, 'reopening the same consultation restores its draft');
    loseSendResponseOnce = true;
    await page.send();
    assert.equal(page.data.pendingSend, true);
    page.onUnload();
    page = makePage(); await page.loadContext();
    assert.equal(page.data.pendingRestored, true);
    assert.equal(page.data.content, durableDraft);
    await page.checkPending();
    assert.equal(page.data.pendingSend, false);
    assert.equal(page.data.content, '', 'confirmed delivery clears the matching draft');
    const finalMessages = await realFetch(`${fixture.base}/api/admin/chat-sessions/${sessionId}/messages/`, {headers:{Authorization:`Bearer ${fixture.staffToken}`}}).then(r => r.json());
    assert.equal(finalMessages.data.list.filter(item => item.content === durableDraft).length, 1);
    page.onUnload();
    page = makePage(); await page.loadContext();
    assert.equal(page.data.content, `我想咨询订单 ${fixture.orderNo} 的服务情况。`, 'a delivered draft is not restored');
    page.onUnload();
    page = makePage(fixture.secondOrderId); await page.loadContext();
    const secondDraft = `我想咨询订单 ${fixture.secondOrderNo} 的服务情况。\n第二笔订单的草稿`;
    page.input({detail:{value:secondDraft}}); page.onUnload();
    page = makePage(); await page.loadContext();
    const crossOrderDraft = `我想咨询订单 ${fixture.orderNo} 的服务情况。\n第一笔订单已经发送`;
    page.input({detail:{value:crossOrderDraft}});
    loseSendResponseOnce = true; await page.send(); page.onUnload();
    page = makePage(fixture.secondOrderId); await page.loadContext();
    assert.equal(page.data.content, secondDraft);
    await page.checkPending();
    assert.equal(page.data.content, secondDraft, 'checking another order must keep this order draft');
    page.onUnload();
    page = makePage(); await page.loadContext();
    assert.equal(page.data.content, `我想咨询订单 ${fixture.orderNo} 的服务情况。`, 'receipt clears the original order draft');
    page.onUnload();
    auth.setSession(fixture.sessions[0]);
    page = makePage();
    let release, arrived;
    const ready = new Promise(resolve => { arrived = resolve });
    gate = {arrived, release: new Promise(resolve => {release = resolve})};
    const pending = page.loadContext(); await ready;
    assert.equal(page.data.contextLoading, true);
    auth.setSession(fixture.sessions[1]);
    release(); await pending;
    assert.equal(page.data.contextLoading, false);
    assert.equal(page.data.content, '另一位老板的普通咨询');
    assert.equal(page.data.contextError, '');
    assert.equal(page.data.contextOrderNo, '');
    page.onUnload();
    auth.setSession(fixture.sessions[0]);
    page = makePage(); await page.loadContext();
    page.input({detail:{value:'订单草稿需要保留'}});
    confirmModal = false; page.clearContext();
    assert.equal(page.data.contextOrderNo, fixture.orderNo, 'cancel keeps the order association');
    confirmModal = true; page.clearContext();
    assert.equal(page.data.contextOrderNo, '');
    assert.equal(page.data.content, '这段草稿不应随图片发送', 'switch displays the existing general draft');
    page.onUnload();
    page = makePage(); await page.loadContext();
    assert.equal(page.data.content, `我想咨询订单 ${fixture.orderNo} 的服务情况。\n订单草稿需要保留`, 'order draft remains available on return');
    console.log('PASS real order context in customer support');
  } finally { page.onUnload(); await transport; }
});
