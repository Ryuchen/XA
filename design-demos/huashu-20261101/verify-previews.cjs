const { chromium } = require('../../.tools/club-source-20261003/node_modules/playwright');
const { pathToFileURL } = require('node:url');
const path = require('node:path');
const assert = require('node:assert/strict');

(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    for (let index = 1; index <= 3; index++) {
      const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 });
      const errors = []; page.on('pageerror', error => errors.push(error.message));
      await page.goto(pathToFileURL(path.join(__dirname, `dir-${index}.html`)).href);
      await page.getByRole('button', { name: '客服安排', exact: true }).click();
      assert.equal(await page.getByRole('button', { name: '客服安排', exact: true }).getAttribute('aria-pressed'), 'true');
      await page.getByRole('button', { name: '选择优惠券 ›' }).click();
      assert.equal(await page.locator('#amountFooter').innerText(), '1800 兴安币');
      await page.getByRole('button', { name: '展开', exact: true }).click();
      assert.equal(await page.getByText('服务信息、安排方式和报名状态将在此处展示。').isVisible(), true);
      await page.getByRole('button', { name: '已支付待接单 3 单 示例 · 查看队列' }).click();
      assert.equal(await page.getByRole('dialog').isVisible(), true);
      await page.getByRole('button', { name: '知道了' }).click();
      assert.deepEqual(errors, []);
      await page.reload(); await page.screenshot({ path: path.join(__dirname, `dir-${index}.png`), fullPage: true });
      await page.setViewportSize({ width: 375, height: 812 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true, `dir-${index}: horizontal overflow`);
      await page.screenshot({ path: path.join(__dirname, `dir-${index}-375.png`), fullPage: true });
      await page.close();
    }
    const overview = await browser.newPage({ viewport: { width: 1560, height: 1100 } });
    await overview.goto(pathToFileURL(path.join(__dirname, 'thumbs.html')).href);
    await overview.screenshot({ path: path.join(__dirname, 'thumbs.png'), fullPage: true });
    console.log('Six direction thumbnails; three interactive desktop and 375px previews verified.');
  } finally { await browser.close(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
