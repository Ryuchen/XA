const assert = require('node:assert/strict'), fs = require('node:fs'), http = require('node:http');
const automator = require('../.tools/wechat-automation/node_modules/miniprogram-automator');
const input = JSON.parse(fs.readFileSync(0, 'utf8')), target = new URL(input.base);
let dropped = false;
const proxy = http.createServer((req, res) => {
  const upstream = http.request({ hostname: target.hostname, port: target.port, path: req.url,
    method: req.method, headers: { ...req.headers, host: target.host } }, response => {
    if (!dropped && req.method === 'POST' && req.url === '/api/club/redeem/') {
      dropped = true; response.resume(); response.on('end', () => res.writeHead(502).end()); return;
    }
    res.writeHead(response.statusCode, response.headers); response.pipe(res);
  });
  upstream.on('error', () => res.destroy()); req.pipe(upstream);
});
async function waitFor(page, predicate, label) {
  let timer;
  try { await Promise.race([page.waitFor(predicate), new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label)), 15000); })]); }
  finally { clearTimeout(timer); }
}
(async () => {
  await new Promise(resolve => proxy.listen(0, '127.0.0.1', resolve));
  const mini = await automator.connect({ wsEndpoint: 'ws://127.0.0.1:9435' });
  try {
    assert.equal((await mini.systemInfo()).windowWidth, 375);
    await mini.evaluate(value => {
      require('config.js').develop.apiBase = 'http://127.0.0.1:' + value.port + '/api';
      require('core/auth.js').setSession(value.session);
    }, { session: input.session, port: proxy.address().port });
    let page = await mini.reLaunch('/pages/points/index');
    await waitFor(page, async () => (await page.data()).visible?.some(r => r.id === input.rewardId), '商品未加载');
    await mini.mockWxMethod('showModal', { confirm: true, cancel: false });
    await (await page.$(`.reward button[data-id="${input.rewardId}"]`)).tap();
    try { await waitFor(page, async () => !!(await page.data()).pendingRedemption && !(await page.data()).busy, 'pending redemption missing'); }
    catch (e) { throw new Error(e.message + ' dropped=' + dropped + ' storage=' + JSON.stringify(await mini.evaluate(id => wx.getStorageSync('xa:native:points-redemption:' + id), input.session.user.id)) + ' data=' + JSON.stringify(await page.data())); }
    assert.equal(dropped, true);
    page = await mini.reLaunch('/pages/points/index');
    await waitFor(page, async () => (await page.data()).visible?.some(r => r.id === input.rewardId && r.soldOut), '售罄状态未更新');
    assert.ok((await page.data()).pendingRedemption);
    await (await page.$('.redemption-pending button')).tap();
    await waitFor(page, async () => (await mini.currentPage()).path === 'pages/reward-claim/index', '重试未打开权益');
    page = await mini.currentPage();
    await waitFor(page, async () => (await page.data()).claim?.name_snapshot === '最后一份权益', '权益详情未加载');
    const claim = (await page.data()).claim;
    const cancelled = await fetch(`${input.base}/api/admin/club-claims/${claim.id}/process/`, {
      method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${input.staffToken}`},
      body:JSON.stringify({status:'CANCELLED',note:'库存损坏，退回积分'}),
    });
    assert.equal((await cancelled.json()).code,0);
    await page.callMethod('load');
    await waitFor(page, async () => (await page.data()).claim?.status === 'CANCELLED', '取消状态未更新');
    assert.equal(await (await page.$('.refund-points')).text(), '已退回 5 积分，可在积分明细查看。');
    const redeemed = await fetch(`${input.base}/api/club/redeem/`, {
      method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${input.session.token}`},
      body:JSON.stringify({reward_id:input.rewardId,client_request_id:'retirement-redeem-real'}),
    });
    const second = await redeemed.json(); assert.equal(second.code,0);
    const retired = await fetch(`${input.base}/api/admin/users/${input.session.user.id}/retire/`, {
      method:'POST',headers:{'Content-Type':'application/json',Authorization:`Bearer ${input.staffToken}`},
      body:JSON.stringify({reason:'隔离测试退店'}),
    });
    assert.equal((await retired.json()).code,0);
    page=await mini.reLaunch('/pages/reward-claim/index?id='+second.data.id);
    await waitFor(page,async()=>(await page.data()).claim?.id===second.data.id,'退店权益未加载');
    assert.equal((await page.data()).claim.points_returned,false);
    assert.equal(await page.$('.refund-points'),null);
  } finally {
    await mini.evaluate(() => { require('config.js').develop.apiBase = 'http://127.0.0.1:19210/api'; });
    await mini.disconnect(); proxy.closeAllConnections(); proxy.close();
  }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
