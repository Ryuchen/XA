// Real browser, real HTTP and real ASGI websocket; no business response stubs.
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve('.tools/provider-team-cancel-h5-20261101');
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
    const pages = [];
    for (let i = 0; i < input.tokens.length; i++) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await context.addInitScript(({ token, id }) => {
        localStorage.setItem('token', JSON.stringify({ data: token }));
        localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
      }, { token: input.tokens[i], id: input.ids[i] });
      const page = await context.newPage();
      await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
      const connected = new Promise((resolve, reject) => {
        const timeout = setTimeout(() => reject(new Error('provider websocket did not send a heartbeat')), 12000);
        page.on('websocket', ws => ws.on('framesent', () => { clearTimeout(timeout); resolve(); }));
      });
      await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/application-detail/index?orderId=${input.orderId}&applicationId=${input.applicationId}`);
      await page.getByText('组队成员').waitFor({ timeout: 15000 });
      await connected;
      pages.push(page);
    }
    if (input.scenario === 'decline') {
      await pages[1].getByText('拒绝邀请', { exact: true }).click();
      await pages[1].getByText('确认拒绝', { exact: true }).click();
      await pages[1].getByText('已拒绝邀请', { exact: true }).waitFor({ timeout: 15000 });
      await pages[0].getByText('报名已撤回。', { exact: true }).waitFor({ timeout: 15000 });
      const response = await fetch(`http://127.0.0.1:19166/api/orders/orders/${input.orderId}/`, {
        headers: { Authorization: `Bearer ${input.bossToken}` },
      });
      const order = await response.json(); assert.equal(order.code, 0, JSON.stringify(order));
      assert.equal(order.data.applications.some(item => item.id === input.applicationId), false);
      assert.equal(await pages[0].getByText('回到抢单池', { exact: true }).count(), 1);
      assert.equal(await pages[0].evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      console.log('Decline updated the applicant and removed the team from the boss candidate list');
      return;
    }
    await pages[1].getByText('确认组队', { exact: true }).click();
    for (const page of pages) await page.getByText('队伍已确认，等待老板选择').waitFor({ timeout: 15000 });
    const bossOrder = await fetch(`http://127.0.0.1:19166/api/orders/orders/${input.orderId}/`, {
      headers: { Authorization: `Bearer ${input.bossToken}` },
    });
    const bossResult = await bossOrder.json();
    assert.equal(bossResult.code, 0, JSON.stringify(bossResult));
    assert.equal(bossResult.data.applications.find(item => item.id === input.applicationId)?.ready, true);
    const response = await fetch(`http://127.0.0.1:19166/api/orders/orders/${input.orderId}/cancel/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.bossToken}`, 'Content-Type': 'application/json' }, body: '{}',
    });
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    for (const page of pages) {
      await page.getByText('订单已取消，本次报名结束。').waitFor({ timeout: 15000 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      for (const label of ['确认组队', '拒绝邀请', '撤回报名']) assert.equal(await page.getByText(label, { exact: true }).count(), 0);
    }
    console.log('Team confirmation reached both open provider pages and the boss; cancellation updated both pages');
  } finally { await browser.close(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
