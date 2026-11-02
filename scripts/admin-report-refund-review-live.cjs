// Real reviewer page, refund and websocket against an isolated ASGI server.
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
    const page = await context.newPage();
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    const connected = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('admin websocket unavailable')), 15000);
      page.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) { clearTimeout(timer); resolve(); } }));
    });
    await page.goto(`${base}/reports?status=PENDING`);
    const card = page.locator('.rp-mobile-card').filter({ hasText: `报单 #${input.reportId}` });
    await card.waitFor({ timeout: 30000 }); await connected;
    await card.getByRole('button', { name: '通过', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '报单审核' }); await dialog.waitFor();
    const request = async (url, method = 'GET', body) => {
      const response = await fetch(input.api + '/api/admin' + url, { method,
        headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' },
        ...(body ? { body: JSON.stringify(body) } : {}) });
      const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result)); return result.data;
    };
    const quote = await request(`/orders/${input.orderId}/refund-preview/`);
    await request(`/orders/${input.orderId}/refund/`, 'POST', { reason: '隔离测试终止', plan_token: quote.plan_token, client_request_id: 'report-review-live-refund' });
    await dialog.waitFor({ state: 'hidden', timeout: 10000 });
    await card.getByText('订单已退款，不能通过报单').waitFor({ timeout: 10000 });
    assert.equal(await card.getByRole('button', { name: '通过', exact: true }).count(), 0);
    await card.getByRole('button', { name: '驳回', exact: true }).click();
    const reject = page.getByRole('dialog', { name: '驳回报单' });
    await reject.getByPlaceholder('填写驳回原因（1–255 字）').fill('订单已退款');
    await reject.getByRole('button', { name: '确认驳回' }).click();
    await card.waitFor({ state: 'hidden', timeout: 10000 });
    const manual = page.locator('.rp-mobile-card').filter({ hasText: `报单 #${input.manualId}` });
    await manual.getByRole('button', { name: '通过', exact: true }).click();
    const approve = page.getByRole('dialog', { name: '报单审核' });
    await approve.getByRole('button', { name: '确认通过' }).click();
    await manual.waitFor({ state: 'hidden', timeout: 10000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
