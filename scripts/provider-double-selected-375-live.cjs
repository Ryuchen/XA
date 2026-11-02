// Real provider workbench rendered against isolated authenticated order APIs.
const fs = require('node:fs'), http = require('node:http'), path = require('node:path'), assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve(input.root), target = new URL(input.base);
const server = http.createServer((req, res) => {
  if (req.url.startsWith('/api/')) {
    const proxy = http.request({ hostname: target.hostname, port: target.port, path: req.url, method: req.method,
      headers: { ...req.headers, host: target.host } }, response => { res.writeHead(response.statusCode, response.headers); response.pipe(res); });
    proxy.on('error', () => res.writeHead(502).end()); req.pipe(proxy); return;
  }
  const relative = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/+/, '') || 'index.html';
  const file = path.resolve(root, relative);
  if (!file.startsWith(root + path.sep)) { res.writeHead(404).end(); return; }
  fs.readFile(file, (error, data) => error ? res.writeHead(404).end() : res.writeHead(200, { 'Content-Type':
    { '.html': 'text/html', '.css': 'text/css', '.js': 'text/javascript', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream' }).end(data));
});
async function main() {
  if (input.stage) {
    const nativeRoot = path.resolve('apps/boss-miniapp/native'), storage = new Map();
    global.wx = {
      getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
      removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }),
      getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
      request(options) {
        const url = new URL(options.url);
        fetch(input.base + url.pathname + url.search, { method: options.method, headers: options.header })
          .then(async response => options.success({ statusCode: response.status, data: await response.json() })).catch(options.fail);
      },
    };
    require(path.join(nativeRoot, 'core/auth')).setSession({ token: input.bossToken, user: { id: input.bossId, role: 'customer' } });
    let definition; global.Page = value => { definition = value; };
    require(path.join(nativeRoot, 'pages/order-detail/index.js'));
    const detail = { ...definition, _visible: true, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
    detail.onLoad({ id: input.orderId }); await detail.load();
    assert.equal(detail.data.error, '');
    assert.deepEqual([detail.data.order.team_progress.stage, detail.data.order.team_progress.submitted_count,
      detail.data.order.team_progress.total_count], [input.stage, 1, 2]);
    detail.onUnload();
  }
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    for (let index = 0; index < 3; index++) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await context.addInitScript(({ token, id }) => {
        localStorage.setItem('token', JSON.stringify({ data: token }));
        localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
      }, { token: input.tokens[index], id: input.ids[index] });
      const page = await context.newPage();
      await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
      await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/orders/index`);
      const tab = input.stage === 'COMPLETION' ? '服务中' : '已接单';
      await page.locator('taro-button-core:visible').filter({ hasText: new RegExp('^' + tab + '$') }).first().click({ timeout: 20000 });
      if (index === 1) {
        await page.getByText('暂无此类订单', { exact: true }).waitFor({ timeout: 15000 });
        assert.equal(await page.getByText(input.orderNo, { exact: true }).count(), 0);
      } else {
        await page.getByText(input.orderNo, { exact: true }).waitFor({ timeout: 15000 });
        if (input.stage) {
          await page.getByText(`${input.stage === 'ENTRY' ? '入队截图' : '结单截图'} · 1/2 已提交`, { exact: true }).waitFor({ timeout: 15000 });
          await page.getByText('陪玩甲 · 已提交', { exact: true }).waitFor();
          await page.getByText('陪玩替补 · 等待提交', { exact: true }).waitFor();
        }
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await context.close();
    }
    console.log('Assigned providers see the order; the replaced provider does not at 375px');
  } finally { await browser.close(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
