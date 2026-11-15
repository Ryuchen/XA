const fs = require('node:fs'), path = require('node:path'), http = require('node:http'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const x = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(x.root), api = new URL(x.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const peer = http.request({ hostname: api.hostname, port: api.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: api.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    peer.on('error', () => res.writeHead(502).end()); req.pipe(peer); return;
  }
  const name = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '');
  const file = path.resolve(root, name.startsWith('assets/') ? name : 'index.html');
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, bytes) => error ? res.writeHead(404).end() : res.writeHead(200,
    { 'Content-Type': { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css' }[path.extname(file)] || 'application/octet-stream' }).end(bytes));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), x.token);
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/club-operations`);
    await page.getByRole('button', { name: /会员权益/ }).click({ timeout: 10000 });
    await page.getByRole('tab', { name: 'VIP 权益' }).click();
    await page.getByRole('row').filter({ hasText: '绑定验收会员' }).getByRole('button', { name: '配置' }).click();
    const dialog = page.getByRole('dialog', { name: '配置VIP 权益' });
    await dialog.getByText(/服务中协商终止时，原券不退/).waitFor();
    await dialog.getByText('已绑定套餐已停用或不再符合条件，请移除后保存。').waitFor();
    assert.equal(await dialog.getByRole('button', { name: '保存' }).isEnabled(), false);
    await dialog.getByRole('button', { name: `移除不可用套餐 #${x.staleId}` }).click();
    await dialog.getByLabel('33安全箱代肝主套餐绑定').click();
    await page.getByRole('option').filter({ hasText: '适用安全箱' }).waitFor();
    assert.equal(await page.getByRole('option').filter({ hasText: '双陪安全箱' }).count(), 0);
    assert.equal(await page.getByRole('option').filter({ hasText: '适用小时陪玩' }).count(), 0);
    await page.getByLabel('搜索套餐').fill('远端安全箱');
    await page.getByRole('option').filter({ hasText: '远端安全箱' }).click();
    await page.keyboard.press('Escape');
    await dialog.getByLabel('1小时陪玩绑定').click();
    await page.getByRole('option').filter({ hasText: '适用小时陪玩' }).waitFor();
    assert.equal(await page.getByRole('option').filter({ hasText: '适用安全箱' }).count(), 0);
    await page.keyboard.press('Escape');
    await dialog.getByRole('button', { name: '保存' }).click();
    await dialog.waitFor({ state: 'hidden' });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    console.log('Admin VIP binding options match actual API eligibility at 375px');
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
})().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
