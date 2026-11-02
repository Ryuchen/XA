const assert=require('node:assert/strict'),fs=require('node:fs');
const automator=require('../.tools/wechat-automation/node_modules/miniprogram-automator');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
async function wait(page,predicate,label){let timer;try{await Promise.race([page.waitFor(predicate),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label)),15000);})]);}finally{clearTimeout(timer);}}
(async()=>{
  const mini=await automator.connect({wsEndpoint:'ws://127.0.0.1:9435'});
  try{
    assert.equal((await mini.systemInfo()).windowWidth,375);
    await mini.evaluate(session=>require('core/auth.js').setSession(session),input.session);
    let page=await mini.reLaunch('/pages/reward-claim/index?id='+input.claimId);
    await wait(page,async()=>(await page.data()).claim?.status==='COMPLETED','标签权益未加载');
    await (await page.$('.manage-tags')).tap();
    await wait(page,async()=>(await mini.currentPage()).path==='pages/membership/index','未打开标签设置');
    page=await mini.currentPage();
    await wait(page,async()=>(await page.data()).member?.selected_tags?.length===1,'有效选择未加载');
    assert.deepEqual((await page.data()).selected,[input.validKey]);
    assert.equal((await page.data()).tags.length,1);
    await (await page.$(`button[data-key="${input.validKey}"]`)).tap();
    await (await page.$('.save-tags')).tap();
    await wait(page,async()=>!(await page.data()).busy && (await page.data()).member?.selected_tags?.length===0,'标签未保存');
  }finally{await mini.disconnect();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
