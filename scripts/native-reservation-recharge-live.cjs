// Native controller against actual isolated APIs; only the wx platform shell is simulated.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve('apps/boss-miniapp/native'), storage = new Map(), navigations = [];
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  removeStorageSync: key => storage.delete(key), getStorageInfoSync: () => ({keys:[...storage.keys()]}),
  getAccountInfoSync: () => ({miniProgram:{envVersion:'develop'}}), showToast() {},
  showModal: options => options.success({confirm:true}),
  navigateTo: options => navigations.push(options.url), switchTab: options => navigations.push(options.url),
  request(options) {
    const url = new URL(options.url);
    fetch(input.api + url.pathname + url.search, {method:options.method, headers:options.header,
      ...(options.data === undefined ? {} : {body:JSON.stringify(options.data)})})
      .then(async response => options.success({statusCode:response.status,data:await response.json()})).catch(options.fail);
  },
};
let definition; global.Page = value => {definition=value};
require(path.join(root,'core/auth')).setSession({token:input.bossToken,user:{id:String(input.bossId),role:'customer'}});
require(path.join(root,'pages/reservation-detail/index.js'));
const page={...definition,data:structuredClone(definition.data),setData(patch){Object.assign(this.data,patch)}};
for(const [key,value] of Object.entries(page)) if(typeof value==='function') page[key]=value.bind(page);
async function waitFor(fn,message){for(let i=0;i<150;i++){if(fn())return;await new Promise(r=>setTimeout(r,50));}throw Error(message)}
(async()=>{
  page.onLoad({id:input.bookingId}); page._visible=true; await page.load();
  assert.equal(page.data.reservation.canConvert,true);
  await page.quote(); assert.equal(page.data.quote.balance_sufficient,false); assert.equal(page.data.quote.wallet_balance,0);
  page.wallet(); assert.equal(navigations.at(-1),'/pages/wallet/index'); assert.equal(page.data.quote,null); page.onHide();
  const amount=input.scenario==='price_changed'?12000:10000;
  const form=new FormData(); form.set('account_id',String(input.bossId)); form.set('amount',String(amount)); form.set('gift_amount','0');
  form.set('trade_no','native-reservation-local-recharge'); form.set('client_request_id','native-reservation-local-recharge');
  form.set('proof_image',new Blob([Buffer.from(input.proof,'base64')],{type:'image/png'}),'receipt.png');
  const response=await fetch(input.api+'/api/admin/wallets/recharge/',{method:'POST',headers:{Authorization:'Bearer '+input.staffToken},body:form});
  const result=await response.json(); assert.equal(result.code,0,JSON.stringify(result));
  if(input.scenario==='price_changed'){
    const changed=await fetch(input.api+'/api/admin/service-items/'+input.serviceId+'/',{method:'PATCH',headers:{Authorization:'Bearer '+input.staffToken,'Content-Type':'application/json'},body:JSON.stringify({price:12000})});
    const body=await changed.json(); assert.equal(body.code,0,JSON.stringify(body));
  }
  if(input.scenario==='cancelled'){
    const cancelled=await fetch(input.api+'/api/orders/reservations/'+input.bookingId+'/cancel/',{method:'POST',headers:{Authorization:'Bearer '+input.bossToken,'Content-Type':'application/json'},body:JSON.stringify({reason:'隔离预约取消'})});
    const body=await cancelled.json(); assert.equal(body.code,0,JSON.stringify(body));
  }
  page.onShow();
  if(input.scenario==='cancelled'){
    await waitFor(()=>page.data.reservation?.status==='CANCELLED'&&!page.data.loading,'cancelled reservation did not refresh');
    assert.equal(page.data.quote,null); assert.equal(page.data.reservation.canConvert,false); page.onUnload(); return;
  }
  await waitFor(()=>page.data.quote?.wallet_balance===amount && page.data.quote.balance_sufficient,'quote did not refresh after return');
  assert.equal(page.data.quote.amount,amount);
  assert.equal(page.data.quote.differsFromEstimate,input.scenario==='price_changed');
  await page.convert();
  assert.match(navigations.at(-1),/^\/pages\/order-detail\/index\?id=\d+$/);
  assert.equal(page.data.reservation.status,'CONVERTED'); page.onUnload();
  console.log('Native controller refreshed real quote after staff recharge and converted reservation');
})().catch(error=>{console.error(error.stack||error);process.exitCode=1});
