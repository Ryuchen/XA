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
    await page.goto(`${base}/wallets`, { waitUntil: 'domcontentloaded' });
    const search = page.getByPlaceholder('编号、账号、昵称或手机');
    await search.fill(input.username);
    const filtered = page.waitForResponse(response => { const url = new URL(response.url()); return url.pathname === '/api/admin/wallets/' && url.searchParams.get('keyword') === input.username; }, { timeout: 15000 });
    await search.press('Enter'); await filtered;
    const card = page.locator('.wl-card').filter({ hasText: input.username });
    await card.getByRole('button', { name: '客服充值' }).click({ timeout: 15000 });
    const dialog = page.getByRole('dialog', { name: '客服充值确认' });
    await dialog.waitFor();
    await dialog.locator('.el-form-item').filter({ hasText: '实收人民币' }).locator('input').fill('100');
    await dialog.locator('.el-form-item').filter({ hasText: '赠送兴安币' }).locator('input').fill('100');
    await dialog.getByPlaceholder('已核实的收款流水号（必填）').fill('ISOLATED-NATIVE-RECHARGE');
    await dialog.locator('input[type=file]').setInputFiles({ name: 'isolated-receipt.png', mimeType: 'image/png', buffer: Buffer.from(input.image, 'base64') });
    await dialog.getByRole('button', { name: '核对并入账' }).click();
    const confirm = page.getByRole('dialog', { name: '确认收款并入账' });
    await confirm.waitFor();
    assert.match(await confirm.innerText(), /1100/);
    await confirm.getByRole('button', { name: '确认入账' }).click();
    await dialog.waitFor({ state: 'hidden', timeout: 15000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Staff recorded real isolated recharge via admin UI at 375px');
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
