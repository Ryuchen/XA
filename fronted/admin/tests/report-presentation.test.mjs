import { after, test } from 'node:test'
import assert from 'node:assert/strict'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({
  root, configFile: false, logLevel: 'silent',
  resolve: { alias: { '@': resolve(root, 'src') } },
  server: { middlewareMode: true, watch: { ignored: ['**/*'] } },
})
after(() => server.close())
const { reportPayoutDisplay, reportSettlementLabel } = await server.ssrLoadModule('/src/lib/reports.ts')

test('negotiated report distinguishes own settled, waiting and review states', () => {
  const row = { status: 'APPROVED', order_payment_status: 'PARTIALLY_REFUNDED',
    negotiated_settlement: true, payout_amount: 3000 }
  assert.match(reportPayoutDisplay(row), /^协商保留收益/)
  assert.equal(reportSettlementLabel({ ...row, settlement_status: 'SETTLED' }), '已结算（以钱包为准）')
  assert.equal(reportSettlementLabel({ ...row, settlement_status: 'WAITING' }), '待本人结算条件满足')
  assert.equal(reportSettlementLabel({ ...row, settlement_status: 'REVIEW' }), '收益待核对')
  assert.equal(reportSettlementLabel({ ...row, negotiated_settlement: false, settlement_status: 'SETTLED' }),
    '已部分退款，净收益以钱包流水为准')
})
