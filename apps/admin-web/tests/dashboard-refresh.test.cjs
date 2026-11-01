// These tests control request timing only; business counts are verified against the real API separately.
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { createRequire } = require('node:module')
const adminRequire = createRequire(require('node:path').resolve('apps/admin-web/package.json'))
const vue = adminRequire('vue'), ts = adminRequire('typescript')
const tick = () => new Promise(resolve => setImmediate(resolve))
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b }); return { promise, resolve, reject } }

function setup() {
  const scope = vue.effectScope(), mounts = [], unmounts = [], timers = new Map(), intervals = new Map(), handlers = new Map(), requests = []
  const navigations = []
  const doc = { hidden: false, listeners: new Map(), addEventListener(name, fn) { this.listeners.set(name, fn) }, removeEventListener(name) { this.listeners.delete(name) } }
  const auth = vue.reactive({ sessionRevision: 1, permissions: ['dashboard:view', 'order:view', 'report:view'], hasPerm(code) { return this.permissions.includes(code) } })
  let nextId = 0
  const modules = {
    vue: { ...vue, onMounted: fn => mounts.push(fn), onUnmounted: fn => unmounts.push(fn) },
    'vue-router': { useRouter: () => ({ push: location => navigations.push(location) }) }, '@element-plus/icons-vue': { Refresh: {} }, '@/components/EChart.vue': {},
    '@/api/modules': { dashboardApi: { stats(silent) { assert.equal(silent, true); const d = deferred(); requests.push(d); return d.promise } } },
    '@/stores/auth': { useAuthStore: () => auth }, '@/utils/format': { amountToXaCoin: String, formatDateTime: String },
    '@/utils/dict': { ORDER_STATUS: { IN_SERVICE: { label: '服务中' } }, PAYMENT_STATUS: {} }, '@/utils/orderParticipants': { providerListText: () => '' },
    '@/utils/ws': { adminWs: { connect() {}, on(type, fn) { handlers.set(type, fn); return () => handlers.delete(type) } } },
  }
  const source = fs.readFileSync('apps/admin-web/src/views/dashboard/index.vue', 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  const code = ts.transpileModule(source + '\nglobalThis.page = { stats, loading, loadError, requestRefresh, openOrderStatus, kpis, hasPendingWithdrawClaims };', { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
  const context = { exports: {}, document: doc, require(name) { assert.ok(modules[name], name); return modules[name] },
    setTimeout(fn) { timers.set(++nextId, fn); return nextId }, clearTimeout(id) { timers.delete(id) },
    setInterval(fn) { intervals.set(++nextId, fn); return nextId }, clearInterval(id) { intervals.delete(id) } }
  scope.run(() => vm.runInNewContext(code, context))
  mounts.forEach(fn => fn())
  return { page: context.page, requests, timers, intervals, handlers, auth, doc, navigations,
    emit(type) { handlers.get(type)?.({}) }, fireTimer() { const [id, fn] = timers.entries().next().value; timers.delete(id); fn() },
    dispose() { unmounts.forEach(fn => fn()); scope.stop() } }
}

test('today KPI uses the final server-timezone trend day for order drilldown', async () => {
  const s = setup()
  s.requests[0].resolve({ data: { today_order_total: 1, daily_new_orders: [
    { date: '2026-10-29', count: 3 }, { date: '2026-10-30', count: 1 },
  ] } }); await tick()
  const today = s.page.kpis.value.find(item => item.label === '今日新增订单')
  assert.equal(today.available, true)
  assert.equal(today.target.created_day, '2026-10-30')
  s.page.stats.value.daily_new_orders = []
  assert.equal(s.page.kpis.value[0].available, false)
  s.dispose()
})

test('status navigation requires order permission and an actual status', () => {
  const s = setup()
  s.page.openOrderStatus('IN_SERVICE')
  assert.equal(s.navigations.length, 1)
  assert.equal(s.navigations[0].path, '/orders')
  assert.equal(s.navigations[0].query.order_status, 'IN_SERVICE')
  s.page.openOrderStatus('toString')
  s.auth.permissions = ['dashboard:view']
  s.page.openOrderStatus('IN_SERVICE')
  assert.equal(s.navigations.length, 1)
  s.dispose()
})

test('event bursts coalesce and changes during a request trigger one later read', async () => {
  const s = setup()
  assert.equal(s.requests.length, 1)
  s.emit('order_status_update'); s.emit('report_status_update'); s.emit('reservation_status_update')
  assert.equal(s.requests.length, 1)
  s.requests[0].resolve({ data: { value: 1 } }); await tick()
  assert.equal(s.timers.size, 1)
  s.fireTimer(); assert.equal(s.requests.length, 2)
  s.emit('order_status_update'); s.emit('order_status_update')
  s.requests[1].resolve({ data: { value: 2 } }); await tick()
  assert.equal(s.timers.size, 1)
  s.fireTimer(); assert.equal(s.requests.length, 3)
  s.requests[2].resolve({ data: { value: 3 } }); await tick()
  assert.equal(s.page.stats.value.value, 3)
  s.dispose(); assert.equal(s.handlers.size, 0); assert.equal(s.intervals.size, 0)
})

test('hidden page defers refresh and failure keeps the previous snapshot until recovery', async () => {
  const s = setup()
  s.requests[0].resolve({ data: { value: 1 } }); await tick()
  s.doc.hidden = true; s.emit('order_status_update'); assert.equal(s.requests.length, 1)
  s.doc.hidden = false; s.doc.listeners.get('visibilitychange')(); assert.equal(s.requests.length, 2)
  s.requests[1].reject(new Error('offline')); await tick()
  assert.equal(s.page.stats.value.value, 1); assert.ok(s.page.loadError.value)
  s.emit('connected'); assert.equal(s.requests.length, 3)
  s.requests[2].resolve({ data: { value: 2 } }); await tick()
  assert.equal(s.page.stats.value.value, 2); assert.equal(s.page.loadError.value, '')
  s.dispose()
})

test('permission and session changes invalidate pending results and clean up', async () => {
  const s = setup()
  s.auth.sessionRevision++; assert.equal(s.requests.length, 2)
  s.requests[0].resolve({ data: { value: 'old' } }); await tick()
  assert.equal(s.page.stats.value, null)
  s.requests[1].resolve({ data: { value: 'new' } }); await tick()
  assert.equal(s.page.stats.value.value, 'new')
  s.auth.permissions = []; assert.equal(s.page.stats.value, null)
  s.emit('order_status_update'); assert.equal(s.requests.length, 2)
  s.dispose(); assert.equal(s.doc.listeners.size, 0)
})

test('withdrawal events refresh the queue and permission changes discard the old count', async () => {
  const s = setup()
  s.auth.permissions = ['dashboard:view', 'withdraw:view']
  const current = s.requests.at(-1)
  current.resolve({ data: { pending_withdraw_total: 2 } }); await tick()
  s.emit('withdraw_status_update')
  assert.equal(s.timers.size, 1)
  s.fireTimer()
  const pending = s.requests.at(-1)
  s.auth.permissions = ['dashboard:view']
  assert.equal(s.page.stats.value, null)
  pending.resolve({ data: { pending_withdraw_total: 1 } }); await tick()
  assert.equal(s.page.stats.value, null)
  s.requests.at(-1).resolve({ data: { pending_withdraw_total: null } }); await tick()
  assert.equal(s.page.stats.value.pending_withdraw_total, null)
  s.dispose()
})

test('chat events refresh the unread queue and permission changes discard the old count', async () => {
  const s = setup()
  s.auth.permissions = ['dashboard:view', 'chat:view']
  s.requests.at(-1).resolve({ data: { unread_chat_session_total: 2 } }); await tick()
  s.emit('chat_message'); s.emit('chat_session_update')
  assert.equal(s.timers.size, 1)
  s.fireTimer()
  const pending = s.requests.at(-1)
  s.auth.permissions = ['dashboard:view']
  assert.equal(s.page.stats.value, null)
  pending.resolve({ data: { unread_chat_session_total: 1 } }); await tick()
  assert.equal(s.page.stats.value, null)
  s.requests.at(-1).resolve({ data: { unread_chat_session_total: null } }); await tick()
  assert.equal(s.page.stats.value.unread_chat_session_total, null)
  s.dispose()
})

test('withdrawal claim breakdown requires complete consistent live counts', async () => {
  const s = setup()
  s.requests[0].resolve({ data: { pending_withdraw_total: 3, pending_withdraw_unclaimed: 1, pending_withdraw_mine: 1 } }); await tick()
  assert.equal(s.page.hasPendingWithdrawClaims.value, true)
  s.page.stats.value.pending_withdraw_mine = null
  assert.equal(s.page.hasPendingWithdrawClaims.value, false)
  s.page.stats.value.pending_withdraw_mine = 3
  assert.equal(s.page.hasPendingWithdrawClaims.value, false)
  s.dispose()
})
