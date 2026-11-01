// Read a real message and follow its order number to the real admin API.
const { readFileSync } = require('node:fs')
const { createRequire } = require('node:module')
const { resolve } = require('node:path')
const vm = require('node:vm')
const assert = require('node:assert/strict')
const req = createRequire(resolve('apps/admin-web/package.json'))
const ts = req('typescript'), vue = req('vue')
const fixture = JSON.parse(readFileSync(0, 'utf8'))
const source = readFileSync('apps/admin-web/src/views/chat/index.vue', 'utf8').match(/<script setup[^>]*>([\s\S]*?)<\/script>/)[1]
const ast = ts.createSourceFile('chat.ts', source, ts.ScriptTarget.Latest, true)
const script = [...ast.statements.filter(statement => !ts.isImportDeclaration(statement)).map(statement => statement.getText(ast)),
  'module.exports = { openSession, messages, orderNumbers, viewOrder }'].join('\n')
const code = ts.transpileModule(script, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const request = async (url, data) => {
  const response = await fetch(fixture.base + url, { method: data ? 'POST' : 'GET',
    headers: { Authorization: `Bearer ${fixture.staffToken}`, 'Content-Type': 'application/json' },
    body: data ? JSON.stringify(data) : undefined })
  const body = await response.json()
  assert.equal(body.code, 0, JSON.stringify(body))
  return body
}
const chatApi = { messages: id => request(`/api/admin/chat-sessions/${id}/messages/`),
  readIds: (id, ids) => request(`/api/admin/chat-sessions/${id}/read/`, { message_ids: ids }) }
const auth = { permitted: true, hasPerm: name => name === 'chat:reply' || (name === 'order:view' && auth.permitted) }
const routes = [], moduleState = { exports: {} }
vm.runInNewContext(code, { module: moduleState, exports: moduleState.exports, ref: vue.ref, watch: vue.watch, nextTick: vue.nextTick,
  onMounted() {}, onUnmounted() {}, useAuthStore: () => auth, useRouter: () => ({ push: route => routes.push(route) }),
  chatApi, formatDateTime: String, adminWs: {}, ElMessage: {}, Picture: {}, FormData, document: { hidden: false } })
;(async () => {
  const page = moduleState.exports
  await page.openSession({ id: fixture.sessionId, unread_support: 1 })
  const message = page.messages.value.find(row => row.content.includes(fixture.orderNo))
  assert.ok(message)
  const imageMessage = page.messages.value.find(row => row.content_type === 'IMAGE' && row.content.includes(fixture.orderNo))
  assert.ok(imageMessage && imageMessage.image_url, 'image message retains its order context')
  assert.deepEqual([...page.orderNumbers(imageMessage.content)], [fixture.orderNo])
  const numbers = [...page.orderNumbers(message.content)]
  assert.deepEqual(numbers, [fixture.orderNo])
  assert.equal(page.orderNumbers('ORD123 and no complete order number').length, 0)
  page.viewOrder(numbers[0])
  assert.equal(routes[0].path, '/orders')
  assert.equal(routes[0].query.find_order, fixture.orderNo)
  const orders = await request(`/api/admin/orders/?order_no_exact=${encodeURIComponent(routes[0].query.find_order)}`)
  assert.equal(orders.data.list.length, 1)
  assert.equal(orders.data.list[0].order_no, fixture.orderNo)
  auth.permitted = false
  page.viewOrder(numbers[0])
  assert.equal(routes.length, 1)
  console.log('PASS real chat order lookup')
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
