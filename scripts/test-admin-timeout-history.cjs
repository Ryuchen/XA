const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')
const { createRequire } = require('node:module')
const adminRequire = createRequire(path.resolve('apps/admin-web/package.json'))
const ts = adminRequire('typescript'), vue = adminRequire('vue')
const tick = () => new Promise(resolve => setImmediate(resolve))
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }

function setup(permissions = ['audit:view']) {
  const scope = vue.effectScope(), mounted = [], unmounted = [], calls = [], pending = []
  const auth = vue.reactive({ token: 'one', sessionRevision: 1, permissions,
    hasPerm: code => auth.permissions.includes(code) })
  const mocks = {
    vue: { ...vue, onMounted: fn => mounted.push(fn), onUnmounted: fn => unmounted.push(fn) },
    '@/stores/auth': { useAuthStore: () => auth },
    '@/utils/format': { formatDateTime: value => value || '-' },
    '@/api/request': { request: options => { calls.push(options); return pending.shift().promise } },
  }
  const source = fs.readFileSync('apps/admin-web/src/views/order/TimeoutHistory.vue', 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  const code = ts.transpileModule(source + '\nglobalThis.page={rows,total,page,loading,error,load,reload};',
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
  const context = { exports: {}, defineExpose() {}, require(name) { assert.ok(Object.hasOwn(mocks, name), name); return mocks[name] } }
  scope.run(() => vm.runInNewContext(code, context))
  return { page: context.page, auth, calls, pending,
    mount() { mounted.forEach(fn => fn()) },
    dispose() { unmounted.forEach(fn => fn()); scope.stop() } }
}

test('without audit permission no history request is made', async () => {
  const s = setup([]); s.mount(); await s.page.load()
  assert.equal(s.calls.length, 0)
  assert.equal(s.page.rows.value.length, 0)
  s.dispose()
})

test('loads records and requests the selected page, then reloads from page one', async () => {
  const s = setup(), first = deferred(); s.pending.push(first); s.mount()
  assert.equal(s.calls[0].params.page, 1); assert.equal(s.calls[0].params.page_size, 10)
  first.resolve({ data: { list: [{ revision: 3, previous_minutes: 30, minutes: 7, operator_name: '客服', created_at: '2026-10-27' }], total: 11, page: 1, page_size: 10 } }); await tick()
  assert.equal(s.page.rows.value[0].previous_minutes, 30); assert.equal(s.page.total.value, 11)
  const second = deferred(); s.pending.push(second); s.page.page.value = 2
  const load = s.page.load(); assert.equal(s.calls[1].params.page, 2)
  second.resolve({ data: { list: [], total: 11, page: 2, page_size: 10 } }); await load
  assert.equal(s.page.rows.value.length, 0)
  const refresh = deferred(); s.pending.push(refresh); s.page.reload()
  assert.equal(s.page.page.value, 1); assert.equal(s.calls[2].params.page, 1)
  refresh.resolve({ data: { list: [], total: 0, page: 1, page_size: 10 } }); await tick()
  assert.equal(s.page.error.value, false); assert.equal(s.page.total.value, 0)
  s.dispose()
})

test('failure is distinct from empty history and can be retried', async () => {
  const s = setup(), first = deferred(); s.pending.push(first); s.mount()
  first.reject(new Error('offline')); await tick()
  assert.equal(s.page.error.value, true); assert.equal(s.page.rows.value.length, 0)
  const retry = deferred(); s.pending.push(retry); const load = s.page.load()
  retry.resolve({ data: { list: [], total: 0 } }); await load
  assert.equal(s.page.error.value, false); assert.equal(s.page.total.value, 0)
  s.dispose()
})

test('permission loss and later session changes discard late responses', async () => {
  const s = setup(), first = deferred(); s.pending.push(first); s.mount()
  s.auth.permissions = []
  first.resolve({ data: { list: [{ revision: 3 }], total: 1 } }); await tick()
  assert.equal(s.page.rows.value.length, 0); assert.equal(s.page.loading.value, false)
  const second = deferred(); s.pending.push(second); s.auth.permissions = ['audit:view']
  assert.equal(s.calls.length, 2)
  const third = deferred(); // A new session may read only after its own authorization.
  s.pending.push(third)
  s.auth.sessionRevision++
  second.resolve({ data: { list: [{ revision: 4 }], total: 1 } }); await tick()
  assert.equal(s.page.rows.value.length, 0)
  third.resolve({ data: { list: [{ revision: 5 }], total: 1 } }); await tick()
  assert.equal(s.page.rows.value[0].revision, 5)
  s.dispose()
})
