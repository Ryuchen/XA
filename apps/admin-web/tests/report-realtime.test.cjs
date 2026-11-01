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
    onDetail, onApprove, onReject, onStatusFilterChange, onOrderBlockedFilterChange, refreshCurrentReport, canReadReports, settlementStatusLabel, reportSettlementLabel, payoutLabel, payoutAmountDisplay, auditBusy, auditReview, auditReviewLoading, auditReviewError, checkAuditReview, finishAuditReview,
    openCommission, loadCommission, saveCommission, commissionVisible, commissionReady, commissionLoading, commissionConfig,
    commissionErrorText, commissionInput, commissionType, commissionFixed, savingRate }`,
].join('\n'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function harness(options = {}) {
  const mounted = [], unmounted = [], stops = [], requests = [], loads = [], actions = [], messages = [], configCalls = []
  const handlers = new Map(), timers = new Map(), storage = options.storage || new Map()
  let nextTimer = 0, connections = 0
  const auth = vue.reactive({
    token: 'synthetic-session', profile: { id: 17 }, sessionRevision: 1, permissions: ['report:view', 'report:audit'],
    hasPerm(permission) { return this.permissions.includes(permission) },
  })
  const route = vue.reactive({ query: {} })
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
    sessionStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
    computed: vue.computed, reactive: vue.reactive, ref: vue.ref,
    watch(...args) { const stop = vue.watch(...args); stops.push(stop); return stop },
    onMounted(callback) { mounted.push(callback) },
    onBeforeUnmount(callback) { unmounted.push(callback) },
    setTimeout(callback) { const id = ++nextTimer; timers.set(id, callback); return id },
    clearTimeout(id) { timers.delete(id) },
    useAuthStore: () => auth, useRoute: () => route, useRouter: () => ({ replace: ({ query }) => { route.query = query } }), adminWs,
    reportApi: {
      retrieve(id) { const pending = deferred(); requests.push({ id, ...pending }); return pending.promise },
    },
    async request(config) {
      if (config.url === '/config/commission') {
        configCalls.push(config)
        if (config.method === 'put') return options.commissionPut ? options.commissionPut(config.data) : { data: { ...config.data, rate: config.data.rate } }
        return options.commissionGet ? options.commissionGet() : { data: { commission_type: 'PERCENT', commission_fixed: 0, rate: 20 } }
      }
      const match = config.url.match(/^\/reports\/(\d+)\/(approve|reject)\/$/)
      assert.ok(match)
      const id = Number(match[1]), action = match[2], payload = config.data
      actions.push({ id, action, payload })
      return options.action?.(id, action, payload)
    },
    commissionApi: { get: async () => ({ data: { rate: 20 } }) },
    ElMessage: { success(message) { messages.push(message) }, warning(message) { messages.push(message) } },
    ElMessageBox: {
      confirm: options.confirm || (async () => {}),
      prompt: options.prompt || (async () => ({ value: '证据不完整' })),
      close() {},
    },
    readCommission: config => ({ type: config.commission_type, rate: config.rate, fixed: config.commission_fixed / 10 }),
    commissionError: () => '',
    commissionPayload: (type, rate, fixed) => ({ commission_type: type, commission_fixed: type === 'FIXED' ? fixed * 10 : 0 }),
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
  return { ...page, auth, requests, loads, actions, messages, configCalls, timers, handlers, emit, runTimer, unmount,
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

test('order-state filter and pending status stay in sync', t => {
  const page = harness()
  t.after(page.unmount)
  let reloads = 0
  page.query.status = 'APPROVED'
  page.query.order_blocked = '1'
  page.onOrderBlockedFilterChange(() => reloads++)
  assert.equal(page.query.status, 'PENDING')
  page.query.status = 'REJECTED'
  page.onStatusFilterChange(() => reloads++)
  assert.equal(page.query.order_blocked, '')
  assert.equal(reloads, 2)
})

test('report events coalesce list refreshes without resetting filters or pagination', async t => {
  const page = harness()
  t.after(page.unmount)
  page.query.game_name = '三角洲'
  page.query.keyword = '隔离关键词'
  page.query.provider_username = 'synthetic-provider'
  page.query.status = 'PENDING'
  for (const status of ['DRAFT', 'PENDING', 'REJECTED', 'PENDING', 'APPROVED']) reportUpdate(page, 11, status)
  await page.runTimer()
  assert.equal(page.connections, 1)
  assert.equal(page.loads.length, 1)
  assert.deepEqual({ ...page.loads[0] }, { page: 4, page_size: 20, keyword: '隔离关键词', game_name: '三角洲',
    provider_username: 'synthetic-provider', provider_nickname: '', status: 'PENDING', order_blocked: '' })
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

test('order refunds invalidate the report list and open detail; blocked approvals are not sent', async t => {
  const page = harness()
  t.after(page.unmount)
  await openDetail(page, 11, { order: 99 })
  page.emit('order_status_update', { id: 99, status: 'CANCELLED' })
  const refreshing = page.runTimer()
  page.requests[1].resolve({ data: { id: 11, order: 99, status: 'PENDING', order_approval_block_reason: '订单已退款，不能通过报单' } })
  await refreshing
  assert.equal(page.loads.length, 1)
  assert.equal(page.current.value.order_approval_block_reason, '订单已退款，不能通过报单')
  await page.onApprove(page.current.value)
  assert.equal(page.actions.length, 0)
  await page.onReject(page.current.value)
  assert.equal(page.actions[0].action, 'reject')
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
  await page.onReject({ id: 11, status: 'PENDING' })
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
    const operation = page[action]({ id: 11, status: 'PENDING' })
    page.auth.sessionRevision++
    confirmation.resolve({ value: 'synthetic reason' })
    await operation
    assert.equal(page.actions.length, 0)
    assert.equal(page.messages.length, 0)
  }
})

test('settlement labels and confirmation distinguish verified income from review', async t => {
  const confirmations = []
  const page = harness({ confirm: async message => confirmations.push(message) })
  t.after(page.unmount)
  assert.equal(page.settlementStatusLabel('REVIEW'), '收益待核对')
  assert.equal(page.settlementStatusLabel(undefined), '收益待核对')
  assert.equal(page.settlementStatusLabel('unexpected'), '收益待核对')
  assert.equal(page.settlementStatusLabel('WAITING'), '待整单结算')
  assert.equal(page.settlementStatusLabel('SETTLED', 0), '已结算（收益为 0）')
  assert.equal(page.settlementStatusLabel('SETTLED', '0'), '已结算（收益为 0）')
  assert.equal(page.settlementStatusLabel('SETTLED', 100), '已入账（可能冻结）')
  for (const invalid of [undefined, null, '', ' ', true, -1, 'bad']) {
    assert.equal(page.settlementStatusLabel('SETTLED', invalid), '收益待核对')
    assert.equal(page.payoutAmountDisplay(invalid), '金额待核对')
  }
  for (const status of ['REVIEW', undefined, 'WAITING', 'SETTLED']) {
    await page.onApprove({ id: 11, status: 'PENDING', order: 99, provider_name: '陪玩', settlement_status: status })
  }
  assert.match(confirmations[0], /收益待核对.*不代表收益已入账/)
  assert.match(confirmations[1], /收益待核对/)
  assert.match(confirmations[2], /售后窗口结束/)
  assert.match(confirmations[3], /收益及冻结情况请查看钱包/)
  await page.onApprove({ id: 12, status: 'PENDING', order: null, provider_name: '陪玩', payout_amount: 123 })
  assert.match(confirmations[4], /实际入账以结算记录为准/)
  assert.ok(page.messages.every(message => message === '报单审核已通过'))
  assert.equal(page.payoutLabel({ order_payment_status: 'PARTIALLY_REFUNDED' }), '原核定收益')
  assert.equal(page.reportSettlementLabel({ order_payment_status: 'PARTIALLY_REFUNDED', settlement_status: 'REVIEW' }), '已部分退款，净收益以钱包流水为准')
  assert.equal(page.reportSettlementLabel({ order_payment_status: 'REFUNDED', settlement_status: 'REVIEW' }), '已退款，收益变动以钱包流水为准')
  assert.equal(page.payoutLabel({ order_payment_status: 'PAID' }), '核定收益')
  assert.equal(page.reportSettlementLabel({ order_payment_status: 'PAID', settlement_status: 'SETTLED', payout_amount: 100 }), '已入账（可能冻结）')
  assert.match(source, /reportSettlementLabel\(row\)/)
  assert.match(source, /reportSettlementLabel\(current\)/)
  assert.match(source, /冻结及可用余额请查看钱包/)
  assert.doesNotMatch(source, /row\.order && !row\.settled_at \? '待整单结算' : '已入账'/)
})

test('unmounting or losing view permission while a review dialog is pending prevents submission', async () => {
  for (const action of ['onApprove', 'onReject']) {
    for (const invalidate of ['unmount', 'revoke-view']) {
      const confirmation = deferred()
      const page = harness({ confirm: () => confirmation.promise, prompt: () => confirmation.promise })
      try {
        const operation = page[action]({ id: 11, status: 'PENDING' })
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

test('a matching push cancels a waiting approval and submission stays locked in flight', async () => {
  const confirmation = deferred(), submission = deferred()
  const page = harness({ confirm: () => confirmation.promise, action: () => submission.promise })
  try {
    const old = page.onApprove({ id: 11, status: 'PENDING' })
    reportUpdate(page, 11, 'REJECTED')
    confirmation.resolve()
    await old
    assert.equal(page.actions.length, 0)
    assert.match(page.messages.at(-1), /报单已变化/)
    const next = page.onApprove({ id: 12, status: 'PENDING' })
    await tick()
    assert.equal(page.auditBusy.value, true)
    await page.onReject({ id: 13, status: 'PENDING' })
    assert.equal(page.actions.length, 1)
    submission.reject(new Error('offline'))
    await next
    assert.match(page.messages.at(-1), /审核结果未确认/)
    assert.equal(page.auditBusy.value, false)
  } finally { page.unmount() }
})

test('commission editing retries failed reads and preserves the manual rate in fixed mode', async () => {
  let fail = false
  const page = harness({ commissionGet: async () => {
    if (fail) throw new Error('offline')
    return { data: { commission_type: 'PERCENT', commission_fixed: 0, rate: 20 } }
  } })
  try {
    await tick(); fail = true; page.openCommission(); await tick()
    assert.equal(page.commissionReady.value, false); assert.equal(page.commissionConfig.value, null)
    assert.match(page.commissionErrorText.value, /读取失败/)
    await page.saveCommission(); assert.equal(page.configCalls.filter(call => call.method === 'put').length, 0)
    fail = false; await page.loadCommission(true)
    page.auth.permissions = ['report:view', 'report:audit']
    assert.equal(page.commissionVisible.value, true)
    page.commissionInput.value = 35; page.commissionType.value = 'FIXED'; page.commissionFixed.value = 25
    await page.saveCommission()
    assert.deepEqual({ ...page.configCalls.at(-1).data }, { commission_type: 'FIXED', commission_fixed: 250, rate: 20 })
    assert.equal(page.savingRate.value, false); assert.equal(page.commissionVisible.value, false)
  } finally { page.unmount() }
})

test('uncertain commission save requires a fresh read before another save', async () => {
  const pending = deferred()
  const page = harness({ commissionPut: () => pending.promise })
  try {
    await tick(); page.openCommission(); await tick()
    const saving = page.saveCommission()
    assert.equal(page.savingRate.value, true)
    pending.reject(new Error('offline')); await saving
    assert.equal(page.commissionReady.value, false)
    assert.match(page.commissionErrorText.value, /结果未确认/)
    await page.saveCommission(); assert.equal(page.configCalls.filter(call => call.method === 'put').length, 1)
    await page.loadCommission(true); assert.equal(page.commissionReady.value, true)
  } finally { page.unmount() }
})

// These tests exercise response timing and browser-session state; the isolated UI scenario uses the real backend.
test('uncertain audit survives reload and requires a fresh, explicitly confirmed read', async t => {
  const storage = new Map()
  const first = harness({ storage, action: async () => { throw new Error('connection lost') } })
  await first.onApprove({ id: 11, status: 'PENDING' })
  assert.equal(first.auditReview.value.id, 11)
  await first.onApprove({ id: 12, status: 'PENDING' })
  assert.equal(first.actions.length, 1)
  first.unmount()
  const page = harness({ storage })
  t.after(page.unmount)
  assert.equal(page.auditReview.value.id, 11)
  const stale = page.checkAuditReview()
  reportUpdate(page, 11, 'APPROVED')
  page.requests[0].resolve({ data: { id: 11, status: 'PENDING' } })
  await stale
  assert.equal(page.auditReview.value.checked, null)
  const fresh = page.checkAuditReview()
  page.requests[1].resolve({ data: { id: 11, status: 'APPROVED' } })
  await fresh
  assert.equal(page.auditReview.value.checked.status, 'APPROVED')
  await page.finishAuditReview()
  assert.equal(page.auditReview.value, null)
  assert.equal(storage.size, 0)
})

test('definitive client rejection clears the marker; other accounts cannot inherit it', async t => {
  const storage = new Map()
  const page = harness({ storage, action: async () => { throw { response: { status: 400, data: { msg: '状态已变化' } } } } })
  t.after(page.unmount)
  await page.onApprove({ id: 11, status: 'PENDING' })
  assert.equal(page.auditReview.value, null)
  assert.equal(storage.size, 0)
  assert.ok(page.messages.some(message => message.includes('状态已变化')))
  storage.set('xa_report_audit_review_17', JSON.stringify({ id: 12, action: 'reject' }))
  page.auth.profile.id = 18
  assert.equal(page.auditReview.value, null)
  page.auth.profile.id = 17
  assert.equal(page.auditReview.value.id, 12)
})
