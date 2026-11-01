// Actual admin Vue app, ASGI WebSocket, order API and browser WebAudio.
const fs = require('node:fs'), path = require('node:path'), net = require('node:net'), assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
async function freePort() { const server = net.createServer(); await new Promise(resolve => server.listen(0, '127.0.0.1', resolve)); const port = server.address().port; await new Promise(resolve => server.close(resolve)); return port; }
async function createOrder(key) {
  const response = await fetch(input.api + '/api/orders/create/', { method: 'POST', headers: { Authorization: 'Bearer ' + input.bossToken, 'Content-Type': 'application/json' }, body: JSON.stringify({ service_id: input.serviceId, dispatch_route: 'STAFF', client_request_id: key }) });
  const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body)); return body.data;
}
async function main() {
  const port = await freePort(), root = path.resolve('apps/admin-web');
  const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], { cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true });
  let browser;
  try {
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 120; i++) { if (vite.exitCode !== null) throw Error('Vite exited'); try { if ((await fetch(base)).ok) break; } catch {} await new Promise(resolve => setTimeout(resolve, 150)); if (i === 119) throw Error('Vite did not start'); }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    await context.addInitScript(token => {
      localStorage.setItem('xa_admin_token', token);
      const original = AudioContext.prototype.createOscillator;
      AudioContext.prototype.createOscillator = function (...args) { window.__qaStaffTones = (window.__qaStaffTones || 0) + 1; return original.apply(this, args); };
    }, input.staffToken);
    const page = await context.newPage();
    let socketReady;
    const ready = new Promise(resolve => { socketReady = resolve; });
    page.on('websocket', socket => socket.on('framesent', socketReady));
    await page.goto(base + '/orders', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: '关闭客服待派提示音' }).waitFor({ timeout: 20000 });
    await Promise.race([ready, new Promise((_, reject) => setTimeout(() => reject(Error('staff websocket did not connect')), 15000))]);
    await page.getByText('客服待派 0 单', { exact: true }).waitFor({ timeout: 15000 });
    const sidebar = await page.locator('.ap-sidebar').boundingBox(); assert.ok(sidebar && sidebar.x === 0, 'desktop navigation is left aligned');
    await page.getByRole('button', { name: '关闭客服待派提示音' }).click();
    await page.getByRole('button', { name: '开启客服待派提示音' }).click();
    await createOrder('qa-staff-sound-first');
    await page.getByText('客服待派订单', { exact: true }).waitFor({ timeout: 15000 });
    await page.waitForFunction(() => (window.__qaStaffTones || 0) === 1, null, { timeout: 5000 });
    await page.locator('.el-notification__closeBtn').first().click();
    await page.getByRole('button', { name: '关闭客服待派提示音' }).click();
    await createOrder('qa-staff-sound-second');
    await page.getByText('客服待派订单', { exact: true }).waitFor({ timeout: 15000 });
    assert.equal(await page.evaluate(() => window.__qaStaffTones), 1, 'mute suppresses the second actual alert');
    await page.locator('.el-notification__closeBtn').first().click();
    await page.setViewportSize({ width: 375, height: 812 });
    await page.getByRole('button', { name: '打开主导航' }).click();
    await page.waitForFunction(() => Math.abs(document.querySelector('.ap-sidebar').getBoundingClientRect().x) < 2, null, { timeout: 5000 });
    const mobile = await page.locator('.ap-sidebar').boundingBox(); assert.ok(mobile && Math.abs(mobile.x) < 2, 'mobile navigation opens from left');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'mobile page does not overflow');
    const folder = path.resolve('.tools/admin-left-nav-sound-20261101'); fs.mkdirSync(folder, { recursive: true });
    await page.screenshot({ path: path.join(folder, 'mobile-nav.png') });
    console.log('Real staff orders, websocket notifications, WebAudio mute and left navigation passed');
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
