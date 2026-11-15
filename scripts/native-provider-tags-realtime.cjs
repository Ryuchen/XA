// Actual native page controllers, HTTP and cross-process Redis/WebSocket; wx is the host adapter.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const input=JSON.parse(fs.readFileSync(0,'utf8')),root=path.resolve('apps/boss-miniapp/native'),storage=new Map();
let connected;const ready=new Promise(resolve=>{connected=resolve});
global.wx={getStorageSync:k=>storage.get(k),setStorageSync:(k,v)=>storage.set(k,v),removeStorageSync:k=>storage.delete(k),
 getStorageInfoSync:()=>({keys:[...storage.keys()]}),getAccountInfoSync:()=>({miniProgram:{envVersion:'develop'}}),showToast(){},
 request(options){const u=new URL(options.url);fetch(input.base+u.pathname+u.search,{method:options.method,headers:options.header,
  ...(options.data===undefined?{}:{body:JSON.stringify(options.data)})}).then(async r=>options.success({statusCode:r.status,data:await r.json()})).catch(options.fail)},
 connectSocket(options){const u=new URL(options.url),s=new WebSocket(input.wsBase.replace(/^http/,'ws')+u.pathname+u.search);return {
  onOpen:f=>s.addEventListener('open',f),onMessage:f=>s.addEventListener('message',e=>{if(String(e.data).includes('pong'))connected();f({data:e.data})}),
  onClose:f=>s.addEventListener('close',f),onError:f=>s.addEventListener('error',f),send:v=>s.send(v.data),close:()=>s.close()}}};
const auth=require(path.join(root,'core/auth'));auth.setSession({token:input.bossToken,user:{id:String(input.bossId),role:'customer'}});
function makePage(name,options){let definition;global.Page=v=>{definition=v};require(path.join(root,'pages',name,'index.js'));
 const p={...definition,data:structuredClone(definition.data),setData(patch){Object.assign(this.data,patch)}};p.onLoad(options);p.onShow();return p}
const list=makePage('companions',{}), detail=makePage('companion-detail',{id:String(input.providerId)}),realtime=require(path.join(root,'core/realtime'));
const row=()=>list.data.visible.find(x=>Number(x.id)===input.providerId), tags=p=>(p?.previewTags||[]).map(x=>x.title);
async function waitFor(fn,label,timeout=12000){const end=Date.now()+timeout;while(Date.now()<end){if(fn())return;await new Promise(r=>setTimeout(r,40))}throw Error(label)}
async function post(url,token,data,method='POST'){const r=await fetch(input.base+'/api'+url,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},body:JSON.stringify(data)}),v=await r.json();assert.equal(v.code,0,JSON.stringify(v));return v.data}
(async()=>{try{realtime.show();await Promise.race([ready,new Promise((_,reject)=>setTimeout(()=>reject(Error('websocket timeout')),10000))]);
 await waitFor(()=>row()&&detail.data.provider&&!list.data.loading&&!detail.data.loading,'initial companion data');
 if(input.fallbackOnly){
  const first=['标签丁','标签丙','标签乙'],fallback=['标签丙','标签乙','标签甲'];
  const both=expected=>JSON.stringify(tags(row()))===JSON.stringify(expected)&&JSON.stringify(tags(detail.data.provider))===JSON.stringify(expected);
  list.search({detail:{value:'标签'}});assert.ok(both(first));
  await post(`/admin/club-claims/${input.claimId}/suspend-tag/`,input.staffToken,{reason:'重新核实内容',expected_tag:'标签丁'});
  await waitFor(()=>both(fallback),'suspended tag did not fall back');
  if(input.syncDir) await waitFor(()=>fs.existsSync(path.join(input.syncDir,'suspended')),'provider did not observe suspension');
  await post(`/admin/club-claims/${input.claimId}/review-tag/`,input.staffToken,{tag_reviewed:true,expected_tag:'标签丁'});
  await waitFor(()=>both(first),'reviewed tag did not return');
  if(input.syncDir) await waitFor(()=>fs.existsSync(path.join(input.syncDir,'restored')),'provider did not observe restored tag');
  await waitFor(()=>both(fallback),'expired tag did not fall back',30000);
  assert.equal(list.data.keyword,'标签');return;
 }
 if(input.expiryOnly){
  list.search({detail:{value:'标签'}});assert.ok(tags(row()).includes('清海搭子'));assert.ok(tags(detail.data.provider).includes('清海搭子'));
  await waitFor(()=>!tags(row()).includes('清海搭子')&&!tags(detail.data.provider).includes('清海搭子'),'expired tag not refreshed');
  assert.equal(list.data.keyword,'标签');return;
 }
 list.search({detail:{value:'标签'}});assert.ok(row());assert.deepEqual(tags(row()),[]);
 await post(`/admin/club-claims/${input.claimId}/process/`,input.staffToken,{status:'COMPLETED',note:'审核通过',tag_reviewed:true});
 await waitFor(()=>tags(row()).includes('海洋搭子')&&tags(detail.data.provider).includes('海洋搭子'),'approved tag not refreshed');
 assert.equal(list.data.keyword,'标签');
 await post(`/admin/club-claims/${input.claimId}/suspend-tag/`,input.staffToken,{reason:'内容需要重新核实',expected_tag:'海洋搭子'});
 await waitFor(()=>!tags(row()).includes('海洋搭子')&&!tags(detail.data.provider).includes('海洋搭子'),'suspended tag not refreshed');
 await post(`/club/claims/${input.claimId}/`,input.providerToken,{details:{tag:'清海搭子'}},'PATCH');
 await post(`/admin/club-claims/${input.claimId}/review-tag/`,input.staffToken,{tag_reviewed:true,expected_tag:'清海搭子'});
 await waitFor(()=>tags(row()).includes('清海搭子')&&tags(detail.data.provider).includes('清海搭子'),'revised tag not refreshed');
 assert.equal(list.data.keyword,'标签');
 }finally{realtime.hide();list.onUnload();detail.onUnload()}
})().catch(e=>{console.error(e.stack||e);process.exitCode=1});
