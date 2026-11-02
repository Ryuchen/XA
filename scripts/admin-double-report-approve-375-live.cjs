// Run the real admin page against an isolated Django LiveServer.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));

async function reservePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
async function main() {
  const port = await reservePort();
  const adminRoot = path.resolve('apps/admin-web');
  const vite = spawn(process.execPath, [path.join(adminRoot, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: adminRoot, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true,
  });
  let browser;
  try {
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw new Error('Vite exited before accepting connections');
      try { if ((await fetch(base)).ok) break; } catch {}
      await new Promise(resolve => setTimeout(resolve, 250));
      if (i === 119) throw new Error('Vite did not start');
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(token => { localStorage.setItem('xa_admin_token', token); }, input.token);
    const page = await context.newPage();
    const imageResponses = [];
    page.on('response', response => { if (response.url().includes('/api/media/private/')) imageResponses.push(response.status()); });
    page.on('requestfailed', request => { if (request.url().includes('/api/media/private/')) imageResponses.push(new URL(request.url()).origin + ' ' + (request.failure()?.errorText || 'network error')); });
    const allowedOrigins = new Set([base, input.api]);
    await page.route('**/*', route => allowedOrigins.has(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/reports`, { waitUntil: 'domcontentloaded' });
    for (const report of input.reports) {
      const card = page.locator('.rp-mobile-card').filter({ hasText: `报单 #${report.id}` });
      await card.waitFor({ timeout: 30000 });
      const text = await card.innerText();
      assert.ok(text.includes(input.orderNo) && text.includes(report.name) && text.includes('待审核'), text);
      await card.getByRole('button', { name: '查看详情' }).click();
      const drawer = page.locator('.el-drawer').filter({ hasText: '报单详情' });
      await drawer.getByText('报单编号').waitFor({ timeout: 15000 });
      await drawer.getByText(`战绩截图（${report.resultCount || 1} 张）`).waitFor();
      const evidence = drawer.getByRole('button', { name: '查看入队截图' });
      await evidence.scrollIntoViewIfNeeded(); await evidence.click();
      const viewer = page.locator('.el-image-viewer__wrapper');
      await viewer.waitFor();
      await page.waitForFunction(() => { const image = document.querySelector('.el-image-viewer__wrapper img'); return image && image.complete && image.naturalWidth > 0; });
      await viewer.locator('.el-image-viewer__close').click();
      assert.equal(await drawer.locator('.el-drawer__body').evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
      if (report.reject) {
        await drawer.getByRole('button', { name: '驳回报单' }).click();
        const dialog = page.locator('.el-message-box').filter({ hasText: '驳回报单' });
        await dialog.locator('textarea').fill('请补充清晰战绩截图');
        await dialog.getByRole('button', { name: '确认驳回' }).click();
        await page.getByText('报单已驳回', { exact: true }).waitFor({ timeout: 15000 });
        await card.getByText('已驳回', { exact: true }).waitFor({ timeout: 15000 });
      } else {
        await drawer.getByRole('button', { name: '审核通过', exact: true }).click();
        const dialog = page.getByRole('dialog', { name: '报单审核' });
        await dialog.getByRole('button', { name: '确认通过' }).click();
        await page.getByText('报单审核已通过', { exact: true }).waitFor({ timeout: 15000 });
        await card.getByText('已通过', { exact: true }).waitFor({ timeout: 15000 });
      }
      await drawer.locator('.el-drawer__close-btn').click();
      await drawer.waitFor({ state: 'hidden' });
    }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    console.log('Provider reports reviewed via actual admin UI at 375px');
  } finally {
    if (browser) await browser.close();
    vite.kill();
  }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
