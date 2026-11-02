// Official DevTools automation connected to a separate project and isolated HTTP server.
const assert = require('node:assert/strict');
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
const automator = require('../.tools/wechat-automation/node_modules/miniprogram-automator');
async function waitFor(page, predicate, label) {
  let timer;
  try { await Promise.race([page.waitFor(predicate), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label)), 15000); })]); }
  finally { clearTimeout(timer); }
}
(async () => {
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:19435' });
  try {
    await mini.evaluate(value => {
      require('config.js').develop.apiBase = value.base + '/api';
      require('core/auth.js').setSession(value.session);
    }, { base: input.base, session: { token: input.token, user: { id: input.bossId, role: 'customer' } } });
    let page = await mini.reLaunch('/pages/order-detail/index?id=' + input.orderId);
    await waitFor(page, async () => (await page.data()).order?.evaluations?.some(row => row.reply_author === 'STAFF'), '订单评价未加载');
    const labels = await page.$$('.evaluation-entry .note-label');
    assert.ok((await Promise.all(labels.map(item => item.text()))).includes('客服回复'));
    page = await mini.reLaunch('/pages/service/index?id=' + input.serviceId);
    await waitFor(page, async () => (await page.data()).reviews?.some(row => row.reply_author === 'STAFF'), '服务评价未加载');
    const replies = await page.$$('.review .reply');
    assert.ok((await Promise.all(replies.map(item => item.text()))).some(text => text.includes('客服回复：客服核实回复')));
    console.log('Native DevTools rendered staff attribution on order and service pages.');
  } finally { await mini.disconnect(); }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
