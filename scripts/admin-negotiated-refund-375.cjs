const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(input.root), target = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  let file = path.resolve(root, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  if (!fs.existsSync(file) && !path.extname(file)) file = path.join(root, 'index.html');
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, {
    'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' }[path.extname(file)] || 'application/octet-stream' }).end(data));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/orders`);
    const card = page.locator('.od-mobile-card').filter({ hasText: input.orderNo });
    await card.waitFor({ timeout: 30000 });
    await card.getByRole('button', { name: '退款', exact: true }).click();
    const dialog = page.getByRole('dialog', { name: '订单退款 · 资金复核' });
    await dialog.getByText('服务中转单协商退款', { exact: true }).waitFor({ timeout: 15000 });
    await dialog.getByPlaceholder('请输入本次退款金额').fill('500');
    const entries = dialog.locator('label.od-refund-input').filter({ has: page.getByPlaceholder('填写承担金额（可填 0）') });
    assert.equal(await entries.count(), 3);
    await entries.nth(0).getByPlaceholder('填写承担金额（可填 0）').fill('100');
    await entries.nth(1).getByPlaceholder('填写承担金额（可填 0）').fill('300');
    await entries.nth(2).getByPlaceholder('填写承担金额（可填 0）').fill('100');
    await dialog.getByRole('button', { name: '刷新试算' }).click();
    await dialog.getByText('协商退款，订单终止').waitFor({ timeout: 15000 });
    await dialog.getByText('本次退款后保留金额', { exact: true }).waitFor();
    await dialog.locator('textarea').fill('隔离场景履约协商');
    await dialog.getByRole('checkbox', { name: '我已核对协商资料和各方承担金额' }).check();
    if (input.shortfall) {
      await dialog.getByText('当前不满足退款条件，无法确认退款', { exact: true }).waitFor();
      const shortage = await dialog.locator('.od-refund-shortfalls').innerText();
      assert.ok(shortage.includes('不足 50 币'), shortage);
      assert.equal(await dialog.getByRole('button', { name: '确认本次退款' }).isDisabled(), true);
      console.log('Actual admin form presents shortfall and blocks submission');
      await context.close();
      return;
    }
    assert.equal(await dialog.getByRole('button', { name: '确认本次退款' }).isEnabled(), true);
    await entries.nth(0).getByPlaceholder('填写承担金额（可填 0）').fill('101');
    assert.equal(await dialog.getByRole('button', { name: '确认本次退款' }).isDisabled(), true);
    await entries.nth(0).getByPlaceholder('填写承担金额（可填 0）').fill('100');
    await dialog.getByRole('button', { name: '刷新试算' }).click();
    await dialog.getByText('协商退款，订单终止').waitFor({ timeout: 15000 });
    assert.equal(await dialog.getByRole('checkbox', { name: '我已核对协商资料和各方承担金额' }).isChecked(), false);
    await dialog.getByRole('checkbox', { name: '我已核对协商资料和各方承担金额' }).check();
    await dialog.getByRole('button', { name: '确认本次退款' }).click();
    await dialog.waitFor({ state: 'hidden', timeout: 15000 });
    await card.getByRole('button', { name: '查看详情' }).click();
    await page.getByText('协商承担 100 币', { exact: false }).first().waitFor({ timeout: 15000 });
    await page.getByText('订单保留金额', { exact: true }).waitFor();
    console.log('Real admin negotiation form, signed preview, submission and detail receipt verified at 375px');
    await context.close();
    const providerRoot = path.resolve(input.providerRoot);
    const providerServer = http.createServer((req, res) => {
      if (req.url.startsWith('/api/')) {
        const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
          headers: { ...req.headers, host: target.host, connection: 'close' }, agent: false }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
        proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
      }
      const file = path.resolve(providerRoot, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
      if (!file.startsWith(providerRoot + path.sep)) return res.writeHead(404).end();
      fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, {
        'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' }[path.extname(file)] || 'application/octet-stream' }).end(data));
    });
    await new Promise(resolve => providerServer.listen(0, '127.0.0.1', resolve));
    try {
      for (const user of input.providers) {
        console.log('Opening provider', user.id);
        const pc = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
        await pc.addInitScript(({ token, id }) => { localStorage.setItem('token', JSON.stringify({ data: token }));
          localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } })); }, user);
        const pp = await pc.newPage();
        pp.on('request', request => { if (request.url().includes('/api/')) console.log('request', new URL(request.url()).pathname); });
        pp.on('response', response => { if (response.url().includes('/api/')) console.log('response', response.status(), new URL(response.url()).pathname); });
        await pp.goto(`http://127.0.0.1:${providerServer.address().port}/#/orders?order=${input.orderId}`);
        console.log('Provider page loaded', user.id);
        const order = pp.locator('.order-card').filter({ hasText: input.orderNo });
        try { await order.getByText('订单部分退款', { exact: true }).waitFor({ timeout: 15000 }); }
        catch (error) { console.error('Provider page:', await pp.locator('body').innerText()); throw error; }
        const rendered = await order.innerText();
        assert.ok(rendered.includes('已退老板：500 币'), rendered);
        assert.ok(rendered.includes(`本人服务收益已追回：${user.recovered} 币`), rendered);
        assert.ok(rendered.includes(`本人服务收益流水净额：${user.net} 币`), rendered);
        console.log('Provider receipt checked', user.id);
        await pp.goto(`http://127.0.0.1:${providerServer.address().port}/#/reports?order=${input.orderId}`);
        const report = pp.locator('.report').filter({hasText: input.orderNo});
        await report.waitFor({timeout:15000});
        const reportText = await report.innerText();
        assert.ok(reportText.includes(input.reportLabel || '原订单分账'), reportText);
        assert.ok(reportText.includes(`${user.snapshot} 兴安币`), reportText);
        assert.ok(reportText.includes(`服务净收益 ${user.net} 币`), reportText);
        assert.ok(reportText.includes('待提交') && reportText.includes('暂无战绩截图'), reportText);
        assert.ok(!reportText.includes('至少上传一张战绩截图'), reportText);
        console.log('Provider report snapshot label and real net:', user.id, reportText.replace(/\s+/g, ' '));
        await pc.close();
      }
      console.log('Both real provider Vue receipts verified');
    } finally { providerServer.closeAllConnections(); providerServer.close(); }
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
})().then(() => process.exit(0)).catch(error => { console.error(error.stack || error); process.exit(1); });
