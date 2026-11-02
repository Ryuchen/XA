// Real H5 UI, reservation API and isolated wallet; no business responses mocked.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(process.env.XA_PROVIDER_RESERVATION_H5_ROOT || '.tools/provider-reservation-h5-20261101'), target = new URL(input.base);
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
const adminRoot = path.resolve(process.env.XA_PROVIDER_RESERVATION_ADMIN_ROOT || '.tools/admin-reservation-review-build-20261101');
const adminServer = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method, headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const pathname = new URL(req.url, 'http://localhost').pathname;
  const file = path.resolve(adminRoot, pathname.startsWith('/assets/') ? pathname.slice(1) : 'index.html');
  if (!file.startsWith(adminRoot + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => { if (error) { res.writeHead(404).end(); return; } res.writeHead(200, { 'Content-Type': { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' }).end(data); });
});
function nativeDetail() {
  const storage = new Map(); let navigation = '', definition;
  global.wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    showToast() {}, showModal: options => options.success({ confirm: true }),
    navigateTo: options => { navigation = options.url; }, stopPullDownRefresh() {},
    request(options) { const url = new URL(options.url); fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header,
      ...(options.data === undefined ? {} : { body: JSON.stringify(options.data) }) }).then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail); },
  };
  const nativeRoot = path.resolve('apps/boss-miniapp/native');
  require(path.join(nativeRoot, 'core/auth')).setSession({ token: input.bossToken, user: { id: input.bossId, role: 'customer' } });
  global.Page = value => { definition = value; };
  require(path.join(nativeRoot, 'pages/reservation-detail/index.js'));
  const instance = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  for (const [key, value] of Object.entries(instance)) if (typeof value === 'function') instance[key] = value.bind(instance);
  instance.onLoad({ id: input.reservationId }); instance._visible = true;
  return { instance, navigation: () => navigation };
}
async function main() {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  await new Promise(resolve => adminServer.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(({ token, id }) => {
      localStorage.setItem('token', JSON.stringify({ data: token }));
      localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩', avatar: '' } }));
    }, { token: input.providerToken, id: input.providerId });
    const page = await context.newPage(), errors = []; page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/reservations/index?id=${input.reservationId}`);
    await page.getByText('待确认', { exact: true }).waitFor({ timeout: 20000 });
    if (input.bossName) await page.getByText(`${input.bossName} · 数量 1`, { exact: true }).waitFor();
    await page.getByText('确认预约', { exact: true }).click();
    await page.getByText('确认预约', { exact: true }).last().click();
    await page.getByText('已确认', { exact: true }).waitFor({ timeout: 15000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    const adminContext = await browser.newContext({ viewport: { width: 375, height: 812 } });
    await adminContext.addInitScript(token => localStorage.setItem('xa_admin_token', token), input.staffToken);
    const admin = await adminContext.newPage();
    await admin.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
    const adminUrl = `http://127.0.0.1:${adminServer.address().port}/reservations?find_reservation=${input.reservationId}`;
    await admin.goto(adminUrl, { waitUntil: 'domcontentloaded' });
    await admin.locator('.el-drawer').getByText('已确认', { exact: true }).waitFor({ timeout: 20000 });
    if (input.bossName) await admin.locator('.el-drawer').getByText(input.bossName, { exact: true }).waitFor();
    const boss = nativeDetail(); await boss.instance.load();
    assert.equal(boss.instance.data.reservation.status, 'CONFIRMED');
    await boss.instance.quote(); assert.equal(boss.instance.data.quote.amount, 10000);
    await boss.instance.convert();
    assert.equal(boss.instance.data.reservation.status, 'CONVERTED');
    assert.match(boss.navigation(), /^\/pages\/order-detail\/index\?id=\d+$/);
    const orderNo = boss.instance.data.reservation.order_no; boss.instance.onUnload();
    await admin.goto(adminUrl, { waitUntil: 'domcontentloaded' });
    await admin.locator('.el-drawer').getByText('已转单', { exact: true }).waitFor({ timeout: 20000 });
    await admin.locator('.el-drawer').getByText('查看订单', { exact: true }).waitFor();
    await page.reload();
    await page.getByText('已转订单', { exact: true }).waitFor({ timeout: 20000 });
    await page.getByText(`订单 ${orderNo}`, { exact: true }).waitFor();
    await page.getByText('查看订单', { exact: true }).click();
    await page.getByText(orderNo, { exact: true }).waitFor({ timeout: 20000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
    assert.deepEqual(errors, []);
    console.log('Provider H5 -> admin -> boss native controller -> admin and provider order passed');
  } finally { await browser.close(); server.closeAllConnections(); server.close(); adminServer.closeAllConnections(); adminServer.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); adminServer.close(); });
