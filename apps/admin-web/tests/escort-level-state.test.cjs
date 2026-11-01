// Timing and failure-state tests; relationship and deletion behavior use the real backend tests.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm')
const {createRequire}=require('node:module'),adminRequire=createRequire(require('node:path').resolve('apps/admin-web/package.json'))
const vue=adminRequire('vue'),ts=adminRequire('typescript')
const tick=()=>new Promise(resolve=>setImmediate(resolve))
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
function setup(){
  const scope=vue.effectScope(),mounts=[],unmounts=[],previews=[],deletes=[],retrieves=[],prompts=[],storage=new Map()
  const auth=vue.reactive({sessionRevision:1,profile:{id:7},permissions:['escort_level:view','escort_level:edit','escort_level:delete'],hasPerm(p){return this.permissions.includes(p)}})
  const api={deletionImpact(id){const d=deferred();previews.push({id,d});return d.promise},remove(id,silent){const d=deferred();deletes.push({id,silent,d});return d.promise},retrieve(id,silent){const d=deferred();retrieves.push({id,silent,d});return d.promise},list(){},create(){},update(){}}
  const mocks={vue:{...vue,onMounted:fn=>mounts.push(fn),onBeforeUnmount:fn=>unmounts.push(fn)},'element-plus':{ElMessage:{success(){},warning(){}},ElMessageBox:{confirm(){const d=deferred();prompts.push(d);return d.promise},close(){}}},
    '@/components/CrudTable.vue':{},'@/utils/qualifications':{qualificationOptions:async()=>({scopes:[],kinds:[],platforms:[]})},'@/api/modules':{escortLevelApi:api},'@/stores/auth':{useAuthStore:()=>auth}}
  const source=fs.readFileSync('apps/admin-web/src/views/escort-level/index.vue','utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  const code=ts.transpileModule(source+'\nglobalThis.page={tableRef,openDelete,readImpact,confirmDelete,checkDeletion,finishReview,impact,impactError,impactLoading,deleteVisible,deleting,marker,checked,checkResult};',{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText
  const context={exports:{},require(name){assert.ok(mocks[name],name);return mocks[name]},sessionStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}}
  scope.run(()=>vm.runInNewContext(code,context));context.page.tableRef.value={load:async()=>{}};mounts.forEach(fn=>fn())
  return {page:context.page,auth,previews,deletes,retrieves,prompts,storage,dispose(){unmounts.forEach(fn=>fn());scope.stop()}}
}
test('preview is required and old reads cannot survive a session change',async()=>{
  const s=setup();s.page.openDelete({id:1});await s.page.confirmDelete();assert.equal(s.deletes.length,0)
  s.auth.sessionRevision++;s.previews[0].d.resolve({data:{id:1,name:'旧等级',providers:1,services:1,registrations:0}});await tick()
  assert.equal(s.page.impact.value,null);assert.equal(s.page.deleteVisible.value,false);s.dispose()
})
test('failed preview can retry and an unknown delete requires a fresh read and acknowledgement',async()=>{
  const s=setup();s.page.openDelete({id:1});s.previews[0].d.reject(new Error('offline'));await tick();assert.ok(s.page.impactError.value)
  const retry=s.page.readImpact();s.previews[1].d.resolve({data:{id:1,name:'测试等级',providers:2,services:1,registrations:0}});await retry
  const pending=s.page.confirmDelete();assert.equal(s.deletes[0].silent,true);s.deletes[0].d.reject({response:{status:503}});await pending
  assert.equal(s.page.marker.value.id,1);assert.equal(s.storage.size,1);assert.equal(s.page.deleteVisible.value,false)
  const read=s.page.checkDeletion();s.retrieves[0].d.reject({response:{status:404}});await read;assert.equal(s.page.checked.value,true)
  const finish=s.page.finishReview();s.prompts[0].resolve();await finish;assert.equal(s.page.marker.value,null);assert.equal(s.storage.size,0);s.dispose()
})
