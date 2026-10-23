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
  const requests = new Map()
  const requestFor = key => {
    const pending = deferred()
    requests.set(key, pending)
    return pending.promise
  }
  const { onDetail, refreshCurrentOrder } = pageFunctions('views/order/index.vue',
    ['onDetail', 'clearOrderDetail', 'isCurrentOrderDetail', 'refreshCurrentOrder'], {
    current, aftersales, logs, detailVisible, detailOrderId, detailLoading, detailFailed,
    pageDisposed: false, canReadOrders: { value: true }, auth: { sessionRevision: 1 },
    refundTarget: { value: null },
    orderApi: { retrieve: id => requestFor(`order-${id}`) },
    aftersaleApi: { list: ({ order }) => requestFor(`tickets-${order}`) },
    request: ({ url }) => requestFor(`logs-${url.match(/orders\/(\d+)/)[1]}`),
  }, 'let detailSequence = 0, detailRefreshPending = false')
  const settle = id => {
    requests.get(`order-${id}`).resolve({ data: { id, order_no: `TEST-${id}` } })
    requests.get(`tickets-${id}`).resolve({ data: { list: [{ order: id }] } })
    requests.get(`logs-${id}`).resolve({ data: [{ order: id }] })
  }
  return { current, aftersales, logs, detailVisible, detailLoading, detailFailed, requests, onDetail, refreshCurrentOrder, settle }
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

test('an incomplete order detail load does not publish a mixed financial drawer', async () => {
  const harness = orderDetailHarness()
  const operation = harness.onDetail({ id: 3 })
  harness.requests.get('order-3').resolve({ data: { id: 3 } })
  harness.requests.get('tickets-3').resolve({ data: { list: [{ order: 3 }] } })
  harness.requests.get('logs-3').reject(new Error('history unavailable'))
  await operation
  assert.equal(harness.current.value.id, 3)
  assert.equal(harness.aftersales.value.length, 0)
  assert.equal(harness.logs.value.length, 0)
  assert.equal(harness.detailVisible.value, true)
  assert.equal(harness.detailLoading.value, false)
  assert.equal(harness.detailFailed.value, true, 'the error state hides financial details until all reads succeed')
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
const commissionModule = { exports: {} }
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(appRoot, 'src/utils/commission.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { module: commissionModule, exports: commissionModule.exports,
  require: () => ({ amountToXaCoin: moneyModule.exports.formatXaCoin, xaCoinToAmount: moneyModule.exports.toRawAmount }) })
const report = pageFunctions('views/report/index.vue', ['reportRate', 'reportCommissionDesc', 'reportEstimatedPayout'], {
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
  const pending = { amount: 2000, suggested_commission_rate: 35, commission_source_label: '报单填写', expected_income: 1300 }
  assert.equal(report.reportCommissionDesc(pending), '35% · 报单填写')
  assert.equal(report.reportEstimatedPayout(pending), 1300)
  const approved = { ...pending, status: 'APPROVED', commission_rate: 20,
    commission_source_label: '审核快照', expected_income: 1111 }
  assert.equal(report.reportCommissionDesc(approved), '20% · 审核快照')
  assert.equal(report.reportEstimatedPayout(approved), 1111)
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
      amountToXaCoin: moneyModule.exports.formatXaCoin,
    })
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

async function dispatchHarness(count, mode, slots) {
  const warnings = [], sent = []
  const dispatchForm = { customer_id: 10, service_id: 13, game_rounds: 1, escort_mode: mode,
    provider_gender: 'ANY', remark: '', providers: slots }
  const { onDispatchSave, refreshDispatchQuote } = pageFunctions('views/order/index.vue',
    ['serviceProviderCount', 'dispatchPendingPercentage', 'dispatchQuoteKey', 'dispatchCommissionParams', 'refreshDispatchQuote', 'onDispatchSave'], {
    pageDisposed: false, auth: { sessionRevision: 1, hasPerm: () => true },
    dispatchForm, currentService: { value: { billing_provider_count: count, price: 2000 } },
    dispatching: { value: false }, quoteLoading: { value: false }, dispatchQuote: { value: { amount: 4000 } },
    dispatchCommissionMode: { value: 'AUTO' }, dispatchCommissionFixed: { value: 0 }, dispatchPendingRate: { value: undefined },
    dispatchCommissionError: { value: '' }, dispatchFixed: { value: false }, commissionError: commissionModule.exports.commissionError,
    insufficient: { value: false }, dispatchRequestId: { value: 'test-dispatch' }, dispatchVisible: { value: true },
    tableRef: { value: { load() {} } }, xaCoinToAmount: moneyModule.exports.toRawAmount,
    ElMessage: { warning: value => warnings.push(value), success() {} },
    orderApi: { collectionAction: async (_, payload) => { sent.push(payload); return { data: { status: 'GRABBED' } } } },
    request: async () => ({ data: { amount: 4000, commission_type: 'PERCENT' } }),
  }, 'let quoteSequence = 0, quotedInputs = "", quotedSession = -1')
  await refreshDispatchQuote()
  return { onDispatchSave, warnings, sent }
}

test('single packages cannot submit a manual double override and missing package counts block dispatch', async () => {
  const slots = [20, 21].map(provider_id => ({ provider_id, commission_type: 'PERCENT', commission_rate: 20 }))
  const single = await dispatchHarness(1, 'DOUBLE', slots)
  await single.onDispatchSave()
  assert.equal(single.sent.length, 0)
  assert.match(single.warnings[0], /陪玩人数与所选套餐不一致/)
  const missing = await dispatchHarness(undefined, 'SINGLE', slots.slice(0, 1))
  await missing.onDispatchSave()
  assert.equal(missing.sent.length, 0)
  assert.match(missing.warnings[0], /套餐人数尚未加载/)
})

test('double dispatch submits two different providers with the unchanged server quote', async () => {
  const slots = [20, 21].map(provider_id => ({ provider_id, commission_type: 'PERCENT', commission_rate: 20 }))
  const valid = await dispatchHarness(2, 'DOUBLE', slots)
  await valid.onDispatchSave()
  assert.equal(valid.sent.length, 1)
  assert.equal(valid.sent[0].expected_amount, 4000)
  assert.equal(valid.sent[0].escort_mode, 'DOUBLE')
  assert.deepEqual(Array.from(valid.sent[0].providers, row => row.provider_id), [20, 21])
  const duplicate = await dispatchHarness(2, 'DOUBLE', [slots[0], slots[0]])
  await duplicate.onDispatchSave()
  assert.equal(duplicate.sent.length, 0)
  assert.match(duplicate.warnings[0], /不可重复指派/)
})

test('single assignment uses required_team_size and preserves historic double orders independently of catalog edits', () => {
  const warnings = [], assignOrder = { value: null }, assignProviderId = { value: undefined }
  const assignVisible = { value: false }, providerOptions = { value: [] }
  const handlers = pageFunctions('views/order/index.vue', ['orderTeamSize', 'onAssign'], {
    assignOrder, assignProviderId, assignVisible, providerOptions, ElMessage: { warning: value => warnings.push(value) },
  })
  const historic = { id: 1, escort_mode: 'DOUBLE', required_provider_count: 1, required_team_size: 2,
    service: { billing_provider_count: 1 }, providers: [{ provider: 20 }] }
  handlers.onAssign(historic)
  assert.equal(assignVisible.value, false)
  assert.match(warnings[0], /需两名陪玩/)
  const single = { id: 2, required_team_size: 1, service: { billing_provider_count: 2 }, providers: [{}, {}] }
  handlers.onAssign(single)
  assert.equal(assignVisible.value, true)
  assert.equal(assignOrder.value, single)
  assert.equal(handlers.orderTeamSize({ required_provider_count: 2 }), null, 'server compatibility team result is required')
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
  const requested = [], providerOptions = { value: [] }
  const { searchProvider } = pageFunctions('views/order/index.vue', ['searchProvider'], {
    dispatchVisible: { value: false }, dispatchForm: { service_id: 99 },
    currentService: { value: { provider_kind: 'WRONG_PACKAGE' } }, providerOptions, providerLoading: { value: false },
    escortApi: { options: async params => {
      requested.push(params)
      return { data: { list: [{ user: 20, qualifications: [{ provider_kind: 'OTHER' }] }] } }
    } },
  })
  await searchProvider('测试陪玩')
  assert.equal(requested[0].service_id, undefined)
  assert.equal(providerOptions.value[0].qualifications.length, 1)
})
