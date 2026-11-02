// Timing and UI state only; the companion API is verified with real order/refund flows separately.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { createRequire } = require('node:module')
const adminRequire = createRequire(require('node:path').resolve('apps/admin-web/package.json'))
const vue = adminRequire('vue'), ts = adminRequire('typescript')
const tick = () => new Promise(resolve => setImmediate(resolve))
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }
function response(value) {
  return { data: { snapshot_at: `time-${value}`, timezone: 'Asia/Shanghai',
    kpi: Object.fromEntries(['total_amount', 'game_amount', 'gift_amount', 'male_game', 'female_game', 'male_gift', 'female_gift'].map(k => [k, value])),
    secondary: Object.fromEntries(['total_deposit', 'total_penalty', 'total_reward', 'total_wallet', 'frozen_amount', 'settled_salary'].map(k => [k, value])),
    rank: { amount: [], income: [], count: [] }, income_review: { order_count: 0, provider_count: 0, unidentified_count: 0 } } }
}
function setup() {
  const scope = vue.effectScope(), mounts = [], unmounts = [], timers = new Map(), intervals = new Map(), handlers = new Map(), requests = []
  const doc = { hidden: false, listeners: new Map(), addEventListener(name, fn) { this.listeners.set(name, fn) }, removeEventListener(name) { this.listeners.delete(name) } }
  const auth = vue.reactive({ sessionRevision: 1, permissions: ['player_dashboard:view'], hasPerm(code) { return this.permissions.includes(code) } })
  let nextId = 0
  const modules = {
    vue: { ...vue, onMounted: fn => mounts.push(fn), onUnmounted: fn => unmounts.push(fn) },
    '@/components/EChart.vue': {},
    '@/api/modules': { playerDashboardApi: { stats(params, silent) { assert.equal(silent, true); const d = deferred(); requests.push({ ...d, params }); return d.promise } } },
    '@/stores/auth': { useAuthStore: () => auth }, '@/utils/format': { amountToXaCoin: String, formatDateTime: String },
    '@/utils/dict': { GENDER: {} }, '@/utils/ws': { adminWs: { connect() {}, on(type, fn) { handlers.set(type, fn); return () => handlers.delete(type) } } },
  }
  const source = fs.readFileSync('apps/admin-web/src/views/player-dashboard/index.vue', 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  const code = ts.transpileModule(source + '\nglobalThis.page = { data, loading, loadError, startDate, endDate, appliedRange, rangeLabel, snapshotText, query, retry };', { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
  const context = { exports: {}, document: doc, require(name) { assert.ok(modules[name], name); return modules[name] },
    setTimeout(fn) { timers.set(++nextId, fn); return nextId }, clearTimeout(id) { timers.delete(id) },
    setInterval(fn) { intervals.set(++nextId, fn); return nextId }, clearInterval(id) { intervals.delete(id) } }
  scope.run(() => vm.runInNewContext(code, context)); mounts.forEach(fn => fn())
  return { page: context.page, requests, timers, intervals, handlers, doc, auth,
    emit(type) { handlers.get(type)?.({}) }, fireTimer() { const [id, fn] = timers.entries().next().value; timers.delete(id); fn() },
    dispose() { unmounts.forEach(fn => fn()); scope.stop() } }
}
test('events coalesce and refresh only the applied range, preserving an unfinished date edit', async () => {
  const s = setup(); s.requests[0].resolve(response(1)); await tick()
  s.page.startDate.value = '2026-10-28'; s.page.endDate.value = '2026-10-28'; s.page.query()
  assert.equal(s.requests[1].params.start_date, '2026-10-28')
  s.requests[1].resolve(response(2)); await tick()
  s.page.startDate.value = '2026-10-29'
  s.emit('order_status_update'); s.emit('order_status_update'); assert.equal(s.timers.size, 1)
  s.fireTimer(); assert.equal(s.requests[2].params.start_date, '2026-10-28')
  s.emit('order_status_update'); s.emit('order_status_update')
  s.requests[2].resolve(response(3)); await tick(); assert.equal(s.timers.size, 1)
  s.fireTimer(); assert.equal(s.requests[3].params.start_date, '2026-10-28')
  s.requests[3].resolve(response(4)); await tick()
  assert.equal(s.page.startDate.value, '2026-10-29'); assert.match(s.page.rangeLabel.value, /2026-10-28/)
  assert.match(s.page.snapshotText.value, /time-4/)
  s.dispose(); assert.equal(s.handlers.size, 0); assert.equal(s.intervals.size, 0); assert.equal(s.doc.listeners.size, 0)
})
test('stale response cannot overwrite newer query; failures keep the last successful range and snapshot', async () => {
  const s = setup(); s.requests[0].resolve(response(1)); await tick()
  s.emit('connected'); assert.equal(s.requests.length, 2)
  s.page.startDate.value = '2026-10-28'; s.page.endDate.value = '2026-10-28'; s.page.query()
  s.requests[2].resolve(response(2)); await tick(); s.requests[1].resolve(response(99)); await tick()
  assert.equal(s.page.data.value.kpi.total_amount, 2)
  s.page.startDate.value = '2026-10-29'; s.page.endDate.value = '2026-10-29'; s.page.query()
  s.requests[3].reject(new Error('offline')); await tick()
  assert.equal(s.page.data.value.kpi.total_amount, 2); assert.ok(s.page.loadError.value); assert.match(s.page.rangeLabel.value, /2026-10-28/)
  s.emit('connected'); assert.equal(s.requests[4].params.start_date, '2026-10-28')
  s.requests[4].resolve(response(3)); await tick(); assert.ok(s.page.loadError.value)
  s.page.retry(); assert.equal(s.requests[5].params.start_date, '2026-10-29')
  s.requests[5].resolve(response(4)); await tick(); assert.equal(s.page.loadError.value, false)
  s.dispose()
})
test('hidden refresh waits for visibility; revoking permission clears data and invalidates responses', async () => {
  const s = setup(); s.requests[0].resolve(response(1)); await tick()
  s.doc.hidden = true; s.emit('order_status_update'); assert.equal(s.requests.length, 1)
  s.doc.hidden = false; s.doc.listeners.get('visibilitychange')(); assert.equal(s.requests.length, 2)
  s.auth.permissions = []; assert.equal(s.page.data.value, null)
  s.requests[1].resolve(response(2)); await tick(); assert.equal(s.page.data.value, null)
  s.dispose()
})
