// Actual workbench, real customer order and provider withdrawal over isolated ASGI.
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
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let connected; const ready = new Promise(resolve => { connected = resolve; });
    page.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) connected(); }));
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    const pending = page.getByRole('button').filter({ hasText: '已支付待接单' });
    const withdrawals = page.getByRole('button').filter({ hasText: '待审核提现' }).first();
    const reports = page.getByRole('button').filter({ hasText: '待审核报单' }).first();
    await pending.waitFor({ timeout: 30000 });
    await withdrawals.waitFor();
    assert.match(await pending.locator('strong').innerText(), /^0\s*单$/);
    assert.match(await withdrawals.locator('strong').innerText(), /^0\s*笔$/);
    assert.match(await reports.locator('strong').innerText(), /^0\s*条$/);
    let timer;
    try { await Promise.race([ready, new Promise((_, reject) => { timer = setTimeout(() => reject(Error('dashboard socket unavailable')), 15000); })]); }
    finally { clearTimeout(timer); }
    const post = async (url, token, body) => {
      const response = await fetch(`${input.api}/api${url}`, { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const data = await response.json(); assert.equal(data.code, 0, JSON.stringify(data)); return data.data;
    };
    await post('/orders/create/', input.bossToken, { service_id: input.serviceId, dispatch_route: 'STAFF', client_request_id: 'dashboard-live-order' });
    await page.waitForFunction(() => [...document.querySelectorAll('.db-work-item')].some(el => el.textContent.includes('已支付待接单') && /1\s*单/.test(el.querySelector('strong')?.textContent || '')), null, { timeout: 18000 });
    await post('/wallet/withdraw/', input.providerToken, input.withdrawPayload);
    await page.waitForFunction(() => [...document.querySelectorAll('.db-work-item')].some(el => el.textContent.includes('待审核提现') && /1\s*笔/.test(el.querySelector('strong')?.textContent || '')), null, { timeout: 18000 });
    const assigned = await post('/orders/create/', input.bossToken, { service_id: input.serviceId,
      provider_id: input.providerId, client_request_id: 'dashboard-live-report-order' });
    const upload = async (url, fields, imageField) => {
      const form = new FormData();
      for (const [key, value] of Object.entries(fields)) form.set(key, value);
      form.set(imageField, new Blob([Buffer.from(input.evidence, 'base64')], { type: 'image/png' }), 'evidence.png');
      const response = await fetch(`${input.api}/api${url}`, { method: 'POST', headers: { Authorization: `Bearer ${input.providerToken}` }, body: form });
      const data = await response.json(); assert.equal(data.code, 0, JSON.stringify(data)); return data.data;
    };
    await upload(`/orders/orders/${assigned.id}/start/`, {}, 'entry_image');
    await upload(`/orders/orders/${assigned.id}/complete/`, {}, 'completion_image');
    const report = await post('/wallet/reports/', input.providerToken, { order_id: assigned.id });
    await upload(`/wallet/reports/${report.id}/images/`, { kind: 'RESULT' }, 'image');
    await post(`/wallet/reports/${report.id}/submit/`, input.providerToken, {});
    await page.waitForFunction(() => [...document.querySelectorAll('.db-work-item')].some(el => el.textContent.includes('待审核报单') && /1\s*条/.test(el.querySelector('strong')?.textContent || '')), null, { timeout: 18000 });
    await post(`/admin/reports/${report.id}/approve/`, input.staffToken, {});
    await page.waitForFunction(() => [...document.querySelectorAll('.db-work-item')].some(el => el.textContent.includes('待审核报单') && /0\s*条/.test(el.querySelector('strong')?.textContent || '')), null, { timeout: 18000 });
    const review = await post('/orders/evaluate/', input.bossToken, { order_id: assigned.id, score: 4, content: '实时评价' });
    await page.waitForFunction(() => [...document.querySelectorAll('.db-work-item')].some(el => el.textContent.includes('未回复评价') && /1\s*条/.test(el.querySelector('strong')?.textContent || '')), null, { timeout: 18000 });
    await page.getByRole('button').filter({ hasText: '未回复评价' }).click();
    const reviewCard = page.locator('.ev-card').filter({ hasText: '实时评价' });
    await reviewCard.waitFor({ timeout: 20000 });
    await reviewCard.getByRole('button', { name: '回复', exact: true }).click();
    const draft = page.locator('#evaluation-reply-text');
    await draft.fill('客服正在编辑的草稿');
    await post(`/orders/evaluations/${review.id}/reply/`, input.providerToken, { reply_content: '陪玩已回复', reply_version: 0 });
    await reviewCard.waitFor({ state: 'detached', timeout: 15000 });
    assert.equal(await draft.inputValue(), '客服正在编辑的草稿');
    await page.getByRole('button', { name: '发布回复' }).click();
    await page.getByText('回复已变化，草稿已保留', { exact: true }).waitFor({ timeout: 10000 });
    assert.equal(await draft.inputValue(), '客服正在编辑的草稿');
    await page.getByRole('button', { name: '读取最新回复' }).click();
    await page.getByText('当前回复：陪玩已回复', { exact: true }).waitFor();
    assert.equal(await draft.inputValue(), '客服正在编辑的草稿');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
