// Verify the actual admin screen receives and previews a provider image.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const http = require('node:http');
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
  let vite, staticServer;
  if (input.production) {
    const build = path.resolve('.tools/admin-chat-order-build-20261101');
    const target = new URL(input.api);
    staticServer = http.createServer((req, res) => {
      if (req.url.startsWith('/api/')) {
        const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
        proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
      }
      const pathname = new URL(req.url, 'http://localhost').pathname;
      const file = path.resolve(build, pathname.startsWith('/assets/') ? pathname.slice(1) : 'index.html');
      if (!file.startsWith(build + path.sep)) { res.writeHead(404).end(); return; }
      fs.readFile(file, (error, data) => {
        if (error) { res.writeHead(404).end(); return; }
        const type = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream';
        res.writeHead(200, { 'Content-Type': type }).end(data);
      });
    });
    await new Promise(resolve => staticServer.listen(port, '127.0.0.1', resolve));
  } else {
    vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
      { cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true });
  }
  let browser;
  try {
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 120; i++) {
      if (vite && vite.exitCode !== null) throw new Error('Vite exited');
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
    if (!input.orderNo) await page.locator('.msg-row').filter({ has: page.locator('.msg-image') }).getByText('排班截图，麻烦核对', { exact: true }).waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('.msg-row .msg-image img')].some(img => img.complete && img.naturalWidth > 0), null, { timeout: 10000 });
    const image = page.locator('.msg-row .msg-image').last();
    let blockedRenewal = 0;
    const failRenewal = route => { blockedRenewal++; return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ code: 503, msg: '暂时无法获取图片' }) }); };
    await page.route('**/api/media/renew/', failRenewal);
    await image.click();
    await page.getByText('图片预览暂时无法打开，请点击图片重试。', { exact: true }).waitFor({ timeout: 10000 });
    assert.equal(blockedRenewal, 1);
    await page.unroute('**/api/media/renew/', failRenewal);
    await image.click();
    const viewer = page.locator('.image-viewer');
    await viewer.waitFor({ timeout: 15000 });
    assert.equal(await page.getByText('图片预览暂时无法打开，请点击图片重试。', { exact: true }).count(), 0);
    await viewer.locator('img').waitFor();
    await page.waitForFunction(() => [...document.querySelectorAll('.image-viewer img')].some(img => img.complete && img.naturalWidth > 0), null, { timeout: 10000 });
    await page.waitForFunction(() => {
      const overlay = document.querySelector('.image-viewer');
      return overlay && Number(getComputedStyle(overlay).opacity) >= 0.98;
    }, null, { timeout: 10000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    const folder = path.resolve('.tools/admin-provider-chat-image-20261101'); fs.mkdirSync(folder, { recursive: true });
    await page.screenshot({ path: path.join(folder, 'preview.png') });
    if (input.orderNo) {
      await viewer.getByRole('button', { name: '关闭', exact: true }).last().click();
      const imageRow = page.locator('.msg-row').filter({ has: page.locator('.msg-image') }).filter({ hasText: input.orderNo });
      const orderLink = imageRow.getByRole('button', { name: `查看订单 ${input.orderNo}` });
      await orderLink.click();
      try { await page.getByText(`精确查找订单 ${input.orderNo}`, { exact: true }).waitFor({ timeout: 15000 }); }
      catch (error) { throw new Error(`${error.message}\nURL ${page.url()}\n${(await page.locator('body').innerText()).slice(0, 1600)}`); }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    }
    assert.deepEqual(errors, []);
    console.log('Real provider image displayed in admin chat and preview at 375px.');
  } finally { if (browser) await browser.close(); if (vite) vite.kill(); if (staticServer) { staticServer.closeAllConnections(); staticServer.close(); } }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
