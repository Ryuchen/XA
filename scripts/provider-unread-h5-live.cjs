// Render the real provider H5 against an isolated Django message API.
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(input.root);
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data));
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const base = `http://127.0.0.1:${server.address().port}`;
    async function contextFor(index, viewport) {
      const context = await browser.newContext({ viewport });
      await context.addInitScript(({ token, id }) => {
        localStorage.setItem('token', JSON.stringify({ data: token }));
        localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
      }, { token: input.tokens[index], id: input.ids[index] });
      const page = await context.newPage();
      let websocketReady;
      if (input.staffToken && index === 0) websocketReady = new Promise(resolve => page.on('websocket', socket => socket.on('framereceived', event => {
        if (String(event.payload).includes('pong')) resolve();
      })));
      await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
      await page.goto(`${base}/#/pages/orders/index`);
      return { context, page, websocketReady };
    }
    const mobile = await contextFor(0, { width: 375, height: 812 });
    await mobile.page.locator('[aria-label="消息，2条未读"]').waitFor({ timeout: 15000 });
    let remaining = 1;
    if (input.staffToken) {
      await Promise.race([mobile.websocketReady, new Promise((_, reject) => setTimeout(() => reject(Error('websocket did not connect')), 10000))]);
      const pushed = await fetch('http://127.0.0.1:19178/api/admin/messages/push/', { method: 'POST',
        headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ audience: 'CUSTOM', recipient_account_ids: [input.firstAccount], title: '实时隔离消息' }) });
      assert.equal((await pushed.json()).code, 0);
      await mobile.page.locator('[aria-label="消息，3条未读"]').waitFor({ timeout: 10000 });
      remaining = 2;
    }
    assert.equal(await mobile.page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await mobile.page.locator(`[aria-label="消息，${remaining + 1}条未读"]`).click();
    await mobile.page.getByText('隔离消息一', { exact: true }).waitFor({ timeout: 10000 });
    await mobile.page.getByText('隔离消息一', { exact: true }).click();
    await mobile.page.locator(`[aria-label="消息，${remaining}条未读"]`).last().waitFor({ timeout: 10000 });
    await mobile.page.getByText('返回', { exact: true }).click();
    await mobile.page.getByText('全部已读', { exact: true }).click();
    await mobile.page.locator('[aria-label="消息"]').last().waitFor({ timeout: 10000 });
    await mobile.context.close();
    const desktop = await contextFor(1, { width: 1280, height: 800 });
    await desktop.page.locator('[aria-label="消息中心，1条未读"]').waitFor({ timeout: 15000 });
    await desktop.context.close();
    console.log('real unread counts: mobile detail and read-all, separate account desktop');
  } finally { await browser.close(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
