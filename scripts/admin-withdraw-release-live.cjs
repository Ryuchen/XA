// Real isolated HTTP; only the Vue/Element Plus host is replaced for testing.
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
  'module.exports = { onRelease, submitRelease, canRelease, claimState, releaseRow, releaseReason, releaseVisible, releaseConfirmed, releaseStale, releaseError, onApprove, onWithdrawalStatusUpdate, startWithdrawalRealtime }',
].join('\n'), {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
let input = ''
process.stdin.on('data', chunk => input += chunk)
process.stdin.on('end', async () => {
  const data = JSON.parse(input), notices = [], actions = []
  const perms = new Set(['withdraw:view', 'withdraw:release'])
  const auth = vue.reactive({token:data.adminToken,profile:{id:1},sessionRevision:1,hasPerm:p=>perms.has(p)})
  async function http(token, id, action, payload) {
    const response = await fetch(`${data.base}/api/admin/withdrawals/${id}/${action ? action + '/' : ''}`, {
      method:action?'POST':'GET', headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},
      ...(action ? {body:JSON.stringify(payload || {})} : {}),
    })
    const body=await response.json()
    if (!response.ok || body.code !== 0) {const error=new Error(body.msg || 'request rejected');error.response={status:response.status,data:body};throw error}
    return body
  }
  const withdrawApi={retrieve:(id)=>http(data.adminToken,id),action:(id,action,payload)=>{actions.push(action);return http(data.adminToken,id,action,payload)}}
  const module={exports:{}}, adminEvents={}
  vm.runInNewContext(code,{module,exports:module.exports,computed:vue.computed,reactive:vue.reactive,ref:vue.ref,
    watch(){},onMounted(){},onBeforeUnmount(){},useAuthStore:()=>auth,useRoute:()=>({query:{}}),withdrawApi,
    WITHDRAW_STATUS:{PENDING:{},APPROVED:{},REJECTED:{}},PAYEE_METHOD:{},amountToXaCoin:v=>String(v/10),
    xaCoinToAmount:v=>v*10,formatDateTime:String,formatRmb:v=>(v/100).toFixed(2),
    ElMessage:{warning:m=>notices.push(m),error:m=>notices.push(m),success:m=>notices.push(m)},
    ElMessageBox:{close(){}},adminWs:{on:(event, fn)=>(adminEvents[event]=fn,()=>{}),connect(){}},
    sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},setTimeout(){return 1},clearTimeout(){},
  })
  const page=module.exports, [first, second]=data.rows
  assert(page.canRelease(first))
  perms.delete('withdraw:release'); assert(!page.canRelease(first)); await page.onRelease(first)
  assert.equal(page.releaseVisible.value,false); perms.add('withdraw:release')
  await page.onRelease(first); assert.equal(page.releaseVisible.value,true)
  page.startWithdrawalRealtime(); adminEvents.connected()
  assert.equal(page.releaseStale.value,true, 'reconnect invalidates an open release confirmation')
  assert.match(page.releaseError.value,/连接已恢复.*重新核对/)
  await page.onRelease(first); assert.equal(page.releaseStale.value,false)
  page.releaseReason.value='领取人已离线，核实尚未打款'
  await page.submitRelease(); assert.deepEqual(actions,[], 'unchecked confirmation must not call release')
  page.releaseConfirmed.value=true; await page.submitRelease()
  assert.equal(page.releaseVisible.value,false); assert.match(notices.at(-1),/已释放/)
  const released=(await http(data.adminToken,first.id)).data
  assert.equal(page.claimState(released),'unclaimed')
  await http(data.staffToken,first.id,'claim')
  assert.equal((await http(data.adminToken,first.id)).data.claimed_by_account,first.claimed_by_account)

  // The claimant opens a real API-backed review prompt. A manager releases
  // that claim; delivering the corresponding event must close the stale
  // prompt and explain what happened without submitting a review.
  const staffNotices=[], staffActions=[], staffModule={exports:{}}
  const staffAuth=vue.reactive({token:data.staffToken,profile:{id:2},sessionRevision:1,
    hasPerm:p=>['withdraw:view','withdraw:audit'].includes(p)})
  let finishPrompt, promptStarted, promptClosed=false
  const promptReady=new Promise(resolve=>{promptStarted=resolve})
  const staffApi={retrieve:id=>http(data.staffToken,id),action:(id,action,payload)=>{staffActions.push({action,payload});return http(data.staffToken,id,action,payload)}}
  vm.runInNewContext(code,{module:staffModule,exports:staffModule.exports,computed:vue.computed,reactive:vue.reactive,ref:vue.ref,
    watch(){},onMounted(){},onBeforeUnmount(){},useAuthStore:()=>staffAuth,useRoute:()=>({query:{}}),withdrawApi:staffApi,
    WITHDRAW_STATUS:{PENDING:{},APPROVED:{},REJECTED:{}},PAYEE_METHOD:{},amountToXaCoin:v=>String(v/10),
    xaCoinToAmount:v=>v*10,formatDateTime:String,formatRmb:v=>(v/100).toFixed(2),
    ElMessage:{warning:m=>staffNotices.push(m),error:m=>staffNotices.push(m),success:m=>staffNotices.push(m)},
    ElMessageBox:{prompt:()=>{promptStarted();return new Promise(resolve=>{finishPrompt=resolve})},close(){promptClosed=true;const finish=finishPrompt;finishPrompt=null;finish(null)}},
    sessionStorage:{getItem:()=>null,setItem(){},removeItem(){}},setTimeout(){return 1},clearTimeout(){},
  })
  const staffPage=staffModule.exports, staffRow=(await http(data.staffToken,first.id)).data
  const pendingApproval=staffPage.onApprove(staffRow); await promptReady
  const oldClaim=(await http(data.adminToken,first.id)).data
  const actualRelease=(await http(data.adminToken,first.id,'release-claim',{
    confirmed_unpaid:true,reason:'隔离测试已核实未打款',
    expected_claimed_by_account:oldClaim.claimed_by_account,expected_claimed_at:oldClaim.claimed_at,
  })).data
  staffPage.onWithdrawalStatusUpdate({withdraw_id:actualRelease.id,account_id:data.ownerId,status:actualRelease.status})
  await pendingApproval
  assert.equal(promptClosed,true); assert.match(staffNotices.at(-1),/状态已变化.*核对凭证/)
  assert.deepEqual(staffActions,[], 'invalidated prompt must not submit a review')
  // If an update was missed, the old row must still be checked against the
  // actual API before showing any payout instruction.
  promptClosed=false
  await staffPage.onApprove(staffRow)
  assert.match(staffNotices.at(-1),/未由你领取/)
  assert.equal(promptClosed,false); assert.deepEqual(staffActions,[])
  await page.onRelease(second); assert.equal(page.releaseVisible.value,true)
  const old=page.releaseRow.value
  await http(data.adminToken,second.id,'release-claim',{confirmed_unpaid:true,reason:'隔离测试重新领取',expected_claimed_by_account:old.claimed_by_account,expected_claimed_at:old.claimed_at})
  await http(data.staffToken,second.id,'claim')
  page.releaseReason.value='核实未打款'; page.releaseConfirmed.value=true
  await page.submitRelease()
  assert.equal(page.releaseStale.value,true)
  assert.match(page.releaseError.value,/变化|核实/)
  assert.notEqual((await http(data.adminToken,second.id)).data.claimed_at,old.claimed_at)

  // The same account may operate two devices. An unseen release and later
  // re-claim must not let the earlier payout confirmation approve the new claim.
  const previous=(await http(data.staffToken,second.id)).data
  const staleApproval=staffPage.onApprove(previous)
  while (!finishPrompt) await new Promise(resolve=>setImmediate(resolve))
  const staleFinish=finishPrompt; finishPrompt=null
  await http(data.adminToken,second.id,'release-claim',{
    confirmed_unpaid:true,reason:'隔离测试确认未打款',
    expected_claimed_by_account:previous.claimed_by_account,expected_claimed_at:previous.claimed_at,
  })
  const renewed=(await http(data.staffToken,second.id,'claim')).data
  assert.notEqual(renewed.claimed_at,previous.claimed_at)
  staleFinish({value:'ISOLATED-REFERENCE-NO-PAYOUT'})
  await staleApproval
  assert.equal(staffActions.length,1); assert.equal(staffActions[0].action,'approve')
  assert.equal(staffActions[0].payload.expected_claimed_at,previous.claimed_at)
  assert.match(staffNotices.at(-1),/领取状态已变化/)
  assert.equal((await http(data.staffToken,second.id)).data.status,'PENDING')
  console.log('PASS real HTTP: permission, explicit confirmation, release, reclaim and stale version rejection')
})
