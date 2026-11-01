const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '..');
const ts = createRequire(path.join(root, 'package.json'))('typescript');
const tick = () => new Promise(resolve => setImmediate(resolve));
const orderTypes = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root, 'src/types/order.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, { module: orderTypes, exports: orderTypes.exports });

function page({ blocked = false, closed = false, isPartner = true, status = 'PENDING', applicationId = '9', orderId = '42', reservationOrigin = null, actual = null } = {}) {
  const values = [], actions = [], notices = [], navigations = [];
  let cursor = 0, effect, tree, confirmed = false;
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    useState: initial => { const i = cursor++; if (!(i in values)) values[i] = typeof initial === 'function' ? initial() : initial; return [values[i], next => { values[i] = typeof next === 'function' ? next(values[i]) : next; }]; },
    useRef: initial => { const i = cursor++; if (!(i in values)) values[i] = { current: initial }; return values[i]; },
    useEffect: fn => { effect = fn; },
  };
  const member = { id: 1, nickname: '队友甲', level: '大师' };
  const application = { id: 9, members: [member], is_partner: isPartner, partner_confirmed: confirmed, ready: confirmed, status };
  const deps = {
    react: { __esModule: true, default: react, ...react },
    '@tarojs/components': { View: 'View', Text: 'Text', Button: 'Button' },
    '@tarojs/taro': { __esModule: true, default: { getCurrentInstance: () => ({ router: { params: { orderId, applicationId } } }), showModal: async () => ({ confirm: true }), showToast: notice => notices.push(notice), switchTab: async ({ url }) => navigations.push(url) }, useDidShow() {} },
    '@/components/PageNav': { default: 'PageNav' }, '@/components/PlatformLayout': { default: 'PlatformLayout' },
    '@/types/order': orderTypes.exports,
    '@/utils/auth': { getSessionRevision: () => 1 }, '@/utils/orderNavigation': { queueOrderPool() { navigations.push('pool'); }, queueOrderFocus(id) { navigations.push(id); }, clearOrderFocus() {} }, './index.module.scss': { default: {} },
    '@/services/order': {
      fetchMyApplications: async () => ({ code: 0, data: { applications: [application, { ...application, id: 10, members: [{ ...member, id: 2, nickname: '队友乙' }] }] } }),
      fetchMyApplication: async (oid, aid) => actual ? actual.detail(oid, aid) : ({ code: 0, data: { order: { order_no: 'ORDER-42', service: { name: '服务' }, reservation_origin: reservationOrigin }, application: { ...application, partner_confirmed: confirmed, ready: confirmed }, can_confirm: !blocked && !confirmed, confirm_reason: blocked ? '接单名额已满' : '', closed_reason: closed ? '订单已取消，本次报名结束。' : '' } }),
      applicationAction: async (oid, aid, action) => { actions.push(action); if (actual) return actual.action(oid, aid, action); if (action === 'CONFIRM_TEAM') confirmed = true; return { code: 0 }; },
    },
  };
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(root, 'src/pages/application-detail/index.tsx'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, Number, Error, require: name => deps[name] });
  const render = () => { cursor = 0; tree = module.exports.default(); };
  const nodes = (node = tree, result = []) => { if (Array.isArray(node)) node.forEach(child => nodes(child, result)); else if (node && typeof node === 'object') { result.push(node); nodes(node.props.children, result); } return result; };
  const text = node => Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object' ? text(node.props.children) : node == null || node === false ? '' : String(node);
  render();
  return { actions, notices, navigations, render, text, nodes, load: async () => { effect(); await tick(); render(); }, button: label => nodes().find(node => node.type === 'Button' && text(node) === label) };
}

test('lost confirmation on invitation detail reloads the committed team status', { skip: !process.env.XA_TEAM_DETAIL_LOST_HTTP }, async () => {
  const data = JSON.parse(process.env.XA_TEAM_DETAIL_LOST_HTTP);
  const api = async (route, method = 'GET', body) => {
    const response = await fetch(data.base + '/api' + route, { method,
      headers: { Authorization: `Bearer ${data.partnerToken}`, 'Content-Type': 'application/json' },
      ...(body ? { body: JSON.stringify(body) } : {}) });
    return response.json();
  };
  const screen = page({ orderId: String(data.orderId), applicationId: String(data.applicationId), actual: {
    detail: (orderId, applicationId) => api(`/orders/orders/${orderId}/applications/${applicationId}/mine/`),
    action: async (orderId, applicationId, action) => {
      const result = await api(`/orders/orders/${orderId}/applications/${applicationId}/action/`, 'POST', { action });
      assert.equal(result.code, 0, JSON.stringify(result));
      throw new Error('隔离测试：确认响应丢失');
    },
  } });
  const wait = async predicate => {
    for (let i = 0; i < 150; i++) { screen.render(); if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
    assert.fail('timed out waiting for the real invitation detail');
  };
  await screen.load(); await wait(() => screen.button('确认组队'));
  await screen.button('确认组队').props.onClick();
  await wait(() => screen.notices.some(n => n.title.includes('组队结果未确认')));
  await wait(() => screen.nodes().some(node => screen.text(node).includes('队伍已确认，等待老板选择')));
  assert.equal(screen.button('确认组队'), undefined);
  assert.deepEqual(screen.actions, ['CONFIRM_TEAM']);
});

test('lost withdrawal reloads the real withdrawn status and provides pool navigation', { skip: !process.env.XA_TEAM_DETAIL_WITHDRAW_LOST_HTTP }, async () => {
  const data = JSON.parse(process.env.XA_TEAM_DETAIL_WITHDRAW_LOST_HTTP);
  const api = async (route, method = 'GET', body) => {
    const response = await fetch(data.base + '/api' + route, { method,
      headers: { Authorization: `Bearer ${data.partnerToken}`, 'Content-Type': 'application/json' },
      ...(body ? { body: JSON.stringify(body) } : {}) });
    return response.json();
  };
  const screen = page({ orderId: String(data.orderId), applicationId: String(data.applicationId), actual: {
    detail: (orderId, applicationId) => api(`/orders/orders/${orderId}/applications/${applicationId}/mine/`),
    action: async (orderId, applicationId, action) => {
      const result = await api(`/orders/orders/${orderId}/applications/${applicationId}/action/`, 'POST', { action });
      assert.equal(result.code, 0, JSON.stringify(result));
      throw new Error('隔离测试：撤回响应丢失');
    },
  } });
  const wait = async predicate => {
    for (let i = 0; i < 150; i++) { screen.render(); if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
    assert.fail('timed out waiting for withdrawn invitation detail');
  };
  await screen.load(); await wait(() => screen.button('拒绝邀请'));
  await screen.button('拒绝邀请').props.onClick();
  await wait(() => screen.notices.some(n => n.title.includes('拒绝结果未确认')));
  await wait(() => screen.nodes().some(node => screen.text(node).includes('报名已撤回。')));
  assert.equal(screen.button('拒绝邀请'), undefined);
  assert.ok(screen.button('回到抢单池'));
  await screen.button('回到抢单池').props.onClick();
  assert.deepEqual(screen.navigations, ['pool', '/pages/orders/index']);
  assert.deepEqual(screen.actions, ['WITHDRAW']);
});

test('capacity-blocked invitation remains readable and can be withdrawn', async () => {
  const screen = page({ blocked: true }); await screen.load();
  assert.ok(screen.nodes().some(node => screen.text(node).includes('接单名额已满')));
  assert.equal(screen.button('确认组队').props.disabled, true);
  await screen.button('拒绝邀请').props.onClick(); screen.render();
  assert.deepEqual(screen.actions, ['WITHDRAW']);
  assert.ok(screen.nodes().some(node => screen.text(node).includes('已拒绝邀请')));
});

test('confirmation reloads state and old notices show all matching applications', async () => {
  const screen = page(); await screen.load(); await screen.button('确认组队').props.onClick(); screen.render();
  assert.deepEqual(screen.actions, ['CONFIRM_TEAM']);
  assert.ok(screen.nodes().some(node => screen.text(node).includes('队伍已确认')));
  assert.ok(screen.button('撤回报名'), 'a confirmed teammate may withdraw the application');
  const applicant = page({ isPartner: false }); await applicant.load();
  assert.ok(applicant.button('撤回报名'), 'the applicant withdraws rather than declines an invitation');
  assert.equal(applicant.button('拒绝邀请'), undefined);
  const legacy = page({ applicationId: '' }); await legacy.load();
  assert.ok(legacy.nodes().some(node => legacy.text(node).includes('这条旧消息没有指定邀请，请选择当前报名查看')));
  assert.ok(legacy.button('队友甲 · 待确认'));
  assert.ok(legacy.button('队友乙 · 待确认'));
});

test('cancelled order remains readable without confirmation or withdrawal actions', async () => {
  const screen = page({ closed: true }); await screen.load();
  assert.ok(screen.nodes().some(node => screen.text(node).includes('订单已取消，本次报名结束')));
  assert.equal(screen.button('确认组队'), undefined);
  assert.equal(screen.button('撤回报名'), undefined);
});

test('selected invitation opens the associated order', async () => {
  const screen = page({ status: 'SELECTED', closed: true }); await screen.load();
  assert.ok(screen.button('查看订单'));
  await screen.button('查看订单').props.onClick();
  assert.deepEqual(screen.navigations, [42, '/pages/orders/index']);
  assert.equal(screen.button('撤回报名'), undefined);
});

test('real selected invitation opens the associated order', { skip: !process.env.XA_TEAM_SELECTED_HTTP }, async () => {
  const data = JSON.parse(process.env.XA_TEAM_SELECTED_HTTP);
  const screen = page({ orderId: String(data.orderId), applicationId: String(data.applicationId), actual: {
    detail: async (orderId, applicationId) => {
      const response = await fetch(`${data.base}/api/orders/orders/${orderId}/applications/${applicationId}/mine/`, {
        headers: { Authorization: `Bearer ${data.partnerToken}` },
      });
      return response.json();
    },
  } });
  await screen.load();
  for (let i = 0; i < 150 && !screen.button('查看订单'); i++) { await new Promise(resolve => setTimeout(resolve, 20)); screen.render(); }
  assert.ok(screen.button('查看订单'), 'selected application should offer its order');
  assert.equal(screen.button('撤回报名'), undefined);
  await screen.button('查看订单').props.onClick();
  assert.deepEqual(screen.navigations, [data.orderId, '/pages/orders/index']);
});

test('real rejected selection shows its historical outcome', { skip: !process.env.XA_REJECTED_SELECTION_HTTP }, async () => {
  const data = JSON.parse(process.env.XA_REJECTED_SELECTION_HTTP);
  const screen = page({ orderId: String(data.orderId), applicationId: String(data.applicationId), actual: {
    detail: async (orderId, applicationId) => {
      const response = await fetch(`${data.base}/api/orders/orders/${orderId}/applications/${applicationId}/mine/`, {
        headers: { Authorization: `Bearer ${data.providerToken}` },
      });
      return response.json();
    },
  } });
  await screen.load();
  for (let i = 0; i < 150 && !screen.nodes().some(node => screen.text(node).includes('订单已回到待接单池')); i++) {
    await new Promise(resolve => setTimeout(resolve, 20)); screen.render();
  }
  assert.ok(screen.nodes().some(node => screen.text(node).includes('本次报名曾被选中，订单已回到待接单池。')));
  assert.equal(screen.button('确认组队'), undefined);
  assert.ok(screen.button('查看订单'));
});

test('application shows the original reservation time before team confirmation', async () => {
  const screen = page({ reservationOrigin: { start_time: '2026-10-30T02:00:00Z', end_time: '2026-10-30T03:00:00Z', can_open: false } });
  await screen.load();
  const text = screen.nodes().map(screen.text).join('');
  assert.ok(text.includes('原预约时段（北京时间）'));
  assert.ok(text.includes('2026-10-30 10:00'));
  assert.ok(text.includes('请与老板确认服务安排'));
});
