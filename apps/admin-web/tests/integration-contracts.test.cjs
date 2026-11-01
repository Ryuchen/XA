const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createRequire } = require('node:module')

const appRoot = path.resolve(__dirname, '..')
const ts = createRequire(path.join(appRoot, 'package.json'))('typescript')

function pageFunctions(relativePath, names, scope, prelude = '') {
  const page = fs.readFileSync(path.join(appRoot, 'src', relativePath), 'utf8')
  const script = page.match(/<script setup[^>]*>([\s\S]*?)<\/script>/)[1]
  const ast = ts.createSourceFile('page.ts', script, ts.ScriptTarget.Latest, true)
  const functions = names.map(name => {
    const declaration = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name)
    assert.ok(declaration, `page function ${name} must exist`)
    return declaration.getText(ast)
  })
  const code = ts.transpileModule(`${prelude}\n${functions.join('\n')}\nmodule.exports = { ${names.join(', ')} }`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText
  const module = { exports: {} }
  vm.runInNewContext(code, { module, exports: module.exports, ...scope })
  return module.exports
}

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function orderDetailHarness() {
  const current = { value: null }, aftersales = { value: [] }, logs = { value: [] }, detailVisible = { value: false }
  const detailOrderId = { value: null }, detailLoading = { value: false }, detailFailed = { value: false }
  const aftersaleFailed = { value: false }, logsFailed = { value: false }
  const requests = new Map()
  const requestFor = key => {
    const pending = deferred()
    requests.set(key, pending)
    return pending.promise
  }
  const { onDetail, refreshCurrentOrder } = pageFunctions('views/order/index.vue',
    ['onDetail', 'clearOrderDetail', 'isCurrentOrderDetail', 'refreshCurrentOrder'], {
    current, aftersales, logs, detailVisible, detailOrderId, detailLoading, detailFailed, aftersaleFailed, logsFailed,
    pageDisposed: false, canReadOrders: { value: true }, auth: { sessionRevision: 1 },
    refundTarget: { value: null },
    request: ({ url, params }) => url === '/aftersales/' ? requestFor(`tickets-${params.order}`)
      : requestFor(`${url.endsWith('/logs/') ? 'logs' : 'order'}-${url.match(/orders\/(\d+)/)[1]}`),
  }, 'let detailSequence = 0, detailRefreshPending = false')
  const settle = id => {
    requests.get(`order-${id}`).resolve({ data: { id, order_no: `TEST-${id}` } })
    requests.get(`tickets-${id}`).resolve({ data: { list: [{ order: id }] } })
    requests.get(`logs-${id}`).resolve({ data: [{ order: id }] })
  }
  return { current, aftersales, logs, detailVisible, detailLoading, detailFailed, aftersaleFailed, logsFailed, requests, onDetail, refreshCurrentOrder, settle }
}

test('late order detail responses cannot mix another order into the selected drawer', async () => {
  const harness = orderDetailHarness()
  const older = harness.onDetail({ id: 1 })
  const latest = harness.onDetail({ id: 2 })
  harness.settle(2)
  await latest
  harness.settle(1)
  await older
  assert.equal(harness.current.value.id, 2)
  assert.equal(harness.aftersales.value[0].order, 2)
  assert.equal(harness.logs.value[0].order, 2)
  assert.equal(harness.detailVisible.value, true)
})

test('history failure keeps independently verified order details and remains retryable', async () => {
  const harness = orderDetailHarness()
  const operation = harness.onDetail({ id: 3 })
  harness.requests.get('order-3').resolve({ data: { id: 3 } })
  harness.requests.get('tickets-3').resolve({ data: { list: [{ order: 3 }] } })
  harness.requests.get('logs-3').reject(new Error('history unavailable'))
  await operation
  assert.equal(harness.current.value.id, 3)
  assert.equal(harness.aftersales.value.length, 1)
  assert.equal(harness.logs.value.length, 0)
  assert.equal(harness.detailVisible.value, true)
  assert.equal(harness.detailLoading.value, false)
  assert.equal(harness.detailFailed.value, false)
  assert.equal(harness.logsFailed.value, true, 'a failed history is identified independently of current order and aftersale')
  const retry = harness.refreshCurrentOrder()
  harness.settle(3)
  await retry
  assert.equal(harness.detailFailed.value, false)
  assert.equal(harness.aftersales.value[0].order, 3)
  assert.equal(harness.logs.value[0].order, 3)
})

const moneySource = fs.readFileSync(path.resolve(appRoot, '../../packages/money/src/index.js'), 'utf8')
const moneyModule = { exports: {} }
vm.runInNewContext(ts.transpileModule(moneySource, { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText,
  { module: moneyModule, exports: moneyModule.exports })
const report = pageFunctions('views/report/index.vue', ['reportRate', 'reportCommissionDesc', 'reportEstimatedPayout', 'validPayoutAmount', 'estimatedPayoutDisplay'], {
  amountToXaCoin: moneyModule.exports.formatXaCoin,
})

test('fixed commission report displays internal units as coins and reads the server income', () => {
  const row = { amount: 8000, commission_type: 'FIXED', commission_fixed: 300,
    commission_rate: 0, suggested_commission_rate: 0, commission_source_label: '订单分账快照', expected_income: 7700 }
  assert.equal(report.reportCommissionDesc(row), '固定抽取 30 兴安币 · 订单分账快照')
  assert.equal(report.reportEstimatedPayout(row), 7700)
  assert.equal(report.reportEstimatedPayout({ ...row, expected_income: 0 }), 0)
})

test('legacy percentage reports retain the confirmed rate and approved payout snapshot', () => {
  const pending = { amount: 2000, commission_type: 'PERCENT', suggested_commission_rate: 35, commission_source_label: '报单填写', expected_income: 1300 }
  assert.equal(report.reportCommissionDesc(pending), '35% · 报单填写')
  assert.equal(report.reportEstimatedPayout(pending), 1300)
  const approved = { ...pending, status: 'APPROVED', commission_rate: 20,
    commission_source_label: '审核快照', expected_income: 1111 }
  assert.equal(report.reportCommissionDesc(approved), '20% · 审核快照')
  assert.equal(report.reportEstimatedPayout(approved), 1111)
})

test('missing personal share never falls back to an order total or guessed commission', () => {
  const missing = { amount: 10000, expected_income: null, suggested_commission_rate: null,
    commission_rate: 0, commission_type: 'PERCENT', commission_source_label: '收益待核对' }
  assert.equal(report.reportEstimatedPayout(missing), null)
  assert.equal(report.estimatedPayoutDisplay(missing), '收益待核对')
  assert.equal(report.reportCommissionDesc(missing), '收益待核对')
  assert.equal(report.reportCommissionDesc({ ...missing, status: 'APPROVED' }), '收益待核对')
  assert.equal(report.reportCommissionDesc({ ...missing, commission_source_label: '订单分账快照' }), '收益待核对')
  assert.equal(report.reportCommissionDesc({ ...missing, commission_type: 'FIXED',
    commission_fixed: null, commission_source_label: '订单分账快照' }), '收益待核对')
  assert.equal(report.estimatedPayoutDisplay({ ...missing, expected_income: 0 }), '预计 0 币')
})

test('every admin CRUD resource resolves to an existing backend router registration', () => {
  const frontend = fs.readFileSync(path.join(appRoot, 'src/api/modules.ts'), 'utf8')
  const backend = fs.readFileSync(path.resolve(appRoot, '../../services/backend/console/urls.py'), 'utf8')
  const registered = new Set([...backend.matchAll(/router\.register\('([^']+)'/g)].map(match => match[1]))
  const resources = [...frontend.matchAll(/createCrudApi\('([^']+)'\)/g)].map(match => match[1])
  assert.ok(resources.length >= 30)
  for (const resource of resources) assert.ok(registered.has(resource), `missing backend resource: ${resource}`)
})

test('selecting a package fixes one or two provider slots and shows the per-person double price', async () => {
  const dispatchForm = { service_id: 12, escort_mode: 'SINGLE', providers: [{ provider_id: undefined }] }
  const currentService = { value: null }
  const serviceOptions = { value: [
    { id: 12, name: '单陪套餐', price: 2000, price_unit: '局', billing_provider_count: 1 },
    { id: 13, name: '双陪套餐', price: 2000, price_unit: '局', billing_provider_count: 2 },
  ] }
  const handlers = pageFunctions('views/order/index.vue',
    ['serviceProviderCount', 'serviceOptionLabel', 'syncDispatchProviderCount', 'onServiceChange'], {
      currentService, dispatchForm, serviceOptions, dispatchCommissionMode: { value: 'FIXED' }, onProviderPicked: async () => {},
      amountToXaCoin: moneyModule.exports.formatXaCoin, providerLoading: { value: false }, providerSearchError: { value: '' }, providerOptions: { value: [] }, serviceLoadError: { value: '' },
    }, 'let providerSearchSequence = 0')
  await handlers.onServiceChange(13)
  assert.equal(dispatchForm.escort_mode, 'DOUBLE')
  assert.equal(dispatchForm.providers.length, 2)
  assert.equal(handlers.serviceOptionLabel(currentService.value), '双陪套餐（每人 200币 × 2 / 局）')
  await handlers.onServiceChange(12)
  assert.equal(dispatchForm.escort_mode, 'SINGLE')
  assert.equal(dispatchForm.providers.length, 1)
  assert.equal(dispatchForm.provider_gender, 'ANY', 'non-companion services cannot retain a gender-only companion filter')
  assert.equal(serviceOptions.value[1].price, 2000, 'catalog unit price is never rewritten into total price')
  assert.equal(handlers.serviceProviderCount({}), null, 'missing backend count is not guessed as one')
})

function invalidDispatchHarness(count, mode, slots) {
  const warnings = [], sent = []
  const dispatchForm = { customer_id: 10, service_id: 13, game_rounds: 1, escort_mode: mode,
    provider_gender: 'ANY', remark: '', providers: slots }
  const { onDispatchSave } = pageFunctions('views/order/index.vue', ['serviceProviderCount', 'onDispatchSave'], {
    pageDisposed: false, auth: { sessionRevision: 1, hasPerm: () => true },
    dispatchForm, currentService: { value: { billing_provider_count: count } },
    dispatching: { value: false }, dispatchConfirming: { value: false }, dispatchResultUnknown: { value: false },
    dispatchReview: { value: null }, dispatchVisible: { value: true },
    ElMessage: { warning: value => warnings.push(value) },
    request: async config => { sent.push(config); throw new Error('invalid input must not send a request') },
  })
  return { onDispatchSave, warnings, sent }
}

test('single packages cannot submit a manual double override and missing package counts block dispatch', async () => {
  const slots = [20, 21].map(provider_id => ({ provider_id, commission_type: 'PERCENT', commission_rate: 20 }))
  const single = invalidDispatchHarness(1, 'DOUBLE', slots)
  await single.onDispatchSave()
  assert.equal(single.sent.length, 0)
  assert.match(single.warnings[0], /陪玩人数与所选套餐不一致/)
  const missing = invalidDispatchHarness(undefined, 'SINGLE', slots.slice(0, 1))
  await missing.onDispatchSave()
  assert.equal(missing.sent.length, 0)
  assert.match(missing.warnings[0], /套餐人数尚未加载/)
})

test('double dispatch rejects duplicate providers before any request', async () => {
  const slots = [20, 21].map(provider_id => ({ provider_id, commission_type: 'PERCENT', commission_rate: 20 }))
  const duplicate = invalidDispatchHarness(2, 'DOUBLE', [slots[0], slots[0]])
  await duplicate.onDispatchSave()
  assert.equal(duplicate.sent.length, 0)
  assert.match(duplicate.warnings[0], /不可重复指派/)
})

test('assignment uses server eligibility and historic team size independently of catalog edits', () => {
  const warnings = [], assignOrder = { value: null }, assignProviderId = { value: undefined }
  const assignVisible = { value: false }, providerOptions = { value: [] }
  const handlers = pageFunctions('views/order/index.vue', ['orderTeamSize', 'isStaffTeamAssign', 'canAssignOrder', 'onAssign'], {
    assignOrder, assignProviderId, assignVisible, providerOptions, pageDisposed: false, assigning: { value: false }, auth: { hasPerm: () => true },
    assignProviderIds: { value: [] }, assignProviderOptions: { value: [] },
    assignSearchKeyword: { value: '' }, assignSearchError: { value: '' }, assignSubmitError: { value: '' }, assignReview: { value: null },
    ElMessage: { warning: value => warnings.push(value) },
  }, 'let assignSearchSequence = 0')
  const historic = { id: 1, escort_mode: 'DOUBLE', required_provider_count: 1, required_team_size: 2,
    dispatch_route: 'POOL', status: 'PENDING', payment_status: 'PAID', dispatch_assignment_allowed: true,
    service: { billing_provider_count: 1 }, providers: [{ provider: 20 }] }
  handlers.onAssign(historic)
  assert.equal(assignVisible.value, false)
  assert.match(warnings[0], /无法派单/)
  const single = { id: 2, required_team_size: 1, status: 'PENDING', payment_status: 'PAID',
    dispatch_assignment_allowed: true, service: { billing_provider_count: 2 }, providers: [{}, {}] }
  handlers.onAssign(single)
  assert.equal(assignVisible.value, true)
  assert.equal(assignOrder.value, single)
  assert.equal(handlers.canAssignOrder({ ...single, dispatch_assignment_allowed: false }), false)
  assert.equal(handlers.canAssignOrder({ ...historic, dispatch_route: 'STAFF' }), true)
  assert.equal(handlers.orderTeamSize({ required_provider_count: 2 }), null, 'server compatibility team result is required')
})

test('staff deadlines remain visible after expiry and schedule a server reread', () => {
  const callbacks = [], delays = [], cancelled = [], refreshes = []
  const document = { hidden: false }
  const handlers = pageFunctions('views/order/index.vue',
    ['isStaffPendingOrder', 'isStaffPending', 'pendingDeadlineDelay', 'schedulePendingDeadline'], {
      document, pageDisposed: false, canReadOrders: { value: true },
      setTimeout: (fn, delay) => { callbacks.push(fn); delays.push(delay); return callbacks.length },
      clearTimeout: id => cancelled.push(id), requestOrderRefresh: () => refreshes.push('server'),
    }, 'let pendingDeadlineTimer = null')
  const now = Date.now()
  const pending = { status: 'PENDING', payment_status: 'PAID', dispatch_route: 'STAFF',
    staff_dispatch_actionable: true, dispatch_assignment_allowed: true, auto_cancel_at: new Date(now + 5000).toISOString() }
  assert.equal(handlers.isStaffPendingOrder(pending), true)
  assert.equal(handlers.isStaffPending({ ...pending, staff_dispatch_actionable: false }), false)
  assert.equal(handlers.isStaffPendingOrder({ ...pending, staff_dispatch_actionable: false }), true)
  assert.equal(handlers.pendingDeadlineDelay([pending], now), 5500)
  assert.equal(handlers.pendingDeadlineDelay([{ ...pending, auto_cancel_at: new Date(now - 5000).toISOString() }], now), 60000)
  assert.equal(handlers.pendingDeadlineDelay([{ ...pending, dispatch_assignment_allowed: false }], now), null)
  handlers.schedulePendingDeadline([pending])
  assert.ok(delays[0] >= 1000 && delays[0] <= 6000)
  callbacks[0]()
  assert.deepEqual(refreshes, ['server'])
  handlers.schedulePendingDeadline([pending])
  document.hidden = true
  handlers.schedulePendingDeadline([pending])
  assert.deepEqual(cancelled, [2])
})

test('an open assignment requires refreshed server eligibility or a retry', async () => {
  let response = deferred()
  const assignOrder = { value: { id: 7 } }, assignVisible = { value: true }
  const assignSubmitError = { value: '' }
  const { refreshAssignmentEligibility } = pageFunctions('views/order/index.vue', ['refreshAssignmentEligibility'], {
    assignOrder, assignVisible, assignSubmitError, assigning: { value: false }, pageDisposed: false,
    canReadOrders: { value: true }, auth: { sessionRevision: 1 },
    canAssignOrder: order => order?.dispatch_assignment_allowed === true,
    request: () => response.promise,
  })
  let refresh = refreshAssignmentEligibility()
  response.resolve({ data: { id: 7, dispatch_assignment_allowed: false } })
  await refresh
  assert.match(assignSubmitError.value, /不可派/)

  assignSubmitError.value = ''
  response = deferred()
  refresh = refreshAssignmentEligibility()
  response.reject(new Error('offline'))
  await refresh
  assert.match(assignSubmitError.value, /更新失败/)

  assignSubmitError.value = ''
  response = deferred()
  refresh = refreshAssignmentEligibility()
  assignOrder.value = { id: 8 }
  response.resolve({ data: { id: 7, dispatch_assignment_allowed: false } })
  await refresh
  assert.equal(assignSubmitError.value, '', 'late response must not block another order')
})

test('late commission suggestions from a previous package cannot alter current provider pricing', async () => {
  const pending = deferred()
  const slot = { provider_id: 20, commission_rate: 10 }
  const dispatchForm = { service_id: 12, providers: [slot] }
  const { onProviderPicked } = pageFunctions('views/order/index.vue', ['onProviderPicked', 'dispatchPendingPercentage', 'dispatchCommissionParams'], {
    dispatchForm, dispatchQuote: { value: { commission_policy: 'LEGACY' } }, request: () => pending.promise,
    auth: { sessionRevision: 1 }, pageDisposed: false, dispatchVisible: { value: true },
    dispatchCommissionMode: { value: 'AUTO' }, dispatchCommissionFixed: { value: 0 }, dispatchPendingRate: { value: undefined },
  })
  const previous = onProviderPicked(0)
  dispatchForm.service_id = 13
  pending.resolve({ data: { commission_rate: 90, source_label: '旧套餐建议' } })
  await previous
  assert.equal(slot.commission_rate, 10)
})

test('existing-order provider search cannot reuse a closed quick-dispatch package filter', async () => {
  const requested = [], assignProviderOptions = { value: [] }
  const { searchAssignProvider } = pageFunctions('views/order/index.vue', ['searchAssignProvider'], {
    pageDisposed: false, auth: { sessionRevision: 1, hasPerm: () => true },
    assignVisible: { value: true }, assignOrder: { value: { id: 7 } }, isStaffTeamAssign: () => false,
    assignProviderIds: { value: [] }, assignProviderId: { value: undefined }, assignProviderOptions,
    assignSearchKeyword: { value: '' }, assignSearchError: { value: '' }, assignProviderLoading: { value: false },
    escortApi: { options: async params => {
      requested.push(params)
      return { data: { list: [{ user: 20, qualifications: [{ provider_kind: 'OTHER' }] }] } }
    } },
  }, 'let assignSearchSequence = 0')
  await searchAssignProvider('测试陪玩')
  assert.equal(requested[0].service_id, undefined)
  assert.equal(assignProviderOptions.value[0].qualifications.length, 1)
})

test('quick dispatch gender changes remove incompatible selection and ignore stale candidate search', async () => {
  const requests = [], pending = []
  const dispatchForm = { service_id: 12, provider_gender: 'ANY', providers: [{ provider_id: 1 }] }
  const providerOptions = { value: [{ user: 1, gender: 'MALE' }] }, warnings = []
  const scope = {
    dispatchForm, providerOptions, currentService: { value: { service_type: 'COMPANION' } },
    providerSearchKeyword: { value: '' }, providerSearchError: { value: '' }, providerLoading: { value: false },
    dispatchRequestId: { value: 'request-1' }, dispatchVisible: { value: true }, pageDisposed: false,
    auth: { sessionRevision: 1, hasPerm: () => true }, ElMessage: { warning: message => warnings.push(message) },
    onProviderPicked: async () => {},
    request: options => { requests.push(options.params); const result = deferred(); pending.push(result); return result.promise },
  }
  const { searchProvider, onDispatchGenderChange } = pageFunctions('views/order/index.vue',
    ['searchProvider', 'onDispatchGenderChange'], scope, 'let providerSearchSequence = 0')
  const oldSearch = searchProvider('陪玩')
  dispatchForm.provider_gender = 'FEMALE'
  onDispatchGenderChange()
  assert.equal(dispatchForm.providers[0].provider_id, undefined)
  assert.match(warnings[0], /已移除/)
  assert.equal(requests[1].provider_gender, 'FEMALE')
  pending[1].resolve({ data: { list: [{ user: 2, gender: 'FEMALE', qualifications: [] }] } })
  await new Promise(resolve => setImmediate(resolve))
  pending[0].resolve({ data: { list: [{ user: 1, gender: 'MALE', qualifications: [] }] } })
  await oldSearch
  assert.deepEqual(Array.from(providerOptions.value, row => row.user), [2])
})

test('assignment only calls explicit client rejection a failure', () => {
  const { assignFailureMessage } = pageFunctions('views/order/index.vue', ['assignRejected', 'assignFailureMessage'], {})
  assert.match(assignFailureMessage({ code: 400, msg: '该陪玩当前不可接单' }), /指派未完成：该陪玩当前不可接单/)
  assert.match(assignFailureMessage({ response: { status: 503, data: { code: 500, msg: '服务异常' } } }), /结果未确认/)
  assert.match(assignFailureMessage({ response: { status: 503, data: { code: 400, msg: '代理错误' } } }), /结果未确认/)
  assert.match(assignFailureMessage({ message: '网络错误' }), /结果未确认/)
})

test('consumption reversal uses only explicit refund effects', () => {
  const { consumptionReversed } = pageFunctions('views/order/index.vue', ['consumptionReversed'], {})
  assert.equal(consumptionReversed({ payment_status: 'REFUNDED' }), false)
  assert.equal(consumptionReversed({ refund_summary: { records: [{ benefit_effects: { confirmation_reversed: true } }] } }), true)
  assert.equal(consumptionReversed({ refund_summary: { benefit_effects: { confirmation_reversed: true } } }), true)
  assert.equal(consumptionReversed({ refund_summary: { records: [{ benefit_effects: { confirmation_reversed: false } }] } }), false)
})

test('same-name providers are distinguished without guessing missing identities', () => {
  const source = fs.readFileSync(path.join(appRoot, 'src/utils/orderParticipants.ts'), 'utf8')
  const module = { exports: {} }
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText, { module, exports: module.exports })
  const { providerListText, currentProviderIdentities } = module.exports
  const { providerIdentityLabel } = pageFunctions('views/order/index.vue', ['providerIdentityLabel'], {})
  const order = { providers: [{ provider_name: '同名', provider_no: 'A1' }, { provider_name: '同名', provider_username: 'old', provider_identity_source: 'LEGACY' }] }
  assert.equal(providerListText(order), '同名（编号 A1）、同名（旧账号 old）')
  assert.equal(providerIdentityLabel(order.providers[1]), '同名 · 旧账号 old')
  assert.equal(providerListText({ providers: [{ provider_name: '同名' }, { provider_name: '同名' }] }), '同名（身份待核对）、同名（身份待核对）')
  assert.equal(providerListText({ providers: [{ provider_name: '同名', provider_no: 'A1', provider_username: 'one' }, { provider_name: '同名', provider_no: 'A1', provider_username: 'two' }] }), '同名（编号 A1 · 账号 one）、同名（编号 A1 · 账号 two）')
  assert.equal(currentProviderIdentities({ providers: [{ provider_name: '转出', released_at: '2026-01-01' }, { provider_name: '当前' }] }).length, 1)
})
