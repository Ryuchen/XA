const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const x = JSON.parse(fs.readFileSync(0, 'utf8')), root = path.resolve('apps/boss-miniapp/native'), ws = new URL(x.wsBase), store = new Map();
let socketOpened, dropNextPatch=false; const connected = new Promise(resolve => socketOpened = resolve);
global.wx = {
  getStorageSync: k => store.get(k), setStorageSync: (k, v) => store.set(k, v), removeStorageSync: k => store.delete(k),
  getAccountInfoSync: () => ({miniProgram:{envVersion:'develop'}}), showToast(){}, showModal(o){o.success({confirm:true})}, stopPullDownRefresh(){},
  request(o){ const u = new URL(o.url); fetch(x.base + u.pathname + u.search, {method:o.method, headers:o.header,
    ...(o.data === undefined ? {} : {body:JSON.stringify(o.data)})}).then(async r => {const data=await r.json();if(dropNextPatch&&o.method==='PATCH'){dropNextPatch=false;o.fail({errMsg:'simulated response loss'})}else o.success({statusCode:r.status,data})}).catch(o.fail); },
  connectSocket({url}){ const u = new URL(url), s = new WebSocket(`ws://${ws.host}${u.pathname}${u.search}`); return {
    onOpen(f){s.addEventListener('open',()=>{f();socketOpened()})}, onMessage(f){s.addEventListener('message',e=>f({data:e.data}))},
    onClose(f){s.addEventListener('close',f)}, onError(f){s.addEventListener('error',f)}, send({data}){s.send(data)}, close(){s.close()} }; }
};
async function wait(predicate, message){for(let i=0;i<400;i++){if(predicate())return;await new Promise(r=>setTimeout(r,25));}throw Error(message);}
async function process(status,note){const r=await fetch(`${x.base}/api/admin/club-claims/${x.claim}/process/`,{method:'POST',headers:{Authorization:'Bearer '+x.staffToken,'Content-Type':'application/json'},body:JSON.stringify({status,note})});const b=await r.json();assert.equal(b.code,0,JSON.stringify(b));}
async function post(url,token,data,method='POST'){const r=await fetch(x.base+url,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(data)});const b=await r.json();assert.equal(b.code,0,JSON.stringify(b));return b.data;}
(async()=>{
  const auth=require(path.join(root,'core/auth'));auth.setSession({token:x.token,user:{id:x.account,role:'customer'}});
  let def;global.Page=value=>def=value;require(path.join(root,'pages/reward-claim/index.js'));
  const page={...def,data:structuredClone(def.data),setData(values){for(const [key,value] of Object.entries(values)){if(key.startsWith('details.'))this.data.details[key.slice(8)]=value;else this.data[key]=value;}}};
  for(const [key,value] of Object.entries(page))if(typeof value==='function')page[key]=value.bind(page);
  page.onLoad({id:x.claim});page.onShow();await wait(()=>page.data.claim?.status==='PENDING','initial claim did not load');
  require(path.join(root,'pages/points/index.js'));
  const list={...def,data:structuredClone(def.data),setData(values){Object.assign(this.data,values)}};
  for(const [key,value] of Object.entries(list))if(typeof value==='function')list[key]=value.bind(list);
  list.onLoad({tab:'claims'});list.onShow();await wait(()=>!list._refreshing&&list.data.claimsNext&&list.data.next,'paginated list did not load');
  await list.moreClaims();await list.more();assert.equal(list.data.claims.length,24);assert.equal(list.data.ledger.length,32);
  const realtime=require(path.join(root,'core/realtime'));
  const outsider=new WebSocket(`ws://${ws.host}/ws/orders/?token=${encodeURIComponent(x.otherToken)}`);let leaked=false;
  outsider.addEventListener('message',e=>{if(JSON.parse(e.data).type==='member_points_update')leaked=true});
  try {await new Promise((resolve,reject)=>{outsider.addEventListener('open',resolve);outsider.addEventListener('error',reject)});realtime.show();await connected;await wait(()=>!page.data.loading&&!list._refreshing,'initial refresh did not finish');
    await post('/api/admin/external-points/',x.staffToken,{account_id:x.account,kind:'KOOK_CHECKIN',day:x.creditDay,source_id:'native-live-another-device'});
    await wait(()=>list.data.points===21,'live points credit missing');
    await post('/api/club/redeem/',x.token,{reward_id:x.reward,client_request_id:'native-live-another-device'});
    await wait(()=>list.data.points===20&&list.data.claims.length===25,'live redemption missing');
    assert.equal(list.data.ledger.length,34);assert.equal(leaked,false);
    page.input({currentTarget:{dataset:{field:'game_uid'}},detail:{value:'其他设备更新前的草稿'}});
    await post(`/api/club/claims/${x.claim}/`,x.token,{details:{game_uid:'另一设备提交的 UID'}},'PATCH');
    await wait(()=>page.data.detailsConflict,'remote edit did not surface as a conflict');assert.equal(page.data.details.game_uid,'其他设备更新前的草稿');
    page.useLatest();await wait(()=>!page.data.loading&&!page.data.detailsConflict,'latest details did not load');assert.equal(page.data.details.game_uid,'另一设备提交的 UID');
    page.input({currentTarget:{dataset:{field:'game_uid'}},detail:{value:'响应丢失但已保存'}});dropNextPatch=true;await page.save();await wait(()=>!page.data.dirty&&page.data.claim?.details?.game_uid==='响应丢失但已保存','lost response was not reconciled');
    page.input({currentTarget:{dataset:{field:'game_uid'}},detail:{value:'尚未提交的游戏 UID'}});
    await process('FULFILLING','客服正在安排');
    await wait(()=>page.data.claim?.status==='FULFILLING'&&page.data.claim.delivery_note==='客服正在安排','live fulfillment missing');
    assert.deepEqual(page.data.claim.submittedFields,[{key:'game_uid',label:'游戏 UID',value:'响应丢失但已保存'}]);
    await wait(()=>list.data.claims.find(row=>row.id===x.claim)?.status==='FULFILLING','live list fulfillment missing');
    assert.equal(list.data.claims.length,25);assert.equal(list.data.ledger.length,34);assert.equal(list.data.tab,'claims');
    assert.equal(page.data.details.game_uid,'尚未提交的游戏 UID');assert.equal(page.data.dirty,true);assert.match(page.data.saveIssue,/尚未保存/);
    await process('COMPLETED','权益已交付');
    await wait(()=>page.data.claim?.status==='COMPLETED'&&page.data.claim.delivery_note==='权益已交付','live completion missing');
    assert.equal(page.data.claim.submittedFields[0].value,'响应丢失但已保存');
    await wait(()=>list.data.claims.find(row=>row.id===x.claim)?.status==='COMPLETED','live list completion missing');
    assert.equal(list.data.claims.length,25);assert.equal(list.data.ledger.length,34);assert.equal(leaked,false);
    assert.equal(page.data.details.game_uid,'尚未提交的游戏 UID');assert.match(page.data.saveIssue,/尚未保存/);
    console.log('Real redeem and admin delivery notifications refreshed native claim while preserving the unsaved draft');
  } finally {outsider.close();realtime.hide();page.onHide();page.onUnload();list.onHide();list.onUnload();}
})().catch(e=>{console.error(e.stack||e);process.exitCode=1});
