const assert=require('node:assert/strict'),fs=require('node:fs');
const automator=require('../.tools/wechat-automation/node_modules/miniprogram-automator');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
async function wait(page,predicate,label){let timer;try{await Promise.race([page.waitFor(predicate),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(label)),15000);})]);}finally{clearTimeout(timer);}}
(async()=>{
  const mini=await automator.connect({wsEndpoint:'ws://127.0.0.1:9435'});
  try{
    assert.equal((await mini.systemInfo()).windowWidth,375);
    await mini.evaluate(session=>require('core/auth.js').setSession(session),input.session);
    let page=await mini.reLaunch('/pages/message-detail/index?id='+input.noticeId);
    await wait(page,async()=>!!(await page.data()).item,'消息未加载');
    assert.equal((await page.data()).actionText,'查看权益记录');
    assert.equal((await page.data()).target,'/pages/points/index?tab=claims');
    await (await page.$('.related-button')).tap();
    await wait(page,async()=>(await mini.currentPage()).path==='pages/points/index','未打开权益列表');
    page=await mini.currentPage();
    await wait(page,async()=>(await page.data()).claims?.some(item=>item.id===input.claimId&&item.status==='CANCELLED'),'已撤销权益未显示');
    assert.equal((await page.data()).tab,'claims');
  }finally{await mini.disconnect();}
})().catch(error=>{console.error(error.stack||error);process.exitCode=1;});
