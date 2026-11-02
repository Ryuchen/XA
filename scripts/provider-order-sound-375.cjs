// Real H5, actual order pool and isolated backend; count audio oscillator starts.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(process.env.XA_PROVIDER_SOUND_BUILD || '.tools/provider-sound-h5-20261101'), target = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => { if (error) { res.writeHead(404).end(); return; } res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' }).end(data); });
});
async function createOrder(id) {
  const response = await fetch(input.base + '/api/orders/create/', { method: 'POST',
    headers: { Authorization: `Bearer ${input.bossToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ service_id: input.serviceId, dispatch_route: 'PUBLIC', dispatch_mode: 'AUTO_GRAB', client_request_id: `sound-real-${id}` }) });
  const data = await response.json(); assert.equal(data.code, 0, JSON.stringify(data)); return data.data;
}
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
      window.__soundCount = 0;
      const original = AudioContext.prototype.createOscillator;
      AudioContext.prototype.createOscillator = function() { window.__soundCount++; return original.call(this); };
    }, { token: input.providerToken, id: input.providerId });
    const page = await context.newPage(), errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/orders/index`);
    const toggle = page.getByText('订单提示音 开', { exact: true });
    await toggle.waitFor({ timeout: 20000 });
    await page.getByText('暂时没有可接订单').waitFor({ timeout: 15000 }).catch(async error => {
      throw new Error(`${error.message}\nPage: ${await page.locator('body').innerText()}`);
    });
    await toggle.click();
    await page.getByText('订单提示音 关', { exact: true }).waitFor();
    const first = await createOrder('mute');
    await page.getByText(first.order_no, { exact: true }).waitFor({ timeout: 20000 });
    assert.equal(await page.evaluate(() => window.__soundCount), 0);
    await page.reload();
    await page.getByText('订单提示音 关', { exact: true }).waitFor({ timeout: 20000 });
    await page.getByText(first.order_no, { exact: true }).waitFor();
    await page.getByText('订单提示音 关', { exact: true }).click();
    const second = await createOrder('on');
    await page.getByText(second.order_no, { exact: true }).waitFor({ timeout: 20000 });
    await page.waitForFunction(() => window.__soundCount === 3, null, { timeout: 5000 });
    for (const action of ['grab', 'reject']) {
      const response = await fetch(`${input.base}/api/orders/orders/${second.id}/${action}/`, {
        method: 'POST', headers: { Authorization: `Bearer ${input.otherProviderToken}`, 'Content-Type': 'application/json' }, body: '{}',
      });
      const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body));
    }
    await page.waitForFunction(() => window.__soundCount === 6, null, { timeout: 20000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Real orders refresh while muted; new and returned orders ring at 375px');
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
