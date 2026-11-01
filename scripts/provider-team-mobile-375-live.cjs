// Render the actual built provider H5 against the isolated Django LiveServer.
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve('.tools/provider-invitation-h5-375-20261031');
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => {
    if (error) { res.writeHead(404).end(); return; }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data);
  });
});
const api = async (route) => {
  const res = await fetch('http://127.0.0.1:19166/api' + route, { headers: { Authorization: `Bearer ${input.bossToken}` } });
  const body = await res.json(); assert.equal(body.code, 0, JSON.stringify(body)); return body.data;
};
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离队友', username: '隔离队友', avatar: '' } }));
    }, { token: input.partnerToken, id: input.partnerId });
    const page = await context.newPage();
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    const base = `http://127.0.0.1:${server.address().port}`;
    const go = async item => {
      await page.goto(`${base}/#/pages/application-detail/index?orderId=${item.orderId}&applicationId=${item.applicationId}`);
      await page.getByText('组队成员').waitFor({ timeout: 15000 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'horizontal overflow');
    };
    const folder = path.join(root, 'proofs'); fs.mkdirSync(folder, { recursive: true });
    await go(input.invitations[0]);
    const confirm = page.getByText('确认组队', { exact: true });
    const withdraw = page.getByText('拒绝邀请', { exact: true });
    for (const control of [confirm, withdraw]) {
      const box = await control.boundingBox(); assert.ok(box && box.height >= 44 && box.width >= 44, JSON.stringify(box));
    }
    await page.screenshot({ path: path.join(folder, 'waiting.png') });
    assert.equal((await api(`/orders/orders/${input.invitations[0].orderId}/`)).applications[0].ready, false);
    await confirm.click();
    await page.getByText('队伍已确认，等待老板选择').waitFor({ timeout: 10000 });
    assert.equal((await api(`/orders/orders/${input.invitations[0].orderId}/`)).applications[0].ready, true);
    await page.screenshot({ path: path.join(folder, 'confirmed.png') });
    await go(input.invitations[1]);
    await page.getByText('拒绝邀请', { exact: true }).click();
    await page.getByText('确认拒绝', { exact: true }).click();
    await page.getByText('已拒绝邀请', { exact: true }).waitFor({ timeout: 10000 });
    assert.equal((await api(`/orders/orders/${input.invitations[1].orderId}/`)).applications.length, 0);
    await page.screenshot({ path: path.join(folder, 'withdrawn.png') });
    const pool = page.getByText('回到抢单池', { exact: true });
    const poolBox = await pool.boundingBox(); assert.ok(poolBox && poolBox.height >= 44 && poolBox.width >= 44, JSON.stringify(poolBox));
    await pool.click();
    await page.getByText('抢单池', { exact: true }).first().waitFor({ timeout: 10000 });
    assert.match(page.url(), /pages\/orders\/index/);
    console.log('375px provider invitation confirmed and withdrawn; boss state verified');
  } finally { await browser.close(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
