const assert=require('node:assert/strict'),input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const automator=require('../.tools/wechat-automation/node_modules/miniprogram-automator');
(async()=>{
  const mini=await automator.connect({wsEndpoint:'ws://127.0.0.1:19435'});
  try {
    const device=await mini.systemInfo();assert.equal(device.model,'iPhone 12/13 (Pro)');assert.equal(device.windowWidth,390);assert.equal(device.screenHeight,844);assert.equal(device.pixelRatio,3);
    console.log('Native target confirmed');
    await mini.evaluate(value=>{require('config.js').develop.apiBase=value.base+'/api';require('core/auth.js').setSession(value.session)},
      {base:input.base,session:{token:input.token,user:{id:input.bossId,role:'customer'}}});
    console.log('Native isolated API session configured');
    let page;
    if(input.useExistingPage){
      page=await mini.currentPage();
      assert.equal(page.path,'pages/order-detail/index');
      assert.equal(String(page.query.id),String(input.orderId));
      console.log('Using already-open isolated order-detail page; navigation is not tested');
    } else {
      let relaunchTimer;
      page=await Promise.race([mini.reLaunch('/pages/order-detail/index?id='+input.orderId),
        new Promise((_,reject)=>{relaunchTimer=setTimeout(()=>reject(Error('原生订单页面重启超时')),10000)})]).finally(()=>clearTimeout(relaunchTimer));
    }
    let timer;
    try {await Promise.race([page.waitFor(async()=>{const order=(await page.data()).order;return order?.refund_summary?.amount===input.refundAmount
      && (!input.orderNo || order.order_no===input.orderNo)}),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('退款信息未加载')),15000)})]);} finally {clearTimeout(timer)}
    let panelTimer;
    await Promise.race([page.waitFor('.refund-panel'),
      new Promise((_,reject)=>{panelTimer=setTimeout(()=>reject(Error('原生退款面板未渲染')),8000)})]).finally(()=>clearTimeout(panelTimer));
    const panel=await page.$('.refund-panel');assert.ok(panel);
    assert.ok((await panel.text()).includes(`已退回钱包：${input.refundText} 兴安币`));
    if(input.negotiated){const order=(await page.data()).order;assert.equal(order.status,'CANCELLED');
      assert.equal(order.payment_status,'PARTIALLY_REFUNDED');
      assert.ok((await panel.text()).includes(`订单剩余金额：${input.remainingText} 兴安币`));}
    console.log(`Native ${device.model}: actual refund receipt rendered.`);
  } finally {await mini.disconnect();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
