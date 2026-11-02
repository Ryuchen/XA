const assert = require('node:assert/strict');
const input = JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const automator = require('../.tools/wechat-automation/node_modules/miniprogram-automator');
async function waitFor(page, predicate, label) {
  let timer;
  try { await Promise.race([page.waitFor(predicate),new Promise((_,reject) => {timer=setTimeout(() => reject(new Error(label)),15000);})]); }
  finally { clearTimeout(timer); }
}
(async () => {
  const mini = await automator.connect({wsEndpoint:'ws://127.0.0.1:19435'});
  try {
    const device = await mini.systemInfo();
    assert.equal(device.model,'iPhone 12/13 (Pro)');
    assert.equal(device.windowWidth,390); assert.equal(device.screenHeight,844); assert.equal(device.pixelRatio,3);
    await mini.evaluate(value => {require('config.js').develop.apiBase=value.base+'/api';require('core/auth.js').setSession(value.session);},
      {base:input.base,session:{token:input.token,user:{id:input.bossId,role:'customer'}}});
    let page = await mini.reLaunch('/pages/order-detail/index?id='+input.orderId);
    await waitFor(page,async () => (await page.data()).order?.reservationOrigin?.id === input.reservationId,'订单原预约未加载');
    const labels = await page.$$('.text-note .note-label');
    assert.ok((await Promise.all(labels.map(label => label.text()))).includes('原预约时段 · 北京时间'));
    const link = await page.$('.text-note button'); assert.ok(link); assert.equal(await link.text(),'查看预约');
    await link.tap();
    const deadline = Date.now() + 15000;
    do { page = await mini.currentPage(); if (page.path === 'pages/reservation-detail/index') break; await new Promise(resolve => setTimeout(resolve,100)); } while (Date.now() < deadline);
    assert.equal(page.path,'pages/reservation-detail/index');
    await waitFor(page,async () => (await page.data()).reservation?.id === input.reservationId,'原预约详情未加载');
    assert.equal((await page.data()).reservation.status,'CONVERTED');
    console.log(`Native ${device.model} ${device.windowWidth}x${device.screenHeight} DPR ${device.pixelRatio}: order opens its converted appointment.`);
  } finally { await mini.disconnect(); }
})().catch(error => {console.error(error.stack || error);process.exitCode=1;});
