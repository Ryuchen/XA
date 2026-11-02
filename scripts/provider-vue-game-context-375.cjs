const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(input.root), target = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) { const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); }); proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return; }
  const file = path.resolve(root, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' }[path.extname(file)] || 'application/octet-stream' }).end(data));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => { localStorage.setItem('token', JSON.stringify({ data: token })); localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } })); }, input);
    const page = await context.newPage(); await page.goto(`http://127.0.0.1:${server.address().port}/#/orders?order=${input.orderId}`);
    const card = page.locator('.order-card').filter({ hasText: input.orderNo });
    for (const text of ['游戏区服：隔离大区', '游戏昵称：隔离游戏昵称', '游戏 UID：ISOLATED-UID-123']) await card.getByText(text, { exact: true }).waitFor({ timeout: 15000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await context.close();
    const candidate = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await candidate.addInitScript(({ poolToken, poolId }) => { localStorage.setItem('token', JSON.stringify({ data: poolToken })); localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id: poolId, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } })); }, input);
    const poolPage = await candidate.newPage(); await poolPage.goto(`http://127.0.0.1:${server.address().port}/#/orders`);
    const poolCard = poolPage.locator('.order-card').filter({ hasText: input.poolOrderNo });
    await poolCard.waitFor({ timeout: 15000 });
    await poolCard.getByText('游戏区服：隔离大区', { exact: true }).waitFor({ timeout: 5000 });
    assert.doesNotMatch(await poolCard.innerText(), /池中隔离昵称|POOL-ISOLATED-UID/);
    await poolCard.getByRole('button', { name: '抢单', exact: true }).click();
    await poolPage.getByRole('dialog').getByRole('button', { name: '确认', exact: true }).click();
    await poolPage.getByText('接单成功', { exact: true }).waitFor({ timeout: 15000 });
    await poolPage.getByRole('button', { name: '已接单', exact: true }).click();
    const accepted = poolPage.locator('.order-card').filter({ hasText: input.poolOrderNo });
    await accepted.waitFor({ timeout: 15000 });
    for (const text of ['游戏区服：隔离大区', '游戏昵称：池中隔离昵称', '游戏 UID：POOL-ISOLATED-UID']) await accepted.getByText(text, { exact: true }).waitFor({ timeout: 15000 });
    await candidate.close();
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
