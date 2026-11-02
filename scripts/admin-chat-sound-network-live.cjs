// Real customer messages through isolated ASGI, admin notification and WebAudio.
const fs = require('node:fs'), path = require('node:path'), net = require('node:net'), assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
async function main() {
  const probe = net.createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  const root = path.resolve('apps/admin-web');
  const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    { cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true });
  let browser;
  try {
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw Error('Vite exited');
      try { if ((await fetch(base)).ok) break; } catch {}
      if (i === 119) throw Error('Vite did not start');
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await context.addInitScript(token => {
      localStorage.setItem('xa_admin_token', token);
      const original = AudioContext.prototype.createOscillator;
      AudioContext.prototype.createOscillator = function (...args) { window.__qaChatTones = (window.__qaChatTones || 0) + 1; return original.apply(this, args); };
    }, input.staffToken);
    const page = await context.newPage();
    let readySocket, chatEvents = 0; const ready = new Promise(resolve => { readySocket = resolve; });
    page.on('websocket', socket => socket.on('framereceived', frame => {
      const payload = String(frame.payload);
      if (payload.includes('pong')) readySocket();
      try { if (JSON.parse(payload).type === 'chat_message') chatEvents++; } catch {}
    }));
    await page.goto(base + '/dashboard', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '关闭咨询提示音' }).waitFor({ timeout: 20000 });
    await page.getByRole('button', { name: '关闭客服待派提示音' }).waitFor({ timeout: 20000 });
    let timeout; try { await Promise.race([ready, new Promise((_, reject) => { timeout = setTimeout(() => reject(Error('staff websocket did not connect')), 15000); })]); }
    finally { clearTimeout(timeout); }
    await page.getByRole('button', { name: '关闭咨询提示音' }).click();
    await page.getByRole('button', { name: '开启咨询提示音' }).click();
    const send = async (content, token = input.bossToken) => {
      const response = await fetch(input.api + '/api/chat/send/', { method: 'POST', headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' }, body: JSON.stringify({ content }) });
      const raw = await response.text(); let body;
      try { body = JSON.parse(raw); } catch { throw Error(`chat send HTTP ${response.status}: ${raw.slice(0, 400)}`); }
      assert.equal(body.code, 0, JSON.stringify(body));
    };
    await send('隔离咨询一');
    const notification = page.locator('.el-notification').filter({ hasText: '新的客服咨询' });
    await notification.waitFor({ timeout: 10000 });
    await page.waitForFunction(() => window.__qaChatTones === 1, null, { timeout: 5000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await notification.click();
    await page.waitForURL(/\/chat/);
    await page.locator('.bubble').filter({ hasText: '隔离咨询一' }).waitFor({ timeout: 10000 });
    const waitForRead = async () => {
      for (let attempt = 0; attempt < 100; attempt++) {
        const rows = await fetch(input.api + '/api/admin/chat-sessions/', { headers: { Authorization: 'Bearer ' + input.staffToken } }).then(r => r.json());
        if (rows.data.list.every(row => row.unread_support === 0)) return;
        await new Promise(resolve => setTimeout(resolve, 100));
      }
      throw Error('read acknowledgement did not settle');
    };
    await waitForRead();
    await send('当前会话的新消息');
    await page.locator('.bubble').filter({ hasText: '当前会话的新消息' }).waitFor({ timeout: 10000 });
    await waitForRead();
    assert.equal(await page.locator('.el-notification').filter({ hasText: '新的客服咨询' }).count(), 0, 'active conversation does not alert again');
    await page.locator('.bubble').filter({ hasText: '隔离历史内容 15' }).waitFor({ timeout: 10000 });
    const wrap = page.locator('.msg-area .el-scrollbar__wrap');
    const dimensions = await wrap.evaluate(element => ({ height: element.clientHeight, total: element.scrollHeight }));
    assert.ok(dimensions.total > dimensions.height + 100, 'the current conversation must have scrollable history');
    await wrap.evaluate(element => { element.scrollTop = 0; element.dispatchEvent(new Event('scroll')); });
    await page.getByRole('button', { name: '回到最新消息' }).waitFor({ timeout: 10000 });
    await send('翻看历史时的新咨询');
    await page.locator('.el-notification').filter({ hasText: '新的客服咨询' }).waitFor({ timeout: 10000 });
    assert.ok(await wrap.evaluate(element => element.scrollTop) < 40, 'new consultation does not move the history position');
    const rows = await fetch(input.api + '/api/admin/chat-sessions/', { headers: { Authorization: 'Bearer ' + input.staffToken } }).then(r => r.json());
    assert.ok(rows.data.list.some(row => row.unread_support > 0), 'unseen message remains unread while browsing history');
    await page.locator('.el-notification').filter({ hasText: '新的客服咨询' }).locator('.el-notification__closeBtn').click();
    await page.locator('.el-notification').filter({ hasText: '新的客服咨询' }).waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: '回到最新消息' }).click();
    await waitForRead();
    const search = page.locator('.session-toolbar input');
    await search.fill('no-matching-session-qa'); await search.press('Enter');
    await page.getByText('当前会话不在已加载的列表中', { exact: true }).waitFor({ timeout: 10000 });
    await send('另一位老板的新咨询', input.secondBossToken);
    await page.locator('.el-notification').filter({ hasText: '新的客服咨询' }).waitFor({ timeout: 10000 });
    await page.locator('.el-notification').filter({ hasText: '新的客服咨询' }).click();
    await page.locator('.bubble').filter({ hasText: '另一位老板的新咨询' }).waitFor({ timeout: 10000 });
    await page.getByText('当前会话不在已加载的列表中', { exact: true }).waitFor({ timeout: 10000 });
    await page.getByRole('button', { name: '清除筛选' }).click();
    await page.locator('.session-item').filter({ hasText: '另一位老板的新咨询' }).waitFor({ timeout: 10000 });
    await page.goto(base + '/dashboard');
    await page.getByRole('button', { name: '关闭咨询提示音' }).click();
    await send('隔离咨询二');
    await page.locator('.el-notification').filter({ hasText: '新的客服咨询' }).waitFor({ timeout: 10000 });
    assert.equal(await page.evaluate(() => window.__qaChatTones || 0), 0, 'muting suppresses audio but keeps visible notices');
    const previousEvents = chatEvents;
    await send('同时到达的另一条咨询', input.secondBossToken);
    for (let attempt = 0; attempt < 100 && chatEvents <= previousEvents; attempt++) await new Promise(resolve => setTimeout(resolve, 50));
    assert.ok(chatEvents > previousEvents, 'second real consultation reaches the active socket');
    await page.locator('.el-notification').filter({ hasText: '新的客服咨询' }).click();
    await page.waitForURL(/\/chat\?only_unread=1/);
    await page.locator('.session-item').filter({ hasText: '隔离咨询二' }).waitFor({ timeout: 10000 });
    await page.locator('.session-item').filter({ hasText: '同时到达的另一条咨询' }).waitFor({ timeout: 10000 });
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
