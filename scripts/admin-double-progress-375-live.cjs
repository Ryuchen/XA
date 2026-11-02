// Real admin order drawer at mobile width, backed by isolated Django HTTP.
const fs = require('node:fs'), path = require('node:path'), net = require('node:net'), assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
async function main() {
  const probe = net.createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  const root = path.resolve('apps/admin-web'), base = `http://127.0.0.1:${port}`;
  const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    { cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true });
  let browser;
  try {
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw Error('Vite exited');
      try { if ((await fetch(base)).ok) break; } catch {}
      if (i === 119) throw Error('Vite did not start');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/orders?find_order=${encodeURIComponent(input.orderNo)}`, { waitUntil: 'domcontentloaded' });
    const card = page.locator('.od-mobile-card').filter({ hasText: input.orderNo });
    await card.getByRole('button', { name: '查看详情' }).click({ timeout: 30000 });
    const drawer = page.locator('.el-drawer');
    await drawer.getByText(input.stage === 'ENTRY' ? '入队截图进度' : '结单截图进度', { exact: true }).waitFor({ timeout: 15000 });
    await drawer.getByText('1/2 已提交', { exact: true }).waitFor();
    await drawer.getByText('陪玩甲 · 已提交', { exact: true }).waitFor();
    await drawer.getByText('陪玩替补 · 等待提交', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.equal(await drawer.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log(`Admin ${input.stage} progress verified at 375px`);
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
