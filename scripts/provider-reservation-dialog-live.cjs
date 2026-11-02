// Real H5 reservation dialog and ASGI websocket against isolated data.
const fs = require('node:fs'), http = require('node:http'), net = require('node:net'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve('.tools/provider-reservation-dialog-20261101'), target = new URL(input.api);
const types = { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' };
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream' }).end(data));
});
server.on('upgrade', (req, socket, head) => {
  const upstream = net.connect(Number(target.port), target.hostname, () => {
    const headers = req.rawHeaders.reduce((result, value, index, all) => index % 2 ? result : result + `${value}: ${all[index + 1]}\r\n`, '');
    upstream.write(`${req.method} ${req.url} HTTP/${req.httpVersion}\r\n${headers}\r\n`);
    if (head.length) upstream.write(head);
    socket.pipe(upstream).pipe(socket);
  });
  upstream.on('error', () => socket.destroy()); socket.on('error', () => upstream.destroy());
});
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
    }, { token: input.providerToken, id: input.providerId });
    const page = await context.newPage();
    const connected = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('provider websocket unavailable')), 15000);
      page.on('websocket', ws => ws.on('framereceived', frame => { if (String(frame.payload).includes('pong')) { clearTimeout(timer); resolve(); } }));
    });
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/reservations/index?id=${input.reservationId}`);
    await page.getByText('拒绝预约', { exact: true }).click({ timeout: 20000 });
    await page.getByRole('textbox', { name: '原因（选填）' }).fill('正在输入的原因');
    await connected;
    const refreshed = page.waitForResponse(response => response.request().method() === 'GET'
      && new URL(response.url()).pathname === `/api/orders/reservations/${input.reservationId}/`, { timeout: 10000 });
    const otherResponse = await fetch(`${input.api}/api/orders/reservations/${input.otherId}/cancel/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.bossToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: '另一预约计划有变' }),
    });
    const other = await otherResponse.json(); assert.equal(other.code, 0, JSON.stringify(other));
    await refreshed;
    assert.equal(await page.getByRole('textbox', { name: '原因（选填）' }).inputValue(), '正在输入的原因');
    const response = await fetch(`${input.api}/api/orders/reservations/${input.reservationId}/cancel/`, {
      method: 'POST', headers: { Authorization: `Bearer ${input.bossToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason: '老板计划有变' }),
    });
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result));
    await page.getByRole('textbox', { name: '原因（选填）' }).waitFor({ state: 'hidden', timeout: 10000 });
    await page.getByText('已取消', { exact: true }).waitFor({ timeout: 10000 });
    await page.getByText('老板计划有变', { exact: true }).waitFor();
    assert.equal(await page.getByText('确认预约', { exact: true }).count(), 0);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.closeAllConnections(); server.close(); });
