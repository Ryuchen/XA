// Real admin order drawer at mobile width, backed by isolated Django HTTP.
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
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/escorts`, { waitUntil: 'domcontentloaded' });
    const escortSearch = page.getByPlaceholder('昵称/账号/手机号');
    await escortSearch.fill(input.username);
    const found = page.waitForResponse(response => { const url = new URL(response.url()); return url.pathname === '/api/admin/escorts/' && url.searchParams.get('keyword') === input.username; }, { timeout: 15000 });
    await escortSearch.press('Enter'); await found;
    const escort = page.locator('.es-mobile-card').filter({ hasText: input.username });
    await escort.waitFor({ timeout: 15000 });
    await escort.getByRole('button', { name: '更多', exact: true }).click();
    await page.getByRole('menuitem', { name: '奖罚', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '奖励 / 罚款' });
    await dialog.waitFor();
    assert.ok((await dialog.innerText()).includes(input.username));
    await dialog.getByLabel('金额(兴安币)', { exact: true }).fill('300');
    await dialog.getByPlaceholder('奖罚原因（可选）').fill(input.reason);
    await dialog.getByRole('button', { name: '核对并提交' }).click();
    const confirm = page.getByRole('dialog', { name: '确认奖罚' });
    await confirm.waitFor();
    const content = await confirm.innerText();
    assert.ok(content.includes(input.username) && content.includes('300 币') && content.includes(input.reason), content);
    const posted = page.waitForResponse(response => /\/api\/admin\/escorts\/\d+\/dispose\/$/.test(new URL(response.url()).pathname) && response.request().method() === 'POST', { timeout: 15000 });
    await confirm.getByRole('button', { name: '确认记账' }).click();
    const postedResponse = await posted; const postedBody = await postedResponse.json();
    assert.equal(postedBody.code, 0, JSON.stringify(postedBody));
    await dialog.waitFor({ state: 'hidden', timeout: 15000 });
    assert.equal(await page.getByText('奖罚结果待核对', { exact: true }).count(), 0);
    await escort.getByRole('button', { name: '更多', exact: true }).click();
    await page.getByRole('menuitem', { name: '奖罚', exact: true }).click();
    await dialog.waitFor();
    await dialog.getByText('罚款', { exact: true }).click();
    await dialog.getByLabel('金额(兴安币)', { exact: true }).fill('100');
    await dialog.getByPlaceholder('奖罚原因（可选）').fill(input.penaltyReason);
    await dialog.getByRole('button', { name: '核对并提交' }).click();
    const penaltyConfirm = page.getByRole('dialog', { name: '确认奖罚' });
    await penaltyConfirm.waitFor();
    const penaltyText = await penaltyConfirm.innerText();
    assert.ok(penaltyText.includes(input.username) && penaltyText.includes('100 币') && penaltyText.includes(input.penaltyReason), penaltyText);
    const penaltyPosted = page.waitForResponse(response => /\/api\/admin\/escorts\/\d+\/dispose\/$/.test(new URL(response.url()).pathname) && response.request().method() === 'POST', { timeout: 15000 });
    await penaltyConfirm.getByRole('button', { name: '确认记账' }).click();
    assert.equal((await (await penaltyPosted).json()).code, 0);
    await dialog.waitFor({ state: 'hidden', timeout: 15000 });
    assert.equal(await page.getByText('奖罚结果待核对', { exact: true }).count(), 0);
    await page.goto(`${base}/dispose-records`, { waitUntil: 'domcontentloaded' });
    const search = page.getByPlaceholder('账号/昵称');
    await search.fill(input.username);
    const filtered = page.waitForResponse(response => { const url = new URL(response.url()); return url.pathname === '/api/admin/dispose-records/' && url.searchParams.get('keyword') === input.username; }, { timeout: 15000 });
    await search.press('Enter'); await filtered;
    const card = page.locator('.dp-card').filter({ hasText: input.reason });
    await card.waitFor({ timeout: 15000 });
    const text = await card.innerText();
    assert.ok(text.includes(input.username) && text.includes(input.nickname) && text.includes('+300 币') && text.includes('奖励') && text.includes(input.staffName), text);
    const penalty = page.locator('.dp-card').filter({ hasText: input.penaltyReason });
    await penalty.waitFor();
    assert.ok((await penalty.innerText()).includes('-100 币'));
    assert.equal(await page.locator('.dp-card').count(), 2);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Admin reward record matched current account at 375px');
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
