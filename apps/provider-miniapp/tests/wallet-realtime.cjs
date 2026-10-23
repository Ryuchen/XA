const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const appRoot = path.resolve(__dirname, '..');
const ts = createRequire(path.join(appRoot, 'package.json'))('typescript');
const tick = () => new Promise(resolve => setImmediate(resolve));
const transpile = filename => ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React },
}).outputText;
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

function environment() {
  const slots = [], timers = new Map(), handlers = new Map(), authHandlers = new Set(), reads = [], mutations = [], notices = [];
  let cursor = 0, dirty = false, tree, effects = [], didShow, didHide, pull, timerId = 0, revision = 1, owner = '10', token = 'fixture', stopped = 0;
  let data = { balance: 9000, frozen_amount: 1000, min_amount: 0, balance_threshold: 5000, can_withdraw: true, tax_rate: 2, requests: [],
    transactions: [], total_income: 2000, total: 0,
    accounts: [{ id: 7, payee_method: 'WECHAT', payee_account: 'fixture-payee', payee_name: '本地测试账户', version: 1 }],
  };
  const readers = {};
  const same = (a, b) => a && b && a.length === b.length && a.every((value, index) => Object.is(value, b[index]));
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }), Fragment: 'Fragment',
    useState(initial) {
      const index = cursor++;
      if (!slots[index]) slots[index] = { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, next => { const value = typeof next === 'function' ? next(slots[index].value) : next; if (!Object.is(value, slots[index].value)) { slots[index].value = value; dirty = true; } }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ||= { current: initial }; },
    useCallback(callback, deps) { const index = cursor++; if (!same(slots[index]?.deps, deps)) slots[index] = { deps, callback }; return slots[index].callback; },
    useEffect(callback, deps) {
      const index = cursor++;
      if (!same(slots[index]?.deps, deps)) effects.push(() => {
        slots[index]?.cleanup?.(); slots[index] = { deps, cleanup: callback() };
      });
    },
  };
  const clock = { setTimeout: callback => { timers.set(++timerId, callback); return timerId; }, clearTimeout: id => timers.delete(id) };
  const refreshModule = { exports: {} };
  vm.runInNewContext(transpile(path.join(appRoot, 'src/utils/coalescedRefresh.ts')), { module: refreshModule, exports: refreshModule.exports, ...clock });
  const moneyModule = { exports: {} };
  vm.runInNewContext(transpile(path.resolve(appRoot, '../../packages/money/src/index.js')), { module: moneyModule, exports: moneyModule.exports });
  const money = moneyModule.exports;
  const withdrawModule = { exports: {} };
  vm.runInNewContext(transpile(path.join(appRoot, 'src/utils/withdraw.ts')), { module: withdrawModule, exports: withdrawModule.exports, require: () => money });
  const read = (kind, args, fallback) => { reads.push({ kind, args, revision }); return readers[kind] ? readers[kind](args) : Promise.resolve({ code: 0, data: fallback() }); };
  const overview = () => ({ ...data, requests: data.requests.slice() });
  const dependencies = {
    react: { __esModule: true, default: react, ...react },
    '@tarojs/components': Object.fromEntries(['Button', 'Input', 'Text', 'View'].map(name => [name, name])),
    '@tarojs/taro': { __esModule: true, default: {
      showToast: value => notices.push(value), navigateTo() {}, stopPullDownRefresh: () => { stopped++; },
      eventCenter: { on: (type, fn) => authHandlers.add(fn), off: (type, fn) => authHandlers.delete(fn) },
    }, useDidShow: fn => { didShow = fn; }, useDidHide: fn => { didHide = fn; }, usePullDownRefresh: fn => { pull = fn; } },
    '@/services/wallet': {
      fetchWithdrawOverview: amount => amount == null ? read('overview', undefined, overview)
        : read('quote', amount, () => ({ ...overview(), amount, tax_amount: 20, actual_amount: amount - 20 })),
      fetchTransactions: args => read('transactions', args, () => ({ transactions: data.transactions, total_income: data.total_income, total: data.total, page: args.page || 1, page_size: 50 })),
      fetchProviderBenefits: args => read('benefits', args, () => ({ summary: { total_reward: 0, total_penalty: 0, total_gift_amount: 0, total_gift_income: 0, gift_count: 0 }, dispose_records: [], gift_records: [], pagination: { page_size: 20, dispose: { page: 1, total: 0 }, gift: { page: 1, total: 0 } } })),
      fetchWithdrawalAccounts: () => read('accounts', undefined, () => data.accounts),
      withdrawRequest: async payload => { mutations.push(payload); return { code: 0 }; },
    },
    '@/utils/format': money, '@/utils/withdraw': withdrawModule.exports,
    '@/utils/auth': { getSessionRevision: () => revision, getStoredToken: () => token, getStoredUser: () => owner ? { id: owner } : null },
    '@/utils/coalescedRefresh': refreshModule.exports,
    '@/services/websocket': { wsService: { on: (type, fn) => { if (!handlers.has(type)) handlers.set(type, new Set()); handlers.get(type).add(fn); return () => handlers.get(type).delete(fn); } } },
    '@/components': { Empty: 'Empty', Skeleton: 'Skeleton' },
    './index.module.scss': { default: new Proxy({}, { get: (_, key) => String(key) }) },
  };
  for (const name of ['PlatformLayout', 'PageHeading', 'SurfaceDialog']) dependencies['@/components/' + name] = { default: name };
  const module = { exports: {} };
  vm.runInNewContext(transpile(path.join(appRoot, 'src/pages/wallet/index.tsx')), { module, exports: module.exports, require: name => {
    if (!(name in dependencies)) throw Error('Unexpected import ' + name); return dependencies[name];
  } });
  const render = () => { dirty = false; cursor = 0; effects = []; tree = module.exports.default(); for (const effect of effects) effect(); };
  const flush = async () => { for (let i = 0; i < 30; i++) { await tick(); if (!dirty) return; render(); } throw Error('Effects did not stabilize'); };
  const visit = (node, all = []) => {
    if (Array.isArray(node)) node.forEach(child => visit(child, all));
    else if (node && typeof node === 'object') { all.push(node); if (node.type !== 'SurfaceDialog' || node.props.open) { visit(node.props.children, all); visit(node.props.footer, all); } }
    return all;
  };
  const text = node => Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object' ? text(node.props?.children) : node == null || node === false ? '' : String(node);
  const nodes = () => visit(tree);
  const button = label => nodes().find(node => node.type === 'Button' && text(node) === label);
  render();
  return { reads, mutations, notices, timers, handlers, authHandlers, render, flush, nodes, button, text: () => text(tree),
    data: next => { data = { ...data, ...next }; }, read: (kind, fn) => { readers[kind] = fn; },
    emit: (type, value = { account_id: 10 }) => { for (const handler of handlers.get(type) || []) handler(value); },
    show: async () => { await didShow(); await flush(); }, hide: () => didHide(),
    fire: async () => { assert.equal(timers.size, 1); const [id, fn] = timers.entries().next().value; timers.delete(id); await fn(); await flush(); },
    pull: async () => { await pull(); await flush(); }, stopped: () => stopped,
    session: async (nextOwner, nextRevision) => { owner = nextOwner; token = owner ? 'fixture-' + nextRevision : ''; revision = nextRevision; for (const handler of authHandlers) handler(); await flush(); },
    unmount: () => { for (const slot of slots) slot?.cleanup?.(); },
    quote: async () => { button('提现').props.onClick(); await flush(); nodes().find(node => node.type === 'Input').props.onInput({ detail: { value: '100' } }); await flush(); },
  };
}

test('one page show performs exactly one initial batch and no effect duplicates it', async () => {
  const env = environment(); assert.equal(env.reads.length, 0); await env.show();
  assert.deepEqual(env.reads.map(row => row.kind).sort(), ['accounts', 'benefits', 'overview', 'transactions']);
  assert.ok(env.text().includes('900币')); assert.ok(env.text().includes('100币'));
});

test('settled refund wallet updates show net income and explicitly labeled signed reversal rows', async () => {
  const env = environment(); await env.show();
  const row = { status: 'SUCCESS', created_at: '2026-10-20T10:00:00Z', is_reversal: true, reversal_of: 1 };
  env.data({ total_income: 0, balance: 9000, frozen_amount: 0, total: 3, transactions: [
    { ...row, id: 2, tx_type: 'INCOME', amount: -800 }, { ...row, id: 3, tx_type: 'EARNINGS_FREEZE', amount: 560 }, { ...row, id: 4, tx_type: 'DEPOSIT', amount: 240 },
  ] }); env.emit('wallet_update'); await env.fire();
  const texts = env.nodes().filter(node => node.type === 'Text').map(node => node.props.children.join(''));
  assert.ok(texts.includes('订单净收益')); assert.ok(texts.includes('0币')); assert.ok(texts.includes('收益退回')); assert.ok(texts.includes('收益冻结退回')); assert.ok(texts.includes('押金抵扣退回'));
  assert.ok(texts.includes('-80币')); assert.ok(texts.includes('+56币')); assert.ok(texts.includes('+24币')); assert.deepEqual(env.mutations, []); env.unmount();
});

test('refunded gift history displays server net receipts and never presents its former approval as still receivable', async () => {
  const env = environment();
  env.read('benefits', async () => ({ code: 0, data: {
    summary: { total_reward: 0, total_penalty: 0, total_gift_amount: 10000, total_gift_income: 0, gift_count: 1 }, dispose_records: [],
    gift_records: [{ id: 8, service_name: '已退款测试礼物', customer_name: '本地老板', order_no: 'LOCAL-GIFT', amount: 10000, provider_income: 8000, received_income: 0, settlement_status: 'REFUNDED', completed_at: '2026-10-20T00:00:00Z' }],
    pagination: { page_size: 20, dispose: { page: 1, total: 0 }, gift: { page: 1, total: 1 } },
  } })); await env.show();
  env.nodes().find(node => node.type === 'Button' && node.props.className?.includes('tab') && node.props.children[0] === '礼物').props.onClick(); await env.flush();
  assert.ok(env.text().includes('礼物净实得')); assert.ok(env.text().includes('已退款 · 收益已冲回')); assert.ok(env.text().includes('原赠礼 1000币')); assert.ok(env.text().includes('0币'));
  for (const obsolete of ['应得 800币', '+800币', '请联系客服核对', '待入账']) assert.ok(!env.text().includes(obsolete), obsolete);
  const zero = env.nodes().find(node => node.type === 'Text' && node.props.className === 'itemTitle' && node.props.children.join('') === '0币'); assert.ok(zero, 'Zero net receipts should use a neutral label');
  assert.deepEqual(env.mutations, []); env.unmount();
});

test('partially refunded gift keeps its server net receipt and later full refund updates it without replaying the historical estimate', async () => {
  const env = environment(); let income = 5000, status = 'PARTIALLY_REFUNDED';
  env.read('benefits', async () => ({ code: 0, data: { summary: { total_reward: 0, total_penalty: 0, total_gift_amount: 10000, total_gift_income: income, gift_count: 1 }, dispose_records: [],
    gift_records: [{ id: 9, service_name: '分笔退款礼物', customer_name: '本地老板', order_no: 'LOCAL-GIFT-PART', amount: 10000, provider_income: 8000, received_income: income, settlement_status: status, completed_at: '2026-10-20T00:00:00Z' }],
    pagination: { page_size: 20, dispose: { page: 1, total: 0 }, gift: { page: 1, total: 1 } },
  } })); await env.show(); env.nodes().find(node => node.type === 'Button' && node.props.className?.includes('tab') && node.props.children[0] === '礼物').props.onClick(); await env.flush();
  assert.ok(env.text().includes('部分退款 · 保留净实得')); assert.ok(env.text().includes('+500币')); assert.ok(!env.text().includes('应得 800币')); assert.ok(!env.text().includes('收益已冲回'));
  income = 0; status = 'REFUNDED'; env.emit('wallet_update'); await env.fire(); assert.ok(env.text().includes('已退款 · 收益已冲回')); assert.ok(!env.text().includes('+500币')); assert.deepEqual(env.mutations, []); env.unmount();
});

test('multiple partial income/freeze reversals remain distinct and use signed net income without merging on reversal_of', async () => {
  const env = environment(), row = { status: 'SUCCESS', created_at: '2026-10-20T00:00:00Z' };
  env.data({ balance: 0, frozen_amount: 5000, total_income: 5000, total: 6, transactions: [
    { ...row, id: 1, tx_type: 'INCOME', amount: 8000 }, { ...row, id: 2, tx_type: 'EARNINGS_FREEZE', amount: -8000 },
    { ...row, id: 3, tx_type: 'INCOME', amount: -2000, is_reversal: true, reversal_of: 1 }, { ...row, id: 4, tx_type: 'EARNINGS_FREEZE', amount: 2000, is_reversal: true, reversal_of: 2 },
    { ...row, id: 5, tx_type: 'INCOME', amount: -1000, is_reversal: true, reversal_of: 1 }, { ...row, id: 6, tx_type: 'EARNINGS_FREEZE', amount: 1000, is_reversal: true, reversal_of: 2 },
  ] }); await env.show(); const text = env.text();
  assert.equal((text.match(/收益退回/g) || []).length, 2); assert.equal((text.match(/收益冻结退回/g) || []).length, 2);
  for (const amount of ['-200币', '-100币', '+200币', '+100币', '500币']) assert.ok(text.includes(amount), amount);
  assert.deepEqual(env.mutations, []); env.unmount();
});

test('wallet and withdrawal bursts reload balances, frozen funds, ledger and withdrawal records together', async () => {
  const env = environment(); await env.show();
  env.data({ balance: 11000, frozen_amount: 500, total_income: 3500, total: 1,
    transactions: [{ id: 1, tx_no: 'FIXTURE', tx_type: 'EARNINGS_RELEASE', amount: 2000, status: 'SUCCESS', created_at: '2026-10-19T10:00:00Z' }],
    requests: [{ id: 3, amount: 1000, actual_amount: 980, tax_amount: 20, status: 'APPROVED', payee_method_display: '微信', payee_name: '本地账户', created_at: '2026-10-19T10:00:00Z' }],
  });
  for (let i = 0; i < 12; i++) { env.emit('wallet_update'); env.emit('withdraw_status_update', { account_id: 10, withdraw_id: 3, status: 'APPROVED' }); }
  assert.equal(env.timers.size, 1); await env.fire(); assert.equal(env.reads.length, 8);
  assert.ok(env.text().includes('1100币')); assert.ok(env.text().includes('50币')); assert.ok(env.text().includes('350币')); assert.ok(env.text().includes('收益解冻'));
  const tab = env.nodes().find(node => node.type === 'Button' && node.props.className?.includes('tab') && node.props.children[0] === '提现');
  tab.props.onClick(); await env.flush(); assert.ok(env.text().includes('已通过')); assert.deepEqual(env.mutations, []);
});

test('unrelated account events are ignored; reconnect reconciles the active account', async () => {
  const env = environment(); await env.show(); env.emit('wallet_update', { account_id: 99 }); env.emit('withdraw_status_update', {});
  assert.equal(env.timers.size, 0); env.emit('connected', undefined); await env.fire(); assert.equal(env.reads.length, 8);
});

test('updates during an in-flight read discard its stale snapshot and coalesce one trailing batch', async () => {
  const env = environment(); await env.show(); const pending = deferred(); env.read('overview', () => pending.promise);
  env.emit('wallet_update'); const reading = env.fire();
  for (let i = 0; i < 10; i++) env.emit('withdraw_status_update', { account_id: 10, withdraw_id: 3, status: 'REJECTED' });
  pending.resolve({ code: 0, data: { balance: 777770, frozen_amount: 0, requests: [] } }); await reading;
  assert.ok(!env.text().includes('77777币')); assert.equal(env.timers.size, 1);
  env.read('overview', undefined); env.data({ balance: 12000, frozen_amount: 0 }); await env.fire();
  assert.ok(env.text().includes('1200币')); assert.equal(env.reads.length, 12); assert.equal(env.timers.size, 0);
});

test('hidden pages skip pushes and deferred work, then read missed changes on show', async () => {
  const env = environment(); await env.show(); env.emit('wallet_update'); env.hide(); await env.fire();
  for (const event of ['wallet_update', 'withdraw_status_update', 'connected']) env.emit(event);
  assert.equal(env.reads.length, 4); assert.equal(env.timers.size, 0); await env.show(); assert.equal(env.reads.length, 8);
});

test('unmount cleans all subscriptions and queued timers and rejects in-flight wallet responses', async () => {
  const env = environment(); await env.show(); const pending = deferred(); env.read('overview', () => pending.promise);
  env.emit('wallet_update'); const reading = env.fire(); env.unmount();
  for (const listeners of env.handlers.values()) assert.equal(listeners.size, 0);
  assert.equal(env.authHandlers.size, 0); pending.resolve({ code: 0, data: { balance: 777770, frozen_amount: 0, requests: [] } }); await reading;
  assert.ok(!env.text().includes('77777币')); assert.equal(env.timers.size, 0);
  const queued = environment(); queued.emit('wallet_update'); queued.unmount(); assert.equal(queued.timers.size, 0);
});

test('account changes clear financial and payee data immediately and discard the old account response', async () => {
  const env = environment(); await env.show(); await env.quote(); assert.equal(env.button('确认提现').props.disabled, false);
  const pending = deferred(); env.read('overview', () => pending.promise); env.emit('wallet_update'); const reading = env.fire();
  await env.session(null, 2); assert.ok(!env.text().includes('900币')); assert.ok(!env.text().includes('本地测试账户')); assert.equal(env.button('确认提现'), undefined);
  pending.resolve({ code: 0, data: { balance: 777770, frozen_amount: 0, requests: [] } }); await reading; assert.ok(!env.text().includes('77777币'));
  env.read('overview', undefined); env.data({ balance: 6300 }); await env.session('11', 3); assert.ok(env.text().includes('630币'));
  const count = env.reads.length; await env.session('11', 3); assert.equal(env.reads.length, count);
});

test('an audit event synchronously invalidates an actionable quote and refreshes it only after wallet data loads', async () => {
  const env = environment(); await env.show(); await env.quote(); const oldSubmit = env.button('确认提现').props.onClick;
  assert.equal(env.button('确认提现').props.disabled, false);
  const pending = deferred(); env.read('overview', () => pending.promise); env.emit('withdraw_status_update', { account_id: 10, withdraw_id: 1, status: 'REJECTED' });
  await oldSubmit(); assert.deepEqual(env.mutations, []); await env.flush(); assert.equal(env.button('确认提现').props.disabled, true);
  const quoteReads = env.reads.filter(row => row.kind === 'quote').length; const reading = env.fire(); await env.flush();
  assert.equal(env.reads.filter(row => row.kind === 'quote').length, quoteReads);
  pending.resolve({ code: 0, data: { balance: 10000, frozen_amount: 0, min_amount: 0, tax_rate: 2, requests: [] } }); await reading;
  assert.equal(env.reads.filter(row => row.kind === 'quote').length, quoteReads + 1); assert.equal(env.button('确认提现').props.disabled, false);
});

test('an old in-flight quote cannot overwrite financial data after a wallet invalidation', async () => {
  const env = environment(); await env.show(); const pending = deferred(); env.read('quote', () => pending.promise);
  await env.quote(); assert.equal(env.button('确认提现').props.disabled, true); env.emit('wallet_update'); await env.flush();
  pending.resolve({ code: 0, data: { amount: 1000, tax_amount: 20, actual_amount: 980, tax_rate: 2, min_amount: 0, balance: 777770, frozen_amount: 0 } }); await env.flush();
  assert.ok(!env.text().includes('77777币')); assert.equal(env.button('确认提现').props.disabled, true); env.unmount();
});

test('a complete refreshed quote submits raw units and the exact server quote and payee version', async () => {
  const env = environment(); await env.show(); await env.quote(); await env.button('确认提现').props.onClick(); await env.flush();
  assert.deepEqual(JSON.parse(JSON.stringify(env.mutations)), [{ amount: 1000, withdrawal_account_id: 7, expected_payee_version: 1,
    expected_tax_rate: 2, expected_tax_amount: 20, expected_actual_amount: 980 }]);
});

test('pull-down completes even on failure and a failed withdrawal ledger is visibly marked returned', async () => {
  const env = environment(); await env.show(); env.read('overview', async () => { throw Error('离线测试'); }); await env.pull(); assert.equal(env.stopped(), 1);
  env.read('overview', undefined); env.data({ total: 1, transactions: [{ id: 1, tx_type: 'WITHDRAW', amount: -1000, status: 'FAILED', remark: '提现驳回退回', created_at: '2026-10-19T10:00:00Z' }] });
  await env.pull(); assert.equal(env.stopped(), 2); assert.ok(env.text().includes('已退回余额 · 原申请金额'));
});

test('rejected withdrawal cards show the returned application without tax or payout while other states keep their amounts', async () => {
  for (const [status, audit_remark] of [['REJECTED', '测试退回'], ['REJECTED', ''], ['APPROVED', ''], ['PENDING', '']]) {
    const env = environment();
    env.data({ requests: [{ id: 9, amount: 10000, tax_amount: 200, actual_amount: 9800, status,
      payee_method_display: '微信', payee_name: '本地测试账户', audit_remark, created_at: '2026-10-19T10:00:00Z' }] });
    await env.show();
    env.nodes().find(node => node.type === 'Button' && node.props.className?.includes('tab') && node.props.children[0] === '提现').props.onClick();
    await env.flush();
    const text = env.text();
    if (status === 'REJECTED') {
      assert.ok(text.includes('原申请 1000币')); assert.ok(text.includes('已退回余额'));
      for (const absent of ['-1000币', '税20', '到账980币', '¥98.00']) assert.ok(!text.includes(absent), absent);
    } else {
      assert.ok(text.includes('-1000币')); assert.ok(text.includes('税20 · 到账980币')); assert.ok(text.includes('¥98.00'));
      assert.ok(!text.includes('已退回余额'));
    }
  }
});
