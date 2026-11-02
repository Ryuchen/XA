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
    const pending = page.getByRole('button', { name: /待审核提现/ }).first();
    await pending.waitFor({ timeout: 30000 });
    assert.match(await pending.innerText(), /1\s*笔/);
    await pending.click();
    await page.waitForURL(/withdrawals\?status=PENDING/, { timeout: 15000 });
    const card = page.locator('.wd-mobile-card').filter({ hasText: `申请 #${input.id} ` });
    await card.waitFor({ timeout: 30000 });
    assert.match(await card.innerText(), /未领取/);
    await card.getByRole('button', { name: '领取', exact: true }).click();
    await card.getByText('我已领取', { exact: true }).waitFor({ timeout: 15000 });
    await card.getByRole('button', { name: '详情', exact: true }).click();
    const drawer = page.locator('.el-drawer');
    await drawer.getByText('isolated-account-20261102', { exact: true }).waitFor({ timeout: 15000 });
    await drawer.getByText('隔离测试', { exact: true }).waitFor();
    assert.equal(await drawer.locator('.el-drawer__body').evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
    await drawer.locator('.el-drawer__close-btn').click();
    await card.getByRole('button', { name: '通过', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '提现审核' });
    await dialog.waitFor({ timeout: 15000 });
    const text = await dialog.innerText();
    assert.ok(text.includes('隔离测试') && text.includes('isolated-account-20261102') && text.includes(`¥${input.actual}`), text);
    const box = await dialog.boundingBox();
    assert.ok(box && box.x >= -1 && box.x + box.width <= 376, JSON.stringify(box));
    await dialog.getByPlaceholder('打款流水号/凭证编号（必填）').fill('ISOLATED-INCOME-WITHDRAW');
    await dialog.getByRole('button', { name: '确认已打款' }).click();
    await dialog.waitFor({ state: 'hidden' });
    await card.waitFor({ state: 'hidden', timeout: 15000 });
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    const after = page.getByRole('button', { name: /待审核提现/ }).first();
    await after.waitFor({ timeout: 30000 });
    assert.match(await after.innerText(), /0\s*笔/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    console.log('Admin claimed and recorded isolated payout via 375px UI');
  } finally {
    if (browser) await browser.close();
    vite.kill();
  }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
