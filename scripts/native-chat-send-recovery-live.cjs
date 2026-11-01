// Page controller, actual Django HTTP, and only transport response loss.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
let input = '';
process.stdin.on('data', chunk => input += chunk);
process.stdin.on('end', () => { (async () => {
  const fixture = JSON.parse(input), storage = new Map(), toasts = [];
  const imagePath = path.join(root, 'assets/icons/chat.png');
  let dropText = false, dropImage = false, failStatus = false, rejectPendingStorage = false, transport = Promise.resolve();
  function http(url, options) {
    const pending = transport.then(async () => {
      const response = await fetch(url, options), data = await response.json();
      return { status: response.status, data };
    });
    transport = pending.then(() => {}, () => {});
    return pending;
  }
  global.wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => { if (rejectPendingStorage && key.startsWith('xa:native:chat-pending:')) throw new Error('storage unavailable'); storage.set(key, value); },
    removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({keys:[...storage.keys()]}),
    getAccountInfoSync: () => ({miniProgram:{envVersion:'develop'}}), showToast: value => toasts.push(value.title),
    getFileSystemManager: () => ({getFileInfo: options => options.success({size:fs.statSync(options.filePath).size})}),
    showModal: options => options.success({confirm:false}),
    request(options) {
      (async () => {
        const parsed = new URL(options.url);
        if (failStatus && parsed.pathname === '/api/chat/message-status/') return options.fail({errMsg:'offline'});
        const response = await http(fixture.base + parsed.pathname + parsed.search, {method:options.method || 'GET',
          headers:{...options.header, 'Content-Type':'application/json'},
          body: options.method === 'POST' ? JSON.stringify(options.data || {}) : undefined});
        if (dropText && parsed.pathname === '/api/chat/send/' && response.data.code === 0) {
          dropText = false; return options.fail({errMsg:'lost response'});
        }
        options.success({statusCode:response.status, data:response.data});
      })().catch(options.fail);
    },
    uploadFile(options) {
      (async () => {
        const parsed = new URL(options.url), body = new FormData();
        body.append(options.name, new Blob([fs.readFileSync(options.filePath)], {type:'image/png'}), 'chat.png');
        for (const [key, value] of Object.entries(options.formData || {})) body.append(key, String(value));
        const response = await http(fixture.base + parsed.pathname + parsed.search, {method:'POST', headers:options.header, body});
        if (dropImage && response.data.code === 0) { dropImage = false; return options.fail({errMsg:'lost response'}); }
        options.success({statusCode:response.status, data:JSON.stringify(response.data)});
      })().catch(options.fail);
    },
  };
  const auth = require(path.join(root, 'core/auth'));
  auth.setSession(fixture.sessions[0]);
  let definition; global.Page = value => { definition = value };
  require(path.join(root, 'pages/chat/index.js'));
  const makePage = () => {
    const page = {...definition, data:structuredClone(definition.data), setData(patch){Object.assign(this.data, patch)}};
    for (const [key, value] of Object.entries(page)) if (typeof value === 'function') page[key] = value.bind(page);
    page.onLoad({}); page._visible = true; return page;
  };
  let page = makePage();
  try {
    page.input({detail:{value:'首次消息'}}); dropText = true; await page.send();
    assert.equal(page.data.pendingSend, true);
    assert.equal(page.data.content, '首次消息');
    const firstId = page._pending.id;
    assert.ok(page.data.items.some(row => row.client_message_id === firstId));
    const saved = storage.get('xa:native:chat-pending:' + fixture.sessions[0].user.id);
    assert.deepEqual(saved, {id:firstId, kind:'text'}, 'only the identifier and type are retained');
    page.onUnload(); page = makePage();
    assert.equal(page.data.pendingRestored, true);
    assert.equal(page._pending.id, firstId);
    auth.setSession(fixture.sessions[1]);
    assert.equal(page.data.pendingSend, false, 'other account must not inherit pending state');
    auth.setSession(fixture.sessions[0]);
    assert.equal(page.data.pendingRestored, true, 'original account can recover its pending receipt');
    await page.checkPending();
    assert.equal(page.data.pendingSend, false); assert.equal(page.data.content, '');
    assert.equal(storage.has('xa:native:chat-pending:' + fixture.sessions[0].user.id), false);

    rejectPendingStorage = true;
    page.input({detail:{value:'存储不可用'}}); dropText = true; await page.send();
    assert.equal(page.data.pendingSend, true);
    assert.equal(page.data.pendingStorageError, true, 'show that the page must stay open');
    await page.checkPending(); rejectPendingStorage = false;
    assert.equal(page.data.pendingSend, false);

    page.input({detail:{value:'重试消息'}}); dropText = true; await page.send();
    const retryId = page._pending.id; failStatus = true; await page.checkPending();
    assert.equal(page.data.pendingSend, true); failStatus = false;
    await page.retryPending();
    assert.equal(page.data.pendingSend, false); assert.equal(page.data.content, '');
    assert.ok(page.data.items.some(row => row.client_message_id === retryId));

    dropImage = true; await page.sendImage({tempFilePath:imagePath, size:fs.statSync(imagePath).size});
    assert.equal(page.data.pendingSend, true);
    const imageId = page._pending.id; await page.retryPending();
    assert.equal(page.data.pendingSend, false);
    assert.ok(page.data.items.some(row => row.client_message_id === imageId && row.content_type === 'IMAGE'));

    dropImage = true; await page.sendImage({tempFilePath:imagePath, size:fs.statSync(imagePath).size});
    const restoredImageId = page._pending.id;
    assert.deepEqual(storage.get('xa:native:chat-pending:' + fixture.sessions[0].user.id), {id:restoredImageId, kind:'image'});
    page.onUnload(); page = makePage();
    assert.equal(page.data.pendingRestored, true);
    await page.checkPending();
    assert.equal(page.data.pendingSend, false);
    assert.ok(page.data.items.some(row => row.client_message_id === restoredImageId && row.content_type === 'IMAGE'));

    const headers = {Authorization:'Bearer ' + fixture.sessions[0].token, 'Content-Type':'application/json'};
    const conflict = await http(fixture.base + '/api/chat/send/', {method:'POST', headers,
      body:JSON.stringify({content:'不同内容', client_message_id:retryId})});
    assert.equal(conflict.status, 409); assert.equal(conflict.data.code, 409);
    const alteredImage = new FormData();
    alteredImage.append('image', new Blob([fs.readFileSync(path.join(root, 'assets/icons/award.png'))], {type:'image/png'}), 'different.png');
    alteredImage.append('client_message_id', imageId);
    const imageConflict = await http(fixture.base + '/api/chat/send/', {method:'POST',
      headers:{Authorization:'Bearer ' + fixture.sessions[0].token}, body:alteredImage});
    assert.equal(imageConflict.status, 409); assert.equal(imageConflict.data.code, 409);
    const other = await http(fixture.base + '/api/chat/message-status/?client_message_id=' + retryId,
      {headers:{Authorization:'Bearer ' + fixture.sessions[1].token}});
    assert.equal(other.data.data.status, 'UNKNOWN');
    const legacy = await http(fixture.base + '/api/chat/send/', {method:'POST', headers, body:JSON.stringify({content:'旧客户端'})});
    assert.equal(legacy.data.code, 0);
    assert.ok(toasts.some(value => value.includes('结果未确认')));
  } finally { page.onUnload(); await transport; }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; }); });
