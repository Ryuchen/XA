// Response ordering only. Business data and permissions are verified against the real isolated API separately.
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createRequire } = require('node:module')
const root = path.resolve(__dirname, '..')
const requireApp = createRequire(path.join(root, 'package.json'))
const ts = requireApp('typescript'), vue = requireApp('vue')
const source = fs.readFileSync(path.join(root, 'src/views/transaction/index.vue'), 'utf8')
const script = source.match(/<script setup[^>]*>([\s\S]*?)<\/script>/)[1]
const ast = ts.createSourceFile('transaction.ts', script, ts.ScriptTarget.Latest, true)
const code = ts.transpileModule([
  ...ast.statements.filter(statement => !ts.isImportDeclaration(statement)).map(statement => statement.getText(ast)),
  'module.exports = { openOriginal, loadOriginal, originalVisible, originalLoading, originalFailed, originalId, original }',
].join('\n'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText

function harness() {
  const pending = [], stops = [], unmounts = []
  const auth = vue.reactive({ token: 'test', sessionRevision: 1, allowed: true, hasPerm: () => auth.allowed })
  const module = { exports: {} }
  vm.runInNewContext(code, {
    module, exports: module.exports, computed: vue.computed, reactive: vue.reactive, ref: vue.ref,
    watch(...args) { const stop = vue.watch(...args); stops.push(stop); return stop },
    onMounted() {}, onBeforeUnmount(fn) { unmounts.push(fn) },
    useAuthStore: () => auth, useRoute: () => vue.reactive({ query: {} }), useRouter: () => ({}),
    transactionApi: { retrieve(id) { return new Promise((resolve, reject) => pending.push({ id, resolve, reject })) } },
    adminWs: {}, setTimeout, clearTimeout,
  })
  return { ...module.exports, auth, pending, close: () => { unmounts.forEach(fn => fn()); stops.forEach(fn => fn()) } }
}
const tick = () => new Promise(resolve => setImmediate(resolve))

test('late original responses cannot replace a newer selection or a closed drawer', async t => {
  const page = harness(); t.after(page.close)
  page.openOriginal(10)
  page.openOriginal(11)
  page.pending[1].resolve({ data: { id: 11 } })
  await tick()
  page.pending[0].resolve({ data: { id: 10 } })
  await tick()
  assert.equal(page.original.value.id, 11)
  page.openOriginal(12)
  page.originalVisible.value = false
  page.pending[2].resolve({ data: { id: 12 } })
  await tick()
  assert.equal(page.original.value, null)
  assert.equal(page.originalId.value, null)
})

test('session or permission changes close the drawer and discard pending data', async t => {
  const page = harness(); t.after(page.close)
  page.openOriginal(10)
  page.auth.sessionRevision++
  page.pending[0].resolve({ data: { id: 10 } })
  await tick()
  assert.equal(page.originalVisible.value, false)
  assert.equal(page.original.value, null)
  page.openOriginal(11)
  page.auth.allowed = false
  page.pending[1].resolve({ data: { id: 11 } })
  await tick()
  assert.equal(page.originalVisible.value, false)
  assert.equal(page.original.value, null)
})
