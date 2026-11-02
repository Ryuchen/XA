const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), target = new URL(input.base), root = path.resolve(input.root);
let droppedRedeemResponse = false;
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: target.host } }, response => {
        if (input.dropRedeemResponse && !droppedRedeemResponse && req.method === 'POST' && req.url === '/api/club/redeem/') {
          droppedRedeemResponse = true; response.resume(); response.on('end', () => res.destroy()); return;
        }
        res.writeHead(response.statusCode, response.headers); response.pipe(res);
      });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const name = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, name);
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  const type = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream';
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': type }).end(data));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, accountId }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id: accountId, role: 'provider', nickname: '分页陪玩' } }));
    }, input);
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/points/index`);
    await page.locator('taro-button-core:visible').filter({ hasText: /^兑换记录$/ }).first().click({ timeout: 20000 });
    await page.locator('taro-button-core:visible').filter({ hasText: '加载更多记录' }).click({ timeout: 20000 });
    const cards = page.locator('taro-view-core[class*="card"]').filter({ hasText: '陪玩兑换权益' });
    await cards.nth(20).waitFor({ timeout: 20000 });
    await cards.nth(20).locator('taro-button-core').filter({ hasText: '补充交付资料' }).click();
    await page.locator('input[placeholder="游戏UID"]').fill('provider-375');
    await page.locator('taro-button-core:visible').filter({ hasText: /^积分兑换$/ }).click();
    const shopUid = page.locator('input[placeholder="游戏UID（可在兑换后补充）"]');
    assert.equal(await shopUid.inputValue(), '');
    await shopUid.fill('新兑换UID');
    await page.locator('input[placeholder="交付要求（可选）"]').fill('新兑换说明');
    await page.locator('taro-button-core:visible').filter({ hasText: /^兑换记录$/ }).click();
    assert.equal(await page.locator('input[placeholder="游戏UID"]').inputValue(), 'provider-375');
    await page.locator('taro-button-core:visible').filter({ hasText: '保存资料' }).click();
    await page.getByText('游戏UID：provider-375', { exact: true }).waitFor({ timeout: 20000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await cards.nth(19).locator('taro-button-core').filter({ hasText: '补充交付资料' }).click();
    await page.locator('input[placeholder="游戏UID"]').fill('未保存的UID');
    const ongoing = await fetch(`${input.base}/api/admin/club-claims/${input.secondOldest}/process/`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${input.staffToken}` },
      body: JSON.stringify({ status: 'FULFILLING', note: '客服正在处理' }),
    });
    assert.equal((await ongoing.json()).code, 0);
    await page.locator('taro-button-core:visible').filter({ hasText: '保存资料' }).click();
    await page.getByText('权益状态已变化，刚填写的资料尚未保存。可复制后联系客服。', { exact: true }).waitFor({ timeout: 20000 });
    const feedback = page.getByText('权益状态已变化，刚填写的资料尚未保存。可复制后联系客服。', { exact: true });
    assert.equal(await page.getByText('交付资料已保存', { exact:true }).isVisible().catch(()=>false),false);
    assert.match(await feedback.evaluate(node=>getComputedStyle(node).animationName),/claim-feedback-enter/);
    await page.emulateMedia({reducedMotion:'reduce'});
    assert.equal(await feedback.evaluate(node=>getComputedStyle(node).animationName),'none');
    await page.emulateMedia({reducedMotion:'no-preference'});
    await page.waitForTimeout(240);
    await page.screenshot({path:'G:/Github/XA/.tools/stageshow-provider-feedback-375.png'});
    assert.equal(await page.locator('input[placeholder="游戏UID"]').inputValue(), '未保存的UID');
    await page.getByText('交付中 · 客服正在处理', { exact: true }).waitFor();
    const returned = await fetch(`${input.base}/api/admin/club-claims/${input.secondOldest}/process/`, {
      method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${input.staffToken}`},
      body:JSON.stringify({status:'PENDING',note:'请补充游戏UID',expected_status:'FULFILLING',expected_details:{}}),
    });
    assert.equal((await returned.json()).code,0);
    await page.locator('taro-button-core:visible').filter({hasText:'刷新交付状态'}).click();
    assert.equal(await page.locator('input[placeholder="游戏UID"]').inputValue(),'未保存的UID');
    await page.locator('taro-button-core:visible').filter({hasText:'保存资料'}).click();
    await page.getByText('游戏UID：未保存的UID',{exact:true}).waitFor({timeout:20000});
    await page.locator('taro-button-core:visible').filter({ hasText: /^积分兑换$/ }).click();
    assert.equal(await page.locator('input[placeholder="游戏UID（可在兑换后补充）"]').inputValue(), '新兑换UID');
    await page.locator('taro-view-core[class*="card"]').filter({hasText:'陪玩兑换权益'}).locator('taro-button-core:visible').filter({ hasText: /^兑换$/ }).click();
    await page.getByText('确认兑换', { exact: true }).waitFor();
    await page.getByText('确定', { exact: true }).last().click();
    if (input.dropRedeemResponse) {
      await page.getByText('重试原申请', { exact: true }).waitFor({ timeout: 20000 });
      await page.reload();
      await page.getByText('重试原申请', { exact: true }).waitFor({ timeout: 20000 });
      await page.getByText('重试原申请', { exact: true }).click();
    }
    await page.getByText('游戏UID：新兑换UID', { exact: true }).waitFor({ timeout: 20000 });
    const processed = await fetch(`${input.base}/api/admin/club-claims/${input.oldest}/process/`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${input.staffToken}` },
      body: JSON.stringify({ status: 'COMPLETED', note: '陪玩权益已交付' }),
    });
    assert.equal((await processed.json()).code, 0);
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/messages/index`);
    await page.getByText('权益交付进度更新', { exact: true }).first().click({ timeout: 20000 });
    await page.locator('taro-button-core:visible').filter({ hasText: '查看权益详情' }).click({ timeout: 20000 });
    await page.getByText('正在查看通知关联的权益', { exact: true }).waitFor({ timeout: 20000 });
    await page.getByText('已完成 · 陪玩权益已交付', { exact: true }).waitFor({ timeout: 20000 });
    await page.getByText('游戏UID：provider-375', { exact: true }).waitFor({ timeout: 20000 });
    await page.locator('taro-button-core:visible').filter({ hasText: '查看全部记录' }).click();
    await page.locator('taro-button-core:visible').filter({ hasText: '加载更多记录' }).waitFor({ timeout: 20000 });
    const cancelled = await fetch(`${input.base}/api/admin/club-claims/${input.secondOldest}/process/`, {
      method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${input.staffToken}`},
      body:JSON.stringify({status:'CANCELLED',note:'确认无法交付，退回积分'}),
    });
    assert.equal((await cancelled.json()).code,0);
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/messages/index`);
    await page.locator('taro-text-core:visible').filter({hasText:'「陪玩兑换权益」已取消，1 积分已退回。'}).first().click({timeout:20000});
    await page.getByText('「陪玩兑换权益」已取消，1 积分已退回。',{exact:true}).last().waitFor();
    await page.locator('taro-button-core:visible').filter({hasText:'查看权益详情'}).click();
    await page.getByText('已退回 1 积分，可在积分明细查看。',{exact:true}).waitFor({timeout:20000});
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/points/index`);
    await page.locator('input[placeholder="收件人"]').fill('收件陪玩',{timeout:20000});
    await page.locator('input[placeholder="联系电话"]').fill('13800138000');
    await page.locator('input[placeholder="完整收货地址"]').fill('测试收货地址');
    const physical=page.locator('taro-view-core[class*="card"]').filter({hasText:'陪玩实物奖品'});
    await physical.locator('taro-button-core').filter({hasText:/^兑换$/}).click();
    await page.getByText('确定',{exact:true}).last().click();
    await page.getByText('完整收货地址：测试收货地址',{exact:true}).waitFor({timeout:20000});
    await context.close();
  } finally { if (browser) await browser.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
