const assert = require('node:assert/strict');
const input=JSON.parse(require('node:fs').readFileSync(0,'utf8'));
const automator=require('../.tools/wechat-automation/node_modules/miniprogram-automator');
(async()=>{
  const mini=await automator.connect({wsEndpoint:'ws://127.0.0.1:19435'});
  try {
    const device=await mini.systemInfo();
    assert.equal(device.model,'iPhone 12/13 (Pro)');assert.equal(device.windowWidth,390);assert.equal(device.screenHeight,844);assert.equal(device.pixelRatio,3);
    await mini.evaluate(value=>{require('config.js').develop.apiBase=value.base+'/api';require('core/auth.js').setSession(value.session)},
      {base:input.base,session:{token:input.token,user:{id:input.bossId,role:'customer'}}});
    const page=await mini.reLaunch('/pages/order-detail/index?id='+input.orderId);
    let timer;
    try {await Promise.race([page.waitFor(async()=>(await page.data()).order?.cancel_reason===input.expected),
      new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('取消原因未加载')),15000)})]);} finally {clearTimeout(timer)}
    const label=await page.$('.status-note .note-label');assert.ok(label);assert.equal(await label.text(),input.label);
    const block=await page.$('.status-note');assert.ok((await block.text()).includes(input.expected));
    console.log(`Native ${device.model}: ${input.label} rendered.`);
  } finally {await mini.disconnect();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
