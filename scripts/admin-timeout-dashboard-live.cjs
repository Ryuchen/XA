// Actual Vite dashboard, isolated API and WebSocket; no simulated business response.
const fs = require('node:fs'), path = require('node:path'), net = require('node:net'), assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
async function main() {
  const probe = net.createServer(); await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const port = probe.address().port; await new Promise(resolve => probe.close(resolve));
  const root = path.resolve('apps/admin-web'), base = `http://127.0.0.1:${port}`;
  const vite = spawn(process.execPath, [path.join(root, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'],
    { cwd: root, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.base }, stdio: 'ignore', windowsHide: true });
  let browser;
  try {
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw Error('Vite exited');
      try { if ((await fetch(base)).ok) break; } catch {}
      if (i === 119) throw Error('Vite did not start');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const connected = new Promise(resolve => page.on('websocket', socket => socket.on('framereceived', frame => {
      if (String(frame.payload).includes('pong')) resolve();
    })));
    await page.route('**/*', route => [base, input.base].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    const pending = page.getByRole('button', { name: /已支付待接单/ }).first();
    const publicRoute = page.locator('.db-route-item').filter({ hasText: '公开接单' });
    const netAmount = page.locator('.ap-kpi').filter({ hasText: '累计订单净额' });
    await pending.waitFor({ timeout: 30000 });
    assert.match(await pending.innerText(), /1\s*单/);
    assert.match(await publicRoute.innerText(), /1\s*单/);
    assert.match(await netAmount.innerText(), /2,?000\s*币/);
    await Promise.race([connected, new Promise((_, reject) => setTimeout(() => reject(Error('admin websocket did not connect')), 10000))]);
    fs.writeFileSync(input.ready, 'ready');
    await page.waitForFunction(() => {
      const button = [...document.querySelectorAll('.db-work-item')].find(el => el.textContent.includes('已支付待接单'));
      return button && /0\s*单/.test(button.textContent);
    }, { timeout: 20000 });
    assert.match(await publicRoute.innerText(), /0\s*单/);
    assert.match(await netAmount.innerText(), /0\s*币/);
    assert.deepEqual(errors, []);
    console.log('Admin dashboard refreshed pending queue and net amount after worker timeout');
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
