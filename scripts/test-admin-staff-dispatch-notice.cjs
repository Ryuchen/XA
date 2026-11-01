const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')
const { createRequire } = require('node:module')
const ts = createRequire(path.resolve('apps/admin-web/package.json'))('typescript')

const source = fs.readFileSync('apps/admin-web/src/utils/staffDispatchNotice.ts', 'utf8')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
const context = { exports: {}, Date, Number, Set, Map }
vm.runInNewContext(js, context)
const { createStaffDispatchNotice } = context.exports

const order = (overrides = {}) => ({ id: 42, dispatch_route: 'STAFF', status: 'PENDING', payment_status: 'PAID', reject_count: 0, ...overrides })
const deferred = () => {
  let resolve
  const promise = new Promise(r => { resolve = r })
  return { promise, resolve }
}

function fixture() {
  let session = 1
  let allowed = true
  let blocked = false
  let next = () => Promise.resolve(order())
  let list = () => Promise.resolve({ list: [], total: 0 })
  let legacyPayload = false
  const shown = []
  const timers = []
  const queue = []
  let opened = 0
  let reads = 0
  const controller = createStaffDispatchNotice({
    session: () => session,
    allowed: () => allowed,
    blocked: () => blocked,
    retrieve: id => { reads++; return next(id) },
    listPending: async () => {
      const result = await list()
      return legacyPayload ? result : { staff_queue_needs_review: false, ...result }
    },
    queue: (status, count) => queue.push({ status, count }),
    schedule: (callback, delay) => { const timer = { callback, delay, cancelled: false }; timers.push(timer); return timer },
    cancel: timer => { timer.cancelled = true },
    show: (open, closed) => {
      const n = { open, closed, close: () => { n.wasClosed = true } }
      shown.push(n)
      return n
    },
    open: () => { opened++ },
  })
  return { controller, shown, timers, queue, reads: () => reads, opened: () => opened,
    nextTimer: delay => timers.find(timer => !timer.cancelled && timer.delay === delay),
    runTimer: delay => { const timer = timers.find(item => !item.cancelled && item.delay === delay); assert(timer, `missing ${delay}ms timer`); timer.cancelled = true; timer.callback() },
    setSession: value => { session = value }, setAllowed: value => { allowed = value },
    setBlocked: value => { blocked = value; controller.modalChanged() },
    setLegacyPayload: value => { legacyPayload = value },
    retrieveWith: fn => { next = fn }, listWith: fn => { list = fn } }
}

test('only currently eligible staff orders notify and duplicate events coalesce', async () => {
  const f = fixture()
  const pending = deferred()
  f.retrieveWith(() => pending.promise)
  const a = f.controller.receive(order())
  await f.controller.receive(order())
  assert.equal(f.reads(), 1)
  pending.resolve(order())
  await a
  await f.controller.receive(order())
  assert.equal(f.reads(), 1)
  assert.equal(f.shown.length, 1)
  f.shown[0].open()
  assert.equal(f.opened(), 1)
  await f.controller.receive(order({ id: 43, dispatch_route: 'PUBLIC' }))
  assert.equal(f.reads(), 1)
})

test('server result and later events prevent stale or expired notices', async () => {
  const f = fixture()
  const pending = deferred()
  f.retrieveWith(() => pending.promise)
  const a = f.controller.receive(order())
  await f.controller.receive(order({ status: 'GRABBED' }))
  pending.resolve(order())
  await a
  assert.equal(f.shown.length, 0)
  f.retrieveWith(() => Promise.resolve(order({ status: 'GRABBED' })))
  await f.controller.receive(order())
  assert.equal(f.shown.length, 0)
  await f.controller.receive(order({ staff_dispatch_actionable: false }))
  assert.equal(f.shown.length, 0)
})

test('initial connection and reconnect reconcile missed staff orders and coalesce an open alert', async () => {
  const f = fixture()
  f.listWith(() => Promise.resolve({ list: [order()], total: 2 }))
  await f.controller.reconcile()
  assert.equal(f.shown.length, 1)
  assert.deepEqual(f.queue.at(-1), { status: 'ready', count: 2 })
  await f.controller.reconcile()
  assert.equal(f.shown.length, 1)
  f.shown[0].closed()
  await f.controller.reconcile()
  assert.equal(f.shown.length, 2)
})

test('incomplete queue remains visible as review while notifying a verified candidate', async () => {
  const f = fixture()
  f.listWith(() => Promise.resolve({ list: [order({ id: 43, staff_dispatch_actionable: true })], total: 1, staff_queue_needs_review: true }))
  f.retrieveWith(id => Promise.resolve(order({ id, staff_dispatch_actionable: true })))
  await f.controller.reconcile()
  assert.deepEqual(f.queue.at(-1), { status: 'review', count: 1 })
  assert.equal(f.shown.length, 1)
})

test('missing or non-boolean queue completeness never appears as an exact count', async () => {
  const f = fixture()
  f.setLegacyPayload(true)
  f.listWith(() => Promise.resolve({ list: [order()], total: 2 }))
  await f.controller.reconcile()
  assert.deepEqual(f.queue.at(-1), { status: 'review', count: 2 })
  f.listWith(() => Promise.resolve({ list: [order()], total: 2, staff_queue_needs_review: 'false' }))
  await f.controller.reconcile()
  assert.deepEqual(f.queue.at(-1), { status: 'review', count: 2 })
})

test('legacy completeness responses cannot survive permission or session changes', async () => {
  const f = fixture(), pending = deferred()
  f.setLegacyPayload(true)
  f.listWith(() => pending.promise)
  const old = f.controller.reconcile()
  f.setAllowed(false); f.setSession(2); f.controller.reset()
  pending.resolve({ list: [order()], total: 1 })
  await old
  assert.deepEqual(f.queue.at(-1), { status: 'idle', count: null })
  assert.equal(f.shown.length, 0)
})

test('actual app notification opens the review list and rechecks permissions', () => {
  const file = fs.readFileSync('apps/admin-web/src/App.vue', 'utf8')
  const script = file.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
  const compiled = ts.transpileModule(script, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
  const auth = { token: 'token', sessionRevision: 1, permissions: ['order:view', 'order:dispatch'], hasPerm(value) { return this.permissions.includes(value) } }
  const queue = { status: 'review', update() {} }, navigations = []
  let captured
  const mocks = {
    vue: { watch() {}, onMounted() {}, onUnmounted() {} }, 'element-plus': { ElNotification() {} },
    'vue-router': { useRouter: () => ({ push: target => { navigations.push(target) } }) },
    '@/stores/auth': { useAuthStore: () => auth }, '@/stores/staffDispatchQueue': { useStaffDispatchQueue: () => queue },
    '@/utils/ws': { adminWs: { on: () => () => {}, connect() {}, disconnect() {} } },
    '@/utils/staffNoticeSound': { staffNoticeSound: { prepare() {}, play() {}, dispose() {} } },
    '@/api/request': { request() {} },
    '@/utils/staffDispatchNotice': { createStaffDispatchNotice: deps => { captured = deps; return { receive() {}, reconcile() {}, reset() {} } } },
  }
  const module = { exports: {} }
  vm.runInNewContext(compiled, { module, exports: module.exports, require: name => {
    if (!(name in mocks)) throw new Error('Unexpected import ' + name)
    return mocks[name]
  } })
  captured.open()
  assert.deepEqual(JSON.parse(JSON.stringify(navigations[0])), { name: 'orders', query: { queue: 'staff_review' } })
  queue.status = 'ready'; captured.open()
  assert.deepEqual(JSON.parse(JSON.stringify(navigations[1])), { name: 'orders', query: { queue: 'staff' } })
  auth.permissions = ['order:view']; captured.open()
  auth.permissions = ['order:dispatch']; captured.open()
  auth.permissions = ['order:view', 'order:dispatch']; auth.token = ''; captured.open()
  assert.equal(navigations.length, 2)
})

test('an overlay defers a new notice, keeps the count current, and rechecks after close', async () => {
  const f = fixture()
  f.listWith(() => Promise.resolve({ list: [order()], total: 1 }))
  f.setBlocked(true)
  await f.controller.reconcile()
  assert.equal(f.shown.length, 0)
  assert.deepEqual(f.queue.at(-1), { status: 'ready', count: 1 })
  f.setBlocked(false)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.shown.length, 1)
})

test('opening an overlay closes an existing notice; a resolved queue stays silent', async () => {
  const f = fixture()
  let pending = true
  f.listWith(() => Promise.resolve({ list: pending ? [order()] : [], total: pending ? 1 : 0 }))
  await f.controller.reconcile()
  f.setBlocked(true)
  assert.equal(f.shown[0].wasClosed, true)
  f.shown[0].open()
  assert.equal(f.opened(), 0)
  pending = false
  f.setBlocked(false)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.shown.length, 1)
  assert.deepEqual(f.queue.at(-1), { status: 'ready', count: 0 })
})

test('session reset clears a deferred notice', async () => {
  const f = fixture()
  f.setBlocked(true)
  await f.controller.receive(order())
  f.setAllowed(false); f.setSession(2); f.controller.reset()
  f.setBlocked(false)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.shown.length, 0)
})

test('failed queue and detail reads retry with bounded intervals and recover silently', async () => {
  const f = fixture()
  let listCalls = 0
  f.listWith(() => ++listCalls === 1 ? Promise.reject(new Error('offline')) : Promise.resolve({ list: [order()], total: 1 }))
  await f.controller.reconcile()
  assert.deepEqual(f.queue.at(-1), { status: 'error', count: null })
  f.runTimer(3000)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(f.shown.length, 1)
  const g = fixture()
  let detailCalls = 0
  g.listWith(() => Promise.resolve({ list: [order()], total: 1 }))
  g.retrieveWith(() => ++detailCalls === 1 ? Promise.reject(new Error('temporary')) : Promise.resolve(order()))
  await g.controller.receive(order())
  g.runTimer(3000)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(g.shown.length, 1)
})

test('repeated detail failures back off instead of polling every three seconds', async () => {
  const f = fixture()
  f.listWith(() => Promise.resolve({ list: [order()], total: 1 }))
  f.retrieveWith(() => Promise.reject(new Error('offline')))
  await f.controller.reconcile()
  f.runTimer(3000)
  await new Promise(resolve => setImmediate(resolve))
  f.runTimer(6000)
  await new Promise(resolve => setImmediate(resolve))
  assert(f.nextTimer(12000))
  f.controller.reset()
})

test('stale queue responses and retry timers cannot leak across session or permission changes', async () => {
  const f = fixture()
  const pending = deferred()
  f.listWith(() => pending.promise)
  const old = f.controller.reconcile()
  f.setSession(2); f.controller.reset()
  pending.resolve({ list: [order()], total: 1 }); await old
  assert.equal(f.shown.length, 0)
  f.listWith(() => Promise.reject(new Error('offline')))
  await f.controller.reconcile()
  assert.equal(f.timers.length, 1)
  f.setAllowed(false); f.controller.reset()
  assert.equal(f.timers[0].cancelled, true)
  f.timers[0].callback()
  assert.equal(f.reads(), 0)
  f.setAllowed(true); f.listWith(() => Promise.resolve({ list: [order()], total: 1 }))
  await f.controller.reconcile()
  assert.equal(f.shown.length, 1)
})

test('server eligibility, rather than workstation clock, determines timeout', async () => {
  const f = fixture()
  f.listWith(() => Promise.resolve({ list: [order({ auto_cancel_at: '2000-01-01T00:00:00Z', staff_dispatch_actionable: true })], total: 1 }))
  f.retrieveWith(() => Promise.resolve(order({ auto_cancel_at: '2000-01-01T00:00:00Z', staff_dispatch_actionable: true })))
  await f.controller.reconcile()
  assert.equal(f.shown.length, 1)
  const g = fixture()
  g.listWith(() => Promise.resolve({ list: [order()], total: 1 }))
  g.retrieveWith(() => Promise.resolve(order({ staff_dispatch_actionable: false })))
  await g.controller.reconcile()
  assert.equal(g.shown.length, 0)
  assert.equal(g.timers.length, 1)
})

test('persistent queue count refreshes on staff events and periodic checks, without repeated toast', async () => {
  const f = fixture()
  let total = 2, listCalls = 0
  f.listWith(() => { listCalls++; return Promise.resolve({ list: total ? [order()] : [], total }) })
  f.retrieveWith(id => Promise.resolve(order({ id })))
  await f.controller.reconcile()
  assert.deepEqual(f.queue.at(-1), { status: 'ready', count: 2 })
  f.shown[0].closed()
  await Promise.all([f.controller.receive(order({ id: 43 })), f.controller.receive(order({ id: 44 }))])
  total = 3
  assert(f.nextTimer(500))
  f.runTimer(500)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(listCalls, 2)
  assert.deepEqual(f.queue.at(-1), { status: 'ready', count: 3 })
  const shown = f.shown.length
  f.runTimer(60000)
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(listCalls, 3)
  assert.equal(f.shown.length, shown, 'periodic counts should not repeat old alerts')
  total = 0
  await f.controller.receive(order({ status: 'CANCELLED' }))
  f.runTimer(500)
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(f.queue.at(-1), { status: 'ready', count: 0 })
  f.controller.reset()
  assert.deepEqual(f.queue.at(-1), { status: 'idle', count: null })
})

test('a burst during an outstanding queue request coalesces into one follow-up', async () => {
  const f = fixture(), pending = deferred()
  let calls = 0
  f.listWith(() => ++calls === 1 ? pending.promise : Promise.resolve({ list: [], total: 0 }))
  const first = f.controller.reconcile()
  await f.controller.receive(order({ status: 'CANCELLED' }))
  await f.controller.receive(order({ status: 'GRABBED' }))
  f.runTimer(500)
  pending.resolve({ list: [], total: 0 })
  await first
  await new Promise(resolve => setImmediate(resolve))
  assert.equal(calls, 2)
  assert.deepEqual(f.queue.at(-1), { status: 'ready', count: 0 })
  f.controller.reset()
})

test('bursts of different new orders use one detail read and reconcile the full count', async () => {
  const f = fixture(), pending = deferred()
  f.retrieveWith(() => pending.promise)
  f.listWith(() => Promise.resolve({ list: [order({ id: 43 })], total: 3 }))
  const first = f.controller.receive(order())
  await Promise.all([f.controller.receive(order({ id: 43 })), f.controller.receive(order({ id: 44 }))])
  assert.equal(f.reads(), 1)
  pending.resolve(order({ status: 'GRABBED' }))
  await first
  f.retrieveWith(id => Promise.resolve(order({ id })))
  f.runTimer(500)
  await new Promise(resolve => setImmediate(resolve))
  assert.deepEqual(f.queue.at(-1), { status: 'ready', count: 3 })
  assert.equal(f.reads(), 2)
  assert.equal(f.shown.length, 1)
  f.controller.reset()
})

test('a late count cannot replace the next session queue', async () => {
  const f = fixture(), old = deferred()
  f.listWith(() => old.promise)
  const stale = f.controller.reconcile()
  f.setSession(2); f.controller.reset()
  f.listWith(() => Promise.resolve({ list: [], total: 0 }))
  await f.controller.reconcile()
  old.resolve({ list: [order()], total: 7 })
  await stale
  assert.deepEqual(f.queue.at(-1), { status: 'ready', count: 0 })
  assert.equal(f.shown.length, 0)
  f.controller.reset()
})

test('session changes and permission loss suppress late results and stale clicks', async () => {
  const f = fixture()
  const pending = deferred()
  f.retrieveWith(() => pending.promise)
  const a = f.controller.receive(order())
  f.setSession(2)
  f.controller.reset()
  pending.resolve(order())
  await a
  assert.equal(f.shown.length, 0)
  f.retrieveWith(() => Promise.resolve(order()))
  await f.controller.receive(order())
  f.setAllowed(false)
  f.shown[0].open()
  assert.equal(f.opened(), 0)
  f.controller.reset()
  assert.equal(f.shown[0].wasClosed, true)
})

test('a new rejection round may notify again without overlapping alerts', async () => {
  const f = fixture()
  await f.controller.receive(order())
  f.retrieveWith(() => Promise.resolve(order({ reject_count: 1 })))
  await f.controller.receive(order({ reject_count: 1 }))
  assert.equal(f.shown.length, 1)
  f.shown[0].closed()
  f.retrieveWith(() => Promise.resolve(order({ reject_count: 2 })))
  await f.controller.receive(order({ reject_count: 2 }))
  assert.equal(f.shown.length, 2)
  f.shown[0].closed()
  f.retrieveWith(() => Promise.resolve(order({ reject_count: 3 })))
  await f.controller.receive(order({ reject_count: 3 }))
  assert.equal(f.shown.length, 2)
})
