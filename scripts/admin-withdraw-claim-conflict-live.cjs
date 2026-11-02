// Two real operators and a live ASGI connection; no payment is performed.
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
      const timer = setTimeout(() => reject(Error('admin websocket did not connect')), 15000);
      page.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) { clearTimeout(timer); resolve(); } }));
    });
    await page.goto(`${base}/withdrawals?status=PENDING&claim_scope=mine`);
    const card = page.locator('.wd-mobile-card').filter({ hasText: `申请 #${input.withdrawId} ` });
    await card.waitFor({ timeout: 30000 }); await connected;
    await card.getByRole('button', { name: '通过', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '提现审核' });
    await dialog.getByPlaceholder('打款流水号/凭证编号（必填）').fill('ISOLATED-NOT-PAID');
    const response = await fetch(`${input.api}/api/admin/withdrawals/${input.withdrawId}/release-claim/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.managerToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmed_unpaid: true, reason: '隔离测试核实未打款',
        expected_claimed_by_account: input.claimed.claimed_by_account, expected_claimed_at: input.claimed.claimed_at }),
    });
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    await dialog.waitFor({ state: 'hidden', timeout: 10000 });
    await page.getByText('提现领取或审核状态已变化，请重新核对；如已线下打款，请先核对凭证，勿重复打款。').waitFor({ timeout: 10000 });
    await card.waitFor({ state: 'hidden', timeout: 10000 });
    const claimResponse = await fetch(`${input.api}/api/admin/withdrawals/${input.withdrawId}/claim/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' }, body: '{}',
    });
    const claim = await claimResponse.json(); assert.equal(claim.code, 0, JSON.stringify(claim));
    await card.waitFor({ state: 'visible', timeout: 10000 });
    await card.getByRole('button', { name: '驳回', exact: true }).click();
    const rejectDialog = page.getByRole('dialog', { name: '驳回提现' });
    await rejectDialog.getByPlaceholder('驳回原因（必填）').fill('隔离测试：收款信息需核对');
    await rejectDialog.getByRole('button', { name: '确认驳回' }).click();
    await rejectDialog.waitFor({ state: 'hidden', timeout: 10000 });
    await card.waitFor({ state: 'hidden', timeout: 10000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
