// Render actual isolated dashboard data and follow its historical-day drilldown.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(input.root), target = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: target.host } }, upstream => { res.writeHead(upstream.statusCode, upstream.headers); upstream.pipe(res); });
    proxy.on('error', () => { if (!res.destroyed) res.writeHead(502).end(); }); req.pipe(proxy); return;
  }
  const name = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '');
  const file = path.resolve(root, name.startsWith('assets/') ? name : 'index.html');
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, {
    'Content-Type': { '.html':'text/html', '.css':'text/css', '.js':'text/javascript' }[path.extname(file)] || 'application/octet-stream',
  }).end(data));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true });
  let page;
  try {
    const context = await browser.newContext({ viewport:{ width:375, height:812 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    page = await context.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/dashboard`);
    const total = page.locator('.ap-kpi').filter({ hasText:'累计订单净额' });
    await total.getByText('910币', { exact:true }).waitFor({ timeout:20000 });
    const refunded = page.locator('.ap-kpi').filter({ hasText:'今日订单退款' });
    await refunded.getByText((input.refundCoins || '300') + '币', { exact:true }).waitFor();await refunded.getByText(`退回余额 · 1 单 / ${input.dayCount || 2} 次`, { exact:true }).waitFor();await refunded.getByRole('button',{name:'今日退款统计口径'}).click();await page.getByText(/不含直接支付测试单、没有退款记录/).waitFor();await refunded.getByRole('button',{name:'今日退款统计口径'}).click();
    const day = page.locator('.db-daily-item').filter({ hasText:input.historicalDay.slice(5) });
    await day.getByText('净额 700 币', { exact:true }).waitFor();
    const today = page.locator('.db-daily-item').filter({ hasText:input.today.slice(5) });
    await today.getByText('净额 210 币', { exact:true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await refunded.getByRole('button', { name:'查看今日退款订单' }).click();
    await page.waitForURL(url => url.pathname === '/orders' && url.searchParams.get('refund_day') === input.today);
    await page.getByText(`退款日期：${input.today}`, { exact:false }).waitFor({ timeout:15000 });
    await page.getByText(input.orderNo, { exact:true }).first().waitFor({ timeout:15000 });
    assert.equal(await page.locator('.od-mobile-card').count(), 1);
    await page.locator('.od-mobile-card').getByText(`${input.refundCoins || '300'} 币 · ${input.dayCount || 2} 次`, { exact:true }).waitFor();
    await page.getByText(/最近退款优先/).waitFor();
    await page.locator('.od-mobile-card').getByText(/^最近 /).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await page.locator('.od-mobile-card').getByRole('button', { name:'查看详情' }).click();
    const detail = page.getByRole('dialog', { name:'订单详情' });
    await detail.getByText('累计退回老板').waitFor({ timeout:15000 });
    assert.equal(await detail.getByText('所选日期', { exact:true }).count(), input.dayCount || 2);
    await detail.getByText('退款笔数').waitFor();
    await page.keyboard.press('Escape');
    await detail.waitFor({ state:'hidden' });
    await page.getByRole('button', { name:'清除日期' }).click();
    await page.waitForURL(url => !url.searchParams.has('refund_day'));
    await page.waitForFunction(() => document.querySelectorAll('.od-mobile-card').length === 2);
    await page.goto(`http://127.0.0.1:${server.address().port}/dashboard`);
    await page.locator('.db-daily-item').filter({ hasText:input.historicalDay.slice(5) }).waitFor({ timeout:15000 });
    await day.click();
    await page.waitForURL(url => url.pathname === '/orders' && url.searchParams.get('created_day') === input.historicalDay);
    await page.getByText(input.orderNo, { exact:true }).first().waitFor({ timeout:15000 });
    console.log('Dashboard shows actual net amounts by order day and opens the matching order');
    await context.close();
  } catch (error) {
    if (page) console.error('Page:', (await page.locator('body').innerText()).slice(0, 1600));
    throw error;
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
