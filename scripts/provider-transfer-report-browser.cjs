// Real transferred orders and reports through the built provider H5.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(input.root), upstream = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: upstream.hostname, port: upstream.port, path: req.url, method: req.method,
      headers: {...req.headers, host: upstream.host} }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const name = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, name); if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, {'Content-Type':
    {'.html':'text/html','.js':'text/javascript','.css':'text/css'}[path.extname(file)] || 'application/octet-stream'}).end(data));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true});
  try {
    for (const [token, id, old, orders] of [[input.oldToken,input.oldId,true,input.orders], ...input.orders.map(order => [order.newToken,order.newId,false,[order]])]) {
      const context = await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
      await context.addInitScript(({token,id}) => { localStorage.setItem('token', JSON.stringify({data:token}));
        localStorage.setItem('xa_provider_user', JSON.stringify({data:{id:String(id),role:'provider',nickname:'隔离陪玩'}})); }, {token,id});
      const page = await context.newPage(), errors = []; page.on('pageerror', e => errors.push(e.message));
      for (const order of orders) {
        await page.goto(`http://127.0.0.1:${server.address().port}/#/orders?order=${order.id}`);
        const card = page.locator('.order-card').filter({hasText:order.no}); await card.waitFor({timeout:15000});
        if (old) await card.getByText('本人已转出').waitFor();
        await card.getByRole('button',{name:'查看报单'}).click();
        const report = page.locator('.report').filter({hasText:order.no}); await report.waitFor({timeout:15000});
        if (old && !order.retained) {
          await report.getByText('本人已退出服务且无结算份额，无需提交报单').waitFor();
          assert.equal(await report.getByText('添加战绩截图').count(), 0);
        } else {
          await report.getByText('添加战绩截图').waitFor();
        }
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),true);
      }
      assert.deepEqual(errors, []); await context.close();
    }
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.closeAllConnections(); server.close(); });
