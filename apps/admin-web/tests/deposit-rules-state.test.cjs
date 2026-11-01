// Request timing tests; the deposit calculation and configuration writes use real backend tests.
const test=require('node:test'), assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm')
const {createRequire}=require('node:module'), adminRequire=createRequire(require('node:path').resolve('apps/admin-web/package.json'))
const vue=adminRequire('vue'), ts=adminRequire('typescript'), money=require('../../../packages/money/src/index.js')
const tick=()=>new Promise(resolve=>setImmediate(resolve))
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
function setup(supported=true){
  const scope=vue.effectScope(), mounts=[], unmounts=[], prompts=[], calls=[], storage=new Map()
  const auth=vue.reactive({sessionRevision:1,profile:{id:7},permissions:['deposit_rule:view','deposit_rule:edit'],hasPerm(p){return this.permissions.includes(p)}})
  const state={cash_target:20000,earnings_target:40000,earnings_multiplier:2,deposit_percent:60,settlement_percent:40,hold_hours:0,conditional_write_supported:supported}
  const mocks={vue:{...vue,onMounted:fn=>mounts.push(fn),onBeforeUnmount:fn=>unmounts.push(fn)},'element-plus':{ElMessage:{success(){}},ElMessageBox:{confirm(){const d=deferred();prompts.push(d);return d.promise},close(){}}},
    '@/api/modules':{depositRulesApi:{get:async()=>({data:{...state}}),update(target,expected,silent){const d=deferred();calls.push({target,expected,silent,d});return d.promise}}},
    '@/stores/auth':{useAuthStore:()=>auth},'@xa/money':money}
  const source=fs.readFileSync('apps/admin-web/src/views/deposit-rules/index.vue','utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  const code=ts.transpileModule(source+'\nglobalThis.page={rules,percent,save,load,reloadRules,finishReview,marker,markerChecked,notice,needsReload,validPercent,loadError};',{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText
  const context={exports:{},require(name){assert.ok(mocks[name],name);return mocks[name]},sessionStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}}
  scope.run(()=>vm.runInNewContext(code,context));mounts.forEach(fn=>fn())
  return {page:context.page,auth,state,prompts,calls,storage,dispose(){unmounts.forEach(fn=>fn());scope.stop()}}
}
test('old server cannot save and empty ratio never behaves as zero',async()=>{
  const s=setup(false);await tick();s.page.percent.value=undefined;assert.equal(s.page.validPercent.value,false)
  s.page.percent.value=25;await s.page.save();assert.equal(s.prompts.length,0);s.dispose()
})
test('confirmation binds values and a session change blocks the old submit',async()=>{
  const s=setup();await tick();s.page.percent.value=25;const pending=s.page.save();s.auth.sessionRevision++;s.prompts[0].resolve();await pending
  assert.equal(s.calls.length,0);s.dispose()
})
test('stale configuration preserves draft until explicit reload',async()=>{
  const s=setup();await tick();s.page.percent.value=25;const pending=s.page.save();s.prompts[0].resolve();await tick()
  assert.deepEqual([s.calls[0].expected,s.calls[0].target,s.calls[0].silent],[60,25,true])
  s.calls[0].d.reject({response:{status:409,data:{code:409,msg:'已变化'}}});await pending
  assert.equal(s.page.needsReload.value,true);assert.equal(s.page.percent.value,25)
  s.state.deposit_percent=40;const reload=s.page.reloadRules();s.prompts[1].resolve();await reload
  assert.equal(s.page.percent.value,40);assert.equal(s.page.needsReload.value,false);s.dispose()
})
test('uncertain save needs a fresh read and explicit acknowledgement',async()=>{
  const s=setup();await tick();s.page.percent.value=100;const pending=s.page.save();s.prompts[0].resolve();await tick()
  s.calls[0].d.reject({response:{status:503,data:{code:500}}});await pending
  assert.equal(s.page.marker.value.target,100);assert.equal(s.page.markerChecked.value,false);assert.equal(s.storage.size,1)
  s.state.deposit_percent=100;const reload=s.page.reloadRules();s.prompts[1].resolve();await reload
  assert.equal(s.page.markerChecked.value,true);const finish=s.page.finishReview();s.prompts[2].resolve();await finish
  assert.equal(s.page.marker.value,null);assert.equal(s.storage.size,0);s.dispose()
})
