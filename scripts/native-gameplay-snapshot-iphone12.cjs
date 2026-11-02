const assert=require('node:assert/strict'),input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const automator=require('../.tools/wechat-automation/node_modules/miniprogram-automator');
(async()=>{
  const mini=await automator.connect({wsEndpoint:'ws://127.0.0.1:19435'});
  try {
    const device=await mini.systemInfo();assert.equal(device.model,'iPhone 12/13 (Pro)');
    assert.equal(device.windowWidth,390);assert.equal(device.screenHeight,844);assert.equal(device.pixelRatio,3);
    await mini.evaluate(value=>{require('config.js').develop.apiBase=value.base+'/api';require('core/auth.js').setSession(value.session)},
      {base:input.base,session:{token:input.token,user:{id:input.bossId,role:'customer'}}});
    const page=await mini.reLaunch('/pages/order-detail/index?id='+input.orderId);let timer;
    try {await Promise.race([page.waitFor(async()=>(await page.data()).order?.roulette_result?.name===input.target),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('转盘结果未加载')),15000)})]);} finally {clearTimeout(timer)}
    const status=await page.$('.status-description'),result=await page.$('.result-name');
    assert.equal(await status.text(),input.fulfillment);assert.equal(await result.text(),input.target);
    console.log(`Native ${device.model}: actual drawn target rendered.`);
  } finally {await mini.disconnect();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
