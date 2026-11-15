// Drive the actual built interfaces against an isolated live Django server.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), target = new URL(input.base);
function serve(rootPath, spa) {
  const root = path.resolve(rootPath);
  return http.createServer((req, res) => {
    if (req.url.startsWith('/api/')) {
      const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
        headers: { ...req.headers, host: target.host, connection: 'close' }, agent: false }, response => {
        res.writeHead(response.statusCode, response.headers); response.pipe(res);
      });
      proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
    }
    let file = path.resolve(root, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
    if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
    if (spa && !fs.existsSync(file) && !path.extname(file)) file = path.join(root, 'index.html');
    fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, {
      'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' }[path.extname(file)] || 'application/octet-stream'
    }).end(data));
  });
}
(async () => {
  const admin = serve(input.adminRoot, true), provider = serve(input.providerRoot, false);
  await Promise.all([new Promise(resolve => admin.listen(0, '127.0.0.1', resolve)), new Promise(resolve => provider.listen(0, '127.0.0.1', resolve))]);
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true, timeout: 15000 });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${admin.address().port}/orders`);
    const card = page.locator('.od-mobile-card').filter({ hasText: input.orderNo });
    await card.waitFor({ timeout: 30000 });
    await card.getByRole('button', { name: '退款', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '订单退款 · 资金复核' });
    await dialog.getByText('安全箱协商报酬', { exact: true }).waitFor({ timeout: 15000 });
    console.log('Admin form loaded');
    await dialog.getByPlaceholder('可填 0，最多 1 位小数').fill('800');
    await dialog.getByRole('button', { name: '试算报酬' }).click();
    await dialog.getByText('协商报酬合计', { exact: true }).waitFor({ timeout: 15000 });
    await dialog.getByLabel('协商说明（2–255 字）').fill('双方协商终止服务');
    await dialog.getByRole('checkbox', { name: '我已核对协商资料、各方报酬与免费券处理' }).check();
    assert.equal(await dialog.getByRole('button', { name: '确认协商终止' }).isEnabled(), true);
    await dialog.getByRole('button', { name: '确认协商终止' }).click();
    await dialog.waitFor({ state: 'hidden', timeout: 15000 });
    console.log('Admin confirmed');
    await context.close();
    const pc = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await pc.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
    }, { token: input.providerToken, id: input.providerId });
    const pp = await pc.newPage();
    await pp.goto(`http://127.0.0.1:${provider.address().port}/#/orders?order=${input.orderId}`);
    const order = pp.locator('.order-card').filter({ hasText: input.orderNo });
    await order.getByText('订单已协商终止', { exact: true }).waitFor({ timeout: 15000 });
    const text = await order.innerText();
    assert.ok(text.includes('本人协商报酬：800 币'), text);
    assert.ok(text.includes('本人协商报酬待结算'), text);
    console.log('Provider pending result rendered');
    await pc.close();
    const wc = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await wc.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
    const wp = await wc.newPage();
    await wp.goto(`http://127.0.0.1:${admin.address().port}/dashboard`);
    const dashboardItem = wp.getByRole('button', { name: /待结算平台补贴/ });
    await dashboardItem.waitFor({ timeout: 15000 });
    assert.ok((await dashboardItem.innerText()).includes('800 币'));
    await dashboardItem.click();
    const funds = wp.getByRole('region', { name: '平台补贴资金' });
    await funds.getByText('待结算 1 单 · 800 币', { exact: true }).waitFor({ timeout: 15000 });
    await funds.getByText('协商终止待结算', { exact: false }).waitFor();
    await funds.getByRole('button', { name: input.orderNo, exact: true }).click();
    await wp.getByText(`精确查找订单 ${input.orderNo}`, { exact: true }).waitFor({ timeout: 15000 });
    await wp.goto(`http://127.0.0.1:${admin.address().port}/wallets`);
    await funds.getByRole('button', { name: '登记线下补入' }).click({ timeout: 15000 });
    const topup = wp.getByRole('dialog', { name: '登记平台线下补入' });
    await topup.getByPlaceholder('最多一位小数').fill('0.1');
    await topup.getByPlaceholder('填写可核对的凭据编号').fill('ui-offline-001');
    await topup.getByRole('button', { name: '核对并登记' }).click();
    await wp.getByRole('dialog', { name: '确认登记线下补入' }).getByRole('button', { name: '确认登记' }).click();
    await topup.waitFor({ state: 'hidden', timeout: 15000 });
    await wc.close();
    console.log('Real admin confirmation, platform topup and provider receipt verified at 375px');
  } finally {
    await browser.close();
    admin.closeAllConnections(); provider.closeAllConnections();
    admin.close(); provider.close();
  }
})().then(() => process.exit(0)).catch(error => { console.error(error); process.exit(1); });
