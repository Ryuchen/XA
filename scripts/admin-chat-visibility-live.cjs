// The page controller uses real HTTP; only socket delivery is relayed from the
// actual saved response so the visibility state can be exercised deterministically.
const {readFileSync} = require('node:fs')
const {createRequire} = require('node:module')
const {resolve} = require('node:path')
const vm = require('node:vm')
const assert = require('node:assert/strict')
const req = createRequire(resolve('apps/admin-web/package.json'))
const ts = req('typescript'), vue = req('vue')
const source = readFileSync(resolve('apps/admin-web/src/views/chat/index.vue'), 'utf8').match(/<script setup[^>]*>([\s\S]*?)<\/script>/)[1]
const ast = ts.createSourceFile('chat.ts', source, ts.ScriptTarget.Latest, true)
const code = ts.transpileModule([...ast.statements.filter(s => !ts.isImportDeclaration(s)).map(s => s.getText(ast)),
  'module.exports = { openSession, messages, current, input, sessions, onlyUnread, loadSessions, toggleUnread }'].join('\n'),
  {compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022}}).outputText
let input = ''
process.stdin.on('data', chunk => input += chunk)
process.stdin.on('end', () => { (async () => {
  const fixture = JSON.parse(input)
  const request = async (url, token, data) => {
    const response = await fetch(fixture.base + url, {method: data === undefined ? 'GET' : 'POST',
      headers: {Authorization: `Bearer ${token}`, 'Content-Type': 'application/json'},
      body: data === undefined ? undefined : JSON.stringify(data)})
    const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body)); return body
  }
  const base = `/api/admin/chat-sessions/${fixture.sessionId}/`
  const events = new Map(), documentEvents = new Map(), mounted = [], unmounted = []
  const document = {hidden: false, addEventListener: (name, fn) => documentEvents.set(name, fn),
    removeEventListener: name => documentEvents.delete(name)}
  const chatApi = {
    list: params => request('/api/admin/chat-sessions/?' + new URLSearchParams(params), fixture.firstStaffToken),
    messages: id => request(`/api/admin/chat-sessions/${id}/messages/`, fixture.firstStaffToken),
    readIds: (id, ids) => request(`/api/admin/chat-sessions/${id}/read/`, fixture.firstStaffToken, {message_ids: ids}),
  }
  const adminWs = {connect(){}, on(name, fn) {events.set(name, fn); return () => events.delete(name)}}
  const route = vue.ref({query: {only_unread: '1'}})
  const router = {currentRoute: route, push(){}, replace: async ({query}) => { route.value = {query} }}
  const module = {exports: {}}
  vm.runInNewContext(code, {module, exports: module.exports, ref: vue.ref, watch: vue.watch, nextTick: vue.nextTick,
    onMounted: fn => mounted.push(fn), onUnmounted: fn => unmounted.push(fn), attendedChatSession: vue.ref(null),
    useAuthStore: () => ({hasPerm: () => true}), useRouter: () => router,
    chatApi, formatDateTime: String, adminWs, ElMessage: {}, Picture: {}, FormData, document})
  const page = module.exports
  try {
    const first = (await request('/api/chat/send/', fixture.bossToken, {content: '订单咨询'})).data
    mounted.forEach(fn => fn())
    assert.equal(page.onlyUnread.value, true, 'workbench deep link opens unread filter')
    await page.loadSessions()
    assert.equal(page.sessions.value.length, 1)
    await page.openSession({id: fixture.sessionId, unread_support: 1})
    // Do not deliver the socket read event: the response itself must keep
    // the "unread" filter accurate when realtime delivery is delayed.
    assert.equal(page.sessions.value.length, 0, 'read session must leave unread filter without socket event')
    assert.equal((await request('/api/admin/chat-sessions/?only_unread=1', fixture.secondStaffToken)).data.total, 0)
    page.input.value = '尚未发送的草稿'
    document.hidden = true
    const unseen = (await request('/api/chat/send/', fixture.bossToken, {content: '后台期间的新消息'})).data
    events.get('chat_message')({session_id: fixture.sessionId, message: unseen})
    const pending = (await request('/api/admin/chat-sessions/?only_unread=1', fixture.secondStaffToken)).data
    assert.equal(pending.total, 1); assert.equal(pending.list[0].unread_support, 1)
    await request(base + 'reply/', fixture.secondStaffToken, {content: '客服回复', read_through_id: first.id})
    assert.equal((await request('/api/admin/chat-sessions/?only_unread=1', fixture.secondStaffToken)).data.list[0].unread_support, 1)
    document.hidden = false; documentEvents.get('visibilitychange')()
    for (let i = 0; i < 100; i++) {
      const result = (await request('/api/admin/chat-sessions/?only_unread=1', fixture.secondStaffToken)).data
      if (!result.total) break
      await new Promise(resolve => setTimeout(resolve, 20))
    }
    assert.equal((await request('/api/admin/chat-sessions/?only_unread=1', fixture.secondStaffToken)).data.total, 0)
    assert.equal(page.input.value, '尚未发送的草稿')
    assert.ok(page.messages.value.some(m => m.id === unseen.id))
    page.toggleUnread(); await vue.nextTick()
    assert.equal(route.value.query.only_unread, undefined, 'toggle synchronizes URL')
    route.value = {query: {only_unread: '1'}}; await vue.nextTick()
    assert.equal(page.onlyUnread.value, true, 'route navigation restores unread filter')
    const owner = (await request('/api/chat/session/', fixture.bossToken)).data
    assert.ok(owner.messages.some(m => m.content === '客服回复'))
    console.log('PASS real HTTP staff visibility')
  } finally { unmounted.forEach(fn => fn()) }
})().catch(error => {console.error(error.stack || error); process.exitCode = 1}) })
