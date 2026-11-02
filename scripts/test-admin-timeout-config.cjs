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

function setup(permissions = ['order:view', 'order:timeout']) {
  const scope = vue.effectScope(), mounted = [], unmounted = [], calls = [], successes = [], pending = [], confirmations = []
  const auth = vue.reactive({ token: 'one', sessionRevision: 1, permissions,
    hasPerm: code => auth.permissions.includes(code) })
  const mocks = {
    vue: { ...vue, onMounted: fn => mounted.push(fn), onUnmounted: fn => unmounted.push(fn) },
    'element-plus': { ElMessage: { success: value => successes.push(value), warning() {} }, ElMessageBox: { confirm: () => confirmations.shift().promise } },
    'vue-router': { onBeforeRouteLeave() {} },
    '@/stores/auth': { useAuthStore: () => auth },
    '@/api/request': { request: options => { calls.push(options); return pending.shift().promise } },
    './TimeoutHistory.vue': {},
  }
  const source = fs.readFileSync('apps/admin-web/src/views/order/TimeoutConfig.vue', 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  const code = ts.transpileModule(source + '\nglobalThis.page={minutes,revision,loaded,loading,saving,requiresRefresh,error,canSave,load,save,historyRef,confirmClose};',
    { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
  const context = { exports: {}, defineExpose() {}, defineEmits: () => () => {}, window: { addEventListener() {}, removeEventListener() {} }, require(name) { assert.ok(Object.hasOwn(mocks, name), name); return mocks[name] } }
  scope.run(() => vm.runInNewContext(code, context))
  return { page: context.page, auth, calls, successes, pending, confirmations,
    mount() { mounted.forEach(fn => fn()) },
    dispose() { unmounted.forEach(fn => fn()); scope.stop() } }
}

test('closing protects changed settings and blocks during saving', async () => {
  const s = setup(), get = deferred(); s.pending.push(get); s.mount()
  get.resolve({ data: { minutes: 30, revision: 0 } }); await tick()
  assert.equal(await s.page.confirmClose(), true)
  s.page.minutes.value = 45
  const cancel = deferred(); s.confirmations.push(cancel)
  const closing = s.page.confirmClose(); cancel.reject(new Error('cancel')); assert.equal(await closing, false)
  assert.equal(s.page.minutes.value, 45)
  const confirm = deferred(); s.confirmations.push(confirm)
  const leaving = s.page.confirmClose(); confirm.resolve(); assert.equal(await leaving, true)
  const put = deferred(); s.pending.push(put); const saving = s.page.save()
  assert.equal(await s.page.confirmClose(), false)
  put.resolve({ data: { minutes: 45, revision: 1 } }); await saving
  s.dispose()
})

test('read-only permission loads settings but cannot save', async () => {
  const s = setup(['order:view']), get = deferred(); s.pending.push(get); s.mount()
  get.resolve({ data: { minutes: 30, revision: 0 } }); await tick()
  s.page.minutes.value = 20
  assert.equal(s.page.canSave.value, false)
  await s.page.save(); assert.equal(s.calls.length, 1); assert.equal(s.successes.length, 0)
  s.dispose()
})

test('only valid changed values save and a confirmed response advances the version', async () => {
  const s = setup(), get = deferred(); s.pending.push(get); s.mount()
  get.resolve({ data: { minutes: 30, revision: 0 } }); await tick()
  for (const invalid of [0, 1441, 1.5, undefined, 30]) { s.page.minutes.value = invalid; assert.equal(s.page.canSave.value, false) }
  let historyRefreshes = 0; s.page.historyRef.value = { reload: () => { historyRefreshes++ } }
  const put = deferred(); s.pending.push(put); s.page.minutes.value = 60
  const save = s.page.save(); assert.equal(s.calls[1].data.minutes, 60); assert.equal(s.calls[1].data.revision, 0)
  assert.equal(s.page.canSave.value, false)
  put.resolve({ data: { minutes: 60, revision: 1 } }); await save
  assert.equal(s.page.revision.value, 1); assert.equal(s.successes.length, 1); assert.equal(s.page.canSave.value, false)
  assert.equal(historyRefreshes, 1)
  s.dispose()
})

test('conflict and ambiguous failure never report success and require refresh', async () => {
  for (const error of [{ response: { status: 409 } }, new Error('network')]) {
    const s = setup(), get = deferred(); s.pending.push(get); s.mount()
    get.resolve({ data: { minutes: 30, revision: 0 } }); await tick()
    const put = deferred(); s.pending.push(put); s.page.minutes.value = 45
    const save = s.page.save(); put.reject(error); await save
    assert.equal(s.successes.length, 0); assert.equal(s.page.requiresRefresh.value, true); assert.equal(s.page.canSave.value, false)
    assert.match(s.page.error.value, /刷新/)
    const reload = deferred(); s.pending.push(reload); const load = s.page.load()
    reload.resolve({ data: { minutes: 35, revision: 1 } }); await load
    assert.equal(s.page.minutes.value, 35); assert.equal(s.page.requiresRefresh.value, false)
    s.dispose()
  }
})

test('session or permission changes discard delayed save responses', async () => {
  const s = setup(), get = deferred(); s.pending.push(get); s.mount()
  get.resolve({ data: { minutes: 30, revision: 0 } }); await tick()
  const put = deferred(); s.pending.push(put); s.page.minutes.value = 45
  const save = s.page.save()
  s.auth.permissions = []
  put.resolve({ data: { minutes: 45, revision: 1 } }); await save
  assert.equal(s.successes.length, 0); assert.equal(s.page.loaded.value, false); assert.equal(s.page.saving.value, false)
  s.dispose()
})
