const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const adminRequire = createRequire(path.resolve('apps/admin-web/package.json'));
const ts = adminRequire('typescript'), vue = adminRequire('vue');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const clone = value => JSON.parse(JSON.stringify(value));
const money = require('../packages/money/src/index.js');
const participantModule = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync('apps/admin-web/src/utils/orderParticipants.ts', 'utf8'),
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText,
  { module: participantModule, exports: participantModule.exports });

test('current provider names exclude transferred rows while preserving teams and legacy orders', () => {
  const current = participantModule.exports.currentProviderNames;
  assert.equal(current({ provider_name: '旧主陪', providers: [
    { provider_name: '已转出', released_at: '2026-10-30T01:00:00Z' },
    { provider_name: '现陪玩', released_at: null },
  ] }), '现陪玩');
  assert.equal(current({ providers: [{ provider_name: '甲' }, { provider_name: '乙' }] }), '甲、乙');
  assert.equal(current({ provider_name: '旧主陪', providers: [{ provider_name: '已转出', released_at: '2026-10-30' }] }), '—');
  assert.equal(current({ provider_name: '旧订单陪玩', providers: [] }), '旧订单陪玩');
});

test('the actual refund entry includes settled paid completed orders and excludes final or unpaid rows', () => {
  const source = fs.readFileSync(path.resolve('apps/admin-web/src/views/order/index.vue'), 'utf8');
  const expression = source.match(/v-if="([^"]*order:refund[^\n"]*row\.payment_status[^\n"]*)"/)[1];
  const visible = (row, allowed = true) => vm.runInNewContext(expression, { row, auth: { hasPerm: () => allowed } });
  assert.equal(visible({ status: 'COMPLETED', payment_status: 'PAID', settled_at: '2026-10-20T00:00:00Z' }), true);
  assert.equal(visible({ status: 'COMPLETED', payment_status: 'PARTIALLY_REFUNDED' }), true);
  for (const status of ['PENDING', 'CANCELLED']) assert.equal(visible({ status, payment_status: 'PAID' }), false);
  for (const payment_status of ['UNPAID', 'REFUNDED']) assert.equal(visible({ status: 'COMPLETED', payment_status }), false);
  assert.equal(visible({ status: 'COMPLETED', payment_status: 'PAID' }, false), false);
});

function setup(kind = 'order', permissions) {
  const scope = vue.effectScope(), mounted = [], unmounted = [], timers = new Map(), handlers = new Map();
  const route = vue.reactive({ query: {} });
  let nextTimer = 0, activeLoads = 0, maximumLoads = 0;
  const tablePending = [], detailPending = [], ticketPending = [], previewPending = [], actionPending = [], escortPending = [];
  const calls = { table: 0, reload: 0, detail: [], tickets: [], logs: [], previews: [], previewOptions: [], actions: [], prompts: [], warnings: [], closed: 0, connected: 0 };
  const auth = vue.reactive({ token: 'session-one', sessionRevision: 1,
    permissions: permissions || ['order:view', 'order:refund', 'order:settle', 'order:cancel', 'transaction:view'],
    hasPerm(code) { return (Array.isArray(code) ? code : [code]).some(value => this.permissions.includes(value)); } });
  const state = { order: { id: 101, status: 'COMPLETED', payment_status: 'PAID' }, tickets: [{ id: 201, order: 101, status: 'OPEN', reason: '本地售后' }], logs: [{ id: 301, action: 'COMPLETE' }],
    preview: { order_id: 101, is_settled: true, eligible: true, original_amount: 10000, refunded_amount: 0, remaining_amount: 10000, remaining_after_refund: 0, full_refund: true, refund_amount: 10000, recovery_total: 10000, recoveries: [{ wallet_id: 11, account_id: 1147, role: 'PROVIDER', income_amount: 8000, held_amount: 5600, deposit_amount: 0, available_required: 2400, available_balance: 4800, shortfall: 0, original_transaction_ids: [51], source_safe: true, explanation: '该单已结算收益' }], shortfalls: [], plan_token: 'local-signed-plan' } };
  const orderApi = {
    async list(params) { return { data: { list: [], total: 0, ...(state.aftersaleQueueOrder ? { aftersale_queue_order: state.aftersaleQueueOrder } : {}) } }; },
    async retrieve(id) { calls.detail.push(id); return detailPending.length ? detailPending.shift().promise : { data: { ...clone(state.order), id } }; },
    async action(id, action, payload) { calls.actions.push({ kind: 'order', id, action, payload }); if (actionPending.length) await actionPending.shift().promise; if (['refund', 'cancel'].includes(action)) { state.order.status = 'CANCELLED'; state.order.payment_status = 'REFUNDED'; state.logs.push({ id: 302, action: 'REFUND' }); } return { data: clone(state.order) }; },
  };
  const aftersaleApi = {
    async list(params) { calls.tickets.push(params.order); return ticketPending.length ? ticketPending.shift().promise : { data: { list: clone(state.tickets) } }; },
    async action(id, action, payload) { calls.actions.push({ kind: 'aftersale', id, action, payload }); if (actionPending.length) await actionPending.shift().promise; state.tickets[0].status = action === 'refund' ? 'REFUNDED' : 'RESOLVED'; return { data: clone(state.tickets[0]) }; },
  };
  const mocks = {
    vue: { ...vue, onMounted: fn => mounted.push(fn), onUnmounted: fn => unmounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn) },
    'vue-router': { useRoute: () => route, useRouter: () => ({ replace: ({ query }) => { route.query = query; } }) },
    'element-plus': { ElMessage: { success() {}, warning(value) { calls.warnings.push(value); } }, ElMessageBox: { prompt() { const item = deferred(); calls.prompts.push(item); return item.promise; }, close() { calls.closed++; } } },
    '@element-plus/icons-vue': { Plus: {}, Search: {}, Setting: {} },
    '@/components/CrudTable.vue': {}, '@/components/DispatchMessageDialog.vue': {},
    '@/api/modules': { orderApi, aftersaleApi, transactionApi: {}, serviceItemApi: {}, userApi: {}, escortApi: { options: () => escortPending.length ? escortPending.shift().promise : Promise.resolve({ data: { list: [] } }) } },
    '@/api/request': { async request(options) {
      if (options.url.endsWith('/refund-preview/')) { calls.previews.push(options.url); calls.previewOptions.push(clone(options)); return previewPending.length ? previewPending.shift().promise : { data: clone(state.preview) }; }
      const detail = options.url.match(/^\/orders\/(\d+)\/$/);
      if (detail && options.method === 'get') return orderApi.retrieve(Number(detail[1]));
      if (options.url === '/aftersales/') return aftersaleApi.list(options.params);
      const action = options.url.match(/^\/(orders|aftersales)\/(\d+)\/(refund|resolve|cancel)\/$/);
      if (action && options.method === 'post') return (action[1] === 'orders' ? orderApi : aftersaleApi).action(Number(action[2]), action[3], options.data);
      calls.logs.push(options.url); return { data: clone(state.logs) };
    } },
    '@/utils/dict': { ORDER_STATUS: { IN_SERVICE: { label: '服务中' } }, PAYMENT_STATUS: {}, TX_TYPE: {}, TX_STATUS: {} },
    '@/utils/format': { amountToXaCoin: money.formatXaCoin, formatDateTime: String, xaCoinToAmount: money.toRawAmount },
    '@/utils/commission': { commissionError: () => '' },
    '@/utils/orderParticipants': participantModule.exports,
    '@/stores/auth': { useAuthStore: () => auth },
    '@/stores/staffDispatchQueue': { useStaffDispatchQueue: () => ({ status: 'idle' }) },
    '@/utils/ws': { adminWs: { connect() { calls.connected++; }, on(type, callback) { if (!handlers.has(type)) handlers.set(type, new Set()); handlers.get(type).add(callback); return () => handlers.get(type).delete(callback); } } },
  };
  const filename = path.resolve('apps/admin-web/src/views/' + kind + '/index.vue');
  const source = fs.readFileSync(filename, 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const exposed = kind === 'order'
    ? 'tableRef,query,current,logs,aftersales,detailOrderId,detailVisible,detailLoading,detailFailed,aftersaleFailed,orderActionBusy,onDetail,refreshCurrentOrder,onRefund,onCancel,processAfterSale,refundVisible,refundLoading,refundSubmitting,refundResultUnknown,reviewUnknownRefund,refundPreview,refundReason,refundError,refundCanSubmit,loadRefundPreview,submitRefund,refundAmountCoins,refundLimits,refundRawAmount,useRefundRemainder,onAssign,onAssignSave,canAssignOrder,assignVisible,assignProviderIds,assignProviderId,assigning,assignSubmitError,assignReview,searchAssignProvider,assignProviderOptions,clearActionableFilter,clearStaffPending,onDispatchMessage,dispatchMessageVisible,dispatchMessageOrderId,fetchOrders,aftersaleQueueOrder'
    : 'tableRef,query';
  const code = ts.transpileModule(source + '\nglobalThis.page = {' + exposed + '};', { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const context = { exports: {}, document: { hidden: false, addEventListener() {}, removeEventListener() {} }, crypto: require('node:crypto').webcrypto, require(name) { assert.ok(Object.hasOwn(mocks, name), 'Unexpected dependency: ' + name); return mocks[name]; },
    setTimeout(callback) { timers.set(++nextTimer, callback); return nextTimer; }, clearTimeout(id) { timers.delete(id); } };
  scope.run(() => vm.runInNewContext(code, context, { filename }));
  const page = context.page, pagination = { page: 4, pageSize: 20 };
  page.tableRef.value = { async load() { calls.table++; activeLoads++; maximumLoads = Math.max(maximumLoads, activeLoads); try { if (tablePending.length) await tablePending.shift().promise; } finally { activeLoads--; } }, refresh() { return this.load(); }, reload() { calls.reload++; pagination.page = 1; } };
  mounted.forEach(callback => callback());
  return { page, calls, auth, route, state, timers, handlers, pagination, tablePending, detailPending, ticketPending, previewPending, actionPending, escortPending,
    emit(type, data) { handlers.get(type)?.forEach(callback => callback(data)); },
    async flush() { while (timers.size) { const [id, callback] = timers.entries().next().value; timers.delete(id); await callback(); await tick(); } },
    startTimer() { const [id, callback] = timers.entries().next().value; timers.delete(id); return callback(); },
    get maximumLoads() { return maximumLoads; },
    dispose() { unmounted.forEach(callback => callback()); scope.stop(); },
  };
}

test('staff queue link opens the full server-filtered pending list', async () => {
  const s = setup();
  s.page.query.order_no = 'old search';
  s.route.query = { queue: 'staff' };
  await tick();
  assert.equal(s.page.query.order_no, '');
  assert.equal(s.page.query.status, 'PENDING');
  assert.equal(s.page.query.payment_status, 'PAID');
  assert.equal(s.page.query.dispatch_route, 'STAFF');
  assert.equal(s.page.query.actionable_staff, '1');
  assert.equal(s.pagination.page, 1);
  assert.equal(s.calls.reload, 1);
  assert.equal(s.route.query.queue, undefined);
  s.dispose();
});

test('open aftersale queue reports confirmed ordering and exposes request time on desktop and mobile', async () => {
  const s = setup();
  s.route.query = { queue: 'open_aftersale' }; await tick();
  assert.equal(s.page.query.aftersale_open, '1');
  s.state.aftersaleQueueOrder = 'opened_asc';
  await s.page.fetchOrders({ ...s.page.query });
  assert.equal(s.page.aftersaleQueueOrder.value, true);
  s.state.aftersaleQueueOrder = '';
  await s.page.fetchOrders({ ...s.page.query });
  assert.equal(s.page.aftersaleQueueOrder.value, false);
  const template = fs.readFileSync('apps/admin-web/src/views/order/index.vue', 'utf8');
  assert.match(template, /按申请时间从早到晚/);
  assert.match(template, /<dt>售后申请<\/dt>/);
  assert.match(template, /label="售后申请时间"/);
  assert.match(template, /时间待核对/);
  s.dispose();
});

test('dashboard status link clears previous filters and applies only the selected status', async () => {
  const s = setup();
  Object.assign(s.page.query, { order_no: 'old', payment_status: 'PAID', dispatch_route: 'STAFF', actionable_staff: '1', aftersale_open: '1' });
  s.route.query = { order_status: 'IN_SERVICE' }; await tick();
  assert.equal(s.page.query.status, 'IN_SERVICE');
  for (const key of ['order_no', 'payment_status', 'dispatch_route', 'actionable_staff', 'aftersale_open']) assert.equal(s.page.query[key], '');
  assert.equal(s.pagination.page, 1);
  assert.equal(s.calls.reload, 1);
  assert.equal(s.route.query.order_status, undefined);
  s.route.query = { order_status: 'toString' }; await tick();
  assert.equal(s.calls.reload, 1);
  assert.equal(s.page.query.status, 'IN_SERVICE');
  s.dispose();
});

test('changing queue filters removes the actionable-only restriction and all-orders clears it', async () => {
  const s = setup();
  s.route.query = { queue: 'staff' }; await tick();
  s.page.query.status = 'GRABBED';
  s.page.clearActionableFilter(() => {});
  assert.equal(s.page.query.actionable_staff, '');
  s.route.query = { queue: 'staff' }; await tick();
  s.page.clearStaffPending(() => {});
  for (const key of ['status', 'payment_status', 'dispatch_route', 'actionable_staff']) assert.equal(s.page.query[key], '');
  s.dispose();
});

test('review queue opens all paid staff pending orders and explains blocked assignment', async () => {
  const s = setup();
  s.route.query = { queue: 'staff_review' }; await tick();
  assert.equal(s.page.query.actionable_staff, '');
  assert.equal(s.page.query.status, 'PENDING');
  assert.equal(s.page.query.payment_status, 'PAID');
  assert.equal(s.page.query.dispatch_route, 'STAFF');
  const template = fs.readFileSync('apps/admin-web/src/views/order/index.vue', 'utf8');
  assert.match(template, /暂不可派，请查看详情并核对订单状态/);
  s.dispose();
});

test('only paid staff team orders can use two-person assignment', async () => {
  const s = setup('order', ['order:view', 'order:dispatch']);
  const team = { id: 101, required_team_size: 2, dispatch_route: 'STAFF', status: 'PENDING', payment_status: 'PAID', staff_dispatch_actionable: true, dispatch_assignment_allowed: true };
  assert.equal(s.page.canAssignOrder(team), true);
  assert.equal(s.page.canAssignOrder({ ...team, dispatch_route: 'PUBLIC' }), false);
  assert.equal(s.page.canAssignOrder({ ...team, payment_status: 'UNPAID' }), false);
  assert.equal(s.page.canAssignOrder({ ...team, dispatch_assignment_allowed: false }), false);
  assert.equal(s.page.canAssignOrder({ ...team, dispatch_assignment_allowed: undefined }), false);
  s.page.onAssign(team);
  s.page.assignProviderIds.value = [11, 11];
  await s.page.onAssignSave();
  assert.equal(s.calls.actions.length, 0);
  s.page.assignProviderIds.value = [11, 12];
  await s.page.onAssignSave();
  assert.equal(s.calls.actions.length, 1);
  assert.equal(s.calls.actions[0].action, 'assign');
  assert.deepEqual(Array.from(s.calls.actions[0].payload.provider_ids), [11, 12]);
  assert.equal(s.page.assignVisible.value, false);
  s.dispose();
});

test('dispatch dialog requires the server action flag and explains blocked messages', () => {
  const s = setup('order', ['order:view', 'order:dispatch']);
  s.page.onDispatchMessage({ id: 101, dispatch_message_allowed: false, dispatch_message_unavailable_reason: '双陪订单暂不支持群发' });
  assert.equal(s.page.dispatchMessageVisible.value, false);
  assert.equal(s.calls.warnings.at(-1), '双陪订单暂不支持群发');
  s.page.onDispatchMessage({ id: 102, dispatch_message_allowed: true });
  assert.equal(s.page.dispatchMessageVisible.value, true);
  assert.equal(s.page.dispatchMessageOrderId.value, 102);
  s.dispose();
});

test('staff team assignment completion cannot reopen or modify a changed session', async () => {
  const s = setup('order', ['order:view', 'order:dispatch']);
  const pending = deferred();
  s.actionPending.push(pending);
  s.page.onAssign({ id: 101, required_team_size: 2, dispatch_route: 'STAFF', status: 'PENDING', payment_status: 'PAID', staff_dispatch_actionable: true, dispatch_assignment_allowed: true });
  s.page.assignProviderIds.value = [11, 12];
  const save = s.page.onAssignSave();
  s.auth.sessionRevision++;
  pending.resolve();
  await save;
  assert.equal(s.page.assignVisible.value, false);
  assert.equal(s.page.assigning.value, false);
  assert.equal(s.timers.size, 0);
  s.dispose();
});

test('assignment error remains uncertain after its later order event', async () => {
  const s = setup('order', ['order:view', 'order:dispatch']);
  const pending = deferred(); s.actionPending.push(pending);
  s.page.onAssign({ id: 101, order_no: 'QA101', required_team_size: 1, dispatch_route: 'STAFF', status: 'PENDING', payment_status: 'PAID', dispatch_assignment_allowed: true });
  s.page.assignProviderId.value = 11;
  const save = s.page.onAssignSave();
  pending.reject({ response: { status: 503, data: { code: 500, msg: 'response lost' } } });
  await save;
  assert.match(s.page.assignSubmitError.value, /结果未确认/);
  assert.equal(s.page.assignReview.value.id, 101);
  s.emit('order_status_update', { id: 101 });
  assert.match(s.page.assignSubmitError.value, /结果未确认/);
  s.page.assignVisible.value = false;
  s.page.onAssign({ id: 102, required_team_size: 1, status: 'PENDING', payment_status: 'PAID', dispatch_assignment_allowed: true });
  assert.equal(s.page.assignVisible.value, false);
  assert.match(s.calls.warnings.at(-1), /核对上一笔/);
  s.dispose();
});

test('team candidate search retains selected names and ignores late results', async () => {
  const s = setup('order', ['order:view', 'order:dispatch']);
  s.page.onAssign({ id: 101, required_team_size: 2, dispatch_route: 'STAFF', status: 'PENDING', payment_status: 'PAID', staff_dispatch_actionable: true, dispatch_assignment_allowed: true });
  const first = deferred(), second = deferred();
  s.escortPending.push(first, second);
  const oldSearch = s.page.searchAssignProvider('old');
  const newSearch = s.page.searchAssignProvider('new');
  second.resolve({ data: { list: [{ user: 12, display_name: '新' }] } });
  await newSearch;
  s.page.assignProviderIds.value = [12];
  first.resolve({ data: { list: [{ user: 11, display_name: '旧' }] } });
  await oldSearch;
  assert.deepEqual(Array.from(s.page.assignProviderOptions.value, p => p.user), [12]);
  const third = deferred();
  s.escortPending.push(third);
  const later = s.page.searchAssignProvider('another');
  third.resolve({ data: { list: [{ user: 13, display_name: '另一人' }] } });
  await later;
  assert.deepEqual(Array.from(s.page.assignProviderOptions.value, p => p.user), [12, 13]);
  s.dispose();
});

test('order notifications refresh current detail, ticket and logs without resetting filtered pagination', async () => {
  const s = setup(); await s.page.onDetail({ id: 101 }); s.page.query.status = 'COMPLETED'; s.page.query.payment_status = 'PAID';
  s.state.order.status = 'CANCELLED'; s.state.order.payment_status = 'REFUNDED'; s.state.tickets[0].status = 'REFUNDED'; s.state.logs.push({ id: 302, action: 'REFUND' });
  for (let i = 0; i < 5; i++) s.emit('order_status_update', { id: 101, status: 'CANCELLED', payment_status: 'REFUNDED' });
  await s.flush(); assert.equal(s.calls.table, 1); assert.equal(s.calls.detail.length, 2); assert.equal(s.calls.tickets.length, 2); assert.equal(s.calls.logs.length, 2);
  assert.equal(s.page.current.value.payment_status, 'REFUNDED'); assert.equal(s.page.aftersales.value[0].status, 'REFUNDED'); assert.equal(s.page.logs.value.length, 2);
  assert.equal(s.page.query.status, 'COMPLETED'); assert.equal(s.page.query.payment_status, 'PAID'); assert.equal(s.pagination.page, 4); assert.equal(s.calls.reload, 0); s.dispose();
});

test('same-status order notifications still reconcile newly opened or resolved aftersale tickets', async () => {
  const s = setup(); await s.page.onDetail({ id: 101 }); s.state.tickets[0].status = 'RESOLVED';
  s.emit('order_status_update', { id: 101, status: 'COMPLETED' }); await s.flush();
  assert.equal(s.page.current.value.status, 'COMPLETED'); assert.equal(s.page.aftersales.value[0].status, 'RESOLVED');
  s.emit('order_status_update', { id: 102 }); await s.flush(); assert.equal(s.calls.detail.length, 2); s.dispose();
});

test('order reconnect reconciles the drawer and a closed drawer is never reopened by pushes', async () => {
  const s = setup(); await s.page.onDetail({ id: 101 }); s.emit('connected'); await s.flush(); assert.equal(s.calls.detail.length, 2);
  s.page.detailVisible.value = false; s.emit('connected'); s.emit('order_status_update', { id: 101 }); await s.flush();
  assert.equal(s.calls.detail.length, 2); assert.equal(s.page.current.value, null); assert.equal(s.page.detailVisible.value, false); s.dispose();
});

test('order invalidations during HTTP retain one serialized follow-up', async () => {
  const s = setup(), pending = deferred(); s.tablePending.push(pending); s.emit('order_status_update', { id: 101 });
  const running = s.startTimer(); await tick(); for (let i = 0; i < 5; i++) s.emit('order_status_update', { id: 101 });
  assert.equal(s.calls.table, 1); pending.resolve(); await running; await s.flush();
  assert.equal(s.calls.table, 2); assert.equal(s.maximumLoads, 1); s.dispose();
});

test('order late detail responses cannot replace another drawer selection or reopen a closed drawer', async () => {
  const s = setup(), first = deferred(), second = deferred(); s.detailPending.push(first, second);
  const openingFirst = s.page.onDetail({ id: 101 }), openingSecond = s.page.onDetail({ id: 102 });
  second.resolve({ data: { id: 102, status: 'IN_SERVICE' } }); await openingSecond;
  first.resolve({ data: { id: 101, status: 'CANCELLED' } }); await openingFirst; assert.equal(s.page.current.value.id, 102);
  const third = deferred(); s.detailPending.push(third); const openingThird = s.page.onDetail({ id: 103 });
  s.page.detailVisible.value = false; third.resolve({ data: { id: 103 } }); await openingThird;
  assert.equal(s.page.detailVisible.value, false); assert.equal(s.page.current.value, null); assert.equal(s.page.aftersales.value.length, 0); s.dispose();
});

test('an incomplete drawer refresh hides stale write controls and remains retryable', async () => {
  const s = setup(), failure = deferred(); s.ticketPending.push(failure); const opening = s.page.onDetail({ id: 101 });
  failure.reject(new Error('offline')); await opening; assert.equal(s.page.aftersaleFailed.value, true); assert.equal(s.page.detailLoading.value, false);
  await s.page.processAfterSale({ id: 201, status: 'OPEN' }, 'refund'); assert.equal(s.calls.prompts.length, 0);
  await s.page.refreshCurrentOrder(); assert.equal(s.page.aftersaleFailed.value, false); assert.equal(s.page.aftersales.value.length, 1); s.dispose();
});

test('order session changes, read revocation and unmount invalidate pending reads and queues', async () => {
  const s = setup(), late = deferred(); s.detailPending.push(late); const opening = s.page.onDetail({ id: 101 });
  s.emit('order_status_update', { id: 101 }); s.auth.sessionRevision++;
  late.resolve({ data: { id: 101, status: 'CANCELLED' } }); await opening; assert.equal(s.page.current.value, null); assert.equal(s.timers.size, 0);
  s.auth.permissions = []; assert.equal(s.handlers.get('order_status_update').size, 0); s.emit('connected'); assert.equal(s.timers.size, 0);
  s.auth.permissions = ['order:view']; assert.equal(s.handlers.get('order_status_update').size, 1); s.emit('order_status_update', { id: 101 });
  s.dispose(); assert.equal(s.timers.size, 0); assert.equal(s.handlers.get('order_status_update').size, 0);
});

for (const reason of ['same-order update', 'reconnect', 'session change', 'write revocation', 'unmount']) {
  test('a waiting refund confirmation cannot submit after ' + reason, async () => {
    const s = setup(); await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '本地退款原因';
    if (reason === 'same-order update') s.emit('order_status_update', { id: 101 });
    if (reason === 'reconnect') s.emit('connected');
    if (reason === 'session change') s.auth.sessionRevision++;
    if (reason === 'write revocation') s.auth.permissions = ['order:view'];
    if (reason === 'unmount') s.dispose();
    await s.page.submitRefund();
    assert.equal(s.calls.actions.length, 0); assert.equal(s.page.orderActionBusy.value, false); assert.equal(s.calls.closed, 1);
    if (reason !== 'unmount') s.dispose();
  });
}

test('successful local refund uses the invalidation queue to update an already open drawer', async () => {
  const s = setup(); await s.page.onDetail({ id: 101 }); await s.page.onRefund({ id: 101 });
  s.page.refundReason.value = '本地核验退款'; await s.page.submitRefund(); await s.flush();
  assert.equal(s.calls.actions[0].action, 'refund'); assert.equal(s.page.current.value.payment_status, 'REFUNDED'); assert.equal(s.page.logs.value.at(-1).action, 'REFUND'); assert.equal(s.calls.reload, 0); s.dispose();
});

test('aftersale resolve and refund respect their separate permissions and refresh the ticket', async () => {
  const s = setup('order', ['order:view', 'order:settle']); await s.page.onDetail({ id: 101 });
  await s.page.processAfterSale(s.page.aftersales.value[0], 'refund'); assert.equal(s.calls.prompts.length, 0);
  const resolve = s.page.processAfterSale(s.page.aftersales.value[0], 'resolve'); s.calls.prompts[0].resolve({ value: '本地处理完成' }); await resolve; await s.flush();
  assert.equal(s.calls.actions[0].kind, 'aftersale'); assert.equal(s.calls.actions[0].action, 'resolve'); assert.equal(s.page.aftersales.value[0].status, 'RESOLVED'); s.dispose();
});

test('aftersale confirmation becomes invalid when its drawer selection changes', async () => {
  const s = setup(); await s.page.onDetail({ id: 101 }); await s.page.processAfterSale(s.page.aftersales.value[0], 'refund');
  s.page.refundReason.value = '本地售后原因'; await s.page.onDetail({ id: 102 }); await s.page.submitRefund();
  assert.equal(s.calls.actions.length, 0); assert.equal(s.page.current.value.id, 102); s.dispose();
});

test('settled refund sends the server plan unchanged and cannot double-submit', async () => {
  const s = setup(), pending = deferred(); await s.page.onRefund({ id: 101 });
  assert.deepEqual(clone(s.page.refundPreview.value), s.state.preview);
  assert.equal(s.calls.actions.length, 0); assert.equal(s.calls.prompts.length, 0);
  s.page.refundReason.value = '  财务复核退款  '; s.actionPending.push(pending);
  const submitting = s.page.submitRefund(); await s.page.submitRefund();
  assert.equal(s.calls.actions.length, 1);
  assert.deepEqual(clone(s.calls.actions[0].payload), { reason: '财务复核退款', plan_token: 'local-signed-plan', refund_amount: 10000, client_request_id: s.calls.actions[0].payload.client_request_id });
  assert.match(s.calls.actions[0].payload.client_request_id, /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i);
  s.emit('order_status_update', { id: 101 }); pending.resolve(); await submitting;
  assert.equal(s.page.refundVisible.value, false); assert.equal(s.page.orderActionBusy.value, false); s.dispose();
});

test('aftersale refund uses the same preview and sends plan token with resolution', async () => {
  const s = setup(); await s.page.onDetail({ id: 101 }); await s.page.processAfterSale(s.page.aftersales.value[0], 'refund');
  assert.equal(s.calls.previews[0], '/orders/101/refund-preview/');
  s.page.refundReason.value = '售后资金复核'; await s.page.submitRefund(); await s.flush();
  assert.deepEqual(clone(s.calls.actions[0]), { kind: 'aftersale', id: 201, action: 'refund', payload: { resolution: '售后资金复核', plan_token: 'local-signed-plan', refund_amount: 10000, client_request_id: s.calls.actions[0].payload.client_request_id } });
  assert.equal(s.page.aftersales.value[0].status, 'REFUNDED'); s.dispose();
});

test('ineligible or underfunded server previews disable confirmation without any mutation', async () => {
  const s = setup(); s.state.preview.eligible = false; s.state.preview.shortfalls = [{ wallet_id: 11, account_id: 1147, required: 2400, available: 1000, shortfall: 1400 }];
  await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '余额不足退款'; await s.page.submitRefund();
  assert.equal(s.page.refundCanSubmit.value, false); assert.equal(s.calls.actions.length, 0);
  assert.equal(s.page.refundPreview.value.shortfalls[0].shortfall, 1400); s.dispose();
});

test('a settled preview without a server token cannot submit', async () => {
  const s = setup(); s.state.preview.plan_token = ''; await s.page.onRefund({ id: 101 });
  s.page.refundReason.value = '无签名方案'; await s.page.submitRefund(); assert.equal(s.calls.actions.length, 0); s.dispose();
});

test('failed preview is retryable and a late closed preview cannot overwrite a reopened dialog', async () => {
  const s = setup(), failed = deferred(); s.previewPending.push(failed); const opening = s.page.onRefund({ id: 101 });
  failed.reject(new Error('offline')); await opening; assert.match(s.page.refundError.value, /试算加载失败/);
  s.page.refundReason.value = '保留原因'; await s.page.loadRefundPreview(); assert.equal(s.page.refundCanSubmit.value, true);
  const late = deferred(); s.previewPending.push(late); const loading = s.page.loadRefundPreview();
  s.page.refundVisible.value = false; await s.page.onRefund({ id: 101 });
  late.resolve({ data: { ...s.state.preview, refund_amount: 1, plan_token: 'stale' } }); await loading;
  assert.equal(s.page.refundPreview.value.plan_token, 'local-signed-plan'); assert.equal(s.page.refundLoading.value, false); s.dispose();
});

for (const errorCode of ['REFUND_PLAN_CHANGED', 'REFUND_RECOVERY_INSUFFICIENT']) {
  test(errorCode + ' replaces preview but never retries a refund automatically', async () => {
    const s = setup(), pending = deferred(); await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '保留复核说明';
    const updated = { ...clone(s.state.preview), plan_token: 'updated-plan', available_balance: 100 };
    if (errorCode.includes('INSUFFICIENT')) { updated.eligible = false; updated.shortfalls = [{ wallet_id: 11, account_id: 1147, required: 2400, available: 100, shortfall: 2300 }]; }
    s.actionPending.push(pending); const submitting = s.page.submitRefund();
    pending.reject({ response: { status: 409, data: { code: 409, errorCode, data: { preview: updated } } } }); await submitting;
    assert.equal(s.calls.actions.length, 1); assert.equal(s.page.refundVisible.value, true);
    assert.equal(s.page.refundReason.value, '保留复核说明'); assert.equal(s.page.refundPreview.value.plan_token, 'updated-plan');
    assert.equal(s.page.refundCanSubmit.value, updated.eligible); assert.match(s.page.refundError.value, errorCode.includes('INSUFFICIENT') ? /不足/ : /已变化/); s.dispose();
  });
}

test('an ambiguous transport failure locks the refund form until order review', async () => {
  const s = setup(), pending = deferred(); await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '网络异常退款';
  s.actionPending.push(pending); const submitting = s.page.submitRefund(); pending.reject(new Error('timeout')); await submitting;
  assert.equal(s.page.refundResultUnknown.value, true); assert.equal(s.page.refundCanSubmit.value, false);
  await s.page.loadRefundPreview(); await s.page.submitRefund(); assert.equal(s.calls.actions.length, 1);
  s.page.reviewUnknownRefund(); await tick(); assert.equal(s.page.detailVisible.value, true); assert.equal(s.page.current.value.id, 101); s.dispose();
});

test('an order event before an ambiguous response preserves the review warning', async () => {
  const s = setup(), pending = deferred(); await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '响应丢失核对';
  s.actionPending.push(pending); const submitting = s.page.submitRefund();
  s.emit('order_status_update', { id: 101 }); pending.reject(new Error('response lost')); await submitting;
  assert.equal(s.page.refundVisible.value, true); assert.equal(s.page.refundResultUnknown.value, true);
  assert.match(s.page.refundError.value, /退款结果未确认/); assert.equal(s.page.refundCanSubmit.value, false);
  s.page.reviewUnknownRefund(); await tick(); assert.equal(s.page.detailVisible.value, true); s.dispose();
});

test('a competing order update during failed refund closes the obsolete review', async () => {
  const s = setup(), pending = deferred(); await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '并发复核';
  s.actionPending.push(pending); const submitting = s.page.submitRefund(); s.emit('order_status_update', { id: 101 });
  pending.reject({ code: 409, errorCode: 'REFUND_PLAN_CHANGED', data: { preview: s.state.preview } }); await submitting;
  assert.equal(s.page.refundVisible.value, false); assert.equal(s.page.orderActionBusy.value, false); assert.equal(s.calls.actions.length, 1); s.dispose();
});

for (const outcome of ['success', 'unknown']) {
  test('reconnect preserves an in-flight refund until its ' + outcome + ' response', async () => {
    const s = setup(), pending = deferred();
    await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '重连核对退款'; s.actionPending.push(pending);
    const submission = s.page.submitRefund(); s.emit('connected');
    assert.equal(s.page.refundVisible.value, true); assert.equal(s.page.refundSubmitting.value, true);
    await s.page.onRefund({ id: 102 }); await s.page.submitRefund(); assert.equal(s.calls.actions.length, 1);
    if (outcome === 'success') pending.resolve(); else pending.reject(new Error('timeout'));
    await submission;
    if (outcome === 'success') assert.equal(s.page.refundVisible.value, false);
    else {
      assert.equal(s.page.refundResultUnknown.value, true); s.emit('connected');
      assert.equal(s.page.refundVisible.value, true); assert.equal(s.page.refundCanSubmit.value, false);
    }
    s.dispose();
  });
}

test('refund readiness remains reactive across a successful first refund and a second order review', async () => {
  const s = setup(); await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '第一笔退款复核';
  assert.equal(s.page.refundCanSubmit.value, true); await s.page.submitRefund();
  assert.equal(s.page.refundVisible.value, false);
  // Render the closed state. A plain-operation-first computed used to drop all
  // reactive dependencies here and remain cached false on every later order.
  assert.equal(s.page.refundCanSubmit.value, false);
  s.state.preview.order_id = 102; s.state.preview.plan_token = 'second-valid-plan';
  await s.page.onRefund({ id: 102 }); s.page.refundReason.value = '第二笔完整资金复核说明'.repeat(5);
  assert.equal(s.page.refundPreview.value.eligible, true); assert.equal(s.page.refundPreview.value.shortfalls.length, 0);
  assert.equal(s.page.refundCanSubmit.value, true); await s.page.submitRefund();
  assert.equal(s.calls.actions.length, 2); assert.equal(s.calls.actions[1].id, 102);
  assert.equal(s.calls.actions[1].payload.plan_token, 'second-valid-plan'); s.dispose();
});

test('refund readiness recomputes after cancelling and rendering a closed review', async () => {
  const s = setup(); await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '首次准备取消';
  assert.equal(s.page.refundCanSubmit.value, true); s.page.refundVisible.value = false;
  assert.equal(s.page.refundCanSubmit.value, false); await s.page.onRefund({ id: 101 });
  assert.equal(s.page.refundCanSubmit.value, false); s.page.refundReason.value = '重新填写原因';
  assert.equal(s.page.refundCanSubmit.value, true); s.dispose();
});

test('changing a coin amount invalidates the full-refund quote and submits only the new server plan', async () => {
  const s = setup(); await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '部分退款复核';
  assert.equal(s.page.refundAmountCoins.value, '1000'); assert.equal(s.page.refundCanSubmit.value, true);
  s.page.refundAmountCoins.value = '250.1'; assert.equal(s.page.refundPreview.value, null); assert.equal(s.page.refundCanSubmit.value, false);
  await s.page.submitRefund(); assert.equal(s.calls.actions.length, 0);
  Object.assign(s.state.preview, { refund_amount: 2501, recovery_total: 2501, remaining_after_refund: 7499, full_refund: false, plan_token: 'partial-2501' });
  s.state.preview.recoveries[0].income_amount = 2001;
  await s.page.loadRefundPreview(); assert.equal(s.calls.previewOptions.at(-1).params.refund_amount, 2501);
  assert.equal(s.page.refundPreview.value.recoveries[0].income_amount, 2001); assert.equal(s.page.refundCanSubmit.value, true);
  await s.page.submitRefund(); assert.equal(s.calls.actions[0].payload.refund_amount, 2501); assert.equal(s.calls.actions[0].payload.plan_token, 'partial-2501'); s.dispose();
});

test('zero, negative, excess, empty and overprecision coin inputs never request or submit a quote', async () => {
  const s = setup(); await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '金额输入边界';
  for (const amount of ['0', '-1', '1000.1', '', '1.01', 'NaN', '1e2']) {
    s.page.refundAmountCoins.value = amount; await s.page.loadRefundPreview(); await s.page.submitRefund();
    assert.equal(s.page.refundCanSubmit.value, false, amount);
  }
  assert.equal(s.calls.previews.length, 1); assert.equal(s.calls.actions.length, 0); s.dispose();
});

test('editing an amount while preview HTTP is pending discards that stale amount response', async () => {
  const s = setup(), old = deferred(); await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '避免旧金额';
  s.page.refundAmountCoins.value = '200'; s.previewPending.push(old); const pending = s.page.loadRefundPreview();
  s.page.refundAmountCoins.value = '300'; old.resolve({ data: { ...s.state.preview, refund_amount: 2000, plan_token: 'wrong-2000' } }); await pending;
  assert.equal(s.page.refundPreview.value, null); assert.equal(s.page.refundCanSubmit.value, false); assert.equal(s.page.refundAmountCoins.value, '300'); s.dispose();
});

test('a partially refunded order defaults to the server remaining balance and can refund the remainder', async () => {
  const s = setup(); Object.assign(s.state.order, { payment_status: 'PARTIALLY_REFUNDED' });
  Object.assign(s.state.preview, { refunded_amount: 2501, remaining_amount: 7499, refund_amount: 7499, remaining_after_refund: 0, full_refund: true, plan_token: 'remainder-7499' });
  await s.page.onRefund({ id: 101 }); assert.deepEqual(clone(s.page.refundLimits.value), { original: 10000, refunded: 2501, remaining: 7499 });
  assert.equal(s.page.refundAmountCoins.value, '749.9'); s.page.refundAmountCoins.value = '10'; s.page.useRefundRemainder();
  assert.equal(s.page.refundAmountCoins.value, '749.9'); await s.page.loadRefundPreview(); s.page.refundReason.value = '退还剩余款项'; await s.page.submitRefund();
  assert.equal(s.calls.actions[0].payload.refund_amount, 7499); assert.equal(s.calls.actions[0].payload.plan_token, 'remainder-7499'); s.dispose();
});

for (const stage of ['preview', 'submit']) {
  test('a competing refund refreshes remaining limits after invalid amount during ' + stage, async () => {
    const s = setup(), failure = deferred(); await s.page.onRefund({ id: 101 }); s.page.refundReason.value = '金额过期重新复核';
    Object.assign(s.state.preview, { refunded_amount: 4000, remaining_amount: 6000, refund_amount: 6000, remaining_after_refund: 0, plan_token: 'new-remaining-6000' });
    let pending;
    if (stage === 'preview') { s.previewPending.push(failure); pending = s.page.loadRefundPreview(); }
    else { s.actionPending.push(failure); pending = s.page.submitRefund(); }
    failure.reject({ code: 400, errorCode: 'REFUND_INVALID_AMOUNT', data: { remaining_amount: 6000 } }); await pending;
    assert.deepEqual(clone(s.page.refundLimits.value), { original: 10000, refunded: 4000, remaining: 6000 });
    assert.equal(s.page.refundAmountCoins.value, '1000'); assert.equal(s.page.refundCanSubmit.value, false); assert.equal(s.page.refundPreview.value, null);
    assert.match(s.page.refundError.value, /余额已变化/); s.page.useRefundRemainder(); await s.page.loadRefundPreview();
    assert.equal(s.page.refundCanSubmit.value, true); assert.equal(s.calls.previewOptions.at(-1).params.refund_amount, 6000); s.dispose();
  });
}

test('a partial aftersale ticket cannot be refunded again until the boss reopens it', async () => {
  const s = setup(); await s.page.onDetail({ id: 101 }); s.page.aftersales.value[0].status = 'PARTIALLY_REFUNDED';
  await s.page.processAfterSale(s.page.aftersales.value[0], 'refund'); assert.equal(s.calls.previews.length, 0); assert.equal(s.calls.actions.length, 0); s.dispose();
});

test('transaction-only readers refresh on account or platform-wallet invalidation, preserving filters/page', async () => {
  const s = setup('transaction', ['transaction:view']); s.page.query.keyword = 'LOCAL'; s.page.query.tx_type = 'REFUND';
  s.emit('wallet_update', { account_id: 1145 }); s.emit('wallet_update', { wallet_id: 1, account_id: null }); await s.flush();
  assert.equal(s.calls.table, 1); assert.equal(s.calls.reload, 0); assert.equal(s.pagination.page, 4); assert.equal(s.page.query.keyword, 'LOCAL'); assert.equal(s.page.query.tx_type, 'REFUND');
  s.emit('connected'); await s.flush(); assert.equal(s.calls.table, 2);
  for (const value of [{}, { account_id: '1145' }, { wallet_id: -1 }]) s.emit('wallet_update', value);
  await s.flush(); assert.equal(s.calls.table, 2); s.dispose();
});

test('transaction invalidations during HTTP retain a serialized follow-up after a failed load', async () => {
  const s = setup('transaction'), pending = deferred(); s.tablePending.push(pending); s.emit('wallet_update', { account_id: 1145 });
  const running = s.startTimer(); await tick(); s.emit('wallet_update', { account_id: 1145 }); pending.reject(new Error('offline')); await running; await s.flush();
  assert.equal(s.calls.table, 2); assert.equal(s.maximumLoads, 1); s.dispose();
});

test('transaction reconnect subscriptions and queued work are removed across sessions, revocation and unmount', async () => {
  const s = setup('transaction'); s.emit('wallet_update', { account_id: 1145 }); s.auth.sessionRevision++; assert.equal(s.timers.size, 0);
  assert.equal(s.handlers.get('wallet_update').size, 1); s.auth.permissions = []; assert.equal(s.handlers.get('wallet_update').size, 0);
  s.emit('connected'); assert.equal(s.timers.size, 0); s.auth.permissions = ['transaction:view']; assert.equal(s.handlers.get('wallet_update').size, 1);
  s.emit('wallet_update', { wallet_id: 1 }); s.dispose(); assert.equal(s.timers.size, 0); assert.equal(s.handlers.get('wallet_update').size, 0);
});
