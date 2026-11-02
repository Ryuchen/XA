// Review two real reports belonging to a transferred order at phone width.
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
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage();
    const connected = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('reviewer websocket unavailable')), 15000);
      page.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) { clearTimeout(timer); resolve(); } }));
    });
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    const firstScopedResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/reports/' && url.searchParams.get('exclude_direct_test') === '1';
    }, { timeout: 30000 });
    await page.goto(`${base}/reports?exclude_direct_test=1`);
    assert.equal((await (await firstScopedResponse).json()).data.exclude_direct_test_applied, true);
    await connected;
    const cards = input.reports.map(item => page.locator('.rp-mobile-card').filter({ hasText: `报单 #${item.id}` }));
    for (let i = 0; i < cards.length; i++) {
      await cards[i].waitFor({ timeout: 30000 });
      const content = await cards[i].innerText();
      assert.ok(content.includes(input.orderNo) && content.includes(input.reports[i].name) && content.includes(input.reports[i].income), content);
      assert.match(content, /待审核/);
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await cards[0].getByRole('button', { name: '通过', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '报单审核' });
    await dialog.waitFor();
    const secondContext = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await secondContext.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.secondToken);
    const secondPage = await secondContext.newPage();
    await secondPage.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await secondPage.goto(`${base}/reports`);
    const secondCard = secondPage.locator('.rp-mobile-card').filter({ hasText: `报单 #${input.reports[0].id}` });
    await secondCard.getByRole('button', { name: '通过', exact: true }).click({ timeout: 30000 });
    const refreshedScope = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/reports/' && url.searchParams.get('exclude_direct_test') === '1';
    }, { timeout: 15000 });
    await secondPage.getByRole('dialog', { name: '报单审核' }).getByRole('button', { name: '确认通过' }).click();
    await dialog.waitFor({ state: 'hidden', timeout: 15000 });
    assert.equal((await (await refreshedScope).json()).data.exclude_direct_test_applied, true);
    await cards[0].getByText('已通过', { exact: true }).waitFor({ timeout: 15000 });
    assert.match(await cards[1].innerText(), /待审核/);
    assert.equal(await cards[0].getByRole('button', { name: '通过', exact: true }).count(), 0);
    const allSourcesResponse = page.waitForResponse(response => {
      const url = new URL(response.url());
      return url.pathname === '/api/admin/reports/' && !url.searchParams.has('exclude_direct_test');
    }, { timeout: 15000 });
    await page.getByRole('button', { name: '查看全部来源' }).click();
    assert.equal((await (await allSourcesResponse).json()).data.exclude_direct_test_applied, undefined);
    await cards[1].getByRole('button', { name: '通过', exact: true }).click();
    await page.getByRole('dialog', { name: '报单审核' }).getByRole('button', { name: '返回' }).click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
