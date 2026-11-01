const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const { createRequire } = require('node:module')

const root = path.resolve(__dirname, '..')
const ts = createRequire(path.join(root, 'package.json'))('typescript')
const source = fs.readFileSync(path.join(root, 'src/views/order/index.vue'), 'utf8')
const script = source.match(/<script setup[^>]*>([\s\S]*?)<\/script>/)[1]
const ast = ts.createSourceFile('order.ts', script, ts.ScriptTarget.Latest, true)
const statements = ast.statements.filter(node => ts.isFunctionDeclaration(node) &&
  ['settlementStatusLabel', 'historicalSettlementRecorded', 'onComplete', 'confirmServiceAction'].includes(node.name?.text))
const code = ts.transpileModule([...statements.map(node => node.getText(ast)),
  'let pendingActionOrderId = null; module.exports = { settlementStatusLabel, historicalSettlementRecorded, onComplete }'].join('\n'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText

test('order details and completion confirmation use conservative settlement facts', async () => {
  const confirmations = []
  const actions = []
  const module = { exports: {} }
  vm.runInNewContext(code, {
    module, exports: module.exports,
    ElMessageBox: { confirm: async message => confirmations.push(message) },
    ElMessage: { success() {}, warning() {} },
    beginOrderAction: () => ({ current: () => true, finish() {} }),
    request: async config => actions.push(config),
    requestOrderRefresh() {},
  })
  const { settlementStatusLabel, historicalSettlementRecorded, onComplete } = module.exports
  assert.equal(settlementStatusLabel(undefined), '收益待核对')
  assert.equal(settlementStatusLabel('unexpected'), '收益待核对')
  assert.equal(settlementStatusLabel('REVIEW'), '收益待核对')
  assert.equal(settlementStatusLabel('WAITING'), '待结算')
  assert.equal(settlementStatusLabel('SETTLED'), '已结算')
  for (const payment_status of ['REFUNDED', 'PARTIALLY_REFUNDED']) {
    assert.equal(historicalSettlementRecorded({ payment_status, settled_at: '2026-01-01', settlement_status: 'REVIEW' }), true)
    assert.equal(historicalSettlementRecorded({ payment_status, settlement_status: 'REVIEW' }, { settled_at: '2026-01-01' }), true)
    assert.equal(historicalSettlementRecorded({ payment_status, settlement_status: 'REVIEW' }), false)
  }
  assert.equal(historicalSettlementRecorded({ payment_status: 'PAID', settled_at: '2026-01-01', settlement_status: 'REVIEW' }), false)
  for (const settlement_mode of ['COMPLETION', 'REPORT_APPROVAL', undefined]) {
    await onComplete({ id: 5, status: 'IN_SERVICE', settlement_mode })
  }
  assert.match(confirmations[0], /旧规则结算.*流水为准/)
  assert.match(confirmations[1], /等待报单审核.*流水为准/)
  assert.match(confirmations[2], /结算方式待核对.*流水为准/)
  assert.equal(actions.length, 3)
  assert.ok(actions.every(item => item.url === '/orders/5/complete/' && item.method === 'post'))
  assert.match(source, /settlementStatusLabel\(current\.settlement_status\)/)
  assert.match(source, /current\.settlement_status === 'SETTLED' && current\.settled_at/)
  assert.match(source, /historicalSettlementRecorded\(current, p\) \? '原结算收益' : '原预计收益'/)
  assert.match(source, /historicalSettlementRecorded\(current\) \? '原结算陪玩收益' : '原预计陪玩收益'/)
  assert.match(source, /'原店铺留存快照' : '店铺留存快照'/)
  assert.match(source, /!\['REFUNDED', 'PARTIALLY_REFUNDED'\]\.includes\(current\.payment_status\) && current\.settlement_status === 'SETTLED'/)
  assert.doesNotMatch(source, /v-if="p\.settled_at" class="od-log-op"/)
})
