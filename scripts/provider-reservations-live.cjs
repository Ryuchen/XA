// Exercise the actual provider page and service against isolated Django HTTP.
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), assert = require('node:assert/strict');
const {createRequire} = require('node:module');
const args = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/provider-miniapp');
const ts = createRequire(path.join(root, 'package.json'))('typescript');
const compile = file => ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,jsx:ts.JsxEmit.React}}).outputText;
const pending = new Set(), storage = {}, events = new Map(), wsEvents = new Map(), toasts = []; let current, holdNext = false, releaseRead, readArrived, dropAction = false, holdActions = false; const heldActions = [];
const taro = {
  getStorageSync:key=>storage[key],setStorageSync:(key,value)=>{storage[key]=value;},removeStorageSync:key=>{delete storage[key];},
  getCurrentInstance:()=>({router:{params:args.listMode ? {} : {id:String(args.firstId)}}}),
  eventCenter:{on:(name,fn)=>{if(!events.has(name))events.set(name,new Set());events.get(name).add(fn);},off:(name,fn)=>events.get(name)?.delete(fn),trigger:name=>events.get(name)?.forEach(fn=>fn())},
  showToast(options){toasts.push(options.title);},showModal:async()=>({confirm:true}),stopPullDownRefresh(){},switchTab:async()=>{},
  request:options=>{const hold=holdNext && options.method==='GET' && options.url.endsWith(`/reservations/${args.firstId}/`);if(hold)holdNext=false;
    const p=fetch(options.url,{method:options.method,headers:options.header,...(options.data===undefined?{}:{body:JSON.stringify(options.data)})}).then(async r=>{const value={statusCode:r.status,data:await r.json(),header:{}};if(hold){readArrived();await new Promise(resolve=>{releaseRead=resolve;});}if(holdActions && options.method==='POST' && /\/reservations\/\d+\/confirm\/$/.test(options.url)){await new Promise(resolve=>heldActions.push(resolve));}if(dropAction && options.method==='POST' && /\/reservations\/\d+\/(confirm|cancel|reject)\/$/.test(options.url) && value.data.code===0){dropAction=false;throw {errMsg:'request:fail response lost'};}return value;});pending.add(p);p.then(()=>pending.delete(p),()=>pending.delete(p));return p;},
};
const react={
  createElement:(type,props,...children)=>({type,props:{...props,children}}),
  useState:initial=>{const i=current.cursor++,owner=current;if(!(i in owner.values))owner.values[i]=typeof initial==='function'?initial():initial;return [owner.values[i],next=>{owner.values[i]=typeof next==='function'?next(owner.values[i]):next;owner.dirty=true;}];},
  useRef:initial=>{const i=current.cursor++;if(!(i in current.values))current.values[i]={current:initial};return current.values[i];},
  useEffect:(fn,deps)=>{const i=current.cursor++,old=current.effects[i];if(!old||deps.some((v,j)=>v!==old[j])){current.effects[i]=deps;current.queue.push(fn);}},
};
const deps={react:{__esModule:true,default:react,...react},'@tarojs/components':{Button:'Button',Text:'Text',Textarea:'Textarea',View:'View'},
  '@tarojs/taro':{__esModule:true,default:taro,useDidShow:fn=>{current.didShow=fn;},useDidHide:fn=>{current.didHide=fn;},usePullDownRefresh:fn=>{current.pull=fn;}},
  './env':{BASE_URL:args.base+'/api'},'./orderNetwork':{scopeFor:()=>'',cooldownRemaining:()=>0,recordOrderRequest(){}},
  '@/components/PageNav':{default:'PageNav'},'@/components/PlatformLayout':{default:'PlatformLayout'},'@/components/SurfaceDialog':{default:'SurfaceDialog'},
  '@/utils/orderNavigation':{queueOrderFocus(){},clearOrderFocus(){}},'@/services/websocket':{wsService:{on:(type,fn)=>{if(!wsEvents.has(type))wsEvents.set(type,new Set());wsEvents.get(type).add(fn);return ()=>wsEvents.get(type).delete(fn);}}},'./index.module.scss':{default:new Proxy({},{get:(_,n)=>n})}};
function load(name,file){const m={exports:{}};vm.runInNewContext(compile(file),{module:m,exports:m.exports,require:id=>{if(!(id in deps))throw Error('Missing '+id);return deps[id];},Number,Date,Error,Promise,Set,setTimeout,clearTimeout});deps[name]=m.exports;return m.exports;}
const auth=load('@/utils/auth','src/utils/auth.ts');deps['./auth']=auth;
load('@/utils/request','src/utils/request.ts');load('@/services/reservation','src/services/reservation.ts');
const Page=load('page','src/pages/reservations/index.tsx').default;
const page={values:[],effects:[],queue:[],cursor:0,dirty:true};
function render(){current=page;page.cursor=0;page.dirty=false;page.tree=Page();for(const effect of page.queue.splice(0))effect();}
async function settle(){for(let i=0;i<80;i++){if(page.dirty)render();if(pending.size)await Promise.all([...pending]);await new Promise(r=>setImmediate(r));if(!page.dirty&&!pending.size)return;}throw Error('page did not settle');}
function nodes(value,out=[]){if(Array.isArray(value))value.forEach(x=>nodes(x,out));else if(value&&typeof value==='object'){out.push(value);nodes(value.props?.children,out);}return out;}
function text(v){return Array.isArray(v)?v.map(text).join(''):v&&typeof v==='object'?text(v.props.children):v==null||v===false?'':String(v);}
function button(label){return nodes(page.tree).find(n=>n.type==='Button'&&text(n)===label);}
async function until(predicate){for(let i=0;i<250;i++){if(page.dirty)render();if(predicate())return;await new Promise(resolve=>setTimeout(resolve,20));}throw Error('Timed out waiting for real reservation response');}
async function main(){
  auth.persistLoginData({token:args.token,userInfo:{id:String(args.providerId),username:'provider'}});
  if (args.listMode) {
    await settle(); assert.equal(page.values[1].length,20); assert.ok(button('加载更多'));
    const response=await fetch(`${args.base}/api/orders/reservations/`,{method:'POST',headers:{Authorization:'Bearer '+args.bossToken,'Content-Type':'application/json'},body:JSON.stringify(args.newReservation)});
    const created=await response.json(); assert.equal(created.code,0,JSON.stringify(created));
    await button('加载更多').props.onClick(); await settle();
    assert.equal(page.values[1].length, args.initialIds.length);
    assert.deepEqual(new Set(page.values[1].map(row=>row.id)),new Set(args.initialIds));
    wsEvents.get('reservation_status_update').forEach(fn=>fn({reservation_id:created.data.id})); await settle();
    assert.equal(page.values[1].length,args.initialIds.length+1);
    assert.equal(new Set(page.values[1].map(row=>row.id)).size,args.initialIds.length+1);
    assert.ok(page.values[1].some(row=>row.id===created.data.id));
    page.didHide(); page.didShow(); await settle(); assert.equal(page.values[1].length,args.initialIds.length+1);
    console.log('Provider: actual reservation pagination survives new bookings and refresh'); return;
  }
  await settle();
  if (args.accountSwitchMode) {
    holdActions=true;
    const first=button('确认预约').props.onClick(); await until(()=>heldActions.length===1);
    auth.persistLoginData({token:args.secondToken,userInfo:{id:String(args.secondProviderId),username:'provider2'}});
    await until(()=>!!button('查看全部预约') && !button('查看全部预约').props.disabled);
    button('查看全部预约').props.onClick();
    await until(()=>!!button('查看预约'));
    button('查看预约').props.onClick();
    await until(()=>!!button('确认预约'));
    const second=button('确认预约').props.onClick(); await until(()=>heldActions.length===2);
    heldActions[0](); await first; await until(()=>!page.dirty);
    assert.equal(button('确认预约').props.disabled,true,'old request must not unlock new account action');
    heldActions[1](); await second; await settle();
    assert.match(text(page.tree),/已确认/);
    console.log('Provider account switch keeps in-flight actions isolated'); return;
  }
  if (args.expiredMode) {
    assert.match(text(page.tree), /已过期/);
    assert.match(text(page.tree), /预约时段已结束，请与老板重新预约/);
    assert.equal(button('确认预约'), undefined);
    assert.equal(button('拒绝预约'), undefined);
    assert.equal(button('取消预约'), undefined);
    console.log('Provider: expired reservation is read-only with a clear next step'); return;
  }
  assert.ok(button('确认预约')); assert.ok(button('拒绝预约'));
  if (args.staleActionMode) {
    await button('拒绝预约').props.onClick(); await settle();
    nodes(page.tree).find(n=>n.type==='Textarea').props.onInput({detail:{value:'时间不合适'}}); await settle();
    const cancelled=await fetch(`${args.base}/api/orders/reservations/${args.firstId}/cancel/`,{method:'POST',headers:{Authorization:'Bearer '+args.bossToken,'Content-Type':'application/json'},body:JSON.stringify({reason:'老板改期'})});
    assert.equal((await cancelled.json()).code,0);
    await nodes(page.tree).find(n=>n.type==='SurfaceDialog').props.footer.props.onClick(); await settle();
    assert.ok(toasts.length, 'server rejection must remain visible');
    assert.match(text(page.tree),/已取消/);
    assert.equal(nodes(page.tree).find(n=>n.type==='SurfaceDialog').props.open,false,'invalidated reject dialog should close');
    assert.equal(button('拒绝预约'),undefined);
    console.log('Provider: stale reject after real boss cancellation closes dialog and refreshes status'); return;
  }
  if (args.firstStart) {
    const expected = new Date(Date.parse(args.firstStart) + 8 * 3600000).toISOString().slice(0, 16).replace('T', ' ');
    assert.ok(text(page.tree).includes(expected), `provider must see Beijing time ${expected}`);
    assert.match(text(page.tree), /北京时间/);
  }
  if (args.eventMode) {
    await button('拒绝预约').props.onClick(); await settle();
    nodes(page.tree).find(n=>n.type==='Textarea').props.onInput({detail:{value:'我正在填写'}}); await settle();
    const response=await fetch(`${args.base}/api/orders/reservations/${args.firstId}/cancel/`,{method:'POST',headers:{Authorization:'Bearer '+args.bossToken,'Content-Type':'application/json'},body:JSON.stringify({reason:'老板改期'})});
    assert.equal((await response.json()).code,0);
    wsEvents.get('reservation_status_update').forEach(fn=>fn({reservation_id:args.firstId})); await settle();
    assert.equal(nodes(page.tree).find(n=>n.type==='SurfaceDialog').props.open,false);
    assert.match(text(page.tree),/已取消/); assert.match(text(page.tree),/老板改期/);
    page.didHide(); wsEvents.get('connected').forEach(fn=>fn()); page.didShow(); await settle();
    assert.match(text(page.tree),/已取消/);
    await button('查看全部预约').props.onClick(); await settle(); assert.match(text(page.tree),/待确认/);
    const other=await fetch(`${args.base}/api/orders/reservations/${args.listId}/cancel/`,{method:'POST',headers:{Authorization:'Bearer '+args.bossToken,'Content-Type':'application/json'},body:JSON.stringify({reason:'调整时间'})});
    assert.equal((await other.json()).code,0); wsEvents.get('reservation_status_update').forEach(fn=>fn({reservation_id:args.listId})); await settle();
    assert.doesNotMatch(text(page.tree),/待确认/);
    console.log('Provider: real cancellation invalidates the open dialog and refreshes on show'); return;
  }
  // Hold delivery of an actual detail response taken before confirmation.
  // The underlying reservation reads and transition still run through Django.
  holdNext=true;const readReady=new Promise(resolve=>{readArrived=resolve;});page.didShow();await readReady;
  dropAction=true;await button('确认预约').props.onClick();releaseRead();await settle();assert.match(text(page.tree),/已确认/);assert.ok(button('取消预约'));assert.equal(button('确认预约'),undefined);assert.equal(toasts.at(-1),'预约结果未确认，请核对状态');
  await button('取消预约').props.onClick(); await settle();
  nodes(page.tree).find(n=>n.type==='Textarea').props.onInput({detail:{value:'临时有事'}}); await settle();
  dropAction=true;await nodes(page.tree).find(n=>n.type==='SurfaceDialog').props.footer.props.onClick(); await settle();
  assert.match(text(page.tree),/已取消/); assert.match(text(page.tree),/临时有事/); assert.equal(button('取消预约'),undefined);assert.equal(toasts.at(-1),'预约结果未确认，请核对状态');
  await button('查看全部预约').props.onClick(); await settle(); assert.match(text(page.tree),/待确认/); assert.match(text(page.tree),/已取消/);
  const details=nodes(page.tree).filter(n=>n.type==='Button'&&text(n)==='查看预约'); assert.equal(details.length,2); await details[0].props.onClick(); await settle();
  assert.ok(button('拒绝预约')); await button('拒绝预约').props.onClick(); await settle();
  const input=nodes(page.tree).find(n=>n.type==='Textarea'); input.props.onInput({detail:{value:'时间不合适'}}); await settle();
  const dialog=nodes(page.tree).find(n=>n.type==='SurfaceDialog'); assert.equal(dialog.props.open,true); dropAction=true;await dialog.props.footer.props.onClick(); await settle();
  assert.match(text(page.tree),/已拒绝/); assert.match(text(page.tree),/时间不合适/); assert.equal(button('拒绝预约'),undefined);assert.equal(toasts.at(-1),'预约结果未确认，请核对状态');
  console.log('Provider reservation page -> real Django HTTP: list, confirm, cancel, reject and reasons passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
