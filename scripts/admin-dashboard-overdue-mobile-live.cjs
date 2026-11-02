// Real isolated dashboard and order endpoints, rendered at mobile width.
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
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    const tile = page.getByRole('button', { name: /已到接单期限/ });
    await tile.waitFor({ timeout: 30000 });
    assert.match(await tile.innerText(), /2\s*单/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await tile.click();
    await page.getByText('已到接单期限 · 等待处理', { exact: true }).waitFor({ timeout: 30000 });
    const cards = page.locator('.od-mobile-card');
    await cards.first().waitFor({ timeout: 30000 });
    assert.equal(await cards.count(), 2);
    assert.match(await cards.first().innerText(), new RegExp(input.oldestNo));
    assert.match(await cards.nth(1).innerText(), new RegExp(input.expiredNo));
    assert.match(await cards.first().innerText(), /公开接单/);
    for (const card of [cards.first(), cards.nth(1)]) {
      assert.match(await card.innerText(), /接单截止/);
      assert.match(await card.innerText(), /已到期/);
    }
    await page.getByText('截止时间从早到晚', { exact: true }).waitFor();
    assert.doesNotMatch(await cards.first().innerText(), new RegExp(input.futureNo));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await cards.first().getByRole('button', { name: '查看详情' }).click();
    const drawer = page.locator('.el-drawer');
    await drawer.getByText('已到期，等待处理', { exact: true }).waitFor();
    assert.match(await drawer.innerText(), /接单截止时间/);
    await page.keyboard.press('Escape'); await drawer.waitFor({ state: 'hidden' });
    await page.getByRole('button', { name: '清除筛选' }).click();
    await page.waitForFunction(() => document.querySelectorAll('.od-mobile-card').length === 3);
    assert.deepEqual(errors, []);
    console.log('Real overdue queue and clear interaction verified at 375px');
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
