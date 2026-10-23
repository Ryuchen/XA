const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { createRequire } = require('node:module');
const adminRequire = createRequire(path.resolve('apps/admin-web/package.json'));
const vue = adminRequire('vue');
const vueRequire = createRequire(adminRequire.resolve('vue/package.json'));
const { renderToString } = vueRequire('@vue/server-renderer');

// Render the actual financial bindings from the order drawer, without API calls.
const source = fs.readFileSync(path.resolve('apps/admin-web/src/views/order/index.vue'), 'utf8');
const lines = source.split(/\r?\n/).filter(line => line.includes('原预计陪玩收益')
  || line.includes('原预计店铺留存') || line.includes('订单已退款，不再结算以上预计收益'));
assert.equal(lines.length, 3, 'The drawer must expose both financial labels and the refund explanation');
const recoveryStart = source.indexOf('<section v-if="current.refund_summary"');
const recoverySection = source.slice(recoveryStart, source.indexOf('<div v-for="ticket in aftersales"', recoveryStart));
const render = vue.compile('<div>' + lines.join('\n') + recoverySection + '</div>');
const Description = vue.defineComponent({ props: ['label'], setup: (props, { slots }) => () =>
  vue.h('div', [vue.h('span', props.label), ...(slots.default?.() || [])]) });
const Alert = vue.defineComponent({ props: ['title'], setup: props => () => vue.h('p', props.title) });

async function renderFinancials(current) {
  const before = JSON.stringify(current);
  const html = await renderToString(vue.createSSRApp({
    components: { ElDescriptionsItem: Description, ElAlert: Alert, ElDescriptions: { setup: (_, { slots }) => () => vue.h('div', slots.default?.()) } },
    setup: () => ({ current, amountToXaCoin: amount => String(amount / 10), formatDateTime: String, TX_TYPE: {}, refundSourceLabel: String }), render,
  }));
  assert.equal(JSON.stringify(current), before, 'Display formatting must not modify financial snapshots');
  assert.match(html, /800 币/); assert.match(html, /200 币/);
  return html;
}

test('refunded orders label original forecast amounts and explain they will not settle', async () => {
  const html = await renderFinancials(Object.freeze({ payment_status: 'REFUNDED', settled_at: null, provider_income: 8000, shop_income: 2000 }));
  assert.match(html, /原预计陪玩收益/); assert.match(html, /原预计店铺留存/);
  assert.match(html, /订单已退款，不再结算以上预计收益/);
});

test('unsettled paid orders label their income as forecasts', async () => {
  const html = await renderFinancials(Object.freeze({ payment_status: 'PAID', settled_at: null, provider_income: 8000, shop_income: 2000 }));
  assert.match(html, />预计陪玩收益</); assert.match(html, />预计店铺留存</);
  assert.doesNotMatch(html, /原预计|订单已退款/);
});

test('settled orders keep their existing actual-income labels', async () => {
  const html = await renderFinancials(Object.freeze({ payment_status: 'PAID', settled_at: '2026-10-20T00:00:00+08:00', provider_income: 8000, shop_income: 2000 }));
  assert.match(html, />陪玩实得</); assert.match(html, />店铺留存</);
  assert.doesNotMatch(html, /预计|订单已退款/);
});

test('settled refunded orders retain original settlement and show actual linked reversals', async () => {
  const html = await renderFinancials(Object.freeze({ payment_status: 'REFUNDED', settled_at: '2026-10-20T00:00:00+08:00', provider_income: 8000, shop_income: 2000,
    refund_summary: { id: 1, amount: 10000, recovered_amount: 10000, created_at: '2026-10-20T08:00:00+08:00', recovery_lines: [
      { original_transaction_id: 51, reversal_transaction_id: 61, wallet_id: 11, account_id: 1147, tx_type: 'INCOME', amount: -8000, source: 'INCOME' },
      { original_transaction_id: 52, reversal_transaction_id: 62, wallet_id: 1, account_id: null, tx_type: 'SHOP_INCOME', amount: -2000, source: 'COMMISSION' },
    ], benefit_effects: { confirmation_reversed: true, net_consumption: 30000, previous_boss_type_id: 2, boss_type_id: 1,
      expired_coupon_ids: [71, 72], cancelled_claim_ids: [81], deactivated_tag_ids: [91], consumed_coupon_ids: [73], delivered_claim_ids: [82],
      consumed_benefits_policy: '已用券和已完成交付保留原历史并列明；不向其他订单追债' } } }));
  assert.match(html, /原结算陪玩收益/); assert.match(html, /原结算店铺留存/); assert.doesNotMatch(html, /预计|不再结算/);
  assert.match(html, /实际追回收益/); assert.match(html, /1000 币/); assert.match(html, /INCOME -800 币/); assert.match(html, /SHOP_INCOME -200 币/);
  assert.match(html, /原流水 #51 → 冲正流水 #61/); assert.match(html, /平台账户/);
  assert.match(html, /该单实销确认已冲回/); assert.match(html, /净实销：3000 币/); assert.match(html, /会员档位：#2 → #1/);
  assert.match(html, /撤销未用优惠券：#71、#72/); assert.match(html, /取消未交付权益：#81/);
  assert.match(html, /保留已用优惠券历史：#73/); assert.match(html, /保留已交付权益历史：#82/); assert.match(html, /不向其他订单追债/);
});

test('a basic refund summary without financial-detail fields does not imply zero recovery', async () => {
  const html = await renderFinancials(Object.freeze({ payment_status: 'REFUNDED', settled_at: '2026-10-20T00:00:00+08:00', provider_income: 8000, shop_income: 2000,
    refund_summary: { id: 1, amount: 10000, created_at: '2026-10-20T08:00:00+08:00', reason: '本地退款' } }));
  assert.match(html, /退回老板/); assert.doesNotMatch(html, /实际追回收益|实际冲正流水|未产生收益冲正/);
});

test('partial refund history displays cumulative server amounts and separate actual records', async () => {
  const html = await renderFinancials(Object.freeze({ payment_status: 'PARTIALLY_REFUNDED', settled_at: '2026-10-20T00:00:00Z', provider_income: 8000, shop_income: 2000,
    refund_summary: { amount: 4000, original_amount: 10000, remaining_amount: 6000, refund_count: 2, recovered_amount: 4000, records: [
      { id: 21, amount: 1500, created_at: '2026-10-20T01:00:00Z', reason: '第一笔部分退款', recovered_amount: 1500, recovery_lines: [] },
      { id: 22, amount: 2500, created_at: '2026-10-20T02:00:00Z', reason: '第二笔部分退款', recovered_amount: 2500, recovery_lines: [
        { original_transaction_id: 51, reversal_transaction_id: 62, wallet_id: 11, account_id: 1147, tx_type: 'INCOME', amount: -2000, source: 'INCOME' },
      ] },
    ] } }));
  assert.match(html, /原结算陪玩收益/); assert.match(html, /累计退回老板/); assert.match(html, /400 币/); assert.match(html, /剩余可退/); assert.match(html, /600 币/);
  assert.match(html, /退款 #21 · 150 币/); assert.match(html, /退款 #22 · 250 币/); assert.match(html, /第一笔部分退款/); assert.match(html, /第二笔部分退款/);
  assert.match(html, /本笔实际追回收益：250 币/); assert.match(html, /原流水 #51 → 冲正流水 #62/); assert.doesNotMatch(html, /不再结算以上预计收益/);
});
