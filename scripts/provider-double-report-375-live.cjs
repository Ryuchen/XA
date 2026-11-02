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
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    for (let index = 0; index < input.tokens.length; index++) {
      const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
      await context.addInitScript(({ token, id }) => {
        localStorage.setItem('token', JSON.stringify({ data: token }));
        localStorage.setItem('xa_provider_user', JSON.stringify({ data: { id, role: 'provider', nickname: '隔离陪玩', username: '隔离陪玩' } }));
      }, { token: input.tokens[index], id: input.ids[index] });
      const page = await context.newPage(), errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname) ? route.continue() : route.abort());
      await page.goto(`http://127.0.0.1:${server.address().port}/#/pages/report/index`);
      if (index === 1) {
        await page.getByText('暂无报单', { exact: true }).waitFor({ timeout: 20000 });
        assert.equal(await page.getByText(`订单 ${input.orderNo}`, { exact: true }).count(), 0);
      } else {
        await page.getByText(`订单 ${input.orderNo}`, { exact: true }).waitFor({ timeout: 20000 });
        await page.getByText(input.status, { exact: true }).waitFor();
        if (input.status === '待提交') {
          await page.getByText('2/3 类已就绪', { exact: true }).waitFor();
          await page.getByText('至少上传一张战绩截图', { exact: true }).waitFor();
          if (input.submit) {
            const chooser = page.waitForEvent('filechooser');
            await page.getByRole('button', { name: '添加战绩截图', exact: true }).click();
            await (await chooser).setFiles({ name: 'isolated-result.png', mimeType: 'image/png', buffer: Buffer.from(input.image, 'base64') });
            await page.getByText('3/3 类已就绪', { exact: true }).waitFor({ timeout: 15000 });
            await page.getByRole('button', { name: '提交审核', exact: true }).click();
            await page.getByText('待审核', { exact: true }).waitFor({ timeout: 15000 });
          }
        }
        if (input.status === '已通过') await page.getByText(input.settled ? '收益已记入内部钱包，冻结及可用金额以钱包为准' : '凭证已通过，等待全部审核及售后窗口结束', { exact: true }).waitFor();
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true);
      assert.deepEqual(errors, []);
      await context.close();
    }
    console.log(`Provider report ${input.status} and ownership verified at 375px`);
  } finally { await browser.close(); server.close(); }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; server.close(); });
