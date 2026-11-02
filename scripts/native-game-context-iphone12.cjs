const assert=require('node:assert/strict'),input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const automator=require('../.tools/wechat-automation/node_modules/miniprogram-automator');
(async()=>{
  const mini=await automator.connect({wsEndpoint:'ws://127.0.0.1:19435'});
  try {
    const device=await mini.systemInfo();assert.equal(device.model,'iPhone 12/13 (Pro)');assert.equal(device.windowWidth,390);assert.equal(device.screenHeight,844);assert.equal(device.pixelRatio,3);
    console.log('Native device confirmed');
    await mini.evaluate(value=>{require('config.js').develop.apiBase=value.base+'/api';require('core/auth.js').setSession(value.session)},
      {base:input.base,session:{token:input.token,user:{id:input.bossId,role:'customer'}}});
    console.log('Isolated session configured');
    const page=await mini.reLaunch('/pages/order-detail/index?id='+input.orderId);let timer;
    console.log('Order page opened');
    try {await Promise.race([page.waitFor(async()=>(await page.data()).order?.game_uid==='POOL-ISOLATED-UID'),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('游戏资料未加载')),15000)})]);} finally {clearTimeout(timer)}
    const panel=await page.$('.game-info-panel');const text=await panel.text();
    for(const expected of ['隔离大区','池中隔离昵称','POOL-ISOLATED-UID']) assert.ok(text.includes(expected));
    console.log(`Native ${device.model}: boss sees assigned order's game details.`);
  } finally {await mini.disconnect();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
