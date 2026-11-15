// Native simulator navigation verification; no payment or real network writes.
const assert=require('node:assert/strict');
const automator=require('../.tools/wechat-automation/node_modules/miniprogram-automator');
(async()=>{const m=await automator.connect({wsEndpoint:'ws://127.0.0.1:19435'});let original;
try{
 const info=await m.systemInfo();assert.equal(info.model,'iPhone 12/13 (Pro)');assert.equal(info.windowWidth,390);assert.equal(info.screenHeight,844);
 original=await m.evaluate(()=>{const c=require('config.js').develop;const x={...c};c.apiBase='http://127.0.0.1:9/api';c.socketBase='';return x});
 for(const key of ['home','companions','orders','messages','mine']){
  const p=await m.switchTab('/pages/'+key+'/index');assert.equal(p.path,'pages/'+key+'/index');
  const s=await m.evaluate(()=>{const p=getCurrentPages().slice(-1)[0];return {route:p.route,selected:p.getTabBar()?.data?.selected}});
  assert.equal(s.selected,key,JSON.stringify(s));console.log('TAB',key,s.selected);
 }
 const detail=await m.navigateTo('/pages/checkout/index');assert.equal(detail.path,'pages/checkout/index');
 const secondary=await detail.$('bottom-navigation');assert.ok(secondary,'checkout navigation');assert.equal((await secondary.data()).selected,'orders');
 await m.navigateBack();const s=await m.evaluate(()=>{const p=getCurrentPages().slice(-1)[0];return {route:p.route,selected:p.getTabBar()?.data?.selected}});
 assert.equal(s.route,'pages/mine/index');assert.equal(s.selected,'mine');console.log('BACK',s.route,s.selected);
 console.log('PASS: five real simulator tabs, secondary checkout selected, and return restores the previous tab.');
}finally{if(original){await m.evaluate(x=>{Object.assign(require('config.js').develop,x)},original).catch(()=>{});await m.reLaunch('/pages/home/index').catch(()=>{})}m.disconnect()}
})().catch(e=>{console.error(e);process.exitCode=1});
