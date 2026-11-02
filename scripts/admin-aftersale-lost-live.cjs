// Run the actual admin controller with isolated Django HTTP. Only the two
// selected successful transport responses are discarded; business stays real.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const adminRequire = createRequire(require('node:path').resolve('apps/admin-web/package.json'));
const vue = adminRequire('vue'), ts = adminRequire('typescript');
const money = require('../packages/money/src/index.js');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const part = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync('apps/admin-web/src/utils/orderParticipants.ts', 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { module: part, exports: part.exports });
let transport = Promise.resolve(), drop = '', writes = 0, calls = [];
async function http(config, token = input.staffToken) {
  const url = new URL((config.url || '').replace(/^\//, ''), input.base + (config.baseURL || '/api/admin') + '/');
  for (const [key, value] of Object.entries(config.params || {})) if (value !== undefined && value !== '') url.searchParams.set(key, String(value));
  const task = transport.then(async () => {
    const response = await fetch(url, { method: (config.method || 'GET').toUpperCase(),
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: config.data === undefined ? undefined : JSON.stringify(config.data) });
    return response.json();
  });
  transport = task.then(() => {}, () => {});
  const result = await task;
  calls.push({ path: url.pathname, query: url.search, status: result.code, msg: result.msg });
  if (result.code !== 0) throw result;
  if ((config.method || 'get').toLowerCase() === 'post') writes++;
  if (url.pathname === drop && (config.method || 'get').toLowerCase() === 'post') { drop = ''; throw new Error('response lost'); }
  return result;
}
const mounted = [], unmounted = [], warnings = [];
const auth = vue.reactive({ token: input.staffToken, sessionRevision: 1, profile: { id: input.staffId }, hasPerm(code) {
  return (Array.isArray(code) ? code : [code]).some(item => ['order:view', 'order:refund', 'order:settle'].includes(item));
} });
const modules = {
  vue: { ...vue, onMounted: fn => mounted.push(fn), onUnmounted: fn => unmounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn) },
  'vue-router': { useRoute: () => vue.reactive({ query: {} }), useRouter: () => ({ replace() {} }) },
  'element-plus': { ElMessage: { success() {}, warning: x => warnings.push(x) }, ElMessageBox: { prompt: async () => ({ value: '已与老板核对问题' }), close() {} } },
  '@element-plus/icons-vue': { Plus: {}, Search: {}, Setting: {} },
  '@/components/CrudTable.vue': {}, '@/components/DispatchMessageDialog.vue': {},
  '@/api/modules': { orderApi: {}, aftersaleApi: {}, transactionApi: {}, serviceItemApi: {}, userApi: {}, escortApi: {} },
  '@/api/request': { request: http }, '@/utils/dict': { ORDER_STATUS: {}, PAYMENT_STATUS: {}, TX_TYPE: {}, TX_STATUS: {} },
  '@/utils/format': { amountToXaCoin: money.formatXaCoin, xaCoinToAmount: money.toRawAmount, formatDateTime: String },
  '@/utils/commission': { commissionError: () => '' }, '@/utils/orderParticipants': part.exports,
  '@/stores/auth': { useAuthStore: () => auth }, '@/stores/staffDispatchQueue': { useStaffDispatchQueue: () => ({ status: 'idle' }) },
  '@/utils/ws': { adminWs: { on() { return () => {}; }, connect() {} } },
};
const source = fs.readFileSync('apps/admin-web/src/views/order/index.vue', 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const exposed = 'tableRef,current,aftersales,onDetail,processAfterSale,refundVisible,refundResultUnknown,reviewUnknownRefund,refundReason,refundError,refundCanSubmit,loadRefundPreview,submitRefund,refundAmountCoins,refundPreview,refreshCurrentOrder,pastAftersales,aftersaleStatusText';
const code = ts.transpileModule(source + '\nglobalThis.page = {' + exposed + '};', { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const context = { exports: {}, crypto: require('node:crypto').webcrypto,
  document: { hidden: false, addEventListener() {}, removeEventListener() {} },
  require: name => { assert.ok(modules[name], name); return modules[name]; }, setTimeout, clearTimeout };
const scope = vue.effectScope(); scope.run(() => vm.runInNewContext(code, context));
const page = context.page;
page.tableRef.value = { load: async () => {}, refresh: async () => {}, reload() {} };
const wait = async predicate => { for (let i = 0; i < 300 && !predicate(); i++) await new Promise(r => setTimeout(r, 10)); assert.ok(predicate(), 'timeout waiting for actual page state'); };
;(async () => {
  mounted.forEach(fn => fn());
  await page.onDetail({ id: input.orderId });
  assert.ok(page.aftersales.value[0], JSON.stringify({ calls, order: page.current.value && page.current.value.id }));
  assert.equal(page.aftersales.value[0].status, 'OPEN');
  await page.processAfterSale(page.aftersales.value[0], 'refund');
  page.refundAmountCoins.value = '250';
  await page.loadRefundPreview();
  page.refundReason.value = '隔离场景协商部分退款';
  assert.equal(page.refundCanSubmit.value, true, JSON.stringify({ preview: page.refundPreview.value, error: page.refundError.value, calls }));
  drop = `/api/admin/aftersales/${input.ticketId}/refund/`;
  await page.submitRefund();
  assert.equal(page.refundResultUnknown.value, true);
  assert.match(page.refundError.value, /退款结果未确认/);
  assert.equal(page.refundCanSubmit.value, false);
  const beforeRetry = writes;
  await page.submitRefund();
  assert.equal(writes, beforeRetry, 'uncertain refund must not be resubmitted');
  page.reviewUnknownRefund();
  await wait(() => page.current.value?.id === input.orderId && page.current.value?.payment_status === 'PARTIALLY_REFUNDED');
  await page.refreshCurrentOrder();
  assert.equal(page.aftersales.value.find(row => row.id === input.ticketId).status, 'PARTIALLY_REFUNDED');
  const boss = (await http({ baseURL: '/api', url: `orders/orders/${input.orderId}/` }, input.bossToken)).data;
  const provider = (await http({ baseURL: '/api', url: `orders/orders/${input.orderId}/` }, input.providerToken)).data;
  assert.equal(boss.refund_summary.amount, 2500);
  assert.equal(provider.refund_summary.amount, 2500);
  assert.equal(provider.refund_summary.net_income, 6000);
  const providerWallet = (await http({ baseURL: '/api', url: 'wallet/transactions/' }, input.providerToken)).data;
  assert.equal(providerWallet.balance, 6000);
  await page.onDetail({ id: input.secondOrder });
  const ticket = page.aftersales.value.find(row => row.id === input.secondTicket);
  assert.equal(ticket.status, 'OPEN');
  drop = `/api/admin/aftersales/${input.secondTicket}/resolve/`;
  await page.processAfterSale(ticket, 'resolve');
  assert.ok(warnings.some(x => x.includes('售后处理结果未确认')));
  await wait(() => page.aftersales.value.some(row => row.id === input.secondTicket && row.status === 'RESOLVED'));
  const secondBoss = (await http({ baseURL: '/api', url: `orders/orders/${input.secondOrder}/` }, input.bossToken)).data;
  const secondProvider = (await http({ baseURL: '/api', url: `orders/orders/${input.secondOrder}/` }, input.providerToken)).data;
  assert.equal(secondBoss.aftersale_status, 'RESOLVED');
  assert.equal(secondProvider.aftersale_status, 'RESOLVED');
  assert.equal(page.pastAftersales(page.aftersales.value[0]).length, 0, 'current resolution is not a past case');
  await http({ baseURL: '/api', url: `orders/orders/${input.secondOrder}/aftersale/`, method: 'post',
    data: { reason: '再次申请核对', client_request_id: 'admin-history-second-case' } }, input.bossToken);
  await page.refreshCurrentOrder();
  const reopened = page.aftersales.value.find(row => row.id === input.secondTicket);
  assert.equal(reopened.status, 'OPEN');
  assert.equal(reopened.reason, '再次申请核对');
  const history = page.pastAftersales(reopened);
  assert.equal(history.length, 1);
  assert.equal(history[0].reason, '仅客服核对的具体售后说明');
  assert.equal(history[0].resolution, '已与老板核对问题');
  assert.equal(page.aftersaleStatusText(history[0].status), '处理完成');
  console.log('real HTTP response-loss refund and resolution verified');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; })
  .finally(() => { unmounted.forEach(fn => fn()); scope.stop(); });
