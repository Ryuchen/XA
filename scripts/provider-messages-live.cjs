// Actual page, authentication and message service against an isolated Django HTTP server.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const args = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/provider-miniapp');
const ts = createRequire(path.join(root, 'package.json'))('typescript');
const compile = file => ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React}}).outputText;
const pending = new Set(), storage = {}, appEvents = new Map(), socketEvents = new Map();
let current, navigation = '', detailGate = null;
const taro = {
  getStorageSync: key => storage[key], setStorageSync: (key, value) => { storage[key] = value; }, removeStorageSync: key => { delete storage[key]; },
  eventCenter: {on: (name, fn) => { if (!appEvents.has(name)) appEvents.set(name, new Set()); appEvents.get(name).add(fn); }, off: (name, fn) => appEvents.get(name)?.delete(fn), trigger: name => appEvents.get(name)?.forEach(fn => fn())}, stopPullDownRefresh() {}, showToast() {}, navigateTo: async ({url}) => { navigation = url; }, getCurrentInstance: () => ({ router: { params: { id: String(args.oldest) } } }),
  request: options => {
    const promise = fetch(options.url, {method: options.method, headers: options.header, ...(options.data === undefined ? {} : {body: JSON.stringify(options.data)})})
      .then(async response => {
        const result = {statusCode: response.status, data: await response.json(), header: {}};
        if (detailGate && options.url.endsWith(`/messages/${args.oldest}/`)) { const gate = detailGate; detailGate = null; gate.arrived(); await gate.release; }
        return result;
      });
    pending.add(promise); promise.finally(() => pending.delete(promise)); return promise;
  },
};
const react = {
  createElement: (type, props, ...children) => ({type, props: {...props, children}}),
  useState: initial => { const i=current.cursor++, owner=current; if (!(i in owner.values)) owner.values[i] = typeof initial === 'function' ? initial() : initial; return [owner.values[i], next => {owner.values[i] = typeof next === 'function' ? next(owner.values[i]) : next; owner.dirty = true;}]; },
  useRef: initial => {const i=current.cursor++; if (!(i in current.values)) current.values[i]={current:initial}; return current.values[i];},
  useCallback: (fn, deps) => {const i=current.cursor++, old=current.values[i]; if (!old || deps.some((v,j) => v !== old.deps[j])) current.values[i]={fn,deps}; return current.values[i].fn;},
  useEffect: (fn, deps) => {const i=current.cursor++, old=current.effects[i]; if (!old || deps.some((v,j) => v !== old[j])) {current.effects[i]=deps; current.queue.push(fn);}},
};
const styles = new Proxy({}, {get: (_, name) => name});
const deps = {
  react: {__esModule:true, default:react, ...react}, '@tarojs/components': {Button:'Button', Text:'Text', View:'View'},
  '@tarojs/taro': {__esModule:true, default:taro, useDidShow: fn => {current.didShow=fn;}, useDidHide: fn => {current.didHide=fn;}, usePullDownRefresh: fn => {current.pull=fn;}},
  '@/services/websocket': {wsService:{on: (name, fn) => {if (!socketEvents.has(name)) socketEvents.set(name,new Set()); socketEvents.get(name).add(fn); return () => socketEvents.get(name)?.delete(fn);}}},
  '@/utils/env': {BASE_URL:args.base + '/api'}, './env': {BASE_URL:args.base + '/api'},
  './orderNetwork': {scopeFor: () => '', cooldownRemaining: () => 0, recordOrderRequest() {}},
  './orderAlerts': {orderAlerts: {}},
  '@/components/PlatformLayout': {default:'PlatformLayout'}, '@/components/PageHeading': {default:'PageHeading'}, '@/components/PageNav': {default:'PageNav'}, '@/components': {Empty:'Empty'},
  '@/utils/orderNavigation': {clearOrderFocus(){}, queueOrderFocus(){}, queueOrderPool(){}}, '@/utils/reportNavigation': {clearReportOrder(){}, queueReportOrder(){}},
  './index.module.scss': {default:styles},
};
function load(name, file) {const module={exports:{}}; vm.runInNewContext(compile(file), {module, exports:module.exports, require:id => {if (!(id in deps)) throw Error('Missing import '+id); return deps[id];}, Number, Date, Error, Promise, Set, setTimeout, clearTimeout}); deps[name]=module.exports; return module.exports;}
const auth = load('@/utils/auth', 'src/utils/auth.ts'); deps['./auth']=auth;
load('@/utils/coalescedRefresh', 'src/utils/coalescedRefresh.ts');
load('@/utils/request', 'src/utils/request.ts');
const service = load('@/services/message', 'src/services/message.ts');
const Page = load('page', 'src/pages/messages/index.tsx').default;
const page={values:[], effects:[], queue:[], cursor:0, dirty:true};
function render() {current=page; page.cursor=0; page.dirty=false; page.tree=Page(); for (const effect of page.queue.splice(0)) effect();}
async function settle() {for(let i=0;i<60;i++) {if(page.dirty) render(); if(pending.size) await Promise.all([...pending]); await new Promise(resolve=>setImmediate(resolve)); if(!page.dirty && !pending.size) return;} throw Error('page did not settle');}
function nodes(value,out=[]) {if(Array.isArray(value)) value.forEach(x=>nodes(x,out)); else if(value && typeof value==='object') {out.push(value); nodes(value.props?.children,out);} return out;}
function text(value) {return Array.isArray(value)?value.map(text).join(''):value && typeof value==='object'?text(value.props.children):value == null || value===false?'':String(value);}
function button(label) {return nodes(page.tree).find(n=>n.type==='Button' && text(n)===label);}
function item(title) {return nodes(page.tree).find(n=>n.type==='View' && n.props.className==='item' && text(n).includes(title));}
async function show() {current=page; page.didShow(); await settle();}
async function main() {
  auth.persistLoginData({token:args.firstToken,userInfo:{id:'1',username:'first'}});
  await settle();
  assert.ok(button('加载更早消息')); assert.equal(item('旧消息 0'), undefined);
  await button('加载更早消息').props.onClick(); await settle();
  const old = item('旧消息 0'); assert.ok(old);
  old.props.onClick(); assert.match(navigation, new RegExp(`id=${args.oldest}$`));
  const detail=await service.fetchMessageDetail(args.oldest); assert.equal(detail.code,0);
  const sent=await fetch(args.base+'/api/admin/messages/push/', {method:'POST',headers:{Authorization:'Bearer '+args.staffToken,'Content-Type':'application/json'},body:JSON.stringify({audience:'CUSTOM',recipient_account_ids:[args.firstAccount],title:'返回前的新消息'})});
  assert.equal((await sent.json()).code,0);
  await show();
  assert.ok(item('返回前的新消息')); assert.ok(item('旧消息 0'));
  assert.equal(nodes(item('旧消息 0')).some(n=>n.props.className==='dot'),false);
  assert.equal(nodes(page.tree).find(n=>n.type==='PageHeading').props.hint,'65 条消息等待查看');
  const push = async title => {
    const response = await fetch(args.base+'/api/admin/messages/push/', {method:'POST',headers:{Authorization:'Bearer '+args.staffToken,'Content-Type':'application/json'},body:JSON.stringify({audience:'CUSTOM',recipient_account_ids:[args.firstAccount],title})});
    assert.equal((await response.json()).code,0);
  };
  await push('页面停留时的新消息');
  socketEvents.get('site_message_update')?.forEach(fn => fn({}));
  await new Promise(resolve => setTimeout(resolve, 600)); await settle();
  assert.ok(item('页面停留时的新消息')); assert.ok(item('旧消息 0'));
  assert.equal(nodes(page.tree).find(n=>n.type==='PageHeading').props.hint,'66 条消息等待查看');
  page.didHide();
  await push('离线期间的新消息');
  socketEvents.get('site_message_update')?.forEach(fn => fn({}));
  await new Promise(resolve => setTimeout(resolve, 600)); await settle();
  assert.equal(item('离线期间的新消息'),undefined);
  await show(); assert.ok(item('离线期间的新消息')); assert.ok(item('旧消息 0'));
  assert.equal(nodes(page.tree).find(n=>n.type==='PageHeading').props.hint,'67 条消息等待查看');
  await push('断线期间的新消息');
  socketEvents.get('connected')?.forEach(fn => fn());
  await new Promise(resolve => setTimeout(resolve, 600)); await settle();
  assert.ok(item('断线期间的新消息')); assert.ok(item('旧消息 0'));
  assert.equal(nodes(page.tree).find(n=>n.type==='PageHeading').props.hint,'68 条消息等待查看');
  await nodes(page.tree).find(n=>n.type==='PageHeading').props.action.props.onClick(); await settle();
  assert.ok(item('旧消息 0')); assert.equal(nodes(page.tree).find(n=>n.type==='PageHeading').props.hint,'所有消息均已阅读');
  auth.clearStoredUser(); auth.persistLoginData({token:args.secondToken,userInfo:{id:'2',username:'second'}});
  await show(); assert.equal(item('旧消息 0'),undefined); assert.equal(item('返回前的新消息'),undefined); assert.ok(item('另一账号消息'));
  // The detail remains mounted when a provider signs out or changes account.
  // It must retire previously rendered content, not expose it to the next user.
  auth.clearStoredUser(); auth.persistLoginData({token:args.firstToken,userInfo:{id:'1',username:'first'}});
  const Detail = load('detail', 'src/pages/message-detail/index.tsx').default;
  const detailPage={values:[], effects:[], queue:[], cursor:0, dirty:true};
  const renderDetail = () => {current=detailPage; detailPage.cursor=0; detailPage.dirty=false; detailPage.tree=Detail(); for (const effect of detailPage.queue.splice(0)) effect();};
  renderDetail(); if (pending.size) await Promise.all([...pending]); await new Promise(resolve=>setImmediate(resolve)); renderDetail();
  assert.match(text(detailPage.tree), /旧消息 0/);
  auth.clearStoredUser(); auth.persistLoginData({token:args.secondToken,userInfo:{id:'2',username:'second'}});
  if (pending.size) await Promise.all([...pending]); await new Promise(resolve=>setImmediate(resolve)); renderDetail();
  assert.doesNotMatch(text(detailPage.tree), /旧消息 0/, 'account switch must remove old message detail');
  auth.clearStoredUser(); auth.persistLoginData({token:args.firstToken,userInfo:{id:'1',username:'first'}});
  const delayedPage={values:[], effects:[], queue:[], cursor:0, dirty:true};
  let releaseDetail, detailArrived;
  const arrived = new Promise(resolve => {detailArrived = resolve;});
  detailGate = {arrived: detailArrived, release: new Promise(resolve => {releaseDetail = resolve;})};
  const renderDelayed = () => {current=delayedPage; delayedPage.cursor=0; delayedPage.dirty=false; delayedPage.tree=Detail(); for (const effect of delayedPage.queue.splice(0)) effect();};
  renderDelayed(); await arrived;
  auth.clearStoredUser(); auth.persistLoginData({token:args.secondToken,userInfo:{id:'2',username:'second'}});
  releaseDetail(); if (pending.size) await Promise.all([...pending]); await new Promise(resolve=>setImmediate(resolve)); renderDelayed();
  assert.doesNotMatch(text(delayedPage.tree), /旧消息 0/, 'late response must not restore old session content');
  assert.match(text(delayedPage.tree), /登录已变化/);
  console.log(JSON.stringify({preserved:65, accountIsolation:true, detailAccountIsolation:true, readAll:true, liveRefresh:true, resumeRefresh:true, reconnectRefresh:true}));
}
main().catch(error=>{console.error(error);process.exitCode=1;});
