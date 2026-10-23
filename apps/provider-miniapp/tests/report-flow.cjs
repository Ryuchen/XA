const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const appRoot = path.resolve(__dirname, '..');
const ts = createRequire(path.join(appRoot, 'package.json'))('typescript');

const order = (id, status = 'COMPLETED') => ({ id, order_no: `XA-${id}`, status, service: { name: `服务 ${id}` } });
const report = overrides => ({ id: 10, order_id: 10, order_no: 'XA-10', game_name: '测试服务', expected_income: 1000, status: 'DRAFT', status_display: '草稿', order_status: 'COMPLETED', submitted_at: null, settled_at: null, entry_image_url: 'entry.jpg', completion_image_url: 'completion.jpg', result_image_urls: ['result.jpg'], result_images: [{ id: 30, url: 'result.jpg' }], audit_remark: '', can_edit: true, can_submit: true, edit_block_reason: '', ...overrides });

// Exercise the actual page event handlers without a browser or business writes.
function environment({ orders = [], reports = [], create = async () => ({ code: 0 }), read } = {}) {
  const values = [];
  let cursor = 0, didShow, didHide, pullDown, tree, firstRender = true, revision = 1, token = 'local-fixture', nextTimer = 0;
  const effects = [], cleanups = [], listeners = new Map(), authListeners = new Set(), timers = new Map();
  const requests = { created: [], uploaded: [], submitted: [], deleted: [], choose: [], notices: [], reads: [], stopped: 0 };
  const refreshModule = { exports: {} };
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(appRoot, 'src/utils/coalescedRefresh.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText, { module: refreshModule, exports: refreshModule.exports,
    setTimeout: callback => { timers.set(++nextTimer, callback); return nextTimer; },
    clearTimeout: id => timers.delete(id),
  });
  const react = {
    createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    Fragment: 'Fragment',
    useState: initial => {
      const index = cursor++;
      if (!(index in values)) values[index] = typeof initial === 'function' ? initial() : initial;
      return [values[index], next => { values[index] = typeof next === 'function' ? next(values[index]) : next; }];
    },
    useRef: initial => {
      const index = cursor++;
      if (!(index in values)) values[index] = { current: initial };
      return values[index];
    },
    useCallback: fn => fn,
    useEffect: effect => { if (firstRender) effects.push(effect); },
  };
  const dependencies = {
    react: { __esModule: true, default: react, ...react },
    '@tarojs/components': { Button: 'Button', Input: 'Input', Text: 'Text', View: 'View' },
    '@tarojs/taro': { __esModule: true, default: {
      showToast: options => requests.notices.push(options),
      chooseImage: async options => { requests.choose.push(options); return { tempFilePaths: ['picked.jpg'] }; },
      stopPullDownRefresh: () => { requests.stopped++; },
      eventCenter: { on: (type, callback) => { assert.equal(type, 'xa:auth.changed'); authListeners.add(callback); }, off: (type, callback) => authListeners.delete(callback) },
    }, useDidShow: callback => { didShow = callback; }, useDidHide: callback => { didHide = callback; }, usePullDownRefresh: callback => { pullDown = callback; } },
    '@/utils/auth': { getSessionRevision: () => revision, getStoredToken: () => token },
    '@/utils/coalescedRefresh': refreshModule.exports,
    '@/services/websocket': { wsService: { on: (type, callback) => {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(callback);
      return () => listeners.get(type).delete(callback);
    } } },
    '@/components/PrivateImage': { default: 'Image' },
    '@/utils/privateMedia': { previewPrivateImages() {} },
    '@/components/PlatformLayout': { default: 'PlatformLayout' },
    '@/components/PageHeading': { default: 'PageHeading' },
    '@/components/SurfaceDialog': { default: 'SurfaceDialog' },
    '@/components': { Empty: 'Empty' },
    '@/services/report': {
      fetchReports: async () => { requests.reads.push(revision); return read ? read() : { code: 0, data: reports }; },
      createReport: async id => { requests.created.push(id); return create(id); },
      uploadReportImage: async (id, image, kind) => { requests.uploaded.push({ id, image, kind }); return { code: 0 }; },
      submitReport: async id => { requests.submitted.push(id); return { code: 0 }; },
      deleteReportImage: async (id, imageId) => { requests.deleted.push({ id, imageId }); return { code: 0 }; },
    },
    '@/services/order': { fetchOrders: async () => ({ code: 0, data: orders }) },
    '@/types/order': { statusTextMap: { GRABBED: '已接单', IN_SERVICE: '服务中', COMPLETED: '已完成' } },
    '@/utils/format': { formatXaCoin: amount => String(amount / 10) },
    './index.module.scss': { default: new Proxy({}, { get: (_, key) => String(key) }) },
  };
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(appRoot, 'src/pages/report/index.tsx'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React },
  }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, require: name => {
    if (!(name in dependencies)) throw new Error('Unexpected import ' + name);
    return dependencies[name];
  }, process: { env: { TARO_ENV: 'h5' } } });
  const render = () => {
    cursor = 0; tree = module.exports.default();
    if (firstRender) { firstRender = false; for (const effect of effects) cleanups.push(effect()); }
    return tree;
  };
  const visit = (value, result = []) => {
    if (Array.isArray(value)) value.forEach(child => visit(child, result));
    else if (value && typeof value === 'object') {
      result.push(value);
      if (value.type !== 'SurfaceDialog' || value.props.open) {
        visit(value.props.children, result);
        visit(value.props.action, result);
        visit(value.props.footer, result);
      }
    }
    return result;
  };
  const text = value => Array.isArray(value) ? value.map(text).join('') : value && typeof value === 'object' ? text(value.props?.children) : value == null || value === false ? '' : String(value);
  const nodes = () => visit(tree);
  const button = label => nodes().find(node => node.type === 'Button' && text(node) === label);
  const dialog = () => nodes().find(node => node.type === 'SurfaceDialog');
  render();
  return { requests, render, nodes, button, dialog, text: () => text(tree), timers, listeners, authListeners,
    setReports: next => { reports = next; }, setRead: next => { read = next; },
    emit: (type, data) => { for (const listener of listeners.get(type) || []) listener(data); },
    session: (next, logged = true) => { revision = next; token = logged ? 'local-fixture-' + next : ''; for (const listener of authListeners) listener(); render(); },
    hide: () => didHide(), unmount: () => cleanups.forEach(cleanup => cleanup?.()),
    fire: async () => { assert.equal(timers.size, 1); const [id, callback] = timers.entries().next().value; timers.delete(id); await callback(); render(); },
    pull: async () => { await pullDown(); render(); },
    load: async () => { await didShow(); render(); }, begin: async () => { await button('从订单创建').props.onClick(); render(); } };
}

for (const count of [6, 7, 18]) test(`all ${count} eligible orders remain reachable, including the last order`, async () => {
  const env = environment({ orders: Array.from({ length: count }, (_, index) => order(index + 1)) });
  await env.load(); await env.begin();
  const options = env.nodes().filter(node => node.type === 'Button' && node.props.className?.includes('orderOption '));
  assert.equal(options.length, count);
  assert.deepEqual(env.requests.created, []);
  options.at(-1).props.onClick(); env.render();
  await env.button('创建报单').props.onClick(); env.render();
  assert.deepEqual(env.requests.created, [count]);
  assert.equal(env.dialog().props.open, false);
});

test('empty order list gives feedback without creating a report', async () => {
  const env = environment(); await env.load(); await env.begin();
  assert.equal(env.dialog().props.open, false);
  assert.deepEqual(env.requests.created, []);
  assert.equal(env.requests.notices.at(-1).title, '暂无可新建报单的订单');
});

test('pending, cancelled and already reported orders are excluded', async () => {
  const env = environment({ orders: [order(1, 'PENDING'), order(2, 'CANCELLED'), order(3), order(4, 'GRABBED'), order(5, 'IN_SERVICE')], reports: [report({ order_id: 3 })] });
  await env.load(); await env.begin();
  const options = env.nodes().filter(node => node.type === 'Button' && node.props.className?.includes('orderOption '));
  assert.equal(options.length, 2);
  options[1].props.onClick(); env.render(); await env.button('创建报单').props.onClick();
  assert.deepEqual(env.requests.created, [5]);
});

test('searching by order number and service name can reach orders beyond six', async () => {
  const env = environment({ orders: Array.from({ length: 12 }, (_, index) => order(index + 1)) });
  await env.load(); await env.begin();
  const input = () => env.nodes().find(node => node.type === 'Input');
  for (const search of ['XA-12', '服务 12']) {
    input().props.onInput({ detail: { value: search } }); env.render();
    const options = env.nodes().filter(node => node.type === 'Button' && node.props.className?.includes('orderOption '));
    assert.equal(options.length, 1);
    options[0].props.onClick(); env.render();
  }
  await env.button('创建报单').props.onClick(); assert.deepEqual(env.requests.created, [12]);
});

test('gift and order-tip records cannot appear as service report choices', async () => {
  const env = environment({ orders: [
    { ...order(1), service: { id: 1, name: '礼物', is_gift: true } },
    { ...order(2), source_order: 99 }, order(3),
  ] });
  await env.load(); await env.begin();
  const options = env.nodes().filter(node => node.type === 'Button' && node.props.className?.includes('orderOption '));
  assert.equal(options.length, 1);
  options[0].props.onClick(); env.render(); await env.button('创建报单').props.onClick();
  assert.deepEqual(env.requests.created, [3]);
});

test('failed report creation keeps the selection for retry', async () => {
  let attempt = 0;
  const env = environment({ orders: [order(1)], create: async () => ++attempt === 1 ? { code: 400, msg: '订单已变化' } : { code: 0 } });
  await env.load(); await env.begin();
  env.nodes().find(node => node.type === 'Button' && node.props.className?.includes('orderOption ')).props.onClick(); env.render();
  await env.button('创建报单').props.onClick(); env.render();
  assert.equal(env.dialog().props.open, true);
  assert.equal(env.button('创建报单').props.disabled, false);
  assert.equal(env.requests.notices.at(-1).title, '订单已变化');
  await env.button('创建报单').props.onClick(); env.render();
  assert.equal(env.dialog().props.open, false);
});

test('rapid confirmation sends one create request even before a rerender', async () => {
  let resolve;
  const env = environment({ orders: [order(1)], create: () => new Promise(done => { resolve = done; }) });
  await env.load(); await env.begin();
  env.nodes().find(node => node.type === 'Button' && node.props.className?.includes('orderOption ')).props.onClick(); env.render();
  const confirm = env.button('创建报单').props.onClick;
  const first = confirm(); await confirm();
  assert.deepEqual(env.requests.created, [1]);
  resolve({ code: 0 }); await first;
});

test('result upload requests only the remaining slot and obeys the nine-image limit', async () => {
  const eight = Array.from({ length: 8 }, (_, index) => `result-${index}.jpg`);
  const env = environment({ reports: [report({ result_image_urls: eight, result_images: eight.map((url, id) => ({ url, id })) })] });
  await env.load(); await env.button('添加战绩截图').props.onClick();
  assert.equal(env.requests.choose[0].count, 1);
  assert.deepEqual(env.requests.uploaded, [{ id: 10, image: 'picked.jpg', kind: 'RESULT' }]);
  const full = environment({ reports: [report({ result_image_urls: [...eight, 'last.jpg'] })] });
  await full.load(); assert.equal(full.button('添加战绩截图').props.disabled, true);
  await full.button('添加战绩截图').props.onClick(); assert.equal(full.requests.choose.length, 0);
});

test('read-only backend state hides upload, delete and submit actions', async () => {
  const env = environment({ reports: [report({ can_edit: false, can_submit: false, status: 'PENDING' })] }); await env.load();
  for (const label of ['添加战绩截图', '更换截图', '删除此图', '提交审核']) assert.equal(env.button(label), undefined);
});

test('missing evidence and unfinished service cannot submit', async () => {
  const incomplete = environment({ reports: [report({ entry_image_url: '' })] }); await incomplete.load();
  assert.equal(incomplete.button('提交审核').props.disabled, true);
  await incomplete.button('提交审核').props.onClick(); assert.deepEqual(incomplete.requests.submitted, []);
  const unfinished = environment({ reports: [report({ can_submit: false, order_status: 'IN_SERVICE' })] }); await unfinished.load();
  assert.equal(unfinished.button('服务完成后提交').props.disabled, true);
  await unfinished.button('服务完成后提交').props.onClick(); assert.deepEqual(unfinished.requests.submitted, []);
});

for (const [status, label] of [['APPROVED', '审核通过'], ['REJECTED', '已驳回']]) test(`audit push refreshes ${status} without submitting or changing the report`, async () => {
  const env = environment({ reports: [report({ status: 'PENDING', status_display: '待审核', can_edit: false, can_submit: false })] });
  await env.load(); assert.ok(env.text().includes('待审核'));
  env.setReports([report({ status, status_display: label, can_edit: status === 'REJECTED', audit_remark: status === 'REJECTED' ? '请补充完整战绩' : '' })]);
  for (let i = 0; i < 20; i++) env.emit('report_status_update', { report_id: 10, status });
  assert.equal(env.timers.size, 1); assert.equal(env.requests.reads.length, 1);
  await env.fire(); assert.equal(env.requests.reads.length, 2); assert.ok(env.text().includes(label));
  for (const operation of ['created', 'uploaded', 'submitted', 'deleted']) assert.deepEqual(env.requests[operation], []);
});

test('audit events during an in-flight refresh trigger one trailing read', async () => {
  const env = environment(); await env.load();
  let finish;
  env.setRead(() => new Promise(resolve => { finish = resolve; }));
  env.emit('report_status_update'); const first = env.fire();
  for (let i = 0; i < 10; i++) env.emit('report_status_update');
  assert.equal(env.timers.size, 0);
  finish({ code: 0, data: [report({ status: 'PENDING', status_display: '待审核' })] }); await first;
  assert.equal(env.timers.size, 1);
  env.setRead(async () => ({ code: 0, data: [report({ status: 'APPROVED', status_display: '审核通过' })] }));
  await env.fire(); assert.equal(env.requests.reads.length, 3); assert.ok(env.text().includes('审核通过'));
});

test('order cancellation and settlement pushes refresh report state through the same queue', async () => {
  const env = environment({ reports: [report({ status: 'APPROVED', status_display: '审核通过', can_edit: false, can_submit: false })] });
  await env.load();
  env.setReports([report({ order_status: 'CANCELLED', can_edit: false, can_submit: false })]);
  env.emit('order_status_update', { id: 10, status: 'CANCELLED' }); await env.fire();
  assert.ok(env.text().includes('订单已取消，报单流程已停止'));
  env.setReports([report({ status: 'APPROVED', status_display: '审核通过', can_edit: false, can_submit: false, settled_at: '2026-10-22T12:00:00Z' })]);
  for (const event of ['order_status_update', 'report_status_update', 'connected']) env.emit(event, { id: 10 });
  assert.equal(env.timers.size, 1); await env.fire();
  assert.equal(env.requests.reads.length, 3); assert.ok(env.text().includes('收益已结算，可在钱包查看流水'));
  assert.ok(!env.text().includes('订单已取消，报单流程已停止'));
});

test('an approved report on a refunded order never promises future settlement', async () => {
  const env = environment({ reports: [report({ status: 'APPROVED', status_display: '审核通过', order_status: 'CANCELLED', settled_at: null, can_edit: false, can_submit: false })] });
  await env.load();
  assert.ok(env.text().includes('订单已取消/退款，不再结算收益'));
  assert.ok(env.text().includes('原预计单钱'));
  assert.ok(!env.text().includes('按本单实际收益结算'));
  assert.ok(!env.text().includes('凭证已通过，等待全部审核及售后窗口结束'));
  assert.ok(!env.text().includes('收益已结算，可在钱包查看流水'));
});

test('refunded settled report labels its original snapshot without claiming it remains earned', async () => {
  const env = environment({ reports: [report({ status: 'APPROVED', status_display: '审核通过', order_status: 'CANCELLED', settled_at: '2026-10-19T00:00:00Z', submitted_at: '2026-10-15T00:00:00Z', can_edit: false, can_submit: false })] }); await env.load();
  assert.ok(env.text().includes('原核定单钱')); assert.ok(env.text().includes('历史金额仅供核对，净收益以钱包为准')); assert.ok(env.text().includes('原结算收益已按退款规则冲回，实际净收益以钱包流水为准'));
  assert.ok(!env.text().includes('收益已结算，可在钱包查看流水')); assert.ok(!env.text().includes('凭证已通过，等待全部审核及售后窗口结束'));
  assert.ok(!env.text().includes('实销从全部有效报单起等待72小时'));
});

test('partially refunded report keeps the completed service and displays actual remaining income/freeze from the API', async () => {
  const env = environment({ reports: [report({ expected_income: 8000, status: 'APPROVED', status_display: '审核通过', order_status: 'COMPLETED', payment_status: 'PARTIALLY_REFUNDED', settled_at: '2026-10-19T00:00:00Z', can_edit: false, can_submit: false,
    refund_summary: { amount: 3750, remaining_amount: 6250, recovered_amount: 3000, net_income: 5000, remaining_frozen_amount: 5000,
      records: [{ id: 8, amount: 2500, recovered_amount: 2000, created_at: '2026-10-20T00:00:00Z' }, { id: 9, amount: 1250, recovered_amount: 1000, created_at: '2026-10-20T01:00:00Z' }] },
  })] }); await env.load();
  for (const expected of ['原核定单钱', '订单已部分退款，剩余净收益及冻结以钱包流水为准', '订单累计已退 375币 · 剩余 625币', '本人累计追回收益 300币', '本单实际净收益 500币', '本单剩余冻结 500币', '退款 #8 · 250币 · 本人追回 200币', '退款 #9 · 125币 · 本人追回 100币']) assert.ok(env.text().includes(expected), expected);
  for (const wrong of ['订单已取消', '不再结算收益', '原结算收益已按退款规则冲回', '收益已结算，可在钱包查看流水']) assert.ok(!env.text().includes(wrong), wrong);
  assert.deepEqual(env.requests.submitted, []); assert.deepEqual(env.requests.deleted, []);
});

test('hidden pages do not refresh; show and socket reconnect recover missed audits', async () => {
  const env = environment(); await env.load();
  env.emit('report_status_update'); env.hide(); await env.fire();
  for (const event of ['report_status_update', 'order_status_update', 'connected']) env.emit(event);
  assert.equal(env.timers.size, 0); assert.equal(env.requests.reads.length, 1);
  await env.load(); assert.equal(env.requests.reads.length, 2);
  env.emit('connected'); await env.fire(); assert.equal(env.requests.reads.length, 3);
});

test('unmount removes socket/auth listeners, cancels queued refresh and ignores in-flight responses', async () => {
  const env = environment(); await env.load();
  let finish;
  env.setRead(() => new Promise(resolve => { finish = resolve; }));
  env.emit('report_status_update'); const reading = env.fire();
  env.emit('report_status_update'); env.unmount();
  assert.equal(env.authListeners.size, 0);
  for (const handlers of env.listeners.values()) assert.equal(handlers.size, 0);
  finish({ code: 0, data: [report({ game_name: '卸载后不可出现' })] }); await reading;
  assert.equal(env.timers.size, 0); assert.ok(!env.text().includes('卸载后不可出现'));
  env.emit('connected'); assert.equal(env.timers.size, 0);
  const queued = environment(); queued.emit('report_status_update'); queued.unmount(); assert.equal(queued.timers.size, 0);
});

test('account changes immediately clear old reports and reject the preceding account response', async () => {
  const env = environment({ reports: [report({ game_name: '前账号历史报单' })] }); await env.load();
  let finish;
  env.setRead(() => new Promise(resolve => { finish = resolve; }));
  env.emit('report_status_update'); const reading = env.fire();
  env.session(2, false); assert.ok(!env.text().includes('前账号历史报单'));
  finish({ code: 0, data: [report({ game_name: '过期账号异步结果' })] }); await reading;
  assert.ok(!env.text().includes('过期账号异步结果'));
  env.emit('connected'); assert.equal(env.timers.size, 0);
  env.setRead(async () => ({ code: 0, data: [report({ game_name: '新账号报单' })] }));
  env.session(3); await new Promise(resolve => setImmediate(resolve)); env.render();
  assert.ok(env.text().includes('新账号报单'));
  const reads = env.requests.reads.length;
  env.session(3); assert.equal(env.requests.reads.length, reads); // Token rotation is not an account change.
});

test('pull down reads current state and always completes the native refresh indicator', async () => {
  const env = environment(); await env.load();
  env.setRead(async () => { throw Error('离线，请重试'); }); await env.pull();
  assert.equal(env.requests.stopped, 1); assert.ok(env.text().includes('离线，请重试'));
  env.setRead(async () => ({ code: 0, data: [report({ status: 'APPROVED', status_display: '审核通过' })] }));
  await env.pull(); assert.equal(env.requests.stopped, 2); assert.ok(env.text().includes('审核通过'));
});
