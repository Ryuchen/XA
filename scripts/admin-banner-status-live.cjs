// Real admin list/detail warning for a target disabled after banner creation.
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
      if (i === 119) throw Error('Vite unavailable');
      await new Promise(resolve => setTimeout(resolve, 250));
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage();
    let failGameplayOptions = true;
    await page.route('**/*', route => {
      const url = new URL(route.request().url());
      if (failGameplayOptions && url.pathname === '/api/admin/options/gameplays/') return route.abort('failed');
      return [base, input.api].includes(url.origin) ? route.continue() : route.abort();
    });
    await page.goto(`${base}/banners`);
    const row = page.locator('.el-table__body tr').filter({ hasText: '隔离首页轮播' });
    await row.getByText('目标不可用', { exact: true }).waitFor({ timeout: 30000 });
    const updateService = async active => {
      const response = await fetch(`${input.api}/api/admin/service-items/${input.serviceId}/`, { method: 'PATCH',
        headers: { Authorization: `Bearer ${input.serviceToken}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ is_active: active }) });
      const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    };
    await updateService(true);
    await row.getByRole('button', { name: '编辑' }).click();
    const dialog = page.getByRole('dialog', { name: '编辑轮播' });
    await dialog.waitFor({ timeout: 15000 });
    assert.equal(await dialog.getByText('当前目标不可用', { exact: true }).count(), 0, 'edit must read the newly enabled target');
    await dialog.getByText('部分目标加载失败', { exact: true }).waitFor();
    assert.match(await dialog.innerText(), /玩法暂未加载/);
    failGameplayOptions = false;
    await dialog.getByRole('button', { name: '重新加载目标' }).click();
    await dialog.getByText('部分目标加载失败', { exact: true }).waitFor({ state: 'hidden', timeout: 15000 });
    await updateService(false);
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await page.getByText('已保存，但目标当前不可用；请更换目标或停用轮播', { exact: true }).waitFor({ timeout: 15000 });
    await dialog.waitFor({ state: 'hidden' });
    await updateService(true);
    await page.getByRole('button', { name: '刷新状态' }).click();
    await row.getByText('目标不可用', { exact: true }).waitFor({ state: 'hidden', timeout: 15000 });
    await updateService(false);
    await page.getByRole('button', { name: '刷新状态' }).click();
    await row.getByText('目标不可用', { exact: true }).waitFor({ timeout: 15000 });
    await row.getByRole('button', { name: '编辑' }).click();
    await dialog.getByText('当前目标不可用', { exact: true }).waitFor();
    assert.equal(await dialog.getByRole('button', { name: '保存' }).isEnabled(), true);
    const type = dialog.locator('.el-form-item').filter({ hasText: '跳转类型' }).locator('.el-select');
    await type.click();
    await page.locator('.el-select-dropdown__item:visible').filter({ hasText: '玩法服务' }).click();
    const target = dialog.locator('.el-form-item').filter({ hasText: '业务目标' }).locator('.el-select');
    await target.click();
    await page.locator('.el-select-dropdown__item:visible').filter({ hasText: input.gameplayName }).waitFor();
    await page.keyboard.press('Escape');
    await type.click();
    await page.locator('.el-select-dropdown__item:visible').filter({ hasText: '活动与权益' }).click();
    await target.click();
    await page.locator('.el-select-dropdown__item:visible').filter({ hasText: input.contentName }).waitFor();
    await page.keyboard.press('Escape');
    await dialog.getByRole('button', { name: '取消', exact: true }).click();
    await page.setViewportSize({ width: 375, height: 812 });
    const mobile = page.locator('.bn-mobile-card').filter({ hasText: '隔离首页轮播' });
    await mobile.getByText('目标不可用 · 老板点击时会收到提示', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, 'mobile horizontal overflow');
    await mobile.getByRole('button', { name: '编辑', exact: true }).click();
    await dialog.getByText('当前目标不可用', { exact: true }).waitFor();
    await page.waitForFunction(() => { const el = document.querySelector('.el-dialog'); const rect = el && el.getBoundingClientRect(); return rect && rect.left >= -1 && rect.right <= innerWidth + 1; });
    assert.equal(await dialog.locator('.el-dialog__body').evaluate(el => el.scrollWidth <= el.clientWidth + 1), true, 'mobile dialog overflow');
    await type.click();
    await page.locator('.el-select-dropdown__item:visible').filter({ hasText: '活动与权益' }).click();
    await target.click();
    await page.locator('.el-select-dropdown__item:visible').filter({ hasText: input.contentName }).click();
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.waitFor({ state: 'hidden', timeout: 15000 });
    await mobile.getByText('活动与权益', { exact: false }).waitFor({ timeout: 15000 });
    assert.equal(await mobile.getByText('目标不可用 · 老板点击时会收到提示', { exact: true }).count(), 0);
  } finally { if (browser) await browser.close(); vite.kill(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
