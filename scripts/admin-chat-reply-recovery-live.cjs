const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict')
const { createRequire } = require('node:module'), { resolve } = require('node:path')
const req = createRequire(resolve('apps/admin-web/package.json')), ts = req('typescript'), vue = req('vue')
const args = JSON.parse(fs.readFileSync(0, 'utf8'))
let token = args.tokens[0], drop = true, replyGate = null
const auth = vue.reactive({ profile: { id: args.staffIds[0] }, sessionRevision: 1, hasPerm: () => true })
const storage = new Map(), alerts = []
async function api(path, options = {}) {
  const response = await fetch(args.base + path, { ...options, headers: { Authorization: 'Bearer ' + token, ...options.headers } })
  const data = await response.json()
  if (!response.ok || data.code !== 0) { const error = new Error(data.msg || 'request failed'); error.response = { status: response.status, data }; throw error }
  return data
}
const chatApi = {
  list: () => api('/api/admin/chat-sessions/'),
  messages: id => api(`/api/admin/chat-sessions/${id}/messages/`),
  readIds: (id, ids) => api(`/api/admin/chat-sessions/${id}/read/`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message_ids:ids})}),
  reply: async (id, data) => { const result = await api(`/api/admin/chat-sessions/${id}/reply/`, {method:'POST',body:data}); if (replyGate) { const gate = replyGate; replyGate = null; gate.arrived(); await gate.release } if (drop) { drop = false; throw new Error('response lost') } return result },
  messageStatus: (id, key) => api(`/api/admin/chat-sessions/${id}/message-status/?client_message_id=${encodeURIComponent(key)}`),
}
let source = fs.readFileSync('apps/admin-web/src/views/chat/index.vue', 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
const ast = ts.createSourceFile('chat.ts', source, ts.ScriptTarget.Latest, true)
source = [...ast.statements.filter(x => !ts.isImportDeclaration(x)).map(x => x.getText(ast)),
  'module.exports={openSession,onSendText,onPickImage,checkPending,retryPending,pending,sending,input,current,messages}'].join('\n')
const code = ts.transpileModule(source, {compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
const moduleState = {exports:{}}
vm.runInNewContext(code, {module:moduleState,exports:moduleState.exports,ref:vue.ref,watch:vue.watch,nextTick:vue.nextTick,
  onMounted(){},onUnmounted(){},useAuthStore:()=>auth,useRouter:()=>({push(){}}),chatApi,formatDateTime:String,
  adminWs:{},ElMessage:{warning:x=>alerts.push(x),success:x=>alerts.push(x),error:x=>alerts.push(x)},
  ElMessageBox:{confirm:async()=>true},Picture:{},FormData,document:{hidden:false},
  sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
  setTimeout,clearTimeout})
const page = moduleState.exports
;(async()=>{
  await page.openSession({id:args.sessions[0],unread_support:0})
  page.input.value='第一位老板的未发送草稿'
  await page.openSession({id:args.sessions[1],unread_support:0})
  assert.equal(page.input.value,'', 'a different session must not inherit another customer\'s draft')
  page.input.value='第二位老板的未发送草稿'
  await page.openSession({id:args.sessions[0],unread_support:0})
  assert.equal(page.input.value,'第一位老板的未发送草稿', 'switching back must restore the unsent draft')
  await page.openSession({id:args.sessions[1],unread_support:0})
  assert.equal(page.input.value,'第二位老板的未发送草稿', 'each customer keeps their own draft')
  await page.openSession({id:args.sessions[0],unread_support:0})
  let releaseReply, replyArrived
  const replyReady = new Promise(resolve => { replyArrived = resolve })
  replyGate = { arrived: replyArrived, release: new Promise(resolve => { releaseReply = resolve }) }
  drop = false; page.input.value='延迟成功的回复'; page.onSendText(); await replyReady
  await page.openSession({id:args.sessions[1],unread_support:0})
  assert.equal(page.input.value,'第二位老板的未发送草稿')
  releaseReply()
  for(let i=0;i<100 && page.sending.value;i++) await new Promise(r=>setTimeout(r,20))
  assert.equal(page.sending.value,false)
  await page.openSession({id:args.sessions[0],unread_support:0})
  assert.equal(page.input.value,'', 'success after switching sessions clears only the sent draft')
  await page.openSession({id:args.sessions[1],unread_support:0})
  assert.equal(page.input.value,'第二位老板的未发送草稿', 'success must preserve the other customer draft')
  await page.openSession({id:args.sessions[0],unread_support:0}); drop = true
  auth.profile=null; page.input.value='身份未加载'; page.onSendText()
  assert.equal(page.pending.value,null, 'do not send with an unidentified staff account')
  assert.ok(alerts.some(value => value.includes('客服身份')))
  assert.equal(storage.size,0, 'do not use an empty account storage key')
  alerts.length=0; auth.profile={id:args.staffIds[0]}
  page.input.value='隔离真实回复'; page.onSendText()
  for(let i=0;i<100 && (!alerts.length || page.sending.value);i++) await new Promise(r=>setTimeout(r,20))
  assert.ok(page.pending.value, 'lost response keeps a pending reply')
  const key=page.pending.value.id
  await page.openSession({id:args.sessions[1],unread_support:0}); assert.equal(page.pending.value,null)
  await page.openSession({id:args.sessions[0],unread_support:0}); assert.equal(page.pending.value.id,key)
  token=args.tokens[1]; auth.profile={id:args.staffIds[1]}; auth.sessionRevision++; await vue.nextTick()
  await page.openSession({id:args.sessions[0],unread_support:0}); assert.equal(page.pending.value,null)
  assert.equal(page.input.value,'', 'switching staff accounts must discard previous account drafts')
  const other=await api(`/api/admin/chat-sessions/${args.sessions[0]}/message-status/?client_message_id=${key}`)
  assert.equal(other.data.status,'UNKNOWN')
  token=args.tokens[0]; auth.profile={id:args.staffIds[0]}; auth.sessionRevision++; await vue.nextTick()
  await page.openSession({id:args.sessions[0],unread_support:0}); assert.equal(page.pending.value.id,key)
  await page.checkPending(); assert.equal(page.pending.value,null)
  const messages=await chatApi.messages(args.sessions[0]); assert.equal(messages.data.list.filter(m=>m.content==='隔离真实回复').length,1)
  alerts.length=0; drop=true
  page.onPickImage(new File([Buffer.from(args.image,'base64')], 'staff-image.png', {type:'image/png'}))
  for(let i=0;i<100 && (!alerts.length || page.sending.value);i++) await new Promise(r=>setTimeout(r,20))
  assert.ok(page.pending.value?.image, 'lost image response preserves original file for same-page retry')
  await page.retryPending(); assert.equal(page.pending.value,null)
  const afterImage=await chatApi.messages(args.sessions[0]); assert.equal(afterImage.data.list.filter(m=>m.content_type==='IMAGE').length,1)
  console.log('PASS staff reply recovery')
})().catch(error=>{console.error(error.stack||error);process.exitCode=1})
