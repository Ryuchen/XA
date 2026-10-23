const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createRequire } = require('node:module')

const appRoot = path.resolve(__dirname, '..')
const appRequire = createRequire(path.join(appRoot, 'package.json'))
const ts = appRequire('typescript')
const vue = appRequire('vue')
const source = fs.readFileSync(path.join(appRoot, 'src/views/report/index.vue'), 'utf8')
const script = source.match(/<script setup[^>]*>([\s\S]*?)<\/script>/)[1]
const ast = ts.createSourceFile('report.ts', script, ts.ScriptTarget.Latest, true)
const code = ts.transpileModule([
  ...ast.statements.filter(statement => !ts.isImportDeclaration(statement)).map(statement => statement.getText(ast)),
  `module.exports = { tableRef, query, current, detailVisible, detailLoading, detailFailed,
    onDetail, onApprove, onReject, refreshCurrentReport, canReadReports }`,
].join('\n'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function harness(options = {}) {
  const mounted = [], unmounted = [], stops = [], requests = [], loads = [], actions = [], messages = []
  const handlers = new Map(), timers = new Map()
  let nextTimer = 0, connections = 0
  const auth = vue.reactive({
    token: 'synthetic-session', sessionRevision: 1, permissions: ['report:view', 'report:audit'],
    hasPerm(permission) { return this.permissions.includes(permission) },
  })
  const adminWs = {
    on(type, callback) {
      if (!handlers.has(type)) handlers.set(type, new Set())
      handlers.get(type).add(callback)
      return () => handlers.get(type).delete(callback)
    },
    connect() { connections++ },
  }
  const module = { exports: {} }
  vm.runInNewContext(code, {
    module, exports: module.exports,
    computed: vue.computed, reactive: vue.reactive, ref: vue.ref,
    watch(...args) { const stop = vue.watch(...args); stops.push(stop); return stop },
    onMounted(callback) { mounted.push(callback) },
    onBeforeUnmount(callback) { unmounted.push(callback) },
    setTimeout(callback) { const id = ++nextTimer; timers.set(id, callback); return id },
    clearTimeout(id) { timers.delete(id) },
    useAuthStore: () => auth, adminWs,
    reportApi: {
      retrieve(id) { const pending = deferred(); requests.push({ id, ...pending }); return pending.promise },
      async action(id, action, payload) { actions.push({ id, action, payload }); return options.action?.(id, action, payload) },
    },
    commissionApi: { get: async () => ({ data: { rate: 20 } }) },
    ElMessage: { success(message) { messages.push(message) } },
    ElMessageBox: {
      confirm: options.confirm || (async () => {}),
      prompt: options.prompt || (async () => ({ value: '证据不完整' })),
    },
    amountToXaCoin: value => String(value), formatDateTime: String,
  })
  const page = module.exports
  const table = { page: 4, pageSize: 20 }
  page.tableRef.value = {
    load() {
      loads.push({ page: table.page, page_size: table.pageSize, ...page.query })
      return options.load?.()
    },
    reload() { assert.fail('push refresh must preserve pagination') },
  }
  const emit = (type, data) => { for (const callback of [...handlers.get(type) || []]) callback(data) }
  const runTimer = () => {
    assert.equal(timers.size, 1, 'exactly one refresh should be scheduled')
    const [id, callback] = timers.entries().next().value
    timers.delete(id)
    return callback()
  }
  const unmount = () => { unmounted.forEach(callback => callback()); stops.forEach(stop => stop()) }
  mounted.forEach(callback => callback())
  return { ...page, auth, requests, loads, actions, messages, timers, handlers, emit, runTimer, unmount,
    get connections() { return connections } }
}

async function openDetail(page, id = 11, data = {}) {
  page.onDetail({ id, status: 'PENDING', ...data })
  const request = page.requests.at(-1)
  request.resolve({ data: { id, status: 'PENDING', ...data } })
  await tick()
}

const tick = () => new Promise(resolve => setImmediate(resolve))
const reportUpdate = (page, report_id, status = 'PENDING') =>
  page.emit('report_status_update', { report_id, order_id: 99, status })

test('report events coalesce list refreshes without resetting filters or pagination', async t => {
  const page = harness()
  t.after(page.unmount)
  page.query.game_name = '三角洲'
  page.query.provider_username = 'synthetic-provider'
  page.query.status = 'PENDING'
  for (const status of ['DRAFT', 'PENDING', 'REJECTED', 'PENDING', 'APPROVED']) reportUpdate(page, 11, status)
  await page.runTimer()
  assert.equal(page.connections, 1)
  assert.equal(page.loads.length, 1)
  assert.deepEqual({ ...page.loads[0] }, { page: 4, page_size: 20, game_name: '三角洲',
    provider_username: 'synthetic-provider', provider_nickname: '', status: 'PENDING' })
  assert.equal(page.requests.length, 0, 'a closed drawer must not retrieve report details')
  assert.equal(page.timers.size, 0)
})

test('matching events retrieve authoritative details; other reports refresh only the list', async t => {
  const page = harness()
  t.after(page.unmount)
  await openDetail(page)
  reportUpdate(page, 12, 'APPROVED')
  await page.runTimer()
  assert.equal(page.requests.length, 1)
  reportUpdate(page, 11, 'APPROVED')
  const refreshing = page.runTimer()
  assert.equal(page.current.value.status, 'PENDING', 'push payloads must not overwrite audit or financial fields')
  assert.equal(page.detailLoading.value, true)
  page.requests[1].resolve({ data: { id: 11, status: 'REJECTED', audit_remark: '服务器最新状态', expected_income: 1234 } })
  await refreshing
  assert.equal(page.current.value.status, 'REJECTED')
  assert.equal(page.current.value.audit_remark, '服务器最新状态')
  assert.equal(page.current.value.expected_income, 1234)
  assert.equal(page.detailLoading.value, false)
})

test('a new event invalidates an older detail response and schedules one trailing read', async t => {
  const listPending = deferred()
  let listCalls = 0
  const page = harness({ load: () => ++listCalls === 1 ? listPending.promise : Promise.resolve() })
  t.after(page.unmount)
  await openDetail(page)
  reportUpdate(page, 11)
  const first = page.runTimer()
  reportUpdate(page, 11, 'APPROVED')
  reportUpdate(page, 11, 'APPROVED')
  page.requests[1].resolve({ data: { id: 11, status: 'PENDING', stale: true } })
  await tick()
  assert.equal(page.current.value.stale, undefined, 'events received in flight invalidate the older response')
  assert.equal(page.timers.size, 0, 'a running refresh must finish before the trailing refresh')
  listPending.resolve()
  await first
  const trailing = page.runTimer()
  assert.equal(page.requests.length, 3)
  page.requests[2].resolve({ data: { id: 11, status: 'APPROVED' } })
  await trailing
  assert.equal(page.loads.length, 2)
  assert.equal(page.current.value.status, 'APPROVED')
  assert.equal(page.detailLoading.value, false)
  assert.equal(page.timers.size, 0)
})

test('reconnect reconciles the filtered list and an open drawer through HTTP', async t => {
  const page = harness()
  t.after(page.unmount)
  await openDetail(page)
  page.emit('connected')
  page.emit('connected')
  const refreshing = page.runTimer()
  page.requests[1].resolve({ data: { id: 11, status: 'APPROVED', settled_at: '2026-10-19T12:00:00Z' } })
  await refreshing
  assert.equal(page.loads.length, 1)
  assert.equal(page.current.value.status, 'APPROVED')
  assert.ok(page.current.value.settled_at)
})

test('closing or switching the drawer prevents late requests from restoring stale details', async t => {
  const page = harness()
  t.after(page.unmount)
  page.onDetail({ id: 11, status: 'PENDING' })
  const closed = page.requests[0]
  page.detailVisible.value = false
  closed.resolve({ data: { id: 11, status: 'APPROVED' } })
  await tick()
  assert.equal(page.current.value, null)
  assert.equal(page.detailLoading.value, false)
  assert.equal(page.detailVisible.value, false)
  page.onDetail({ id: 12, status: 'PENDING' })
  page.onDetail({ id: 13, status: 'PENDING' })
  page.requests[2].resolve({ data: { id: 13, status: 'REJECTED' } })
  page.requests[1].resolve({ data: { id: 12, status: 'APPROVED' } })
  await tick()
  assert.equal(page.current.value.id, 13)
  assert.equal(page.current.value.status, 'REJECTED')
})

test('unmount cancels timers, removes subscriptions and ignores pending detail responses', async () => {
  const page = harness()
  page.onDetail({ id: 11, status: 'PENDING' })
  reportUpdate(page, 11, 'APPROVED')
  assert.equal(page.timers.size, 1)
  page.unmount()
  assert.equal(page.timers.size, 0)
  assert.equal(page.handlers.get('report_status_update').size, 0)
  assert.equal(page.handlers.get('connected').size, 0)
  page.requests[0].resolve({ data: { id: 11, status: 'APPROVED' } })
  page.emit('connected')
  reportUpdate(page, 11)
  await tick()
  assert.equal(page.current.value, null)
  assert.equal(page.loads.length, 0)
  assert.equal(page.timers.size, 0)
})

test('session changes and permission loss dispose old work while access-token refresh retains it', async t => {
  const page = harness()
  t.after(page.unmount)
  page.onDetail({ id: 11, status: 'PENDING' })
  reportUpdate(page, 11)
  page.auth.sessionRevision++
  assert.equal(page.current.value, null)
  assert.equal(page.detailVisible.value, false)
  assert.equal(page.timers.size, 0)
  assert.equal(page.handlers.get('report_status_update').size, 1, 'new session has exactly one subscription')
  page.requests[0].resolve({ data: { id: 11, status: 'APPROVED' } })
  await tick()
  assert.equal(page.current.value, null)
  reportUpdate(page, 12)
  page.auth.token = 'synthetic-refreshed-token'
  await page.runTimer()
  assert.equal(page.loads.length, 1, 'token rotation inside the same session retains the queued refresh')
  reportUpdate(page, 12)
  page.auth.permissions = []
  assert.equal(page.canReadReports.value, false)
  assert.equal(page.timers.size, 0)
  assert.equal(page.handlers.get('report_status_update').size, 0)
  page.emit('connected')
  reportUpdate(page, 12)
  page.onDetail({ id: 12 })
  assert.equal(page.requests.length, 1)
  assert.equal(page.loads.length, 1)
  assert.match(source, /v-if="canReadReports"\s+:key="auth.sessionRevision"/,
    'the previous session table must be destroyed, not reused with stale rows')
})

test('failed refreshes release the queue and a failed detail can be retried', async t => {
  const page = harness({ load: () => Promise.reject(new Error('synthetic list failure')) })
  t.after(page.unmount)
  await openDetail(page)
  reportUpdate(page, 11)
  const refreshing = page.runTimer()
  page.requests[1].reject(new Error('synthetic detail failure'))
  await refreshing
  assert.equal(page.detailFailed.value, true)
  assert.equal(page.detailLoading.value, false)
  assert.match(source, /v-if="current && !detailFailed"/, 'failed detail refresh must hide stale audit data')
  const retry = page.refreshCurrentReport()
  page.requests[2].resolve({ data: { id: 11, status: 'APPROVED' } })
  await retry
  assert.equal(page.detailFailed.value, false)
  assert.equal(page.current.value.status, 'APPROVED')
  reportUpdate(page, 12)
  await page.runTimer()
  assert.equal(page.loads.length, 2)
})

test('malformed events are ignored and local review actions also refresh the selected detail', async t => {
  const page = harness()
  t.after(page.unmount)
  for (const data of [null, {}, { report_id: '11' }, { report_id: 0 }, { report_id: -1 }]) {
    page.emit('report_status_update', data)
  }
  assert.equal(page.timers.size, 0)
  await openDetail(page, 11, { order: 99, provider_name: 'synthetic-provider' })
  await page.onApprove(page.current.value)
  const approvalRefresh = page.runTimer()
  page.requests[1].resolve({ data: { id: 11, order: 99, status: 'APPROVED' } })
  await approvalRefresh
  assert.equal(page.actions[0].action, 'approve')
  assert.equal(page.current.value.status, 'APPROVED')
  await page.onReject({ id: 11 })
  const rejectionRefresh = page.runTimer()
  page.requests[2].resolve({ data: { id: 11, status: 'REJECTED', audit_remark: '证据不完整' } })
  await rejectionRefresh
  assert.equal(page.actions[1].action, 'reject')
  assert.equal(page.actions[1].payload.audit_remark, '证据不完整')
  assert.equal(page.current.value.status, 'REJECTED')
})

test('switching accounts while a review confirmation is open cannot submit the old row', async t => {
  for (const action of ['onApprove', 'onReject']) {
    const confirmation = deferred()
    const page = harness({ confirm: () => confirmation.promise, prompt: () => confirmation.promise })
    t.after(page.unmount)
    const operation = page[action]({ id: 11 })
    page.auth.sessionRevision++
    confirmation.resolve({ value: 'synthetic reason' })
    await operation
    assert.equal(page.actions.length, 0)
    assert.equal(page.messages.length, 0)
  }
})

test('unmounting or losing view permission while a review dialog is pending prevents submission', async () => {
  for (const action of ['onApprove', 'onReject']) {
    for (const invalidate of ['unmount', 'revoke-view']) {
      const confirmation = deferred()
      const page = harness({ confirm: () => confirmation.promise, prompt: () => confirmation.promise })
      try {
        const operation = page[action]({ id: 11 })
        if (invalidate === 'unmount') page.unmount()
        else page.auth.permissions = ['report:audit']
        confirmation.resolve({ value: 'synthetic reason' })
        await operation
        assert.equal(page.actions.length, 0, `${action} must not submit after ${invalidate}`)
        assert.equal(page.messages.length, 0)
      } finally {
        if (invalidate !== 'unmount') page.unmount()
      }
    }
  }
})
