// Run the real admin page against an isolated Django LiveServer.
const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));

async function reservePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
async function main() {
  const port = await reservePort();
  const adminRoot = path.resolve('apps/admin-web');
  const vite = spawn(process.execPath, [path.join(adminRoot, 'node_modules/vite/bin/vite.js'), '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: adminRoot, env: { ...process.env, XA_ADMIN_DEV_API_TARGET: input.api }, stdio: 'ignore', windowsHide: true,
  });
  let browser;
  try {
    const base = `http://127.0.0.1:${port}`;
    for (let i = 0; i < 120; i++) {
      if (vite.exitCode !== null) throw new Error('Vite exited before accepting connections');
      try { if ((await fetch(base)).ok) break; } catch {}
      await new Promise(resolve => setTimeout(resolve, 250));
      if (i === 119) throw new Error('Vite did not start');
    }
    browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
    const context = await browser.newContext({ viewport: { width: 375, height: 812 }, isMobile: true, hasTouch: true });
    await context.addInitScript(token => { localStorage.setItem('xa_admin_token', token); }, input.token);
    const page = await context.newPage();
    const imageResponses = [];
    page.on('response', response => { if (response.url().includes('/api/media/private/')) imageResponses.push(response.status()); });
    page.on('requestfailed', request => { if (request.url().includes('/api/media/private/')) imageResponses.push(new URL(request.url()).origin + ' ' + (request.failure()?.errorText || 'network error')); });
    const allowedOrigins = new Set([base, input.api]);
    await page.route('**/*', route => allowedOrigins.has(new URL(route.request().url()).origin) ? route.continue() : route.abort());
    await page.goto(`${base}/reports`, { waitUntil: 'domcontentloaded' });
    const card = page.locator('.rp-mobile-card').filter({ hasText: `报单 #${input.reportId}` });
    await card.waitFor({ timeout: 30000 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, 'list horizontal overflow');
    await card.getByRole('button', { name: '查看详情' }).click();
    const drawer = page.getByRole('dialog', { name: '报单详情' });
    await drawer.getByText(`报单编号`).waitFor({ timeout: 15000 });
    await drawer.getByText('战绩截图（1 张）').waitFor();
    await drawer.locator('img').evaluateAll(images => Promise.all(images.map(image => image.decode())));
    const broken = await drawer.getByText('图片加载失败').count();
    assert.equal(broken, 0, `report evidence did not load: ${JSON.stringify(imageResponses)}`);
    await drawer.evaluate(el => Promise.all(el.getAnimations().map(animation => animation.finished)));
    const box = await drawer.boundingBox();
    assert.ok(box && box.x >= -1 && box.x + box.width <= 376, `drawer outside viewport ${JSON.stringify(box)}`);
    assert.equal(await drawer.evaluate(el => el.scrollWidth <= el.clientWidth + 1), true, 'drawer horizontal overflow');
    const folder = path.resolve('.tools/admin-report-375-20261031'); fs.mkdirSync(folder, { recursive: true });
    await page.screenshot({ path: path.join(folder, 'detail.png') });
    const evidence = drawer.getByRole('button', { name: '查看入队截图' });
    await evidence.scrollIntoViewIfNeeded();
    await evidence.click();
    const viewer = page.getByRole('dialog', { name: '查看入队截图' });
    await viewer.waitFor();
    await viewer.getByRole('img').evaluate(image => image.decode());
    await viewer.getByRole('button', { name: '关闭', exact: true }).click();
    const reject = drawer.getByRole('button', { name: '驳回报单' });
    await reject.scrollIntoViewIfNeeded();
    await reject.click();
    const dialog = page.getByRole('dialog', { name: '驳回报单' });
    await dialog.waitFor();
    await dialog.evaluate(el => Promise.all(el.getAnimations().map(animation => animation.finished)));
    const dialogBox = await dialog.boundingBox();
    assert.ok(dialogBox && dialogBox.x >= -1 && dialogBox.x + dialogBox.width <= 376, `dialog outside viewport ${JSON.stringify(dialogBox)}`);
    await dialog.getByRole('button', { name: '确认驳回' }).click();
    await dialog.getByText('驳回原因须为 1–255 字').waitFor();
    await dialog.getByLabel('驳回原因').fill('请补充清晰战绩截图');
    await page.screenshot({ path: path.join(folder, 'reject.png') });
    await dialog.getByRole('button', { name: '确认驳回' }).click();
    await page.getByText('报单已驳回', { exact: true }).waitFor({ timeout: 15000 });
    console.log('375px admin review drawer and rejection submitted against real API');
  } finally {
    if (browser) await browser.close();
    vite.kill();
  }
}
main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
