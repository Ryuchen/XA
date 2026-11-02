// Actual admin controller and actual isolated APIs; discard only a committed response.
const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const { createRequire } = require('node:module');
const adminRequire = createRequire(require('node:path').resolve('apps/admin-web/package.json'));
const vue = adminRequire('vue'), ts = adminRequire('typescript');
const money = require('../packages/money/src/index.js');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const part = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync('apps/admin-web/src/utils/orderParticipants.ts', 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { module: part, exports: part.exports });
let transport = Promise.resolve(), drop = false, transfers = 0;
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
  if (result.code !== 0) throw result;
  if (url.pathname.endsWith('/transfer/') && (config.method || 'get').toLowerCase() === 'post') {
    transfers++;
    if (drop) { drop = false; throw new Error('response lost after commit'); }
  }
  return result;
}
const unmounted = [], storage = new Map(), warnings = [];
const auth = vue.reactive({ token: input.staffToken, sessionRevision: 1, profile: { id: input.staffId },
  hasPerm(code) { return (Array.isArray(code) ? code : [code]).some(item => ['order:view', 'order:dispatch'].includes(item)); } });
const modules = {
  vue: { ...vue, onMounted() {}, onUnmounted: fn => unmounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn) },
  'vue-router': { useRoute: () => vue.reactive({ query: {} }), useRouter: () => ({ replace() {} }) },
  'element-plus': { ElMessage: { success() {}, warning: x => warnings.push(x) }, ElMessageBox: { confirm: async () => 'confirm', close() {} } },
  '@element-plus/icons-vue': { Plus: {}, Search: {}, Setting: {} },
  '@/components/CrudTable.vue': {}, '@/components/DispatchMessageDialog.vue': {},
  '@/api/modules': { orderApi: { action: (id, name, data) => http({ url: `/orders/${id}/${name}/`, method: 'post', data }) },
    aftersaleApi: {}, transactionApi: {}, serviceItemApi: {}, userApi: {}, escortApi: {} },
  '@/api/request': { request: http }, '@/utils/dict': { ORDER_STATUS: {}, PAYMENT_STATUS: {}, TX_TYPE: {}, TX_STATUS: {} },
  '@/utils/format': { amountToXaCoin: money.formatXaCoin, xaCoinToAmount: money.toRawAmount, formatDateTime: String },
  '@/utils/commission': { commissionError: () => '' }, '@/utils/orderParticipants': part.exports,
  '@/stores/auth': { useAuthStore: () => auth }, '@/stores/staffDispatchQueue': { useStaffDispatchQueue: () => ({ status: 'idle' }) },
  '@/utils/ws': { adminWs: { on() { return () => {}; }, connect() {} } },
};
const source = fs.readFileSync('apps/admin-web/src/views/order/index.vue', 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
const exposed = 'onTransfer,onTransferSave,transferRows,transferReview,transferSubmitError,viewTransferReview,transferReviewChecked,current,logs,finishTransferReview,closeTransferAndRefresh,tableRef';
const code = ts.transpileModule(source + '\nglobalThis.page = {' + exposed + '};', { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const context = { exports: {}, crypto: require('node:crypto').webcrypto, document: { hidden: false, addEventListener() {}, removeEventListener() {} },
  sessionStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
  require: name => { assert.ok(modules[name], name); return modules[name]; }, setTimeout, clearTimeout };
const scope = vue.effectScope(); scope.run(() => vm.runInNewContext(code, context));
const page = context.page;
page.tableRef.value = { load: async () => {}, refresh: async () => {}, reload() {} };
;(async () => {
  const order = (await http({ url: `orders/${input.order}/` })).data;
  page.onTransfer(order);
  assert.equal(page.transferRows.value.length, 1);
  Object.assign(page.transferRows.value[0], { new_provider_id: input.incoming, split_percent: 40 });
  drop = true;
  await page.onTransferSave();
  assert.equal(transfers, 1);
  assert.ok(page.transferReview.value);
  assert.match(page.transferSubmitError.value, /转单结果未确认/);
  await page.onTransferSave();
  assert.equal(transfers, 1, 'must not automatically repeat the handoff');
  page.closeTransferAndRefresh();
  await page.viewTransferReview();
  assert.equal(page.transferReviewChecked.value, true);
  assert.ok(page.logs.value.length, 'staff must see order history');
  const old = (await http({ baseURL: '/api', url: `orders/orders/${input.order}/` }, input.oldToken)).data;
  const incoming = (await http({ baseURL: '/api', url: `orders/orders/${input.order}/` }, input.newToken)).data;
  const boss = (await http({ baseURL: '/api', url: `orders/orders/${input.order}/` }, input.bossToken)).data;
  assert.equal(old.my_assignment_released, true);
  assert.equal(incoming.my_assignment_released, false);
  assert.ok(boss.participants.some(person => person.id === input.incoming));
  assert.ok(!boss.participants.some(person => person.id === input.old));
  await page.finishTransferReview();
  assert.equal(page.transferReview.value, null);
  console.log('real handoff response-loss recovery verified');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; })
  .finally(() => { unmounted.forEach(fn => fn()); scope.stop(); });
