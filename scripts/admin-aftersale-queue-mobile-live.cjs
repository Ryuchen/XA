// Real workbench and aftersale resolution using the isolated Django API.
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
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage(), errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => [base, input.api].includes(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    const tile = page.getByRole('button', { name: /处理中售后/ });
    await tile.waitFor({ timeout: 30000 });
    assert.match(await tile.innerText(), /2\s*单/);
    await tile.click();
    await page.getByText(/处理中售后 · 按申请时间从早到晚/).waitFor();
    const cards = page.locator('.od-mobile-card');
    await cards.first().waitFor();
    assert.equal(await cards.count(), 2);
    assert.ok((await cards.first().innerText()).includes(input.firstNo));
    assert.ok((await cards.nth(1).innerText()).includes(input.secondNo));
    await cards.first().getByRole('button', { name: '查看详情' }).click();
    const drawer = page.locator('.el-drawer');
    await drawer.getByText('申请说明：仅客服核对的具体售后说明', { exact: true }).waitFor();
    await drawer.getByRole('button', { name: '处理完成', exact: true }).click();
    const prompt = page.locator('.el-message-box');
    await prompt.locator('textarea').fill('已沟通并处理');
    await prompt.getByRole('button', { name: '确认处理' }).click();
    await drawer.getByText('处理说明：已沟通并处理', { exact: true }).waitFor();
    assert.equal(await drawer.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true);
    await page.goto(`${base}/dashboard`, { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: /处理中售后/ }).waitFor();
    assert.match(await page.getByRole('button', { name: /处理中售后/ }).innerText(), /1\s*单/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('375px workbench queue, oldest-first and actual aftersale resolution passed');
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
