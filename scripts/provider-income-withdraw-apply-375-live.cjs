// Provider H5 rendered against the isolated real withdrawal APIs.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(input.root);
const target = new URL(input.base);
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => { if (error) { res.writeHead(404).end(); return; } res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data); });
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
    }, input);
    const page = await context.newPage();
    const errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(`${base}/#/pages/wallet/index`);
    await page.getByRole('button', { name: '管理账户', exact: true }).click({ timeout: 15000 });
    await page.waitForURL(/#\/pages\/withdrawAccounts\/index/);
    await page.getByRole('button', { name: '绑定账户', exact: true }).first().click({ timeout: 15000 });
    const binding = page.getByRole('dialog', { name: '绑定微信账户' });
    await binding.waitFor();
    await binding.getByPlaceholder('请输入微信号').locator('input').fill('isolated-account-20261102');
    await binding.getByPlaceholder('请输入真实姓名').locator('input').fill('隔离测试');
    const saved = page.waitForResponse(response => new URL(response.url()).pathname === '/api/wallet/withdraw-accounts/WECHAT/' && response.request().method() === 'PUT');
    await binding.getByRole('button', { name: '保存账户', exact: true }).click();
    assert.equal((await (await saved).json()).code, 0);
    await binding.waitFor({ state: 'hidden' });
    await page.getByText('隔离测试', { exact: true }).waitFor();
    await page.getByRole('button', { name: '返回钱包', exact: true }).click();
    await page.waitForURL(/#\/pages\/wallet\/index/);
    await page.getByRole('button', { name: '提现', exact: true }).first().click({ timeout: 15000 });
    const dialog = page.getByRole('dialog', { name: '申请提现' });
    await dialog.waitFor();
    await dialog.getByText('隔离测试', { exact: true }).waitFor();
    await dialog.getByText('isolated-account-20261102', { exact: true }).waitFor();
    await dialog.getByLabel('提现兴安币金额').locator('input').fill('600');
    await dialog.getByText('预计打款人民币 ¥58.80', { exact: false }).waitFor({ timeout: 15000 });
    const bounds = await dialog.boundingBox();
    assert.ok(bounds && bounds.x >= -1 && bounds.x + bounds.width <= 376, JSON.stringify(bounds));
    const submitted = page.waitForResponse(response => new URL(response.url()).pathname === '/api/wallet/withdraw/' && response.request().method() === 'POST');
    await dialog.getByRole('button', { name: '确认提现', exact: true }).click();
    const body = await (await submitted).json();
    assert.equal(body.code, 0, JSON.stringify(body));
    await dialog.waitFor({ state: 'hidden' });
    await page.getByText('等待客服领取', { exact: true }).waitFor({ timeout: 15000 });
    await page.getByRole('button', { name: '管理账户', exact: true }).click();
    await page.waitForURL(/#\/pages\/withdrawAccounts\/index/);
    await page.getByRole('button', { name: '修改账户', exact: true }).first().click();
    const edit = page.getByRole('dialog', { name: '修改微信账户' });
    await edit.getByPlaceholder('请输入微信号').locator('input').fill('isolated-updated-20261102');
    await edit.getByPlaceholder('请输入真实姓名').locator('input').fill('隔离新资料');
    const updated = page.waitForResponse(response => new URL(response.url()).pathname === '/api/wallet/withdraw-accounts/WECHAT/' && response.request().method() === 'PUT');
    await edit.getByRole('button', { name: '保存账户', exact: true }).click();
    assert.equal((await (await updated).json()).code, 0);
    await edit.waitFor({ state: 'hidden' });
    await page.getByText('隔离新资料', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Provider applied for withdrawal via actual 375px H5.');
  } finally { await browser.close(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
