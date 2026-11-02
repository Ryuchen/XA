// These tests exercise response/session timing only; coupon behavior is verified via the real backend.
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm')
const {createRequire}=require('node:module'),adminRequire=createRequire(require('node:path').resolve('apps/admin-web/package.json'))
const vue=adminRequire('vue'),ts=adminRequire('typescript')
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}}
function setup(){
  const scope=vue.effectScope(),requests=[]
  const auth=vue.reactive({sessionRevision:1,hasPerm:()=>true})
  const mocks={vue,'@/composables/useConfirmDialog':{useConfirmDialog:()=>({open:vue.ref(false),options:vue.ref({}),ask(){const d=deferred();return d.promise.then(()=>true)},confirm(){},cancel(){}})},
    '@/api/modules':{couponApi:{},userCouponApi:{list(params){const d=deferred();requests.push({params,d});return d.promise}}},
    '@/utils/dict':{COUPON_TYPE:{}},'@/utils/format':{amountToXaCoin:String,xaCoinToAmount:Number,formatDateTime:String},'@/stores/auth':{useAuthStore:()=>auth}}
  const source=fs.readFileSync('apps/admin-web/src/views/coupon/index.vue','utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  const code=ts.transpileModule(source+'\nglobalThis.page={onClaims,fetchClaims,claimsVisible,claimsCoupon,claimsIncompatible};',{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020}}).outputText
  const context={exports:{},require(name){assert.ok(mocks[name],name);return mocks[name]}}
  scope.run(()=>vm.runInNewContext(code,context));return {page:context.page,auth,requests,dispose(){scope.stop()}}
}
test('an older service response cannot present misleading claim statuses',async()=>{
  const s=setup();s.page.onClaims({id:1});const pending=s.page.fetchClaims({coupon:1})
  s.requests[0].d.resolve({data:{list:[],total:0}});await assert.rejects(pending);assert.equal(s.page.claimsIncompatible.value,true);s.dispose()
})
test('late responses from another coupon or session cannot alter current claim state',async()=>{
  const s=setup();s.page.onClaims({id:1});const old=s.page.fetchClaims({coupon:1});s.page.onClaims({id:2})
  s.requests[0].d.resolve({data:{list:[],total:0}});await assert.rejects(old);assert.equal(s.page.claimsIncompatible.value,false)
  const next=s.page.fetchClaims({coupon:2});s.auth.sessionRevision++;s.requests[1].d.resolve({data:{claim_status_version:1,list:[],total:0}})
  await assert.rejects(next);assert.equal(s.page.claimsVisible.value,false);assert.equal(s.page.claimsCoupon.value,null);s.dispose()
})
