const fs = require('node:fs'), http = require('node:http'), net = require('node:net'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(input.root), target = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) { const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); }); proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return; }
  const file = path.resolve(root, decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html');
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' }[path.extname(file)] || 'application/octet-stream' }).end(data));
});
const sockets = new Set();
server.on('upgrade', (req, client, head) => {
  const upstream = new URL(input.wsBase || input.base); sockets.add(client); client.on('close', () => sockets.delete(client));
  const peer = net.connect(Number(upstream.port), upstream.hostname, () => {
    const headers = req.rawHeaders.reduce((s, item, i, all) => i % 2 ? s : s + `${item}: ${all[i + 1]}\r\n`, '');
    peer.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`); if (head.length) peer.write(head); client.pipe(peer).pipe(client);
  });
  peer.on('error', () => client.destroy()); client.on('error', () => peer.destroy()); client.on('close', () => peer.destroy());
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    if (input.realtime) {
      const viewers = [];
      for (const provider of input.providers) {
        const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
        await context.addInitScript(({ token, id }) => { localStorage.setItem('token', JSON.stringify({ data: token })); localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } })); }, provider);
        const page = await context.newPage(), frames = [];
        page.on('websocket', ws => ws.on('framereceived', frame => { try { frames.push(JSON.parse(frame.payload)); } catch {} }));
        await page.goto(`http://127.0.0.1:${server.address().port}/#/orders?order=${input.orderId}`);
        viewers.push({ context, page, frames });
      }
      await viewers[0].page.locator('.order-card').getByText('服务中', { exact: true }).waitFor({ timeout: 15000 });
      for (const { frames } of viewers) { for (let i = 0; i < 100 && !frames.some(frame => frame.type === 'pong'); i++) await new Promise(resolve => setTimeout(resolve, 50)); assert.ok(frames.some(frame => frame.type === 'pong'), 'real socket heartbeat'); }
      const response = await fetch(`${input.base}/api/admin/orders/${input.orderId}/transfer/`, { method: 'POST', headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ transfers: [{ old_provider_id: input.providers[0].id, new_provider_id: input.providers[1].id, split_type: 'PERCENT', split_value: 40, penalty_amount: 0 }], reason: '隔离实时转单' }) });
      const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
      for (const { frames } of viewers) { for (let i = 0; i < 100 && !frames.some(frame => frame.type === 'order_status_update' && frame.data?.id === input.orderId); i++) await new Promise(resolve => setTimeout(resolve, 50)); assert.ok(frames.some(frame => frame.type === 'order_status_update' && frame.data?.id === input.orderId), 'real transfer event'); }
      const oldCard = viewers[0].page.locator('.order-card'), newCard = viewers[1].page.locator('.order-card');
      await oldCard.getByText('本人已转出', { exact: true }).waitFor({ timeout: 15000 });
      await newCard.getByText('服务中', { exact: true }).waitFor({ timeout: 15000 });
      assert.equal(await oldCard.getByRole('button', { name: '完成订单', exact: true }).count(), 0);
      assert.equal(await newCard.getByText('本人已转出', { exact: true }).count(), 0);
      const teammate = viewers[2].page.locator('.order-card');
      await teammate.getByText('服务中', { exact: true }).waitFor({ timeout: 15000 });
      assert.equal(await teammate.getByText('本人已转出', { exact: true }).count(), 0);
      for (const [index, { page }] of viewers.entries()) {
        const row = await fetch(`${input.base}/api/orders/orders/${input.orderId}/`, { headers: { Authorization: `Bearer ${input.providers[index].token}` } }).then(r => r.json());
        assert.equal(row.code, 0);
        const canComplete = row.data.allowed_actions.includes('COMPLETE');
        assert.equal((await page.locator('.order-card').getByRole('button', { name: '完成订单', exact: true }).count()) > 0, canComplete);
      }
      for (const { context } of viewers) await context.close();
      return;
    }
    for (const [index, provider] of input.providers.entries()) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await context.addInitScript(({ token, id }) => { localStorage.setItem('token', JSON.stringify({ data: token })); localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } })); }, provider);
      const page = await context.newPage(); await page.goto(`http://127.0.0.1:${server.address().port}/#/orders?order=${input.orderId}`);
      const card = page.locator('.order-card').filter({ hasText: input.orderNo });
      await card.getByText('服务中', { exact: true }).waitFor({ timeout: 15000 });
      if (index === 0) {
        await card.getByText('本人已转出', { exact: true }).waitFor();
        await card.getByText(input.settled ? '本人转单份额已结算，实际到账请查看钱包流水。' : '本人转单份额尚未结算，请查看钱包流水。', { exact: true }).waitFor();
        assert.equal(await card.getByRole('button', { name: '完成订单', exact: true }).count(), 0);
      } else assert.equal(await card.getByText('本人已转出', { exact: true }).count(), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      await context.close();
    }
  } finally { await browser.close(); for (const client of sockets) client.destroy(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
