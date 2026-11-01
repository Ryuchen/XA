// Real isolated backend and real admin UI; no business endpoints are mocked.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));

async function main() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  const root = path.resolve('apps/admin-web');
  const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true,
  });
  let browser;
  try {
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw new Error('Vite exited');
      try { if ((await fetch(base)).ok) break; } catch {}
      await new Promise(resolve => setTimeout(resolve, 250));
      if (i === 119) throw new Error('Vite did not start');
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    page.on('console', message => { if (message.type() === 'error' && !message.text().includes('WebSocket connection')) errors.push(message.text()); });
    page.on('response', response => { if (response.status() >= 400 && new URL(response.url()).origin === base) errors.push(`${response.status()} ${response.url()}`); });
    const origins = new Set([base, input.api]);
    await page.route('**/*', route => origins.has(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    const group = page.locator('[aria-label="待审核提现按领取状态"]');
    const other = group.getByRole('button', { name: /其他客服已领取/ });
    await other.waitFor({ timeout: 30000 });
    // Vite may reload the page once after discovering lazy chart dependencies.
    await page.waitForTimeout(2500);
    assert.match(await other.innerText(), /1\s*笔/);
    assert.match(await group.getByRole('button', { name: /未领取/ }).innerText(), /1\s*笔/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    // A cold Vite dependency optimization can reload the current page while
    // the lazy withdrawal screen first loads; retry once after that reload.
    await other.click();
    try { await page.waitForURL(/claim_scope=other/, { timeout: 4000 }); }
    catch { await page.locator('[aria-label="待审核提现按领取状态"]').getByRole('button', { name: /其他客服已领取/ }).click(); }
    try { await page.waitForURL(/claim_scope=other/, { timeout: 10000 }); }
    catch (error) { throw new Error(`${error.message}\nURL ${page.url()}\nErrors ${JSON.stringify(errors)}\n${(await page.locator('body').innerText()).slice(0, 1800)}`); }
    const cards = page.locator('.wd-mobile-card');
    await cards.first().waitFor({ timeout: 30000 });
    assert.equal(await cards.count(), 1);
    assert.match(await cards.first().innerText(), new RegExp(`申请 #${input.claimedId}\\b`));
    assert.doesNotMatch(await cards.first().innerText(), new RegExp(`申请 #${input.unclaimedId}\\b`));
    const status = cards.first().locator('.wd-mobile-head .el-tag');
    assert.equal(await status.innerText(), '待审核');
    await page.waitForFunction(() => {
      const tag = document.querySelector('.wd-mobile-card .wd-mobile-head .el-tag');
      return tag && tag.getBoundingClientRect().width >= tag.clientWidth * 0.98;
    });
    const statusBox = await status.evaluate(el => ({ width: el.getBoundingClientRect().width, height: el.getBoundingClientRect().height, clientWidth: el.clientWidth, scrollWidth: el.scrollWidth, color: getComputedStyle(el).color, background: getComputedStyle(el).backgroundColor, transform: getComputedStyle(el).transform }));
    assert.ok(statusBox.width >= 35 && statusBox.height >= 18 && statusBox.clientWidth >= statusBox.scrollWidth, JSON.stringify(statusBox));
    const bounds = await cards.first().locator('.wd-mobile-head').evaluate(head => {
      const name = head.querySelector('strong').getBoundingClientRect();
      const badge = head.querySelector('.el-tag').getBoundingClientRect();
      const container = head.getBoundingClientRect();
      return { nameRight: name.right, badgeLeft: badge.left, badgeRight: badge.right, containerRight: container.right };
    });
    assert.ok(bounds.nameRight <= bounds.badgeLeft && bounds.badgeRight <= bounds.containerRight + 1, JSON.stringify(bounds));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    const screenshotDir = path.resolve('.tools/admin-withdraw-mobile-20261101');
    fs.mkdirSync(screenshotDir, { recursive: true });
    await page.screenshot({ path: path.join(screenshotDir, 'other-claim.png') });
    assert.equal(await cards.first().getByRole('button', { name: '领取', exact: true }).count(), 0);
    await cards.first().getByRole('button', { name: '释放领取' }).click();
    const dialog = page.getByRole('dialog', { name: '释放提现领取' });
    await dialog.getByPlaceholder('填写核实情况或释放原因').fill('隔离验收：已核实未打款');
    await dialog.getByText('我已核实此申请尚未打款').click();
    await dialog.getByRole('button', { name: '确认释放' }).click();
    await dialog.waitFor({ state: 'hidden' });
    await cards.first().waitFor({ state: 'hidden' });
    assert.deepEqual(errors, []);
    console.log('Real withdrawal dashboard drilldown verified at 375px.');
  } finally {
    if (browser) await browser.close();
    vite.kill();
  }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
