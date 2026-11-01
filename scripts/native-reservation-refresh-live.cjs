// Real native page + HTTP; wx and delivery of the existing event are host adapters.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict');
const args = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native'), storage = new Map();
let modal;
global.wx = {
  getStorageSync: k => storage.get(k), setStorageSync: (k,v) => storage.set(k,v), removeStorageSync: k => storage.delete(k),
  getStorageInfoSync: () => ({keys:[...storage.keys()]}), getAccountInfoSync: () => ({miniProgram:{envVersion:'develop'}}),
  showToast() {}, showModal: options => { modal = options; }, stopPullDownRefresh() {},
  request: options => { const url = new URL(options.url); fetch(args.base + url.pathname + url.search, {method:options.method, headers:options.header,
    ...(options.data === undefined ? {} : {body:JSON.stringify(options.data)})}).then(async r => options.success({statusCode:r.status,data:await r.json()})).catch(options.fail); },
};
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function waitFor(test) {for(let i=0;i<200;i++){if(test()) return; await delay(20);} throw Error('native page did not settle');}
async function action(name, id=args.id) {const r = await fetch(`${args.base}/api/orders/reservations/${id}/${name}/`, {method:'POST', headers:{Authorization:'Bearer '+args.token,'Content-Type':'application/json'}, body:JSON.stringify({reason:'陪玩改期'})}); const data = await r.json(); assert.equal(data.code,0,JSON.stringify(data));}
async function main() {
  const auth = require(path.join(root,'core/auth')), events = require(path.join(root,'core/events'));
  auth.setSession({token:args.bossToken,refresh:args.refresh,user:{id:String(args.account),role:'customer'}});
  let def; global.Page = value => {def = value;}; require(path.join(root,'pages/reservation-detail/index.js'));
  const page = {...def,data:structuredClone(def.data),setData(patch){Object.assign(this.data,patch);}};
  for(const [key,value] of Object.entries(page)) if(typeof value === 'function') page[key] = value.bind(page);
  page.onLoad({id:String(args.id)}); page.onShow(); await waitFor(()=>!page.data.loading && page.data.reservation);
  assert.equal(page.data.reservation.status,'PENDING');
  await action('confirm'); events.emit('reservation.changed', {reservation_id: args.id}); await waitFor(()=>!page.data.loading && page.data.reservation.status==='CONFIRMED');
  await page.quote(); assert.ok(page.data.quote, page.data.quoteError); const quote = page.data.quote;
  events.emit('reservation.changed', {reservation_id: args.id}); await waitFor(()=>!page.data.loading); assert.equal(page.data.quote,quote);
  const operation = page.cancel(); await waitFor(()=>modal);
  await action('cancel'); events.emit('reservation.changed', {reservation_id: args.id}); await delay(60);
  assert.equal(page.data.reservation.status,'CONFIRMED'); assert.equal(page.data.quote,quote);
  modal.success({confirm:false}); await operation; await waitFor(()=>!page.data.loading && page.data.reservation.status==='CANCELLED');
  assert.equal(page.data.quote,null); assert.equal(page.data.reservation.cancel_reason,'陪玩改期');
  page.onHide(); events.emit('reservation.changed', {reservation_id: args.id}); page.onShow(); await waitFor(()=>!page.data.loading); assert.equal(page.data.reservation.status,'CANCELLED');
  page.onUnload();
  require(path.join(root,'pages/reservations/index.js'));
  const list = {...def,data:structuredClone(def.data),setData(patch){Object.assign(this.data,patch);}};
  for(const [key,value] of Object.entries(list)) if(typeof value === 'function') list[key] = value.bind(list);
  list.onLoad({}); list.onShow(); await waitFor(()=>!list.data.loading && list.data.items.length);
  assert.equal(list.data.items.find(row=>row.id===args.listId).status,'PENDING');
  await action('reject',args.listId); events.emit('reservation.changed', {reservation_id: args.listId});
  await waitFor(()=>!list.data.loading && list.data.items.find(row=>row.id===args.listId)?.status==='REJECTED');
  list.onUnload();
  console.log('Boss: confirmation, retained quote, deferred cancellation event and foreground refresh passed');
}
main().catch(error=>{console.error(error);process.exitCode=1;});
