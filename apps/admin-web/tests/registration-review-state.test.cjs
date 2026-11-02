// Timing checks only. Registration and deposit behavior are verified through real backend tests.
const test = require('node:test'), assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm')
const { createRequire } = require('node:module')
const adminRequire = createRequire(require('node:path').resolve('apps/admin-web/package.json'))
const vue = adminRequire('vue'), ts = adminRequire('typescript'), money = require('../../../packages/money/src/index.js')
const tick = () => new Promise(resolve => setImmediate(resolve))
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => { resolve=a; reject=b }); return { promise, resolve, reject } }

function setup() {
  const scope=vue.effectScope(), unmounts=[], prompts=[], actions=[], reads=[], storage=new Map()
  const auth=vue.reactive({ sessionRevision:1, profile:{id:7}, permissions:['registration:view','registration:audit'], hasPerm(p){return this.permissions.includes(p)} })
  const application={id:1, username:'test_account', display_name:'测试', status:'PENDING', revision:1, deposit_mode:'CASH', deposit_required:20000}
  const api={ async retrieve(id) { reads.push(id); return {data:{...application}} }, async action(id,name,data,silent) { const request=deferred(); actions.push({id,name,data,silent,request}); return request.promise } }
  const mocks={ vue:{...vue,onBeforeUnmount:fn=>unmounts.push(fn)}, '@/composables/useConfirmDialog':{useConfirmDialog(){return {open:vue.ref(false),options:vue.ref({}),ask(){const d=deferred();prompts.push(d);return d.promise},confirm(){},cancel(){}}}},
    '@/components/CrudTable.vue':{}, '@/components/PrivateImage.vue':{}, '@/api/modules':{providerRegistrationApi:api,escortLevelApi:{allOptions:async()=>({data:{list:[{id:10,name:'等级'}]}})}},
    '@/stores/auth':{useAuthStore:()=>auth}, '@/utils/format':{formatDateTime:String}, '@xa/money':money }
  const source=fs.readFileSync('apps/admin-web/src/views/provider-registration/index.vue','utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  const code=ts.transpileModule(source+'\nglobalThis.page = { openDetail, reviewApplication, finishReview, review, visible, current, confirming, saving, reviewError, reviewNeedsReload, reviewMarker, markerChecked };', {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText
  const context={exports:{},require(name){assert.ok(mocks[name],name);return mocks[name]},sessionStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}}
  scope.run(()=>vm.runInNewContext(code,context))
  return {page:context.page, auth, application, prompts, actions, storage, reads, dispose(){unmounts.forEach(fn=>fn());scope.stop()}}
}
async function ready(s){await s.page.openDetail(1); Object.assign(s.page.review,{level_id:10,identity_confirmed:true,deposit_confirmed:true,deposit_yuan:200})}

test('review confirmation binds the original amount and ignores a changed session',async()=>{
  const s=setup(); await ready(s)
  const pending=s.page.reviewApplication(true); assert.equal(s.page.confirming.value,true)
  s.auth.sessionRevision++; s.prompts[0].resolve(true); await pending
  assert.equal(s.actions.length,0); s.dispose()
})
test('an uncertain response retains a marker until a fresh read and explicit acknowledgement',async()=>{
  const s=setup(); await ready(s)
  const pending=s.page.reviewApplication(true); s.page.review.deposit_yuan=300
  s.prompts[0].resolve(true); await tick(); assert.equal(s.actions[0].data.deposit_paid,20000)
  s.actions[0].request.reject({response:{status:503,data:{code:500}}}); await pending
  assert.equal(s.page.reviewMarker.value.id,1); assert.equal(s.page.markerChecked.value,false); assert.ok(s.page.reviewError.value)
  s.application.status='APPROVED'; await s.page.openDetail(1); assert.equal(s.page.markerChecked.value,true)
  const done=s.page.finishReview(); s.prompts[1].resolve(true); await done
  assert.equal(s.page.reviewMarker.value,null); assert.equal(s.storage.size,0); s.dispose()
})
test('a conflict keeps the form blocked until a fresh read',async()=>{
  const s=setup(); await ready(s)
  const pending=s.page.reviewApplication(true); s.prompts[0].resolve(true); await tick()
  s.actions[0].request.reject({response:{status:409,data:{code:409,msg:'已处理'}}}); await pending
  assert.equal(s.page.reviewMarker.value,null); assert.equal(s.page.reviewNeedsReload.value,true)
  await s.page.reviewApplication(true); assert.equal(s.actions.length,1)
  s.application.status='REJECTED'; await s.page.openDetail(1); assert.equal(s.page.current.value.status,'REJECTED'); s.dispose()
})
