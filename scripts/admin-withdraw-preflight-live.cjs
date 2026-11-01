// Real backend HTTP; only the Vue/Element Plus host is replaced for testing.
const {readFileSync} = require('node:fs')
const {createRequire} = require('node:module')
const {resolve} = require('node:path')
const vm = require('node:vm')
const assert = require('node:assert/strict')
const req = createRequire(resolve('apps/admin-web/package.json'))
const ts = req('typescript'), vue = req('vue')
const file = readFileSync(resolve('apps/admin-web/src/views/withdraw/index.vue'), 'utf8')
const script = file.match(/<script setup[^>]*>([\s\S]*?)<\/script>/)[1]
const ast = ts.createSourceFile('withdraw.ts', script, ts.ScriptTarget.Latest, true)
const code = ts.transpileModule([
  ...ast.statements.filter(s => !ts.isImportDeclaration(s)).map(s => s.getText(ast)),
  'module.exports = { onApprove, onReject, onClaim, claimState, auditBusy, query, fetchWithdrawals, claimScopeUnsupported }',
].join('\n'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText

let input = ''
process.stdin.on('data', chunk => input += chunk)
process.stdin.on('end', async () => {
  const data = JSON.parse(input), prompts = [], notices = [], calls = []
  const auth = vue.reactive({token: data.token, profile:{id:1},sessionRevision:1,
    hasPerm: p => ['withdraw:view','withdraw:audit'].includes(p)})
  const module = {exports:{}}
  const withdrawApi = {
    async list(params) {
      const query = new URLSearchParams(params)
      const res = await fetch(`${data.base}/api/admin/withdrawals/?${query}`, {headers:{Authorization:`Bearer ${data.token}`}})
      const body = await res.json()
      if (!res.ok || body.code !== 0) throw new Error('list failed')
      return body
    },
    async retrieve(id) {
      calls.push(id)
      const res = await fetch(`${data.base}/api/admin/withdrawals/${id}/`,{headers:{Authorization:`Bearer ${data.token}`}})
      const body = await res.json()
      if (!res.ok || body.code !== 0) throw new Error('retrieve failed')
      return body
    },
    async action(id, action, payload) {
      assert.equal(action, 'claim', 'test prompt cancelled: no audit should be submitted')
      const res = await fetch(`${data.base}/api/admin/withdrawals/${id}/${action}/`, {
        method:'POST', headers:{Authorization:`Bearer ${data.token}`,'Content-Type':'application/json'}, body:JSON.stringify(payload),
      })
      const body = await res.json()
      if (!res.ok || body.code !== 0) { const e = new Error('claim rejected'); e.response = {status:res.status,data:body}; throw e }
      return body
    },
  }
  vm.runInNewContext(code,{module,exports:module.exports,computed:vue.computed,reactive:vue.reactive,ref:vue.ref,
    watch(){},onMounted(){},onBeforeUnmount(){},useAuthStore:()=>auth,useRoute:()=>({query:{status:'PENDING',claim_scope:'mine'}}),withdrawApi,
    WITHDRAW_STATUS:{PENDING:{},APPROVED:{},REJECTED:{}},PAYEE_METHOD:{},amountToXaCoin:v=>String(v/10),
    xaCoinToAmount:v=>v*10,formatDateTime:String,formatRmb:v=>(v/100).toFixed(2),
    ElMessage:{warning:m=>notices.push(m),error:m=>notices.push(m),success:m=>notices.push(m)},
    ElMessageBox:{prompt:async message=>{prompts.push(message);throw new Error('cancelled')},close(){}},
    sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},setTimeout(){return 1},clearTimeout(){},
  })
  const page = module.exports
  assert.equal(page.query.status, 'PENDING')
  assert.equal(page.query.claim_scope, 'mine')
  async function fetchScope() {
    const body = await page.fetchWithdrawals({status:page.query.status, claim_scope:page.query.claim_scope})
    assert.equal(body.data.claim_scope_applied, page.query.claim_scope)
    return body.data.list.map(row => row.id)
  }
  assert.deepEqual(await fetchScope(), [data.pending.id], 'my claimed queue matches real backend')
  page.query.claim_scope = 'unclaimed'
  assert.deepEqual(await fetchScope(), [data.unclaimed.id], 'unclaimed queue matches real backend')
  assert.equal(page.claimState(data.unclaimed), 'unclaimed')
  assert.equal(page.claimState(data.pending), 'mine')
  assert.equal(page.claimState({}), 'unknown')
  await page.onClaim(data.conflict)
  assert.match(notices.at(-1), /领取|处理/)
  const otherClaimed = (await withdrawApi.retrieve(data.conflict.id)).data
  assert.equal(page.claimState(otherClaimed), 'other')
  await page.onApprove(otherClaimed)
  assert.equal(prompts.length, 0, 'another claimant must not expose payout instructions')
  await page.onApprove({...otherClaimed, claim_is_mine:true})
  assert.equal(prompts.length, 0, 'a stale own-claim view must recheck the actual claimant')
  assert.match(notices.at(-1), /未由你领取/)
  await page.onClaim(data.unclaimed)
  assert.match(notices.at(-1), /已领取/)
  const newlyClaimed = (await withdrawApi.retrieve(data.unclaimed.id)).data
  assert.equal(page.claimState(newlyClaimed), 'mine')
  await page.onApprove(data.stale)
  assert.equal(prompts.length,0,'handled withdrawal must not show instructions to pay')
  assert.match(notices.at(-1),/已处理/)
  await page.onApprove({...data.pending,actual_amount:1,payee_name:'过期资料'})
  assert.equal(prompts.length,1,'pending withdrawal must still be reviewable')
  assert.match(prompts[0],/本地真实收款人/)
  assert.doesNotMatch(prompts[0],/过期资料/)
  assert.match(prompts[0],/196\.00/)
  assert.deepEqual(calls,[data.conflict.id,data.conflict.id,data.unclaimed.id,data.stale.id,data.pending.id])
  // An older server that ignores the new filter must not be presented as a filtered queue.
  withdrawApi.list = async () => ({data:{list:[data.pending],total:1}})
  await assert.rejects(page.fetchWithdrawals({status:'PENDING',claim_scope:'mine'}), /领取状态筛选未确认/)
  assert.equal(page.claimScopeUnsupported.value, true)
  console.log('PASS real HTTP: claim conflict, own claim, stale withdrawal and current payout snapshot')
})
