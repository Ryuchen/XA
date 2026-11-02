const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const appRoot = path.resolve(__dirname, '..');
const ts = createRequire(path.join(appRoot, 'package.json'))('typescript');
const tick = () => new Promise(resolve => setImmediate(resolve));

function environment(orders, { grabResult = { code: 0 }, grabRequest = null, ordersRequest = null, detailRequest = null, createRequest = null, teammateRequest = null, applicationRequest = null, startRequest = null, completeRequest = null, rejectResult = { code: 0 }, rejectError = null, rejectChoice = 1, teammates = [{ id: 9, nickname: '测试队友', level: '大师' }], filterByStatus = false, networkSample = { stale: true, samples: 0, failures: 0 }, focusIntent = null, fetchEvaluations = async () => ({ code: 0, data: { list: [] } }) } = {}) {
  const values = [];
  let cursor = 0, didShow, tree, sessionRevision = 1;
  const created = [], navigation = [], grabs = [], rejections = [], teammateRequests = [], notices = [], orderQueries = [], detailQueries = [];
  let pendingFocus = focusIntent;
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }), Fragment: 'Fragment',
    useState: initial => { const index = cursor++; if (!(index in values)) values[index] = typeof initial === 'function' ? initial() : initial; return [values[index], next => { values[index] = typeof next === 'function' ? next(values[index]) : next; }]; },
    useRef: initial => { const index = cursor++; if (!(index in values)) values[index] = { current: initial }; return values[index]; },
    useCallback: fn => fn, useEffect() {},
  };
  const dependencies = {
    react: { __esModule: true, default: react, ...react },
    '@tarojs/components': Object.fromEntries(['View', 'Text', 'Button', 'Picker'].map(name => [name, name])),
    '@tarojs/taro': { __esModule: true, default: { chooseImage: async () => ({ tempFilePaths: ['isolated-test.png'] }), switchTab: async options => navigation.push(options.url), showToast: value => notices.push(value), showLoading() {}, hideLoading() {}, navigateTo: options => navigation.push(options.url), showActionSheet: options => options.success({ tapIndex: rejectChoice }) }, useDidShow: fn => { didShow = fn; }, useDidHide() {}, usePullDownRefresh() {} },
    '@/services/user': { fetchEscortMe: async () => ({ code: 0, data: { qualifications: [] } }), fetchProviderPass: async () => ({ code: 0, data: null }) },
    '@/utils/auth': { getSessionRevision: () => sessionRevision },
    '@/utils/orderNavigation': { takeOrderFocus: () => { const result = pendingFocus; pendingFocus = null; return result; } },
    '@/utils/reportNavigation': { queueReportOrder: id => navigation.push(`report:${id}`), clearReportOrder() {} },
    '@/utils/orderNetwork': { networkSnapshot: () => networkSample, cooldownRemaining: () => 0 },
    '@/utils/request': { request: async () => ({ code: 0 }) },
    '@/services/order': { fetchOrders: async params => { orderQueries.push(params); if (ordersRequest) return ordersRequest(params); const statuses = { pending: 'PENDING', grabbed: 'GRABBED', in_progress: 'IN_SERVICE', completed: 'COMPLETED', cancelled: 'CANCELLED' }; return { code: 0, data: filterByStatus ? orders.filter(order => order.status === statuses[params.status]) : orders }; }, fetchOrderDetail: async id => { detailQueries.push(id); return detailRequest ? detailRequest(id) : { code: 0, data: orders.find(order => order.id === id) }; }, fetchProviderStats: async () => ({ code: 0, data: {} }), fetchMyEvaluations: fetchEvaluations,
      grabOrder: async (id, teamSize = 1, partnerId) => { grabs.push({ id, teamSize, partnerId }); return grabRequest ? grabRequest(id, teamSize, partnerId) : grabResult; },
      startOrder: (id, image) => startRequest(id, image), completeOrder: (id, image) => completeRequest(id, image),
      rejectOrder: async (id, reason) => { rejections.push({ id, reason }); if (rejectError) throw rejectError; return rejectResult; },
      fetchTeammates: async id => { teammateRequests.push(id); return teammateRequest ? teammateRequest(id) : { code: 0, data: teammates }; },
      applicationAction: (orderId, applicationId, action) => applicationRequest(orderId, applicationId, action),
    },
    '@/types/order': (() => { const mod = { exports: {} }; vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(appRoot, 'src/types/order.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, { module: mod, exports: mod.exports }); return { ...mod.exports, REJECT_REASONS: ['档期变化', '其他'] }; })(),
    '@/services/websocket': { wsService: {} }, '@/services/orderAlerts': { orderAlerts: { soundEnabled: () => true, setSoundEnabled() {} } },
    '@/utils/coalescedRefresh': {}, '@/utils/format': { formatXaCoin: value => String((value || 0) / 10) },
    '@/services/report': { createReport: async id => { created.push(id); return createRequest ? createRequest(id) : { code: 0 }; } },
    '@/components': { Empty: 'Empty', Skeleton: 'Skeleton' },
    './index.module.scss': { default: new Proxy({}, { get: (_, key) => String(key) }) },
  };
  for (const name of ['PlatformLayout', 'PageHeading', 'ReplyDialog', 'SurfaceDialog', 'StackDisclosure']) dependencies['@/components/' + name] = { default: name };
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(appRoot, 'src/pages/orders/index.tsx'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, Error, process: { env: { TARO_ENV: 'h5' } }, require: name => { if (!(name in dependencies)) throw new Error('Unexpected import ' + name); return dependencies[name]; } });
  const render = () => { cursor = 0; tree = module.exports.default(); };
  const visit = (value, rows = []) => { if (Array.isArray(value)) value.forEach(child => visit(child, rows)); else if (value && typeof value === 'object') { rows.push(value); if (value.type !== 'SurfaceDialog' || value.props.open) { visit(value.props.children, rows); visit(value.props.footer, rows); } visit(value.props.action, rows); } return rows; };
  const text = value => Array.isArray(value) ? value.map(text).join('') : value && typeof value === 'object' ? text(value.props.children) : value == null || value === false ? '' : String(value);
  render();
  return { created, navigation, grabs, rejections, teammateRequests, notices, orderQueries, detailQueries, render, text, nodes: () => visit(tree), changeSession: () => { sessionRevision++; }, load: async () => { didShow(); await tick(); render(); }, button: label => visit(tree).find(node => node.type === 'Button' && text(node) === label) };
}

const base = { status: 'COMPLETED', payment_status: 'PAID', expected_income: 1000, game_rounds: 1, created_at: '2026-10-13T10:00:00+08:00', remark: '' };
test('real team evidence destination focuses the selected order', { skip: !process.env.XA_TEAM_REPORT_NAV_HTTP }, async () => {
  const fixture = JSON.parse(process.env.XA_TEAM_REPORT_NAV_HTTP);
  const headers = { Authorization: 'Bearer ' + fixture.providerToken };
  const env = environment([], { focusIntent: fixture.orderId,
    detailRequest: async id => (await fetch(fixture.base + `/api/orders/orders/${id}/`, { headers })).json(),
    ordersRequest: async () => (await fetch(fixture.base + '/api/orders/orders/?role=provider', { headers })).json(),
  });
  await env.load();
  for (let attempt = 0; attempt < 100 && !env.button('开始服务'); attempt++) { await new Promise(resolve => setTimeout(resolve, 20)); env.render(); }
  const visible = env.nodes().map(env.text).join('');
  assert.ok(visible.includes(fixture.orderNo));
  assert.ok(!visible.includes(fixture.otherOrderNo));
  assert.ok(env.button('开始服务'));
  assert.deepEqual(env.detailQueries, [fixture.orderId]);
});
test('old-account report response does not focus an order after a session change', async () => {
  let release;
  const result = new Promise(resolve => { release = resolve; });
  const env = environment([{ ...base, id: 3, service: { id: 1, name: '测试服务' } }], { createRequest: () => result });
  await env.load();
  const action = env.button('查看 / 补充报单').props.onClick();
  env.changeSession(); release({ code: 0 }); await action;
  assert.deepEqual(env.navigation, []);
});
test('real order report entry opens report after a lost response', { skip: !process.env.XA_REPORT_ACTIONS_LOST_HTTP }, async () => {
  const data = JSON.parse(process.env.XA_REPORT_ACTIONS_LOST_HTTP);
  const headers = { Authorization: 'Bearer ' + data.token, 'Content-Type': 'application/json' };
  const response = await (await fetch(data.base + '/api/orders/orders/?role=provider', { headers })).json();
  const order = response.data.find(item => item.id === data.firstOrderId);
  assert.ok(order);
  const env = environment([order], { focusIntent: order.id, createRequest: async id => {
    const result = await (await fetch(data.base + '/api/wallet/reports/', {
      method: 'POST', headers, body: JSON.stringify({ order_id: id }),
    })).json();
    assert.equal(result.code, 0, JSON.stringify(result));
    throw new Error('response lost after commit');
  } });
  await env.load();
  await env.button('查看 / 补充报单').props.onClick();
  assert.deepEqual(env.created, [order.id]);
  assert.deepEqual(env.navigation, [`report:${order.id}`, '/pages/report/index']);
  assert.ok(env.notices.some(item => item.title === '报单结果未确认，请核对报单'));
});
test('real order report entry queues the same order after creating its report', { skip: !process.env.XA_REPORT_FOCUS_HTTP }, async () => {
  const data = JSON.parse(process.env.XA_REPORT_FOCUS_HTTP);
  const headers = { Authorization: 'Bearer ' + data.token, 'Content-Type': 'application/json' };
  const response = await (await fetch(data.base + '/api/orders/orders/?role=provider', { headers })).json();
  const order = response.data.find(item => item.id === data.secondOrderId);
  assert.ok(order);
  const env = environment([order], { focusIntent: order.id, createRequest: async id =>
    (await fetch(data.base + '/api/wallet/reports/', { method: 'POST', headers, body: JSON.stringify({ order_id: id }) })).json() });
  await env.load();
  const button = env.button('查看 / 补充报单');
  assert.ok(button); await button.props.onClick();
  assert.deepEqual(env.created, [order.id]);
  assert.deepEqual(env.navigation, [`report:${order.id}`, '/pages/report/index']);
});
test('lost team application and confirmation recover across both providers and boss', { skip: !process.env.XA_TEAM_APPLICATION_LOST_HTTP }, async () => {
  const data = JSON.parse(process.env.XA_TEAM_APPLICATION_LOST_HTTP);
  const api = async (token, route, method = 'GET', body) => {
    const response = await fetch(data.base + '/api' + route, { method,
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      ...(body ? { body: JSON.stringify(body) } : {}) });
    return response.json();
  };
  let applications = 0, confirmations = 0;
  const applicant = environment([], {
    ordersRequest: params => api(data.providerToken, `/orders/orders/?status=${params.status}&role=provider`),
    teammateRequest: id => api(data.providerToken, `/orders/orders/${id}/teammates/`),
    grabRequest: async (id, teamSize, partnerId) => {
      applications++;
      const result = await api(data.providerToken, `/orders/orders/${id}/grab/`, 'POST', { team_size: teamSize, partner_id: partnerId });
      assert.equal(result.code, 0, JSON.stringify(result));
      throw new Error('隔离测试：报名响应丢失');
    },
  });
  const wait = async (env, condition) => {
    for (let i = 0; i < 150; i++) { env.render(); if (condition()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
    assert.fail('timed out waiting for the real team application');
  };
  await applicant.load(); await wait(applicant, () => applicant.button('报名抢单'));
  await applicant.button('报名抢单').props.onClick(); applicant.render();
  const picker = applicant.nodes().find(node => node.type === 'Picker' && node.props.mode === 'selector');
  assert.ok(picker);
  const partnerIndex = picker.props.range.findIndex(label => label.startsWith(data.partnerName));
  assert.ok(partnerIndex >= 0, 'actual teammate must be eligible');
  picker.props.onChange({ detail: { value: String(partnerIndex) } }); applicant.render();
  await applicant.button('提交报名').props.onClick();
  await wait(applicant, () => applicant.notices.some(n => n.title.includes('报名结果未确认')));
  await wait(applicant, () => applicant.button('撤回报名'));
  assert.equal(applicant.button('提交报名'), undefined);
  assert.equal(applications, 1);
  const before = await api(data.bossToken, `/orders/orders/${data.orderId}/`);
  assert.equal(before.code, 0, JSON.stringify(before));
  assert.equal(before.data.applications.length, 1);
  assert.equal(before.data.applications[0].ready, false);
  const partner = environment([], {
    ordersRequest: params => api(data.partnerToken, `/orders/orders/?status=${params.status}&role=provider`),
    applicationRequest: async (orderId, applicationId, action) => {
      confirmations++;
      const result = await api(data.partnerToken, `/orders/orders/${orderId}/applications/${applicationId}/action/`, 'POST', { action });
      assert.equal(result.code, 0, JSON.stringify(result));
      throw new Error('隔离测试：确认响应丢失');
    },
  });
  await partner.load(); await wait(partner, () => partner.button('确认组队'));
  await partner.button('确认组队').props.onClick();
  await wait(partner, () => partner.notices.some(n => n.title.includes('组队结果未确认')));
  await wait(partner, () => !partner.button('确认组队'));
  assert.equal(confirmations, 1);
  const ready = await api(data.bossToken, `/orders/orders/${data.orderId}/`);
  assert.equal(ready.code, 0, JSON.stringify(ready));
  assert.equal(ready.data.applications.length, 1);
  assert.equal(ready.data.applications[0].ready, true);
  const selected = await api(data.bossToken, `/orders/orders/${data.orderId}/applications/${ready.data.applications[0].id}/select/`, 'POST', {});
  assert.equal(selected.code, 0, JSON.stringify(selected));
  assert.equal(selected.data.status, 'GRABBED');
});
test('lost first teammate evidence shows real partial team progress', { skip: !process.env.XA_PROVIDER_TEAM_LOST_HTTP }, async () => {
  const data = JSON.parse(process.env.XA_PROVIDER_TEAM_LOST_HTTP);
  const api = async (token, route, options = {}) => {
    const response = await fetch(data.base + '/api' + route, { ...options, headers: { Authorization: `Bearer ${token}`, ...(options.headers || {}) } });
    return response.json();
  };
  const evidence = field => {
    const form = new FormData();
    form.append(field, new Blob([Buffer.from(data.image, 'base64')], { type: 'image/png' }), 'evidence.png');
    return { method: 'POST', body: form };
  };
  const attempts = { start: 0, complete: 0 };
  const lose = async (kind, route, field) => {
    attempts[kind]++;
    const result = await api(data.providerToken, route, evidence(field));
    assert.equal(result.code, 0, JSON.stringify(result));
    assert.ok(result.msg.includes('等待队友'));
    throw new Error('隔离测试：响应在传输中丢失');
  };
  const env = environment([], {
    ordersRequest: params => api(data.providerToken, `/orders/orders/?status=${params.status}&role=provider`),
    startRequest: id => lose('start', `/orders/orders/${id}/start-service/`, 'entry_image'),
    completeRequest: id => lose('complete', `/orders/orders/${id}/complete/`, 'completion_image'),
  });
  const wait = async predicate => {
    for (let i = 0; i < 150; i++) { env.render(); if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
    assert.fail('timed out waiting for team order state');
  };
  await env.load(); env.button('已接单').props.onClick(); env.render(); await env.load();
  await wait(() => env.button('开始服务'));
  env.button('开始服务').props.onClick();
  await wait(() => env.notices.some(n => n.title.includes('开始结果未确认')));
  await wait(() => !env.button('开始服务') && env.nodes().map(env.text).join('').includes('1/2 已提交'));
  assert.equal(attempts.start, 1);
  const teammateStart = await api(data.teammateToken, `/orders/orders/${data.orderId}/start-service/`, evidence('entry_image'));
  assert.equal(teammateStart.code, 0, JSON.stringify(teammateStart));
  env.button('服务中').props.onClick(); env.render(); await env.load();
  await wait(() => env.button('完成订单'));
  env.button('完成订单').props.onClick();
  await wait(() => env.notices.some(n => n.title.includes('完成结果未确认')));
  await wait(() => !env.button('完成订单') && env.nodes().map(env.text).join('').includes('1/2 已提交'));
  assert.equal(attempts.complete, 1);
  const teammateComplete = await api(data.teammateToken, `/orders/orders/${data.orderId}/complete/`, evidence('completion_image'));
  assert.equal(teammateComplete.code, 0, JSON.stringify(teammateComplete));
  env.button('已完成').props.onClick(); env.render(); await env.load();
  await wait(() => env.nodes().map(env.text).join('').includes(data.orderNo));
});
test('lost application and evidence responses reconcile against real API state', { skip: !process.env.XA_PROVIDER_FLOW_LOST_HTTP }, async () => {
  const data = JSON.parse(process.env.XA_PROVIDER_FLOW_LOST_HTTP);
  const api = async (route, options = {}) => {
    const response = await fetch(data.base + '/api' + route, { ...options, headers: { Authorization: `Bearer ${data.providerToken}`, ...(options.headers || {}) } });
    return response.json();
  };
  const attempts = { apply: 0, start: 0, complete: 0 };
  const lose = async (kind, route, options) => {
    attempts[kind]++;
    const result = await api(route, options);
    assert.equal(result.code, 0, `real ${kind} must succeed: ${JSON.stringify(result)}`);
    throw new Error('隔离测试：响应在传输中丢失');
  };
  const evidence = (field) => {
    const form = new FormData();
    form.append(field, new Blob([Buffer.from(data.image, 'base64')], { type: 'image/png' }), 'evidence.png');
    return { method: 'POST', body: form };
  };
  const env = environment([], {
    ordersRequest: params => api(`/orders/orders/?status=${params.status}&role=provider`),
    grabRequest: id => lose('apply', `/orders/orders/${id}/grab/`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"team_size":1}' }),
    startRequest: id => lose('start', `/orders/orders/${id}/start-service/`, evidence('entry_image')),
    completeRequest: id => lose('complete', `/orders/orders/${id}/complete/`, evidence('completion_image')),
  });
  const wait = async predicate => {
    for (let i = 0; i < 150; i++) { env.render(); if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 20)); }
    assert.fail('timed out waiting for the real order page');
  };
  await env.load(); await wait(() => env.button('报名抢单'));
  await env.button('报名抢单').props.onClick(); env.render();
  const beforeApply = env.orderQueries.length;
  await env.button('提交报名').props.onClick();
  await wait(() => env.notices.some(n => n.title.includes('报名结果未确认')));
  await wait(() => env.orderQueries.length > beforeApply && env.button('撤回报名'));
  assert.equal(env.button('提交报名'), undefined, 'uncertain submission must close the stale dialog');
  assert.equal(attempts.apply, 1);
  env.button('已接单').props.onClick(); env.render(); await env.load();
  await wait(() => env.button('开始服务'));
  const beforeStart = env.orderQueries.length;
  env.button('开始服务').props.onClick();
  await wait(() => env.notices.some(n => n.title.includes('开始结果未确认')));
  await wait(() => env.orderQueries.length > beforeStart && !env.button('开始服务'));
  assert.equal(attempts.start, 1);
  env.button('服务中').props.onClick(); env.render(); await env.load();
  await wait(() => env.button('完成订单'));
  const beforeComplete = env.orderQueries.length;
  env.button('完成订单').props.onClick();
  await wait(() => env.notices.some(n => n.title.includes('完成结果未确认')));
  await wait(() => env.orderQueries.length > beforeComplete && !env.button('完成订单'));
  assert.equal(attempts.complete, 1);
  env.button('已完成').props.onClick(); env.render(); await env.load();
  await wait(() => env.nodes().map(env.text).join('').includes(data.serviceNo));
});
test('a lost grab response prompts verification and refreshes the real pool', { skip: !process.env.XA_PROVIDER_GRAB_LOST_HTTP }, async () => {
  const data = JSON.parse(process.env.XA_PROVIDER_GRAB_LOST_HTTP);
  const api = async (route, method = 'GET') => {
    const response = await fetch(data.base + '/api' + route, {
      method, headers: { Authorization: `Bearer ${data.providerToken}`, 'Content-Type': 'application/json' },
      ...(method === 'POST' ? { body: '{}' } : {}),
    });
    return response.json();
  };
  let attempts = 0;
  const env = environment([], {
    ordersRequest: params => api(`/orders/orders/?status=${params.status}&role=provider`),
    grabRequest: async id => {
      attempts++;
      const result = await api(`/orders/orders/${id}/grab/`, 'POST');
      assert.equal(result.code, 0, 'the real backend must commit the grab');
      throw new Error('隔离测试：响应在传输中丢失');
    },
  });
  await env.load();
  for (let attempt = 0; attempt < 100 && !env.nodes().map(env.text).join('').includes(data.orderNo); attempt++) {
    await new Promise(resolve => setTimeout(resolve, 20)); env.render();
  }
  assert.ok(env.nodes().map(env.text).join('').includes(data.orderNo));
  const before = env.orderQueries.length;
  await env.button('抢单').props.onClick(); await tick(); env.render();
  assert.equal(attempts, 1, 'an uncertain outcome must not submit again automatically');
  assert.ok(env.notices.some(notice => notice.icon === 'none' && notice.title.includes('结果未确认')));
  assert.ok(env.orderQueries.length > before, 'the pool must be refreshed');
  assert.ok(!env.nodes().map(env.text).join('').includes(data.orderNo), 'a grabbed order leaves the public pool');
  const own = await api('/orders/orders/?status=grabbed&role=provider');
  assert.ok(own.data.some(row => row.id === data.orderId), 'the provider can verify the actual grabbed order');
});
test('a rejected stale grab refreshes the real pool after another provider takes the order', { skip: !process.env.XA_PROVIDER_STALE_GRAB_HTTP }, async () => {
  const data = JSON.parse(process.env.XA_PROVIDER_STALE_GRAB_HTTP);
  const api = async (token, route, method = 'GET', body) => {
    const response = await fetch(data.base + '/api' + route, {
      method, headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    });
    return response.json();
  };
  const env = environment([], {
    ordersRequest: params => api(data.providerToken, `/orders/orders/?status=${params.status}&role=provider`),
    grabRequest: id => api(data.providerToken, `/orders/orders/${id}/grab/`, 'POST', {}),
  });
  await env.load();
  for (let attempt = 0; attempt < 100 && !env.nodes().map(env.text).join('').includes(data.orderNo); attempt++) {
    await new Promise(resolve => setTimeout(resolve, 20)); env.render();
  }
  assert.ok(env.nodes().map(env.text).join('').includes(data.orderNo), 'initial pool must contain the paid order');
  assert.equal((await api(data.otherToken, `/orders/orders/${data.orderId}/grab/`, 'POST', {})).code, 0);
  const before = env.orderQueries.length;
  await env.button('抢单').props.onClick();
  await tick(); env.render();
  assert.ok(env.notices.some(notice => notice.icon === 'none'), 'losing provider must see the rejection');
  assert.ok(env.orderQueries.length > before, 'the stale pool must be re-read after rejection');
  assert.ok(!env.nodes().map(env.text).join('').includes(data.orderNo), 'the already grabbed order must disappear');
});
test('transferred provider sees their own handoff without an active completion action', async () => {
  const order = process.env.XA_TRANSFERRED_ORDER_JSON
    ? JSON.parse(process.env.XA_TRANSFERRED_ORDER_JSON)
    : { ...base, id: 85, status: 'IN_SERVICE', my_assignment_released: true, allowed_actions: [], service: { id: 1, name: '转单服务' } };
  const env = environment([order], { focusIntent: order.id });
  await env.load();
  assert.ok(env.nodes().map(env.text).join('').includes('本人已转出'));
  assert.equal(env.button('完成服务'), undefined);
  const active = environment([{ ...order, my_assignment_released: false }], { focusIntent: order.id });
  await active.load();
  assert.ok(!active.nodes().map(active.text).join('').includes('本人已转出'));
  const finished = environment([{ ...order, status: 'COMPLETED' }], { focusIntent: order.id });
  await finished.load();
  assert.ok(!finished.nodes().map(finished.text).join('').includes('等待老板评价'));
});
test('evaluation network failure does not masquerade as an unrated order and can retry', async () => {
  let attempts = 0;
  const order = { ...base, id: 81, service: { id: 1, name: '评价通知服务' } };
  const env = environment([order], { focusIntent: 81, fetchEvaluations: async () => {
    if (++attempts === 1) throw new Error('网络连接失败');
    return { code: 0, data: { list: [{ id: 2, order: 81, score: 5, content: '合作顺利', reply_content: '' }] } };
  } });
  await env.load();
  assert.ok(env.nodes().map(env.text).join('').includes('评价暂时无法加载，请重试'));
  assert.ok(!env.nodes().map(env.text).join('').includes('等待老板评价'));
  await env.button('重新加载评价').props.onClick(); env.render();
  assert.ok(env.nodes().map(env.text).join('').includes('合作顺利'));
  assert.ok(env.button('回复老板'));
});
test('two-person order explains which teammate still needs to submit evidence', async () => {
  const order = { ...base, id: 91, order_no: 'TEAM-91', status: 'GRABBED', escort_mode: 'DOUBLE', allowed_actions: [], service: { id: 1, name: '双陪服务' },
    team_progress: { stage: 'ENTRY', submitted_count: 1, total_count: 2, members: [{ id: 1, nickname: '甲', submitted: true }, { id: 2, nickname: '乙', submitted: false }] } };
  const env = environment([order]); await env.load();
  const text = env.nodes().filter(node => node.type === 'Text').map(env.text).join('');
  for (const expected of ['入队截图 · 1/2 已提交', '甲 · 已提交', '乙 · 等待提交']) assert.ok(text.includes(expected), expected);
  assert.equal(env.button('开始服务'), undefined);
});
test('notification focus opens the requested order and can return to the normal list', async () => {
  const env = environment([{ ...base, id: 81, order_no: 'FOCUSED-81', status: 'GRABBED', allowed_actions: ['START'], service: { id: 1, name: '通知订单' } }], { focusIntent: 81 });
  await env.load();
  assert.deepEqual(env.detailQueries, [81]);
  assert.ok(env.nodes().some(node => node.type === 'Text' && env.text(node).includes('FOCUSED-81')));
  assert.ok(env.button('开始服务'));
  env.button('查看全部订单').props.onClick(); await tick(); env.render();
  assert.ok(env.orderQueries.length > 0);
  assert.equal(env.button('查看全部订单'), undefined);
});
test('pending team notification opens the public pool instead of a private detail', async () => {
  const env = environment([], { focusIntent: 0 });
  await env.load();
  assert.deepEqual(env.detailQueries, []);
  assert.equal(env.orderQueries.at(-1).status, 'pending');
});
test('converted reservation shows the original Beijing time and a usable own link', async () => {
  const origin = { id: 27, reservation_no: 'RSV27', start_time: '2026-10-30T02:00:00Z', end_time: '2026-10-30T03:00:00Z', can_open: true };
  const env = environment([{ ...base, id: 82, status: 'GRABBED', reservation_origin: origin, service: { id: 1, name: '预约服务' } }], { focusIntent: 82 });
  await env.load();
  assert.ok(env.nodes().map(env.text).join('').includes('2026-10-30 10:00'));
  env.button('查看预约').props.onClick();
  assert.equal(env.navigation.at(-1), '/pages/reservations/index?id=27');
  const transferred = environment([{ ...base, id: 83, status: 'GRABBED', reservation_origin: { ...origin, can_open: false }, service: { id: 1, name: '预约服务' } }], { focusIntent: 83 });
  await transferred.load();
  assert.ok(transferred.nodes().map(transferred.text).join('').includes('2026-10-30 10:00'));
  assert.equal(transferred.button('查看预约'), undefined);
  const pool = environment([{ ...base, id: 84, status: 'PENDING', reservation_origin: { ...origin, can_open: false }, service: { id: 1, name: '预约服务' } }]);
  await pool.load();
  const poolText = pool.nodes().map(pool.text).join('');
  assert.ok(poolText.includes('2026-10-30 10:00'));
  assert.ok(poolText.includes('请与老板确认服务安排'));
  assert.equal(pool.button('查看预约'), undefined);
});
test('open aftersale distinguishes a pending settlement from already settled income', async () => {
  for (const [settled_at, expected] of [[null, '售后处理中，本单暂不结算'], ['2026-10-29T10:00:00Z', '售后处理中，请关注处理结果；已结算收益以钱包流水为准']]) {
    const env = environment([{ ...base, id: 30, aftersale_status: 'OPEN', settled_at, service: { id: 1, name: '服务' } }]);
    await env.load();
    const text = env.nodes().map(env.text).join('');
    assert.ok(text.includes(expected));
  }
  const resolved = environment([{ ...base, id: 31, aftersale_status: 'RESOLVED', service: { id: 1, name: '服务' } }]);
  await resolved.load();
  const resolvedText = resolved.nodes().map(resolved.text).join('');
  assert.ok(!resolvedText.includes('售后处理中'));
  assert.ok(resolvedText.includes('售后已处理，结算状态请查看下方收益与结算'));
});
test('real resolved aftersale response is visible on the provider order', { skip: !process.env.XA_REAL_RESOLVED_AFTERSALE }, async () => {
  const order = JSON.parse(process.env.XA_REAL_RESOLVED_AFTERSALE);
  const env = environment([order], { focusIntent: order.id });
  await env.load();
  const text = env.nodes().map(env.text).join('');
  assert.ok(text.includes('售后已处理，结算状态请查看下方收益与结算'));
  assert.ok(!text.includes('售后处理中'));
});
test('real running legacy team response explains each provider report', { skip: !process.env.XA_REAL_LEGACY_TEAM_ORDER }, async () => {
  const order = JSON.parse(process.env.XA_REAL_LEGACY_TEAM_ORDER);
  const env = environment([order], { focusIntent: order.id });
  await env.load();
  assert.ok(env.nodes().map(env.text).join('').includes('旧版双陪单：服务结束后，请各自在报单中补齐入队、结单和战绩截图'));
});
test('pool request measurements do not claim push connection health', async () => {
  const baseSample = { stale: false, samples: 5, failures: 0, jitter_ms: 20, server_errors: 0, grab_wait: 0, pool_wait: 0 };
  for (const [changes, title] of [[{}, '未见网络异常'], [{ stale: true }, '等待采样'], [{ failures: 1 }, '请求波动'], [{ server_errors: 1 }, '服务异常'], [{ pool_wait: 3 }, '限流等待']]) {
    const env = environment([], { networkSample: { ...baseSample, ...changes } });
    await env.load();
    const panel = env.nodes().find(node => node.type === 'StackDisclosure' && node.props.title.startsWith('抢单请求'));
    assert.equal(panel.props.title, `抢单请求 · ${title}`);
    const text = env.nodes().filter(node => node.type === 'Text').map(env.text).join('');
    assert.ok(text.includes('仅统计抢单池和抢单请求，不代表推送连接状态'));
    assert.ok(text.includes('前台约每10秒尝试刷新抢单池'));
    assert.ok(!text.includes('连接稳定'));
  }
});
test('cancelled tab requests cancelled orders and distinguishes refund from cancellation', async () => {
  const env = environment([
    { ...base, id: 71, order_no: 'REFUNDED-71', status: 'CANCELLED', payment_status: 'REFUNDED', cancel_reason: '客服确认终止服务', service: { id: 1, name: '服务' }, refund_summary: { amount: 1000, remaining_amount: 0, recovered_amount: 0, net_income: 0, remaining_frozen_amount: 0 } },
    { ...base, id: 72, order_no: 'CANCELLED-72', status: 'CANCELLED', payment_status: 'UNPAID', cancel_reason: '订单取消', service: { id: 1, name: '服务' } },
    { ...base, id: 73, order_no: 'COMPLETED-73', service: { id: 1, name: '服务' } },
  ], { filterByStatus: true });
  await env.load();
  env.button('已取消').props.onClick(); env.render(); await env.load();
  assert.equal(env.orderQueries.at(-1).status, 'cancelled');
  const text = env.nodes().filter(node => node.type === 'Text').map(env.text).join('');
  assert.ok(text.includes('REFUNDED-71') && text.includes('CANCELLED-72') && !text.includes('COMPLETED-73'));
  assert.ok(text.includes('已退款') && text.includes('已取消'));
  assert.ok(text.includes('退款原因') && text.includes('客服确认终止服务') && text.includes('取消原因'));
  assert.ok(text.includes('累计已退 100币') && text.includes('服务净收益 0币'));
  assert.ok(!env.nodes().some(node => node.type === 'Button' && env.text(node) === '查看 / 补充报单'));
});
test('reject dialog explains whether the order returns to staff or the public pool', async () => {
  for (const [route, expected] of [['STAFF', '退回客服重新安排'], ['PUBLIC', '重新进入待接单池']]) {
    const env = environment([{ ...base, id: 20, status: 'GRABBED', dispatch_route: route, allowed_actions: ['START', 'REJECT'], service: { id: 1, name: '服务' } }]);
    await env.load(); env.button('拒单').props.onClick(); env.render();
    const dialog = env.nodes().find(node => node.type === 'ReplyDialog' && node.props.title === '拒单原因');
    assert.equal(dialog.props.open, true); assert.ok(dialog.props.hint.includes(expected));
  }
});
test('reject feedback follows the server result for preset and custom reasons', async () => {
  const order = { ...base, id: 20, status: 'GRABBED', dispatch_route: 'STAFF', allowed_actions: ['START', 'REJECT'], service: { id: 1, name: '服务' } };
  const preset = environment([order], { rejectChoice: 0, rejectResult: { code: 0, msg: '已拒单，订单已退回客服' } });
  await preset.load(); preset.button('拒单').props.onClick(); await tick(); preset.render();
  const presetDialog = preset.nodes().find(node => node.type === 'ReplyDialog' && node.props.title === '拒单原因');
  assert.equal(presetDialog.props.initialValue, '档期变化');
  assert.equal(preset.rejections.length, 0, 'preset reason must be confirmed before rejection');
  await presetDialog.props.onSubmit(presetDialog.props.initialValue); preset.render();
  assert.deepEqual(preset.rejections, [{ id: 20, reason: '档期变化' }]);
  assert.ok(preset.notices.some(item => item.title === '已拒单，订单已退回客服'));
  const custom = environment([{ ...order, dispatch_route: 'PUBLIC' }], { rejectResult: { code: 0, msg: '已拒单，订单已回到待接单池' } });
  await custom.load(); custom.button('拒单').props.onClick(); custom.render();
  const dialog = custom.nodes().find(node => node.type === 'ReplyDialog' && node.props.title === '拒单原因');
  await dialog.props.onSubmit('临时有事'); custom.render();
  assert.deepEqual(custom.rejections, [{ id: 20, reason: '临时有事' }]);
  assert.ok(custom.notices.some(item => item.title === '已拒单，订单已回到待接单池'));
});
test('reject network failure reports the error without a success notice', async () => {
  const order = { ...base, id: 20, status: 'GRABBED', dispatch_route: 'STAFF', allowed_actions: ['START', 'REJECT'], service: { id: 1, name: '服务' } };
  const env = environment([order], { rejectChoice: 0, rejectError: new Error('网络连接失败') });
  await env.load(); env.button('拒单').props.onClick(); await tick(); env.render();
  const dialog = env.nodes().find(node => node.type === 'ReplyDialog' && node.props.title === '拒单原因');
  await dialog.props.onSubmit(dialog.props.initialValue); env.render();
  assert.ok(env.notices.some(item => item.title === '拒单结果未确认，请刷新已接单核对' && item.icon === 'none'));
  assert.ok(!env.notices.some(item => item.icon === 'success'));
  assert.ok(env.orderQueries.length >= 2, 'unknown outcome triggers a read of current order state');
});
test('cancelled order card treats its price as historical and directs net income checks to the wallet', async () => {
  const env = environment([{ ...base, id: 7, status: 'CANCELLED', service: { id: 1, name: '已退款测试单' } }]); await env.load();
  const text = env.nodes().filter(node => node.type === 'Text').map(env.text).join('');
  assert.ok(text.includes('原预计单钱')); assert.ok(text.includes('实际净收益以钱包流水为准')); assert.ok(!text.includes('报单全部通过且售后窗口结束后入账'));
});

test('real provider refund response renders the order income and refund', { skip: !process.env.XA_REAL_PROVIDER_REFUND }, async () => {
  const { order } = JSON.parse(process.env.XA_REAL_PROVIDER_REFUND);
  const env = environment([order], { focusIntent: order.id });
  await env.load();
  const text = env.nodes().filter(node => node.type === 'Text').map(env.text).join('');
  assert.ok(text.includes(`累计已退 ${order.refund_summary.amount / 10}币`));
  assert.ok(text.includes(`服务净收益 ${order.refund_summary.net_income / 10}币`));
  assert.ok(text.includes(order.payment_status === 'PARTIALLY_REFUNDED' ? '已完成 · 部分退款' : '已退款'));
});
test('partial order cards retain completed state and separate original estimate from server net income and remaining freeze', async () => {
  const env = environment([{ ...base, id: 8, expected_income: 8000, payment_status: 'PARTIALLY_REFUNDED', settled_at: '2026-10-19T00:00:00Z', service: { id: 1, name: '部分退款服务' },
    refund_summary: { amount: 3750, remaining_amount: 6250, recovered_amount: 3000, net_income: 5000, remaining_frozen_amount: 5000, records: [{ id: 8, amount: 2500, recovered_amount: 2000, created_at: '2026-10-20T00:00:00Z' }, { id: 9, amount: 1250, recovered_amount: 1000, created_at: '2026-10-20T01:00:00Z' }] },
  }]); await env.load(); const text = env.nodes().filter(node => node.type === 'Text').map(env.text).join('');
  for (const expected of ['已完成 · 部分退款', '原核定单钱', '累计已退 375币 · 订单剩余 625币', '服务收益累计追回 300币', '服务净收益 500币', '本单剩余冻结 500币', '退款 #8 · 250币', '退款 #9 · 125币']) assert.ok(text.includes(expected), expected);
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
  const env = environment([{ ...base, id: 3, order_no: 'SERVICE-3', settlement_status: 'WAITING', service: { id: 3, name: '服务订单', is_gift: false } }]);
  await env.load();
  assert.ok(env.nodes().some(node => node.type === 'Text' && env.text(node) === '报单全部通过、售后窗口结束且无未结客诉后结算'));
  assert.ok(env.nodes().some(node => node.type === 'Text' && env.text(node) === '100币'));
  assert.ok(env.nodes().some(node => node.type === 'Text' && env.text(node) === '等待老板评价'));
  const action = env.nodes().find(node => node.type === 'Button' && env.text(node) === '查看 / 补充报单');
  assert.ok(action); await action.props.onClick();
  assert.deepEqual(env.created, [3]); assert.deepEqual(env.navigation, ['report:3', '/pages/report/index']);
});

test('report and wallet actions follow the order sale type after the catalog changes', async () => {
  const service = environment([{ ...base, id: 80, order_no: 'SERVICE-80', is_gift_snapshot: false,
    service: { id: 1, name: '原服务', is_gift: true }, report_entry: { available: true, reason: '' } }]);
  await service.load();
  assert.ok(service.button('查看 / 补充报单'));
  assert.equal(service.button('前往钱包'), undefined);
  await service.button('查看 / 补充报单').props.onClick();
  assert.deepEqual(service.created, [80]);

  const gift = environment([{ ...base, id: 81, order_no: 'GIFT-81', is_gift_snapshot: true,
    service: { id: 2, name: '原礼物', is_gift: false } }]);
  await gift.load();
  assert.equal(gift.button('查看 / 补充报单'), undefined);
  assert.ok(gift.button('前往钱包'));
});

test('a transferred order denied by the server explains why without presenting a report action', async () => {
  const reason = '转单后本人无结算份额，无需提交报单';
  const env = environment([{ ...base, id: 4, order_no: 'TRANSFER-4', service: { id: 4, name: '转出订单' }, report_entry: { available: false, reason } }]);
  await env.load();
  assert.ok(env.nodes().some(node => node.type === 'Text' && env.text(node) === reason));
  assert.equal(env.button('查看 / 补充报单'), undefined);
  assert.deepEqual(env.created, []);
});

test('a transferred participant with server-granted report eligibility can still open a report', async () => {
  const env = environment([{ ...base, id: 5, order_no: 'TRANSFER-5', service: { id: 5, name: '保留份额订单' }, report_entry: { available: true, reason: '' } }]);
  await env.load();
  await env.button('查看 / 补充报单').props.onClick();
  assert.deepEqual(env.created, [5]);
  assert.deepEqual(env.navigation, ['report:5', '/pages/report/index']);
});

test('unknown or direct settlement does not promise automatic income', async () => {
  for (const settlement_status of ['REVIEW', undefined, 'unknown']) {
    const env = environment([{ ...base, id: 3, settlement_status, service: { id: 3, name: '服务订单', is_gift: false } }]);
    await env.load();
    assert.ok(env.nodes().some(node => node.type === 'Text' && env.text(node) === '收益待核对，请联系客服核对'));
    assert.ok(!env.nodes().some(node => node.type === 'Text' && env.text(node).includes('售后窗口结束且无未结客诉后结算')));
  }
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
  assert.ok(env.nodes().some(node => node.type === 'Text' && env.text(node).includes('手续费不参与分成')));
  assert.ok(!env.nodes().some(node => node.type === 'Text' && env.text(node).includes('已付总金额均分')));
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
