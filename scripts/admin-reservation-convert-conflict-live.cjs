// A pending conversion confirmation follows actual reservation updates.
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
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
    await context.addInitScript(() => {
      const NativeWebSocket = window.WebSocket;
      window.WebSocket = class extends NativeWebSocket {
        constructor(url, protocols) {
          super(url, protocols);
          if (String(url).includes('/ws/orders/')) window.__qaOrderSocket = this;
        }
      };
    });
    const page = await context.newPage(), responses = [];
    page.on('response', async response => {
      if (new URL(response.url()).pathname === `/api/orders/reservations/${input.reservationId}/`) {
        try { responses.push(await response.json()); } catch {}
      }
    });
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    const connected = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('admin websocket unavailable')), 15000);
      page.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) { clearTimeout(timer); resolve(); } }));
    });
    await page.goto(`${base}/reservations?find_reservation=${input.reservationId}`);
    const drawer = page.locator('.el-drawer');
    const actionName = input.reject ? '拒绝预约' : '报价并转单';
    try { await drawer.getByRole('button', { name: actionName }).waitFor({ timeout: 30000 }); }
    catch (error) { throw new Error(`${error.message}\n${await page.locator('body').innerText()}\n${page.url()}`); }
    await connected;
    await drawer.getByRole('button', { name: actionName }).click();
    const dialog = page.getByRole('dialog', { name: input.reject ? '拒绝预约' : '确认预约转单' });
    await dialog.waitFor({ timeout: 10000 });
    if (input.reject) await dialog.getByPlaceholder('原因（选填）').fill('填写中的原因');
    if (input.normalReject) {
      await dialog.getByRole('button', { name: '确认', exact: true }).click();
      await dialog.waitFor({ state: 'hidden', timeout: 10000 });
      await drawer.getByText('已拒绝', { exact: true }).waitFor({ timeout: 10000 });
      await drawer.getByText('填写中的原因', { exact: true }).waitFor();
      return;
    }
    if (input.reconnect) {
      await page.evaluate(() => new Promise(resolve => {
        window.__qaOrderSocket.addEventListener('close', resolve, { once: true });
        window.__qaOrderSocket.close();
      }));
    }
    const response = await fetch(`${input.api}/api/orders/reservations/${input.reservationId}/cancel/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.bossToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: '老板改变安排' }),
    });
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    assert.equal(result.data.status, 'CANCELLED', JSON.stringify(result));
    await dialog.waitFor({ state: 'hidden', timeout: 10000 });
    await page.getByText('预约状态可能已变化，请重新核对后操作。').waitFor();
    try { await drawer.getByText('已取消', { exact: true }).waitFor({ timeout: 10000 }); }
    catch (error) { throw new Error(`${error.message}\n${JSON.stringify(responses)}\n${await page.locator('body').innerText()}`); }
    assert.equal(await drawer.getByRole('button', { name: actionName }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
