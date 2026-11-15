const assert = require('node:assert/strict'), fs = require('node:fs');
const automator = require('../.tools/wechat-automation/node_modules/miniprogram-automator');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:19435' });
  const originalPage = await mini.currentPage();
  const original = await mini.evaluate(() => ({ api: require('config.js').develop.apiBase,
    session: require('core/auth.js').getSession() }));
  let failure;
  try {
    const device = await mini.systemInfo();
    assert.equal(device.model, 'iPhone 12/13 (Pro)'); assert.equal(device.windowWidth, 390);
    for (const item of input.cases) {
      await mini.evaluate(value => {
        require('config.js').develop.apiBase = value.base + '/api';
        require('core/auth.js').setSession({ token: value.token, user: { id: value.bossId, role: 'customer' } });
      }, { base: input.base, token: item.token, bossId: item.bossId });
      const page = await mini.reLaunch('/pages/order-detail/index?id=' + item.orderId);
      let timer;
      await Promise.race([page.waitFor(async () => (await page.data()).order?.refundRecords?.[0]?.replenishmentText === item.expected),
        new Promise((_, reject) => { timer = setTimeout(() => reject(Error('订单券状态未加载')), 15000); })]).finally(() => clearTimeout(timer));
      const panel = await page.$('.refund-panel'); assert.ok(panel);
      const text = await panel.text();
      assert.ok(text.includes('已核销，不返还')); assert.ok(text.includes(item.expected));
      assert.ok((await page.size()).width <= device.windowWidth);
      assert.ok((await panel.size()).width <= device.windowWidth);
      if (item === input.cases[1]) {
        const offset = await panel.offset();
        await mini.pageScrollTo(Math.max(0, offset.top - 100));
        await mini.screenshot({ path: input.screenshot });
      }
    }
    console.log('WeChat IDE iPhone 12 Pro: actual pending and settled coupon receipts rendered');
  } catch (error) { failure = error; }
  finally {
    try {
      await mini.evaluate(value => {
        require('config.js').develop.apiBase = value.api;
        const auth = require('core/auth.js');
        if (value.session) auth.setSession(value.session); else auth.clearSession();
      }, original);
      if (originalPage && originalPage.path) {
        const params = new URLSearchParams(originalPage.query || {}).toString();
        await mini.reLaunch('/' + originalPage.path + (params ? '?' + params : ''));
      }
    } catch (error) {
      console.error('开发者工具连接已关闭，无法通过自动化恢复原页面和登录状态。');
      if (!failure) failure = error;
    }
    try { await mini.disconnect(); } catch (_) { /* Tool may already have exited. */ }
  }
  if (failure) throw failure;
})().then(() => process.exit(0)).catch(error => { console.error(error.stack || error); process.exit(1); });
