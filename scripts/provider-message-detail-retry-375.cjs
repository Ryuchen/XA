// Current provider H5 against isolated HTTP; interrupt only the first detail request.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(input.root), target = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) return res.writeHead(404).end();
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript' }[path.extname(file)] || 'application/octet-stream' }).end(data));
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
    }, input);
    const page = await context.newPage();
    let interrupted = false;
    await page.route(`**/api/messages/${input.messageId}/`, route => {
      if (!interrupted) { interrupted = true; return route.abort('failed'); }
      return route.continue();
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/#/messages`);
    await page.getByRole('button', { name: /隔离通知验收/ }).click({ timeout: 15000 });
    await page.getByText('消息暂时无法加载，请重试', { exact: true }).waitFor({ timeout: 15000 });
    const before = await fetch(input.base + '/api/messages/', { headers: { Authorization: `Bearer ${input.token}` } }).then(r => r.json());
    assert.equal(before.data.messages.find(row => row.id === input.messageId).is_read, false);
    await page.getByText('重新加载', { exact: true }).click();
    await page.getByRole('dialog').getByText('隔离通知验收', { exact: true }).waitFor({ timeout: 15000 });
    assert.equal(interrupted, true);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    await context.close();
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
