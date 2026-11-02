// Exercise the real dashboard, filtered report queue and rejection at 375px.
const fs = require('node:fs'), path = require('node:path'), net = require('node:net'), http = require('node:http'), assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
async function main() {
  const probe = net.createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  const root = path.resolve('apps/admin-web');
  const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    { cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true });
  let browser, providerServer;
  try {
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw Error('Vite exited');
      try { if ((await fetch(base)).ok) break; } catch {}
      if (i === 119) throw Error('Vite did not start');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/dashboard`);
    const tile = page.getByRole('button', { name: /订单状态待核对/ });
    await tile.waitFor({ timeout: 30000 }); assert.match(await tile.innerText(), /1\s*条/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    const scopeResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/reports/' && url.searchParams.get('exclude_direct_test') === '1' && url.searchParams.get('order_blocked') === '1';
    }, { timeout: 15000 });
    await tile.click();
    const scoped = await (await scopeResponse).json();
    assert.equal(scoped.code, 0); assert.equal(scoped.data.exclude_direct_test_applied, true); assert.equal(scoped.data.total, 1);
    await page.locator('[role="status"]').filter({ hasText: '工作台口径：不含直接支付测试单' }).waitFor();
    const card = page.locator('.rp-mobile-card').filter({ hasText: `报单 #${input.reportId}` });
    await card.waitFor({ timeout: 30000 });
    assert.equal(await page.locator('.rp-mobile-card').count(), 1);
    assert.match(await card.innerText(), /订单已退款，不能通过报单/);
    assert.match(await card.innerText(), new RegExp(input.orderNo));
    assert.equal(await card.getByRole('button', { name: '通过', exact: true }).count(), 0);
    await card.getByRole('button', { name: '驳回', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '驳回报单' });
    await dialog.getByPlaceholder('填写驳回原因（1–255 字）').fill('订单已退款，请核对');
    await dialog.getByRole('button', { name: '确认驳回' }).click();
    await card.waitFor({ state: 'hidden', timeout: 15000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    const providerRoot = path.resolve('.tools/provider-team-cancel-h5-20261101');
    const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
    providerServer = http.createServer((req, res) => {
      const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
      const file = path.resolve(providerRoot, relative);
      if (!file.startsWith(providerRoot + path.sep)) { res.writeHead(404).end(); return; }
      fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data));
    });
    await new Promise(resolve => providerServer.listen(0, '127.0.0.1', resolve));
    const providerContext = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await providerContext.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
    }, { token: input.providerToken, id: input.providerId });
    const providerPage = await providerContext.newPage();
    await providerPage.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    await providerPage.goto(`http://127.0.0.1:${providerServer.address().port}/#/pages/report/index`);
    await providerPage.getByText(`订单 ${input.orderNo}`, { exact: true }).waitFor({ timeout: 20000 });
    await providerPage.getByText('审核备注：订单已退款，请核对', { exact: true }).waitFor();
    await providerPage.getByText('订单已取消，报单流程已停止', { exact: true }).waitFor();
    assert.equal(await providerPage.getByText('提交审核', { exact: true }).count(), 0);
    assert.equal(await providerPage.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
  } finally { if (browser) await browser.close(); if (providerServer) providerServer.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
