const assert=require('node:assert/strict'),fs=require('node:fs');
const automator=require('../.tools/wechat-automation/node_modules/miniprogram-automator');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
async function wait(page,predicate,label){let timer;try{await Promise.race([page.waitFor(predicate),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label)),15000);})]);}finally{clearTimeout(timer);}}
(async()=>{
  const mini=await automator.connect({wsEndpoint:'ws://127.0.0.1:9435'});
  try{
    assert.equal((await mini.systemInfo()).windowWidth,375);
    await mini.evaluate(session=>require('core/auth.js').setSession(session),input.session);
    const page=await mini.reLaunch('/pages/reward-claim/index?id='+input.claimId);
    await wait(page,async()=>(await page.data()).claim?.tag_editable,'暂停标签修改入口未加载');
    const field=await page.$('input[data-field="tag"]');assert.ok(field);
    await field.input('清海老板');
    await (await page.$('.claim-form button.button')).tap();
    await wait(page,async()=>(await page.data()).claim?.details?.tag==='清海老板'&&!(await page.data()).busy,'修改未保存');
    assert.equal((await page.data()).claim.tag_reviewed,false);
    assert.equal((await page.data()).claim.tag_revision_submitted,true);
    await page.callMethod('load');
    await wait(page,async()=>(await page.data()).claim?.tag_revision_submitted,'复核状态刷新后丢失');
  }finally{await mini.disconnect();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
