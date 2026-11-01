const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '..');
const ts = createRequire(path.join(root, 'package.json'))('typescript');
const compile = file => ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;

test('order navigation intent is consumed once and does not cross sessions', () => {
  let revision = 1;
  const module = { exports: {} };
  vm.runInNewContext(compile('src/utils/orderNavigation.ts'), { module, exports: module.exports, require: () => ({ getSessionRevision: () => revision }) });
  const nav = module.exports;
  nav.queueOrderFocus(42); assert.equal(nav.takeOrderFocus(), 42); assert.equal(nav.takeOrderFocus(), null);
  nav.queueOrderPool(); assert.equal(nav.takeOrderFocus(), 0);
  nav.queueOrderFocus(42); revision++; assert.equal(nav.takeOrderFocus(), null);
});

async function messagePage(message) {
  let cursor = 0, effect;
  const state = [], routed = [];
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    useState: initial => { const index = cursor++; if (!(index in state)) state[index] = initial; return [state[index], value => { state[index] = value; }]; },
    useRef: initial => { const index = cursor++; if (!(index in state)) state[index] = { current: initial }; return state[index]; },
    useEffect: fn => { effect = fn; },
  };
  const dependencies = {
    react: { __esModule: true, default: react, ...react },
    '@tarojs/components': { Button: 'Button', Text: 'Text', View: 'View' },
    '@tarojs/taro': { __esModule: true, default: { eventCenter: { on() {}, off() {}, trigger() {} }, getCurrentInstance: () => ({ router: { params: { id: '1' } } }), switchTab: async ({ url }) => routed.push(url), navigateTo: async ({ url }) => routed.push(url), showToast() {} } },
    '@/utils/auth': { getSessionRevision: () => 1 },
    '@/services/message': { fetchMessageDetail: async () => ({ code: 0, data: message }) },
    '@/utils/orderNavigation': { queueOrderFocus: id => routed.push(id), queueOrderPool: () => routed.push('pool'), clearOrderFocus() {} },
    '@/utils/reportNavigation': { queueReportOrder: id => routed.push(`report:${id}`), clearReportOrder() {} },
    '@/components/PageNav': { default: 'PageNav' }, '@/components/PlatformLayout': { default: 'PlatformLayout' },
    './index.module.scss': { default: {} },
  };
  const module = { exports: {} };
  vm.runInNewContext(compile('src/pages/message-detail/index.tsx'), { module, exports: module.exports, Number, Date, require: name => dependencies[name] });
  const render = () => { cursor = 0; return module.exports.default(); };
  render(); effect(); await new Promise(resolve => setImmediate(resolve));
  const visit = (node, found = []) => { if (Array.isArray(node)) node.forEach(item => visit(item, found)); else if (node && typeof node === 'object') { found.push(node); visit(node.props.children, found); } return found; };
  const button = visit(render()).find(node => node.type === 'Button');
  if (button) await button.props.onClick();
  return { button, routed };
}

test('selected order notice opens its order and invitation opens the public pool', async () => {
  const base = { related_order_id: 42, title: '订单消息', preview: '查看更新', created_at: '2026-10-30T00:00:00Z' };
  const selected = await messagePage({ ...base, action_url: '/pages/orderList/index?orderId=42' });
  assert.deepEqual(selected.routed, [42, '/pages/orders/index']);
  const invitation = await messagePage({ ...base, action_url: '/pages/orders/index?tab=pending' });
  assert.deepEqual(invitation.routed, ['/pages/application-detail/index?orderId=42']);
  const directInvitation = await messagePage({ ...base, action_url: '/pages/orders/index?application_id=9' });
  assert.deepEqual(directInvitation.routed, ['/pages/application-detail/index?orderId=42&applicationId=9']);
  for (const action_url of ['/pages/orders/index?tab=pool', '']) {
    const withdrawn = await messagePage({ ...base, title: '队友已撤回组队报名', action_url });
    assert.equal(withdrawn.button.props.children[0], '前往抢单池');
    assert.deepEqual(withdrawn.routed, ['pool', '/pages/orders/index']);
  }
  const unrelated = await messagePage({ ...base, related_order_id: null, action_url: '' });
  assert.equal(unrelated.button, undefined);
  const wallet = await messagePage({ ...base, related_order_id: null, action_url: '/pages/wallet/index' });
  assert.equal(wallet.button.props.children[0], '前往钱包');
  assert.deepEqual(wallet.routed, ['/pages/wallet/index']);
  const external = await messagePage({ ...base, related_order_id: null, action_url: 'https://example.com/pages/wallet/index' });
  assert.equal(external.button, undefined);
  const reservation = await messagePage({ ...base, related_order_id: null, action_url: '/pages/reservations/index?id=17' });
  assert.equal(reservation.button.props.children[0], '查看预约');
  assert.deepEqual(reservation.routed, ['/pages/reservations/index?id=17']);
});

test('report notice focuses its related order', async () => {
  const opened = await messagePage({ related_order_id: 42, title: '报单已驳回', preview: '请补充', created_at: '2026-10-30T00:00:00Z', action_url: '/pages/report/index' });
  assert.equal(opened.button.props.children[0], '查看报单');
  assert.deepEqual(opened.routed, ['report:42', '/pages/report/index']);
});

test('real withdrawn-team notice opens the pool', { skip: !process.env.XA_WITHDRAWN_TEAM_NOTICE }, async () => {
  const message = JSON.parse(process.env.XA_WITHDRAWN_TEAM_NOTICE);
  assert.equal(message.title, '队友已撤回组队报名');
  assert.equal(message.action_url, '/pages/orders/index?tab=pool');
  const opened = await messagePage(message);
  assert.equal(opened.button.props.children[0], '前往抢单池');
  assert.deepEqual(opened.routed, ['pool', '/pages/orders/index']);
});

test('real evaluation notice opens the order returned by the message API', { skip: !process.env.XA_EVALUATION_NOTICE }, async () => {
  const message = JSON.parse(process.env.XA_EVALUATION_NOTICE);
  assert.equal(message.title, '收到新的服务评价');
  assert.ok(Number.isSafeInteger(message.related_order_id));
  const opened = await messagePage(message);
  assert.deepEqual(opened.routed, [message.related_order_id, '/pages/orders/index']);
});

test('real report review notice opens the report page', { skip: !process.env.XA_REPORT_NOTICE }, async () => {
  const message = JSON.parse(process.env.XA_REPORT_NOTICE);
  assert.equal(message.title, '报单审核未通过');
  assert.ok(Number.isSafeInteger(message.related_order_id));
  const opened = await messagePage(message);
  assert.equal(opened.button.props.children[0], '查看报单');
  assert.deepEqual(opened.routed, [`report:${message.related_order_id}`, '/pages/report/index']);
});
