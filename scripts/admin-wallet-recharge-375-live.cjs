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
    await card.waitFor({ timeout: 15000 });
    assert.ok((await card.innerText()).includes(input.nickname));
    await card.getByRole('button', { name: '充值记录', exact: true }).click();
    await page.waitForURL(new RegExp(`/recharge-records\\?wallet_id=${input.walletId}`));
    await page.getByText(`钱包 #${input.walletId} 关联账户`, { exact: true }).waitFor({ timeout: 15000 });
    const record = page.locator('.rc-card').filter({ hasText: input.receipt });
    await record.waitFor({ timeout: 15000 });
    const text = await record.innerText();
    assert.ok(text.includes(input.username) && text.includes(input.nickname) && text.includes('1000 币') && text.includes('100 币'), text);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    const desktop = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await desktop.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const desktopPage = await desktop.newPage();
    await desktopPage.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await desktopPage.goto(`${base}/wallets`, { waitUntil: 'domcontentloaded' });
    const row = desktopPage.locator('[data-slot=table] tbody tr').filter({ hasText: input.username });
    await row.first().waitFor({ timeout: 30000 });
    const actions = row.first().locator('td').last();
    for (const name of ['流水', '充值记录', '客服充值', '调账']) {
      const button = actions.getByRole('button', { name, exact: true });
      await button.waitFor();
      const bounds = await button.evaluate(el => { const button = el.getBoundingClientRect(); const cell = el.closest('td').getBoundingClientRect(); return { left: button.left, right: button.right, top: button.top, bottom: button.bottom, cellLeft: cell.left, cellRight: cell.right, cellTop: cell.top, cellBottom: cell.bottom }; });
      assert.ok(bounds.left >= bounds.cellLeft - 1 && bounds.right <= bounds.cellRight + 1 && bounds.top >= bounds.cellTop - 1 && bounds.bottom <= bounds.cellBottom + 1, `${name}: ${JSON.stringify(bounds)}`);
    }
    await desktop.close();
    assert.deepEqual(errors, []);
    console.log('Admin opened actual customer recharge history from wallet at 375px');
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
