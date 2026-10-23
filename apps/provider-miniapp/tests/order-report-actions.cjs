const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const appRoot = path.resolve(__dirname, '..');
const ts = createRequire(path.join(appRoot, 'package.json'))('typescript');
const tick = () => new Promise(resolve => setImmediate(resolve));

function environment(orders, { grabResult = { code: 0 }, teammates = [{ id: 9, nickname: '测试队友', level: '大师' }] } = {}) {
  const values = [];
  let cursor = 0, didShow, tree;
  const created = [], navigation = [], grabs = [], teammateRequests = [], notices = [];
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }), Fragment: 'Fragment',
    useState: initial => { const index = cursor++; if (!(index in values)) values[index] = typeof initial === 'function' ? initial() : initial; return [values[index], next => { values[index] = typeof next === 'function' ? next(values[index]) : next; }]; },
    useRef: initial => { const index = cursor++; if (!(index in values)) values[index] = { current: initial }; return values[index]; },
    useCallback: fn => fn, useEffect() {},
  };
  const dependencies = {
    react: { __esModule: true, default: react, ...react },
    '@tarojs/components': Object.fromEntries(['View', 'Text', 'Button', 'Picker'].map(name => [name, name])),
    '@tarojs/taro': { __esModule: true, default: { switchTab: async options => navigation.push(options.url), showToast: value => notices.push(value), showLoading() {}, hideLoading() {}, navigateTo() {} }, useDidShow: fn => { didShow = fn; }, useDidHide() {}, usePullDownRefresh() {} },
    '@/services/user': { fetchEscortMe: async () => ({ code: 0, data: { qualifications: [] } }), fetchProviderPass: async () => ({ code: 0, data: null }) },
    '@/utils/auth': { getSessionRevision: () => 1 },
    '@/utils/orderNetwork': { networkSnapshot: () => ({ stale: true, samples: 0, failures: 0 }), cooldownRemaining: () => 0 },
    '@/utils/request': { request: async () => ({ code: 0 }) },
    '@/services/order': { fetchOrders: async () => ({ code: 0, data: orders }), fetchProviderStats: async () => ({ code: 0, data: {} }), fetchMyEvaluations: async () => ({ code: 0, data: { list: [] } }),
      grabOrder: async (id, teamSize = 1, partnerId) => { grabs.push({ id, teamSize, partnerId }); return grabResult; },
      fetchTeammates: async id => { teammateRequests.push(id); return { code: 0, data: teammates }; },
    },
    '@/types/order': { statusTextMap: { COMPLETED: '已完成' }, REJECT_REASONS: [] },
    '@/services/websocket': { wsService: {} }, '@/services/orderAlerts': { orderAlerts: {} },
    '@/utils/coalescedRefresh': {}, '@/utils/format': { formatXaCoin: value => String((value || 0) / 10) },
    '@/services/report': { createReport: async id => { created.push(id); return { code: 0 }; } },
    '@/components': { Empty: 'Empty', Skeleton: 'Skeleton' },
    './index.module.scss': { default: new Proxy({}, { get: (_, key) => String(key) }) },
  };
  for (const name of ['PlatformLayout', 'PageHeading', 'ReplyDialog', 'SurfaceDialog']) dependencies['@/components/' + name] = { default: name };
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(appRoot, 'src/pages/orders/index.tsx'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, process: { env: { TARO_ENV: 'h5' } }, require: name => { if (!(name in dependencies)) throw new Error('Unexpected import ' + name); return dependencies[name]; } });
  const render = () => { cursor = 0; tree = module.exports.default(); };
  const visit = (value, rows = []) => { if (Array.isArray(value)) value.forEach(child => visit(child, rows)); else if (value && typeof value === 'object') { rows.push(value); if (value.type !== 'SurfaceDialog' || value.props.open) { visit(value.props.children, rows); visit(value.props.footer, rows); } visit(value.props.action, rows); } return rows; };
  const text = value => Array.isArray(value) ? value.map(text).join('') : value && typeof value === 'object' ? text(value.props.children) : value == null || value === false ? '' : String(value);
  render();
  return { created, navigation, grabs, teammateRequests, notices, render, text, nodes: () => visit(tree), load: async () => { didShow(); await tick(); render(); }, button: label => visit(tree).find(node => node.type === 'Button' && text(node) === label) };
}

const base = { status: 'COMPLETED', payment_status: 'PAID', expected_income: 1000, game_rounds: 1, created_at: '2026-10-13T10:00:00+08:00', remark: '' };
test('cancelled order card treats its price as historical and directs net income checks to the wallet', async () => {
  const env = environment([{ ...base, id: 7, status: 'CANCELLED', service: { id: 1, name: '已退款测试单' } }]); await env.load();
  const text = env.nodes().filter(node => node.type === 'Text').map(env.text).join('');
  assert.ok(text.includes('原预计单钱')); assert.ok(text.includes('实际净收益以钱包流水为准')); assert.ok(!text.includes('报单全部通过且售后窗口结束后入账'));
});

test('partial order cards retain completed state and separate original estimate from server net income and remaining freeze', async () => {
  const env = environment([{ ...base, id: 8, expected_income: 8000, payment_status: 'PARTIALLY_REFUNDED', settled_at: '2026-10-19T00:00:00Z', service: { id: 1, name: '部分退款服务' },
    refund_summary: { amount: 3750, remaining_amount: 6250, recovered_amount: 3000, net_income: 5000, remaining_frozen_amount: 5000, records: [{ id: 8, amount: 2500, recovered_amount: 2000, created_at: '2026-10-20T00:00:00Z' }, { id: 9, amount: 1250, recovered_amount: 1000, created_at: '2026-10-20T01:00:00Z' }] },
  }]); await env.load(); const text = env.nodes().filter(node => node.type === 'Text').map(env.text).join('');
  for (const expected of ['已完成 · 部分退款', '原核定单钱', '累计已退 375币 · 订单剩余 625币', '本人累计追回收益 300币', '本单实际净收益 500币', '本单剩余冻结 500币', '退款 #8 · 250币', '退款 #9 · 125币']) assert.ok(text.includes(expected), expected);
  assert.ok(!text.includes('订单已取消/退款')); assert.ok(!text.includes('报单全部通过且售后窗口结束后入账'));
});
test('completed gifts retain wallet guidance without service report or evaluation actions', async () => {
  const env = environment([
    { ...base, id: 1, order_no: 'GIFT-1', service: { id: 1, name: '礼物', is_gift: true } },
    { ...base, id: 2, order_no: 'TIP-2', source_order: 99, service: { id: 2, name: '订单打赏' } },
  ]);
  await env.load();
  const content = env.nodes().filter(node => node.type === 'Text').map(env.text);
  assert.equal(content.filter(value => value === '礼物结算进度请在钱包查看').length, 2);
  assert.equal(content.includes('等待老板评价'), false);
  assert.equal(env.nodes().some(node => node.type === 'Button' && env.text(node) === '查看 / 补充报单'), false);
  const walletAction = env.nodes().find(node => node.type === 'Button' && env.text(node) === '前往钱包');
  assert.ok(walletAction); await walletAction.props.onClick();
  assert.deepEqual(env.navigation, ['/pages/wallet/index']);
  assert.deepEqual(env.created, []);
});

test('completed service keeps report navigation and evaluation waiting state', async () => {
  const env = environment([{ ...base, id: 3, order_no: 'SERVICE-3', service: { id: 3, name: '服务订单', is_gift: false } }]);
  await env.load();
  assert.ok(env.nodes().some(node => node.type === 'Text' && env.text(node) === '等待老板评价'));
  const action = env.nodes().find(node => node.type === 'Button' && env.text(node) === '查看 / 补充报单');
  assert.ok(action); await action.props.onClick();
  assert.deepEqual(env.created, [3]); assert.deepEqual(env.navigation, ['/pages/report/index']);
});

test('percentage discount is rendered as Chinese discount notation', async () => {
  const env = environment([{ ...base, id: 3, order_no: 'SERVICE-3', service: { id: 3, name: '服务订单' }, customer: { nickname: '老板', boss_type: { name: 'VIP', discount_rate: 90, color: '#136398' } } }]);
  await env.load();
  assert.ok(env.nodes().some(node => node.type === 'Text' && env.text(node) === '9折 VIP价'));
});

const pending = overrides => ({ ...base, id: 10, order_no: 'APPLICATION-10', status: 'PENDING', requires_boss_selection: true, service: { id: 10, name: '报名测试服务' }, ...overrides });

test('order quantity shows its saved unit and never guesses from the current service', async () => {
  const env = environment([
    { ...base, id: 21, order_no: 'HOUR-21', game_rounds: 2, quantity_unit: '小时', service: { id: 21, name: '历史小时单', price_unit: '局' } },
    { ...base, id: 22, order_no: 'ROUND-22', game_rounds: 3, quantity_unit: '局', service: { id: 22, name: '历史局数单', price_unit: '小时' } },
    { ...base, id: 23, order_no: 'LEGACY-23', game_rounds: 4, service: { id: 23, name: '旧记录', price_unit: '小时' } },
  ]);
  await env.load();
  const labels = env.nodes().filter(node => node.type === 'Text').map(env.text);
  for (const label of ['2 小时', '3 局', '4 份']) assert.ok(labels.includes(label), label);
  for (const label of ['2 局', '3 小时', '4 小时']) assert.equal(labels.includes(label), false, label);
});

test('single service never fetches teammates or permits a partner and submits team_size one', async () => {
  const env = environment([pending({ required_team_size: 1, can_team_apply: false })]);
  await env.load(); await env.button('报名抢单').props.onClick(); env.render();
  assert.deepEqual(env.teammateRequests, []);
  assert.equal(env.button('请选择队友'), undefined);
  assert.ok(env.nodes().some(node => node.type === 'Text' && env.text(node) === '单人报名'));
  await env.button('提交报名').props.onClick(); env.render();
  assert.deepEqual(env.grabs, [{ id: 10, teamSize: 1, partnerId: undefined }]);
});

test('double service requires an eligible partner and submits team_size two', async () => {
  const env = environment([pending({ required_team_size: 2, can_team_apply: true })]);
  await env.load(); await env.button('报名抢单').props.onClick(); env.render();
  assert.deepEqual(env.teammateRequests, [10]);
  assert.equal(env.button('提交报名').props.disabled, true);
  assert.ok(env.nodes().some(node => node.type === 'Text' && env.text(node) === '双人组队'));
  const picker = env.nodes().find(node => node.type === 'Picker' && node.props.mode === 'selector');
  assert.ok(picker); picker.props.onChange({ detail: { value: '0' } }); env.render();
  assert.equal(env.button('提交报名').props.disabled, false);
  await env.button('提交报名').props.onClick();
  assert.deepEqual(env.grabs, [{ id: 10, teamSize: 2, partnerId: 9 }]);
});

test('missing attendance fields safely uses single mode without guessing from directory or participants', async () => {
  const env = environment([pending({ participants: [{ id: 7 }, { id: 8 }], service: { id: 10, name: '已有订单', billing_provider_count: 2 } })]);
  await env.load(); await env.button('报名抢单').props.onClick(); env.render();
  assert.deepEqual(env.teammateRequests, []);
  assert.equal(env.button('请选择队友'), undefined);
  await env.button('提交报名').props.onClick();
  assert.deepEqual(env.grabs, [{ id: 10, teamSize: 1, partnerId: undefined }]);
});

test('denied team eligibility never opens a double application', async () => {
  const env = environment([pending({ required_team_size: 2, can_team_apply: false })]);
  await env.load(); await env.button('报名抢单').props.onClick(); env.render();
  assert.deepEqual(env.teammateRequests, []); assert.deepEqual(env.grabs, []);
  assert.equal(env.button('提交报名'), undefined);
  assert.equal(env.notices.at(-1).title, '当前订单暂不可组队，请刷新后重试');
});

test('backend rejection keeps the double choice open and exposes the reason', async () => {
  const env = environment([pending({ required_team_size: 2, can_team_apply: true })], { grabResult: { code: 400, msg: '队友接单资格已变化' } });
  await env.load(); await env.button('报名抢单').props.onClick(); env.render();
  env.nodes().find(node => node.type === 'Picker' && node.props.mode === 'selector').props.onChange({ detail: { value: '0' } }); env.render();
  await env.button('提交报名').props.onClick(); env.render();
  assert.ok(env.button('提交报名')); assert.equal(env.notices.at(-1).title, '队友接单资格已变化');
});

test('double order without a boss-selection flow cannot be directly grabbed by one person', async () => {
  const env = environment([pending({ requires_boss_selection: false, required_team_size: 2, can_team_apply: true })]);
  await env.load(); await env.button('抢单').props.onClick(); env.render();
  assert.deepEqual(env.grabs, []); assert.deepEqual(env.teammateRequests, []);
  assert.equal(env.notices.at(-1).title, '本单需要组队，请刷新订单或联系客服');
});
