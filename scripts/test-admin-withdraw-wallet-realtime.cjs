const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const adminRequire = createRequire(path.resolve('apps/admin-web/package.json'));
const ts = adminRequire('typescript');
const vue = adminRequire('vue');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

function setup(kind, permissions) {
  const scope = vue.effectScope(), mounted = [], unmounted = [], timers = new Map(), handlers = new Map();
  const calls = { table: 0, reload: 0, details: [], actions: [], prompts: [], close: 0, connect: 0, success: [], packages: [] };
  const tablePending = [], detailPending = [], actionPending = [], packagePending = [];
  let timerId = 0, activeTable = 0, maxActiveTable = 0;
  const auth = vue.reactive({ token: 'first', sessionRevision: 1, permissions: permissions || (kind === 'withdraw' ? ['withdraw:view', 'withdraw:audit'] : ['wallet:view', 'wallet:adjust', 'wallet:recharge']),
    hasPerm(code) { return this.permissions.includes(code); } });
  const api = {
    async list() { return { data: { list: [] } }; },
    async retrieve(id) { calls.details.push({ id }); return detailPending.length ? detailPending.shift().promise : { data: { id, status: 'APPROVED', status_display: '已通过', amount: 2000 } }; },
    async action(...args) { calls.actions.push(args); return actionPending.length ? actionPending.shift().promise : { data: {} }; },
    async collectionAction(...args) { calls.actions.push(args); return { data: {} }; },
  };
  const adminWs = { on(type, fn) { if (!handlers.has(type)) handlers.set(type, new Set()); handlers.get(type).add(fn); return () => handlers.get(type).delete(fn); }, connect() { calls.connect++; } };
  const message = { success: text => calls.success.push(text), warning() {} };
  const messageBox = { prompt(...args) { const pending = deferred(); calls.prompts.push({ args, pending }); return pending.promise; }, close() { calls.close++; } };
  const mocks = {
    vue: { ...vue, onMounted: fn => mounted.push(fn), onBeforeUnmount: fn => unmounted.push(fn) },
    'element-plus': { ElMessage: message, ElMessageBox: messageBox },
    '@element-plus/icons-vue': { Search: {} }, '@/components/CrudTable.vue': {},
    '@/api/modules': { withdrawApi: api, walletApi: api, withdrawConfigApi: { get: async () => ({ data: { min_amount: 1000, tax_rate: 5 } }), update: async () => ({ data: {} }) } },
    '@/utils/dict': { WITHDRAW_STATUS: {}, PAYEE_METHOD: {} },
    '@/utils/format': { amountToXaCoin: value => String(Number(value || 0) / 10), xaCoinToAmount: value => Number(value) * 10, formatDateTime: String },
    '@/stores/auth': { useAuthStore: () => auth }, '@/utils/ws': { adminWs },
    '@xa/money': { formatRmb: value => String(Number(value || 0) / 100), rmbToRawAmount: value => Number(value) * 100, toCoin: value => Number(value) / 10 },
    '@/api/request': { async request(options) {
      if (options.url === '/wallets/recharge-packages/') { calls.packages.push(options); return packagePending.length ? packagePending.shift().promise : { data: [] }; }
      calls.details.push(options); return detailPending.length ? detailPending.shift().promise : { data: { id: Number(options.url.split('/')[2]), account_id: 42, user_role: 'PROVIDER', balance: 8800, frozen_amount: 1200 } };
    } },
  };
  const file = path.resolve('apps/admin-web/src/views/' + kind + '/index.vue');
  const source = fs.readFileSync(file, 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1];
  const exposed = kind === 'withdraw'
    ? 'query,tableRef,current,detailVisible,detailLoading,detailFailed,auditBusy,onDetail,onApprove,onReject,refreshCurrentWithdrawal'
    : 'query,tableRef,current,adjustVisible,rechargeVisible,adjustCoin,adjustRemark,rechargeYuan,rechargeRemark,tradeNo,onAdjust,onAdjustSave,onRecharge,onRechargeSave,rechargePackages';
  const code = ts.transpileModule(source + '\nglobalThis.page = {' + exposed + '};', { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  const context = { exports: {}, require(name) { assert.ok(Object.hasOwn(mocks, name), 'unexpected import: ' + name); return mocks[name]; },
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; }, clearTimeout(id) { timers.delete(id); },
    crypto: { randomUUID: () => 'local-test-request' }, URL: { createObjectURL: () => 'blob:test' }, FormData,
  };
  scope.run(() => vm.runInNewContext(code, context, { filename: file }));
  const page = context.page;
  page.tableRef.value = { async load() { calls.table++; activeTable++; maxActiveTable = Math.max(maxActiveTable, activeTable); try { if (tablePending.length) await tablePending.shift().promise; } finally { activeTable--; } }, reload() { calls.reload++; } };
  mounted.forEach(fn => fn());
  return { page, auth, calls, timers, handlers, tablePending, detailPending, actionPending, packagePending,
    emit(type, data) { handlers.get(type)?.forEach(fn => fn(data)); },
    async flush() { while (timers.size) { const [id, fn] = timers.entries().next().value; timers.delete(id); await fn(); await tick(); } },
    startTimer() { const [id, fn] = timers.entries().next().value; timers.delete(id); return fn(); },
    get maxActiveTable() { return maxActiveTable; },
    dispose() { unmounted.forEach(fn => fn()); scope.stop(); },
  };
}
const event = (id = 9, status = 'APPROVED') => ({ withdraw_id: id, account_id: 42, status });
const pendingRow = id => ({ id, status: 'PENDING', user_name: '本地陪玩', actual_amount: 1900, tax_amount: 100, payee_name: '测试收款人', payee_method_display: '微信', payee_account: 'LOCAL' });

test('withdraw pushes coalesce without resetting filters/pages and refresh only the opened matching detail', async () => {
  const s = setup('withdraw'); await tick();
  s.page.query.status = 'PENDING'; s.page.query.provider_username = 'local';
  s.page.onDetail(pendingRow(9)); await tick(); assert.equal(s.calls.details.length, 1);
  s.emit('withdraw_status_update', event(8)); await s.flush(); assert.equal(s.calls.details.length, 1);
  for (let i = 0; i < 5; i++) s.emit('withdraw_status_update', event());
  await s.flush(); assert.equal(s.calls.table, 2); assert.equal(s.calls.details.length, 2); assert.equal(s.calls.reload, 0);
  assert.equal(s.page.query.status, 'PENDING'); assert.equal(s.page.query.provider_username, 'local');
  assert.equal(s.page.current.value.status, 'APPROVED'); s.dispose();
});

test('withdraw invalidations arriving during HTTP retain a sequential follow-up refresh', async () => {
  const s = setup('withdraw'), pending = deferred(); s.tablePending.push(pending);
  s.emit('withdraw_status_update', event()); const running = s.startTimer(); await tick();
  s.emit('withdraw_status_update', event(10)); s.emit('withdraw_status_update', event());
  assert.equal(s.calls.table, 1); assert.equal(s.timers.size, 0);
  pending.resolve(); await running; await s.flush();
  assert.equal(s.calls.table, 2); assert.equal(s.maxActiveTable, 1); s.dispose();
});

test('withdraw late detail replies cannot replace another selection or reopen a closed drawer', async () => {
  const s = setup('withdraw'), first = deferred(), second = deferred(); s.detailPending.push(first, second);
  s.page.onDetail(pendingRow(9)); s.page.onDetail(pendingRow(10));
  second.resolve({ data: { id: 10, status: 'REJECTED' } }); await tick();
  first.resolve({ data: { id: 9, status: 'APPROVED' } }); await tick();
  assert.equal(s.page.current.value.id, 10); assert.equal(s.page.current.value.status, 'REJECTED');
  const third = deferred(); s.detailPending.push(third); s.page.onDetail(pendingRow(11));
  s.page.detailVisible.value = false; third.resolve({ data: { id: 11 } }); await tick(); assert.equal(s.page.current.value, null); s.dispose();
});

test('withdraw detail failure stays retryable and reconnect refreshes drawer plus list', async () => {
  const s = setup('withdraw'), failed = deferred(); s.detailPending.push(failed); s.page.onDetail(pendingRow(9));
  failed.reject(new Error('offline')); await tick(); assert.equal(s.page.detailFailed.value, true); assert.equal(s.page.detailLoading.value, false);
  await s.page.refreshCurrentWithdrawal(); assert.equal(s.page.detailFailed.value, false);
  s.emit('connected'); await s.flush(); assert.equal(s.calls.table, 1); assert.equal(s.calls.details.length, 3); s.dispose();
});

test('withdraw session changes, permission loss and unmount stop queues and discard old detail replies', async () => {
  const s = setup('withdraw'), detail = deferred(); s.detailPending.push(detail); s.page.onDetail(pendingRow(9));
  s.emit('withdraw_status_update', event()); s.auth.sessionRevision++;
  assert.equal(s.page.detailVisible.value, false); assert.equal(s.timers.size, 0);
  detail.resolve({ data: { id: 9, status: 'APPROVED' } }); await tick(); assert.equal(s.page.current.value, null);
  s.auth.permissions = []; s.emit('withdraw_status_update', event()); assert.equal(s.timers.size, 0); assert.equal(s.handlers.get('withdraw_status_update').size, 0);
  s.auth.permissions = ['withdraw:view']; assert.equal(s.handlers.get('withdraw_status_update').size, 1);
  s.emit('withdraw_status_update', event()); s.dispose(); assert.equal(s.timers.size, 0); assert.equal(s.handlers.get('withdraw_status_update').size, 0);
});

test('withdraw audits require pending state and current audit permission, then reconcile through the queue', async () => {
  const s = setup('withdraw'); const approval = s.page.onApprove(pendingRow(9));
  await s.page.onApprove(pendingRow(9)); assert.equal(s.calls.prompts.length, 1);
  s.calls.prompts[0].pending.resolve({ value: ' LOCAL-PAYOUT ' }); await approval;
  assert.equal(s.calls.actions[0][0], 9); assert.equal(s.calls.actions[0][1], 'approve'); assert.equal(s.calls.actions[0][2].payout_reference, 'LOCAL-PAYOUT');
  assert.equal(s.page.auditBusy.value, false); await s.flush(); assert.equal(s.calls.table, 1);
  await s.page.onReject({ ...pendingRow(9), status: 'APPROVED' }); assert.equal(s.calls.prompts.length, 1);
  s.auth.permissions = ['withdraw:view']; await s.page.onApprove(pendingRow(10)); assert.equal(s.calls.actions.length, 1); s.dispose();
});

for (const reason of ['same-withdrawal push', 'reconnect', 'session change', 'audit permission loss', 'unmount']) {
  test('withdraw pending confirmation cannot submit after ' + reason, async () => {
    const s = setup('withdraw'), approval = s.page.onApprove(pendingRow(9));
    if (reason === 'same-withdrawal push') s.emit('withdraw_status_update', event());
    if (reason === 'reconnect') s.emit('connected');
    if (reason === 'session change') s.auth.sessionRevision++;
    if (reason === 'audit permission loss') s.auth.permissions = ['withdraw:view'];
    if (reason === 'unmount') s.dispose();
    s.calls.prompts[0].pending.resolve({ value: 'LATE-CONFIRM' }); await approval;
    assert.equal(s.calls.actions.length, 0); assert.equal(s.page.auditBusy.value, false); assert.equal(s.calls.close, 1);
    if (reason !== 'unmount') s.dispose();
  });
}

test('withdraw unrelated pushes keep a pending rejection usable and invalid payloads do not refresh', async () => {
  const s = setup('withdraw'), rejection = s.page.onReject(pendingRow(9));
  s.emit('withdraw_status_update', event(10)); s.calls.prompts[0].pending.resolve({ value: '本地核验不符' }); await rejection;
  assert.equal(s.calls.actions[0][1], 'reject'); await s.flush(); const count = s.calls.table;
  for (const data of [{}, event('9'), { ...event(), account_id: -1 }, event(9, 'UNKNOWN')]) s.emit('withdraw_status_update', data);
  await s.flush(); assert.equal(s.calls.table, count); s.dispose();
});

test('withdraw a final-state push blocks auditing an old pending row before the list request finishes', async () => {
  const s = setup('withdraw');
  s.emit('withdraw_status_update', event(9, 'REJECTED'));
  await s.page.onApprove(pendingRow(9)); await s.page.onReject(pendingRow(9));
  assert.equal(s.calls.prompts.length, 0); assert.equal(s.calls.actions.length, 0); s.dispose();
});

test('wallet updates reconcile list and the current provider balance without replacing recharge drafts', async () => {
  const s = setup('wallet'); s.page.query.value.account_type = 'PROVIDER'; s.page.query.value.nickname = 'local';
  await s.page.onRecharge({ id: 7, account_id: 42, user_role: 'PROVIDER', balance: 1000, frozen_amount: 500 });
  s.page.rechargeYuan.value = 188; s.page.rechargeRemark.value = '保留草稿';
  for (let i = 0; i < 4; i++) s.emit('wallet_update', { account_id: 42 }); await s.flush();
  assert.equal(s.calls.table, 1); assert.equal(s.calls.reload, 0); assert.equal(s.calls.details.length, 1);
  assert.equal(s.calls.details[0].url, '/wallets/7/'); assert.equal(s.calls.details[0].params.account_type, 'PROVIDER');
  assert.equal(s.page.current.value.balance, 8800); assert.equal(s.page.current.value.frozen_amount, 1200);
  assert.equal(s.page.rechargeYuan.value, 188); assert.equal(s.page.rechargeRemark.value, '保留草稿');
  assert.equal(s.page.query.value.nickname, 'local');
  s.emit('wallet_update', { account_id: 43 }); await s.flush(); assert.equal(s.calls.details.length, 1);
  s.emit('connected'); await s.flush(); assert.equal(s.calls.details.length, 2); s.dispose();
});

test('wallet updates received during a refresh retain exactly one serialized follow-up', async () => {
  const s = setup('wallet'), pending = deferred(); s.tablePending.push(pending);
  s.emit('wallet_update', { account_id: 42 }); const running = s.startTimer(); await tick();
  for (let i = 0; i < 5; i++) s.emit('wallet_update', { account_id: 42 });
  pending.resolve(); await running; await s.flush(); assert.equal(s.calls.table, 2); assert.equal(s.maxActiveTable, 1); s.dispose();
});

test('wallet session changes and revocation close drafts, ignore old replies and remove subscriptions', async () => {
  const s = setup('wallet'), detail = deferred();
  s.page.onAdjust({ id: 7, account_id: 42, user_role: 'CUSTOMER', balance: 1000 });
  s.detailPending.push(detail); s.emit('wallet_update', { account_id: 42 }); const running = s.startTimer(); await tick();
  s.auth.sessionRevision++; assert.equal(s.page.adjustVisible.value, false); assert.equal(s.page.current.value, null);
  detail.resolve({ data: { id: 7, balance: 99999 } }); await running; assert.equal(s.page.current.value, null);
  s.auth.permissions = []; s.emit('wallet_update', { account_id: 42 }); assert.equal(s.timers.size, 0); assert.equal(s.handlers.get('wallet_update').size, 0);
  s.auth.permissions = ['wallet:view']; s.emit('wallet_update', { account_id: 42 }); s.dispose(); assert.equal(s.timers.size, 0); assert.equal(s.handlers.get('wallet_update').size, 0);
});

test('wallet late package replies do not refill a closed or switched-account recharge draft', async () => {
  const s = setup('wallet'), packages = deferred(); s.packagePending.push(packages);
  const opening = s.page.onRecharge({ id: 7, account_id: 42, user_role: 'CUSTOMER', balance: 1000 });
  s.auth.sessionRevision++; packages.resolve({ data: [{ id: 1, amount: 10000 }] }); await opening;
  assert.equal(s.page.rechargeVisible.value, false); assert.equal(s.page.rechargePackages.value.length, 0); assert.equal(s.page.current.value, null);
  await s.page.onAdjustSave(); await s.page.onRechargeSave(); assert.equal(s.calls.actions.length, 0); s.dispose();
});

test('wallet invalid payloads and users without read permission cannot start refreshes', async () => {
  const s = setup('wallet', ['wallet:recharge']); assert.equal(s.calls.connect, 0);
  s.emit('wallet_update', { account_id: 42 }); assert.equal(s.timers.size, 0);
  await s.page.onRecharge({ id: 7 }); assert.equal(s.page.rechargeVisible.value, false);
  s.auth.permissions.push('wallet:view'); assert.equal(s.calls.connect, 1);
  for (const data of [{}, { account_id: '42' }, { account_id: 0 }]) s.emit('wallet_update', data);
  await s.flush(); assert.equal(s.calls.table, 0); s.dispose();
});
