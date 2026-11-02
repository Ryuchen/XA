// Render actual provider and admin pages against the isolated business server.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), target = new URL(input.base);
function staticServer(root, spa) {
  root = path.resolve(root);
  return http.createServer((req, res) => {
    if (req.url.startsWith('/api/')) {
      const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
        headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
      proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
    }
    const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '');
    const file = path.resolve(root, spa && !pathname.startsWith('assets/') ? 'index.html' : pathname || 'index.html');
    if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
    const type = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream';
    fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': type }).end(data));
  });
}
async function main() {
  const providerServer = staticServer(input.providerRoot, false), adminServer = staticServer(input.adminRoot, true);
  await new Promise(resolve => providerServer.listen(0, '127.0.0.1', resolve));
  await new Promise(resolve => adminServer.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    if (input.replyAuthorOnly) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await context.addInitScript(({ token, id }) => {
        localStorage.setItem('token', JSON.stringify({ data: token }));
        localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
      }, input.provider);
      const page = await context.newPage();
      await page.goto(`http://127.0.0.1:${providerServer.address().port}/#/evaluations`);
      const card = page.locator('.review:visible').filter({ hasText: '客服核实回复' });
      try { await card.getByText('客服回复', { exact: true }).waitFor({ timeout: 15000 }); }
      catch (error) { throw new Error(`Reply author not visible: ${(await page.locator('body').innerText()).slice(0, 1800)}\n${error}`); }
      assert.equal(await card.getByText('我的回复', { exact: true }).count(), 0);
      await card.getByText('修改回复', { exact: true }).click();
      await page.getByText('保存后将替换当前回复，并显示为你的回复。', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      await context.close();
      const adminContext = await browser.newContext({ viewport: { width: 375, height: 812 } });
      await adminContext.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
      const admin = await adminContext.newPage();
      await admin.goto(`http://127.0.0.1:${adminServer.address().port}/evaluations`);
      const adminCard = admin.locator('.ev-card').filter({ hasText: input.orderNo }).filter({ hasText: '独立评价0' });
      await adminCard.getByRole('button', { name: '详情', exact: true }).click({ timeout: 20000 });
      const detail = admin.locator('.ev-detail');
      await detail.getByText('回复来源', { exact: true }).waitFor();
      assert.equal(await detail.locator('div').filter({ hasText: '回复来源' }).getByText('客服', { exact: true }).count(), 1);
      await detail.getByText('客服核实回复', { exact: true }).waitFor();
      await adminContext.close();
      return;
    }
    if (input.kookWarningOnly) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
      await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
      const page = await context.newPage();
      await page.goto(`http://127.0.0.1:${adminServer.address().port}/kook-dispatch`);
      await page.getByText('部分自定义文案包含旧结算说明', { exact: true }).waitFor({ timeout: 20000 });
      const route = page.locator('.route-card:visible').filter({ hasText: '隔离旧文案' });
      await route.getByRole('button', { name: '编辑', exact: true }).click();
      await page.getByText('请核对旧结算说明', { exact: true }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      await context.close();
      return;
    }
    if (input.queueOnly) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
      await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
      const page = await context.newPage();
      await page.goto(`http://127.0.0.1:${adminServer.address().port}/`);
      const entry = page.locator('button.db-work-item').filter({ hasText: '未回复评价' });
      await entry.waitFor({ timeout: 20000 });
      assert.match(await entry.innerText(), /2\s*条/);
      await entry.click();
      await page.getByText('当前不含直接支付测试单评价', { exact: true }).waitFor({ timeout: 20000 });
      const cards = page.locator('.ev-card').filter({ hasText: input.orderNo });
      await cards.first().waitFor({ timeout: 20000 });
      assert.equal(await cards.count(), 2);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      await context.close();
      return;
    }
    for (let i = 0; i < input.providers.length; i++) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await context.addInitScript(({ token, id }) => {
        localStorage.setItem('token', JSON.stringify({ data: token }));
        localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
      }, input.providers[i]);
      const page = await context.newPage();
      await page.goto(`http://127.0.0.1:${providerServer.address().port}/#/pages/orders/index`);
      await page.locator('taro-button-core:visible').filter({ hasText: /^已完成$/ }).first().click({ timeout: 20000 });
      const card = page.locator('[class*="orderCard"]:visible').filter({ hasText: input.orderNo });
      await card.getByText(`独立评价${i}`, { exact: true }).waitFor({ timeout: 20000 });
      await card.getByText(`独立回复${i}`, { exact: true }).waitFor();
      assert.equal(await card.getByText(`独立评价${1 - i}`, { exact: true }).count(), 0);
      if (i === 0) {
        await card.getByText('修改回复', { exact: true }).click();
        const textarea = page.locator('textarea:visible');
        await textarea.waitFor();
        assert.equal(await textarea.inputValue(), '独立回复0');
        await textarea.fill('修改后的回复0');
        await page.getByText('发布回复', { exact: true }).click();
        await card.getByText('修改后的回复0', { exact: true }).waitFor({ timeout: 20000 });
        await page.goto(`http://127.0.0.1:${providerServer.address().port}/#/pages/evaluations/index`);
        const review = page.locator('[class*="card"]:visible').filter({hasText:'独立评价0'});
        await review.getByText('修改后的回复0',{exact:true}).waitFor({timeout:20000});
        await review.getByText('修改回复',{exact:true}).click();
        const reviewDraft=page.locator('textarea:visible');
        assert.equal(await reviewDraft.inputValue(),'修改后的回复0');
        await reviewDraft.fill('评价页更新回复');
        await page.getByText('发布回复',{exact:true}).click();
        await review.getByText('评价页更新回复',{exact:true}).waitFor({timeout:20000});
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      await context.close();
    }
    const adminContext = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await adminContext.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
    const admin = await adminContext.newPage();
    await admin.goto(`http://127.0.0.1:${adminServer.address().port}/evaluations`);
    const cards = admin.locator('.ev-card').filter({ hasText: input.orderNo });
    await cards.first().waitFor({ timeout: 20000 });
    assert.equal(await cards.count(), 2);
    for (let i = 0; i < 2; i++) {
      const card = cards.filter({ hasText: `独立评价${i}` });
      assert.equal(await card.count(), 1);
      assert.ok((await card.innerText()).includes(`${5 - i} 分`));
    }
    assert.equal(await admin.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await adminContext.close();
  } finally { if (browser) await browser.close(); providerServer.closeAllConnections(); providerServer.close(); adminServer.closeAllConnections(); adminServer.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
