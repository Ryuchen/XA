const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), target = new URL(input.base), root = path.resolve(input.root);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const pathname = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '');
  const file = path.resolve(root, pathname.startsWith('assets/') ? pathname : 'index.html');
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  const type = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream';
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': type }).end(data));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/dashboard`);
    await page.waitForURL('**/club-operations', { timeout: 20000 });
    if (input.pointsOnly) {
      await page.getByRole('tab', { name: 'KOOK 积分核对' }).waitFor({ timeout: 20000 });
      assert.equal(await page.getByRole('tab', { name: '奖品交付' }).count(), 0);
      assert.equal(await page.getByRole('tab', { name: '积分好礼' }).count(), 0);
      assert.equal(await page.getByText('数据加载失败，请点击刷新重试').count(), 0);
      await context.close();
      return;
    }
    await page.getByRole('tab', { name: '奖品交付' }).click({ timeout: 20000 });
    assert.equal(await page.getByRole('tab', { name: '积分好礼' }).count(), 0);
    assert.equal(await page.getByRole('button', { name: /会员权益/ }).count(), 0);
    const search = page.getByRole('textbox', { name: '搜索权益交付记录' });
    await search.fill('赠礼专属'); await search.press('Enter');
    const card = page.locator('.claim-mobile-card').filter({ hasText: `记录 #${input.claimId}` });
    await card.getByText('赠礼权益', { exact: false }).waitFor({ timeout: 20000 });
    const updated = await fetch(`${input.base}/api/club/claims/${input.claimId}/`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${input.bossToken}` },
      body: JSON.stringify({ details: { game_uid: 'new-uid-375', note: '最新交付要求' } }),
    });
    assert.equal((await updated.json()).code, 0);
    await card.getByRole('button', { name: '处理交付' }).click();
    const dialog = page.locator('.el-dialog:visible');
    await dialog.getByText('赠礼权益', { exact: false }).first().waitFor();
    await dialog.getByText('new-uid-375', { exact: true }).waitFor();
    await dialog.getByText('最新交付要求', { exact: true }).waitFor();
    await dialog.locator('.el-select').click();
    await page.getByRole('option', { name: '已完成交付' }).click();
    if (input.raceOnly) {
      await dialog.locator('textarea').fill('第二位客服的说明');
      const first = await fetch(`${input.base}/api/admin/club-claims/${input.claimId}/process/`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${input.otherStaffToken}` },
        body: JSON.stringify({ status: 'COMPLETED', note: '第一位客服已交付', expected_status: 'PENDING',
          expected_details: { game_uid: 'new-uid-375', note: '最新交付要求' } }),
      });
      assert.equal((await first.json()).code, 0);
      await dialog.getByRole('button', { name: '保存', exact: true }).click();
      await dialog.getByText('交付状态已变化，请核对最新处理结果。', { exact: true }).waitFor({ timeout: 20000 });
      await dialog.getByText('第一位客服已交付', { exact: true }).waitFor();
      assert.equal(await dialog.getByRole('button', { name: '保存', exact: true }).isDisabled(), true);
      const response = await fetch(`${input.base}/api/admin/club-claims/${input.claimId}/`, { headers: { Authorization: `Bearer ${input.token}` } });
      assert.equal((await response.json()).data.delivery_note, '第一位客服已交付');
      await context.close();
      return;
    }
    await dialog.locator('textarea').fill('权益已交付');
    const changedAgain = await fetch(`${input.base}/api/club/claims/${input.claimId}/`, {
      method: 'PATCH', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${input.bossToken}` },
      body: JSON.stringify({ details: { game_uid: 'updated-uid-375', note: '变更后的交付要求' } }),
    });
    assert.equal((await changedAgain.json()).code, 0);
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await dialog.getByText('交付资料已更新，请核对最新内容后重新提交。', { exact: true }).waitFor({ timeout: 20000 });
    await dialog.getByText('updated-uid-375', { exact: true }).waitFor();
    assert.equal(await dialog.locator('textarea').inputValue(), '权益已交付');
    const stillPending = await fetch(`${input.base}/api/admin/club-claims/${input.claimId}/`, { headers: { Authorization: `Bearer ${input.token}` } });
    assert.equal((await stillPending.json()).data.status, 'PENDING');
    const inbox = await fetch(`${input.base}/api/messages/`, { headers: { Authorization: `Bearer ${input.bossToken}` } });
    assert.equal((await inbox.json()).data.messages.filter(item => item.title === '权益交付进度更新').length, 0);
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await card.getByText('已完成', { exact: true }).waitFor({ timeout: 20000 });
    await card.getByRole('button', { name: '查看详情' }).click();
    await dialog.getByText('权益已交付', { exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await context.close();
  } finally { if (browser) await browser.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
