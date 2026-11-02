// Exercise the actual provider order -> support flow over isolated HTTP.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(process.env.XA_PROVIDER_CHAT_H5_ROOT || '.tools/provider-order-chat-h5-nav-fix-20261101');
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
    await context.addInitScript(({ providerToken, providerId }) => {
      localStorage.setItem('token', JSON.stringify({ data: providerToken }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id: providerId, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
    }, input);
    const page = await context.newPage();
    const failedApi = [];
    page.on('response', response => { if (response.url().includes('/api/') && response.status() >= 500) failedApi.push(`${response.status()} ${response.url()}`); });
    await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    const base = `http://127.0.0.1:${server.address().port}`;
    await page.goto(`${base}/#/pages/orders/index`);
    await page.locator('taro-button-core:visible').filter({ hasText: /^已接单$/ }).first().click({ timeout: 15000 });
    const order = page.locator('[class*="orderCard"]:visible').filter({ hasText: input.orderNo });
    await order.waitFor({ timeout: 15000 });
    await order.getByText('咨询客服', { exact: true }).click();
    await page.getByText(`关联订单 ${input.orderNo}`, { exact: true }).waitFor({ timeout: 15000 });
    if (!process.env.XA_RAPID_NAV) await page.waitForTimeout(500);
    await page.locator('textarea').fill('第一单未发送的草稿');
    await page.locator('taro-button-core:visible').filter({ hasText: '返回' }).first().click();
    await page.locator('taro-button-core:visible').filter({ hasText: /^已接单$/ }).first().click();
    const second = page.locator('[class*="orderCard"]:visible').filter({ hasText: input.secondOrderNo });
    await second.getByText('咨询客服', { exact: true }).click();
    await page.getByText(`关联订单 ${input.secondOrderNo}`, { exact: true }).waitFor();
    if (!process.env.XA_RAPID_NAV) await page.waitForTimeout(500);
    assert.equal(await page.locator('textarea').inputValue(), '', 'other order does not inherit the first order draft');
    await page.locator('taro-button-core:visible').filter({ hasText: '返回' }).first().click();
    try { await page.locator('taro-button-core:visible').filter({ hasText: /^已接单$/ }).first().click({ timeout: 8000 }); }
    catch (error) {
      const folder = path.resolve('.tools/provider-order-rapid-nav-20261101'); fs.mkdirSync(folder, { recursive: true });
      await page.screenshot({ path: path.join(folder, 'failure.png') });
      throw new Error(`${error.message}\nURL ${page.url()}\n${await page.locator('body').innerText()}\n${(await page.locator('body').innerHTML()).slice(0, 5000)}`);
    }
    await order.getByText('咨询客服', { exact: true }).click();
    await page.getByText(`关联订单 ${input.orderNo}`, { exact: true }).waitFor();
    assert.equal(await page.locator('textarea').inputValue(), '第一单未发送的草稿');
    await page.locator('textarea').fill('订单咨询测试');
    await page.getByText('发送', { exact: true }).click();
    await page.getByText(`订单号 ${input.orderNo}\n订单咨询测试`, { exact: true }).waitFor({ timeout: 15000 });
    await page.locator('textarea').fill('图片情况');
    const chooser = page.waitForEvent('filechooser');
    await page.getByText('发送图片', { exact: true }).click();
    const uploaded = page.waitForResponse(response => response.url().includes('/api/chat/send/') && response.request().method() === 'POST' && response.status() === 200);
    await (await chooser).setFiles(path.resolve('apps/boss-miniapp/native/assets/icons/chat.png'));
    assert.equal((await (await uploaded).json()).code, 0);
    const chat = await fetch(`${input.base}/api/chat/session/`, { headers: { Authorization: `Bearer ${input.providerToken}` } }).then(r => r.json());
    assert.equal(chat.code, 0);
    const staff = await fetch(`${input.base}/api/admin/chat-sessions/${chat.data.session.id}/messages/`, { headers: { Authorization: `Bearer ${input.staffToken}` } }).then(r => r.json());
    assert.equal(staff.code, 0);
    assert.ok(staff.data.list.some(message => message.content_type === 'IMAGE' && message.content.includes(input.orderNo) && message.content.includes('图片情况')));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(failedApi, []);
    console.log('Provider order context reaches staff in text and image.');
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
