const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), target = new URL(input.base);
function serve(root, spa) {
  root = path.resolve(root);
  return http.createServer((req, res) => {
    if (req.url.startsWith('/api/')) {
      const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
        headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
      proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
    }
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '');
    const file = path.resolve(root, spa && !pathname.startsWith('assets/') ? 'index.html' : pathname || 'index.html');
    if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
    const type = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream';
    fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': type }).end(data));
  });
}
(async () => {
  const providerServer = serve(input.providerRoot, false), adminServer = serve(input.adminRoot, true);
  await Promise.all([new Promise(r => providerServer.listen(0, '127.0.0.1', r)), new Promise(r => adminServer.listen(0, '127.0.0.1', r))]);
  let browser;
  try {
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const provider = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await provider.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
    }, input.provider);
    const page = await provider.newPage();
    await page.goto(`http://127.0.0.1:${providerServer.address().port}/#/pages/wallet/index`);
    await page.locator('taro-button-core:visible').filter({ hasText: /^礼物$/ }).first().click({ timeout: 20000 });
    try { await page.getByText(input.sourceNo ? `服务订单 ${input.sourceNo}` : '直接赠送', { exact: true }).first().waitFor({ timeout: 20000 }); }
    catch (error) { console.error('Provider wallet:', await page.locator('body').innerText()); throw error; }
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    if (input.refundStatus) {
      const gift = page.locator('taro-view-core[class*="listItem"]').filter({ hasText: input.giftNo });
      await gift.getByText(input.refundStatus === 'PARTIAL' ? '部分退款 · 保留净实得' : '已退款 · 收益已冲回', { exact: true }).waitFor({ timeout: 20000 });
      await gift.getByText(input.walletIncome, { exact: true }).waitFor({ timeout: 20000 });
      await page.goto(`http://127.0.0.1:${providerServer.address().port}/#/pages/messages/index`);
      await page.getByText(input.refundStatus === 'PARTIAL' ? '订单部分退款' : '订单已退款', { exact: true }).first().click({ timeout: 20000 });
      await page.getByText('查看关联订单', { exact: true }).click({ timeout: 20000 });
      await page.locator('taro-text-core:visible').filter({ hasText: input.giftNo }).first().waitFor({ timeout: 20000 });
      await page.getByText(input.refundStatus === 'PARTIAL' ? '已完成 · 部分退款' : '已退款', { exact: true }).first().waitFor({ timeout: 20000 });
      await page.locator('taro-text-core:visible').filter({ hasText: '原礼物收益' }).first().waitFor({ timeout: 20000 });
      await page.locator('taro-button-core:visible').filter({ hasText: '收益与结算' }).first().click();
      await page.getByText(`礼物净收益 ${input.serviceIncome}币`, { exact: true }).waitFor({ timeout: 20000 });
      await page.getByText(`礼物收益累计追回 ${input.recoveredIncome}币`, { exact: true }).waitFor({ timeout: 20000 });
      if (input.referralIncome) await page.getByText(`推荐分佣净额 ${input.referralIncome}币`, { exact: true }).waitFor({ timeout: 20000 });
      if (input.hasNaming) await page.locator('taro-text-core:visible').filter({ hasText: input.noNaming ? '本次处理后该冠名无剩余赠礼时长' : '本次处理后冠名截止：' }).first().waitFor({ timeout: 20000 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      await provider.close();
      if (input.staffToken && input.hasNaming) {
        const admin = await browser.newContext({ viewport: { width: 375, height: 812 } });
        await admin.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
        const panel = await admin.newPage();
        await panel.goto(`http://127.0.0.1:${adminServer.address().port}/orders`);
        const card = panel.locator('.od-mobile-card').filter({ hasText: input.giftNo });
        await card.getByRole('button', { name: '查看详情', exact: true }).click({ timeout: 20000 });
        const expected = input.noNaming ? '本次处理后该冠名无剩余赠礼时长' : await panel.evaluate(value => {
          const d = new Date(value), pad = n => String(n).padStart(2, '0');
          return `本次处理后冠名截止：${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
        }, input.namingExpiry);
        await panel.getByText(expected, { exact: true }).waitFor({ timeout: 20000 });
        assert.equal(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
        const drawer = panel.locator('.el-drawer:visible');
        assert.equal(await drawer.evaluate(element => element.scrollWidth <= element.clientWidth + 1), true);
        await admin.close();
      }
      return;
    }
    await page.goto(`http://127.0.0.1:${providerServer.address().port}/#/pages/messages/index`);
    await page.getByText('收到老板礼物', { exact: true }).first().click({ timeout: 20000 });
    await page.getByText('查看关联订单', { exact: true }).click({ timeout: 20000 });
    await page.getByText(input.giftNo, { exact: false }).first().waitFor({ timeout: 20000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await provider.close();
    const admin = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await admin.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
    const panel = await admin.newPage();
    await panel.goto(`http://127.0.0.1:${adminServer.address().port}/orders`);
    const card = panel.locator('.od-mobile-card').filter({ hasText: input.giftNo });
    await card.getByRole('button', { name: '查看详情', exact: true }).click({ timeout: 20000 });
    await panel.getByText(`服务订单 ${input.sourceNo}`, { exact: true }).waitFor({ timeout: 20000 });
    assert.equal(await panel.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await admin.close();
  } finally { if (browser) await browser.close(); providerServer.closeAllConnections(); providerServer.close(); adminServer.closeAllConnections(); adminServer.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
