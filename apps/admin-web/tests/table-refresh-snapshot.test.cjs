// Transport is controlled only to exercise overlapping reads and pagination timing.
// The order workflow itself is verified separately against the isolated real API.
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createRequire } = require('node:module')

const root = path.resolve(__dirname, '..')
const req = createRequire(path.join(root, 'package.json'))
const ts = req('typescript')
const { ref } = req('vue')
const source = fs.readFileSync(path.join(root, 'src/components/CrudTable.vue'), 'utf8').match(/<script setup[^>]*>([\s\S]*?)<\/script>/)[1]
const ast = ts.createSourceFile('table.ts', source, ts.ScriptTarget.Latest, true)
const functions = ast.statements.filter(s => ts.isFunctionDeclaration(s) && ['runLoad', 'load', 'refresh'].includes(s.name?.text)).map(s => s.getText(ast)).join('\n')
const script = ts.transpileModule(`${functions}\nmodule.exports = { load, refresh }`, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
function harness() {
  const requests = [], module = { exports: {} }
  const props = { query: { customer_username: 'applied' }, fetcher(params) { const d = deferred(); requests.push({ params, ...d }); return d.promise } }
  const rows = ref([]), total = ref(0), page = ref(1), pageSize = ref(10), loading = ref(false), loadFailed = ref(false)
  vm.runInNewContext(`let loadSequence = 0; let lastRequestedQuery = null; ${script}`, { module, props, rows, total, page, pageSize, loading, loadFailed, clearSelection() {} })
  return { ...module.exports, props, requests, rows, page, loading, loadFailed }
}

test('background refresh preserves the applied filter and ignores an older response', async () => {
  const h = harness()
  const first = h.load()
  h.props.query.customer_username = 'draft'
  const second = h.refresh()
  assert.equal(h.requests[1].params.customer_username, 'applied')
  h.requests[1].resolve({ data: { list: [{ id: 2 }], total: 1 } })
  await second
  h.requests[0].resolve({ data: { list: [{ id: 1 }], total: 1 } })
  await first
  assert.equal(h.rows.value[0].id, 2)
  const apply = h.load()
  assert.equal(h.requests[2].params.customer_username, 'draft')
  h.requests[2].resolve({ data: { list: [], total: 0 } })
  await apply
})

test('missing last page retries page one using the same applied filter', async () => {
  const h = harness()
  h.page.value = 2
  const initial = h.load()
  h.requests[0].resolve({ data: { list: [{ id: 3 }], total: 11 } })
  await initial
  h.props.query.customer_username = 'draft'
  const refresh = h.refresh()
  h.requests[1].reject({ response: { status: 404 } })
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(h.requests[2].params.customer_username, 'applied')
  assert.equal(h.requests[2].params.page, 1)
  h.requests[2].resolve({ data: { list: [{ id: 4 }], total: 10 } })
  await refresh
  assert.equal(h.page.value, 1)
  assert.equal(h.rows.value[0].id, 4)
  assert.equal(h.loadFailed.value, false)
})

test('retry after a failed read preserves the submitted filter while the user edits a draft', async () => {
  const h = harness()
  const first = h.load()
  h.requests[0].reject({ response: { status: 503 } })
  await first
  assert.equal(h.loadFailed.value, true)
  h.props.query.customer_username = 'draft'
  const retry = h.refresh()
  assert.equal(h.requests[1].params.customer_username, 'applied')
  h.requests[1].resolve({ data: { list: [{ id: 5 }], total: 1 } })
  await retry
  assert.equal(h.rows.value[0].id, 5)
  assert.equal(h.props.query.customer_username, 'draft')
  assert.equal(h.loadFailed.value, false)
})
