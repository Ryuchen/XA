// Two isolated clients use the real backend; wx is the native host adapter only.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve(__dirname,'../apps/boss-miniapp/native');
const storage=new Map(),toasts=[],posts=[],dialogs=[];let shownDraft='';
global.wx={
  getStorageSync:key=>storage.get(key),setStorageSync:(key,value)=>storage.set(key,value),removeStorageSync:key=>storage.delete(key),
  getStorageInfoSync:()=>({keys:[...storage.keys()]}),getAccountInfoSync:()=>({miniProgram:{envVersion:'develop'}}),
  showToast:value=>toasts.push(value.title),showModal:value=>{if(value.title==='售后未提交')dialogs.push(value);else shownDraft=value.content||'';value.success({confirm:true,content:'第二设备填写的新说明'})},stopPullDownRefresh(){},
  request(options){(async()=>{try{const url=new URL(options.url);const response=await fetch(input.base+url.pathname+url.search,{method:options.method,
    headers:options.header,...(options.data===undefined?{}:{body:JSON.stringify(options.data)})});const data=await response.json();
    if(url.pathname.endsWith('/aftersale/')&&options.method==='POST')posts.push({body:options.data,response:data});
    options.success({statusCode:response.status,data});}catch(error){options.fail(error)}})()},
};
let definition;global.Page=value=>{definition=value};
require(path.join(root,'core/auth')).setSession(input.session);
require(path.join(root,'pages/order-detail/index.js'));
const page={...definition,data:structuredClone(definition.data),setData(patch){Object.assign(this.data,patch)}};
for(const [key,value] of Object.entries(page))if(typeof value==='function')page[key]=value.bind(page);
page.onLoad({id:input.orderId});page._visible=true;
(async()=>{
  await page.load();assert.equal(page.data.order.canAfterSale,true);
  const first=await fetch(`${input.base}/api/orders/orders/${input.orderId}/aftersale/`,{method:'POST',
    headers:{Authorization:`Bearer ${input.session.token}`,'Content-Type':'application/json'},
    body:JSON.stringify({reason:'第一设备已提交的说明',client_request_id:'concurrent-first-case'})});
  const result=await first.json();assert.equal(result.code,0,JSON.stringify(result));
  await page.aftersale();
  const detailResponse=await fetch(`${input.base}/api/orders/orders/${input.orderId}/aftersale/`,{
    headers:{Authorization:`Bearer ${input.session.token}`}});
  const detail=await detailResponse.json();assert.equal(detail.code,0);
  assert.equal(detail.data.reason,'第一设备已提交的说明');
  assert.equal(posts.length,1);assert.equal(posts[0].body.reason,'第二设备填写的新说明');
  assert.equal(posts[0].response.errorCode,'AFTERSALE_ALREADY_OPEN');
  assert.ok(!toasts.includes('售后已提交'));
  assert.equal(dialogs.length,1);
  assert.match(dialogs[0].content,/已有售后处理中.*本次说明未提交.*联系客服/);
  assert.equal(page._aftersaleDraft,'第二设备填写的新说明');
  assert.equal(page.data.aftersale.reason,'第一设备已提交的说明');
  assert.equal(page.data.order.canAfterSale,false);
  const staffRead=await fetch(`${input.base}/api/admin/aftersales/?order=${input.orderId}`,{headers:{Authorization:`Bearer ${input.staffToken}`}});
  const staffData=await staffRead.json();assert.equal(staffData.code,0);assert.equal(staffData.data.list[0].reason,'第一设备已提交的说明');
  const resolution=await fetch(`${input.base}/api/admin/aftersales/${detail.data.id}/resolve/`,{method:'POST',
    headers:{Authorization:`Bearer ${input.staffToken}`,'Content-Type':'application/json'},body:JSON.stringify({resolution:'已处理第一设备申请'})});
  assert.equal((await resolution.json()).code,0);
  await page.load();assert.equal(page.data.order.canAfterSale,true);
  await page.aftersale();assert.equal(shownDraft,'第二设备填写的新说明');
  assert.equal(posts.length,2);assert.equal(posts[1].response.code,0);
  assert.equal(posts[1].body.client_request_id,posts[0].body.client_request_id);
  assert.equal(page.data.aftersale.reason,'第二设备填写的新说明');
  assert.equal(page._aftersaleDraft,'');
  console.log('Second description is explicitly rejected; the existing case is shown and draft retained');
  page.onUnload();
})().then(()=>process.exit(0)).catch(error=>{console.error(error.stack||error);process.exit(1)});
