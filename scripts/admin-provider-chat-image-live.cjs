// Verify the actual admin screen receives and previews a provider image.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
async function main() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const port = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  const root = path.resolve('apps/admin-web');
  const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    { cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true });
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
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    const origins = new Set([base, input.api]);
    await page.route('**/*', route => origins.has(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/chat`, { waitUntil: 'domcontentloaded' });
    const session = page.locator('.session-item').filter({ hasText: input.name });
    await session.click({ timeout: 30000 });
    const img = page.locator('.msg-row .msg-image img').last();
    await img.waitFor({ timeout: 15000 });
    await page.locator('.msg-row').filter({ has: page.locator('.msg-image') }).getByText('排班截图，麻烦核对', { exact: true }).waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('.msg-row .msg-image img')].some(img => img.complete && img.naturalWidth > 0));
    await page.locator('.msg-row .msg-image').last().click();
    const viewer = page.locator('.el-image-viewer__wrapper');
    await viewer.waitFor({ timeout: 15000 });
    await viewer.locator('img').waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('.el-image-viewer__wrapper img')].some(img => img.complete && img.naturalWidth > 0));
    await page.waitForFunction(() => {
      const overlay = document.querySelector('.el-image-viewer__wrapper');
      return overlay && Number(getComputedStyle(overlay).opacity) >= 0.98;
    });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    const folder = path.resolve('.tools/admin-provider-chat-image-20261101'); fs.mkdirSync(folder, { recursive: true });
    await page.screenshot({ path: path.join(folder, 'preview.png') });
    assert.deepEqual(errors, []);
    console.log('Real provider image displayed in admin chat and preview at 375px.');
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
