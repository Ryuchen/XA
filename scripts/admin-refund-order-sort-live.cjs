// Exercise the real order list with two independently refunded orders.
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
  try {
    const context = await browser.newContext({ viewport:{ width:375, height:812 } });
    await context.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.token);
    const page = await context.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/orders?refund_day=${input.today}&exclude_direct_test=1`);
    const cards = page.locator('.od-mobile-card');
    await cards.nth(1).waitFor({ timeout:20000 });
    assert.equal(await cards.count(), 2);
    await cards.nth(0).getByText(input.newest, { exact:true }).waitFor();
    await cards.nth(0).getByText('50 币 · 1 次', { exact:true }).waitFor();
    await cards.nth(1).getByText(input.older, { exact:true }).waitFor();
    await cards.nth(1).getByText('300 币 · 2 次', { exact:true }).waitFor();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await context.close();
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
