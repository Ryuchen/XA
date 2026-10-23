const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const root = path.resolve(__dirname, '..');
const ts = createRequire(path.join(root, 'apps/boss-miniapp/package.json'))('typescript');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const compile = file => ts.transpileModule(fs.readFileSync(path.join(root, file), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, jsx: ts.JsxEmit.React } }).outputText;
const baseOrder = () => ({ id: 1, order_no: 'FIXTURE-1', status: 'COMPLETED', payment_status: 'PAID', amount: 1234, original_amount: 1500,
  boss_discount: 266, promo_discount: 0, coupon_discount: 0, service: { id: 1, name: '本地服务', is_gift: false }, provider: { id: 9, nickname: '本地陪玩' },
  customer: { id: 10, nickname: '本地老板' }, game_rounds: 1, created_at: '2026-10-20T10:00:00Z', refunded_at: null, aftersale_status: '', can_aftersale: true, aftersale_deadline: '2099-10-20T10:00:00Z', allowed_actions: [], evaluation: null });

function native(name) {
  const listeners = new Map(), timers = new Map(), calls = []; let definition, revision = 1, owner = '10', next = 0, order = baseOrder(), sale = null, response, modal = { confirm: false };
  const on = (type, fn) => { if (!listeners.has(type)) listeners.set(type, new Set()); listeners.get(type).add(fn); return () => listeners.get(type).delete(fn); };
  const emit = (type, data) => { for (const fn of listeners.get(type) || []) fn(data); };
  const auth = { getSession: () => owner ? { token: 'fixture', user: { id: owner } } : null, ensure: () => !!owner, revision: () => revision };
  const request = async (url, method = 'GET', data) => { calls.push({ url, method, data }); return response ? response(url, method, data)
    : url.includes('/aftersale/') ? sale : url.includes('/orders/') ? order : { balance: 1234, frozen_amount: 0, total: 1, transactions: [{ id: 1, amount: 1234, tx_type: 'REFUND', created_at: '2026-10-20T10:00:00Z' }] }; };
  const ui = { money: value => String(value / 10), date: value => value || '', image: value => value || '', toast() {}, go() {} };
  const orderModule = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'apps/boss-miniapp/native/core/orders.js'), 'utf8'), { module: orderModule, exports: orderModule.exports, require: () => ui });
  const pageModule = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'apps/boss-miniapp/native/core/page.js'), 'utf8'), { module: pageModule, exports: pageModule.exports, require: value => value === './auth' ? auth : { on, emit } });
  vm.runInNewContext(fs.readFileSync(path.join(root, `apps/boss-miniapp/native/pages/${name}/index.js`), 'utf8'), {
    Page: value => { definition = value; }, wx: { stopPullDownRefresh() {}, showModal: options => typeof modal === 'function' ? modal(options) : options.success(modal) },
    setTimeout: fn => { timers.set(++next, fn); return next; }, clearTimeout: id => timers.delete(id),
    require: value => value.endsWith('/auth') ? auth : value.endsWith('/api') ? { request } : value.endsWith('/events') ? { on, emit } : value.endsWith('/orders') ? orderModule.exports : value.endsWith('/page') ? pageModule.exports : value.endsWith('/brand-assets') ? { brandAssets: () => ({}) } : ui,
  });
  const page = { ...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); } };
  page.onLoad({ id: '1' });
  return { page, calls, timers, emit, set: (nextOrder, nextSale) => { order = nextOrder; sale = nextSale; }, respond: fn => { response = fn; }, modal: value => { modal = value; },
    session: nextOwner => { owner = nextOwner; revision++; emit('session.changed'); },
    show: async () => { page.onShow(); await tick(); }, fire: async () => { assert.equal(timers.size, 1); const [id, fn] = timers.entries().next().value; timers.delete(id); fn(); await tick(); } };
}

test('native wallet coalesces owner updates, ignores hidden/foreign events and clears subscriptions', async () => {
  const s = native('wallet'); await s.show(); assert.equal(s.calls.length, 1);
  for (let i = 0; i < 20; i++) s.emit('wallet.changed', { account_id: 10 }); await s.fire();
  assert.equal(s.calls.length, 2); assert.equal(s.page.data.balance, '123.4'); assert.equal(s.page.data.items[0].displayAmount, '+123.4');
  s.emit('wallet.changed', { account_id: 99 }); assert.equal(s.timers.size, 0);
  s.page.onHide(); s.emit('wallet.changed', { account_id: 10 }); assert.equal(s.timers.size, 0);
  await s.show(); assert.equal(s.calls.length, 3); s.page.onUnload(); s.emit('wallet.changed', { account_id: 10 }); assert.equal(s.timers.size, 0);
});

test('native wallet rejects in-flight data across account changes and page unload', async () => {
  const s = native('wallet'), pending = deferred(); s.respond(() => pending.promise); s.page.onShow();
  s.session(null); pending.resolve({ balance: 999990, transactions: [], total: 0 }); await tick();
  assert.equal(s.page.data.balance, ''); s.page.onUnload();
});

const mineData = (url, { vip = '原会员', points = 100, balance = 1000 } = {}) => url === '/club/me/' ? { vip: { name: vip }, points, display_tags: [{ key: 'vip', title: vip }] }
  : url === '/users/me/' ? { id: '10', nickname: '本地老板', avatar: '' } : url === '/wallet/info/' ? { balance } : { pending: 0, grabbed: 0, in_service: 0 };

test('native mine coalesces owner wallet/order pushes and reloads membership, tags and points with wallet data', async () => {
  const s = native('mine'); let member = {};
  s.respond(url => mineData(url, member)); await s.show(); assert.equal(s.calls.length, 4); assert.equal(s.page.data.member.vip.name, '原会员');
  s.emit('wallet.changed', { account_id: 99 }); assert.equal(s.timers.size, 0);
  member = { vip: '退款后会员', points: 0, balance: 2234 };
  for (let i = 0; i < 12; i++) { s.emit('order.changed', { id: 1 }); s.emit('wallet.changed', { account_id: 10 }); }
  await s.fire(); assert.equal(s.calls.length, 8); assert.equal(s.page.data.member.vip.name, '退款后会员'); assert.equal(s.page.data.member.display_tags[0].title, '退款后会员'); assert.equal(s.page.data.member.points, 0); assert.equal(s.page.data.balance, '223.4');
  s.emit('wallet.changed', { account_id: 10 }); s.page.onHide(); assert.equal(s.timers.size, 0);
  s.emit('order.changed'); assert.equal(s.timers.size, 0); await s.show(); assert.equal(s.calls.length, 12);
  s.page.onUnload(); s.emit('wallet.changed', { account_id: 10 }); s.emit('order.changed'); assert.equal(s.timers.size, 0);
});

test('native mine clears a switched account and cannot restore an old membership response', async () => {
  const s = native('mine'); s.respond(url => mineData(url)); await s.show(); const pending = deferred();
  s.respond(url => pending.promise.then(() => mineData(url, { vip: '错误旧会员', points: 999, balance: 999990 })));
  s.emit('wallet.changed', { account_id: 10 }); await s.fire(); s.session(null);
  assert.equal(s.page.data.member, null); assert.equal(s.page.data.profile, null); assert.equal(s.page.data.balance, '');
  pending.resolve(); await tick(); assert.equal(s.page.data.member, null);
  s.respond(url => mineData(url, { vip: '当前账号会员', points: 7, balance: 700 })); s.session('11'); await tick();
  assert.equal(s.page.data.member.vip.name, '当前账号会员'); assert.equal(s.page.data.balance, '70');
  s.emit('wallet.changed', { account_id: 11 }); s.page.onUnload(); assert.equal(s.timers.size, 0);
});

test('native aftersale detail reloads the actual resolution and distinguishes refund from resolution', async () => {
  const s = native('order-detail');
  s.set({ ...baseOrder(), aftersale_status: 'OPEN' }, { status: 'OPEN', reason: '测试问题', resolution: '' }); await s.show();
  assert.equal(s.page.data.order.aftersaleStatusText, '处理中'); assert.equal(s.page.data.aftersale.reason, '测试问题');
  s.set({ ...baseOrder(), aftersale_status: 'REFUNDED', status: 'CANCELLED', payment_status: 'REFUNDED', refunded_at: '2026-10-20T11:00:00Z' }, { status: 'REFUNDED', reason: '测试问题', resolution: '本地审核已全额退款' });
  s.emit('order.changed'); await tick(); assert.equal(s.page.data.order.aftersaleStatusText, '已退款');
  assert.equal(s.page.data.aftersale.resolution, '本地审核已全额退款'); assert.equal(s.page.data.order.amountText, '123.4'); assert.equal(s.page.data.order.canAfterSale, false);
  const template = fs.readFileSync(path.join(root, 'apps/boss-miniapp/native/pages/order-detail/index.wxml'), 'utf8');
  assert.match(template, /aftersale.resolution/); assert.match(template, /order.refundAmountText.*已退回钱包/); assert.match(template, /<bottom-navigation\/>/);
  s.page.onUnload();
});

test('native honors settled aftersale eligibility and preserves closed history and actual refund summary', async () => {
  const s = native('order-detail'), settled = { ...baseOrder(), aftersale_deadline: '2020-01-01T00:00:00Z', consumption_confirmed_at: '2020-01-02T00:00:00Z', aftersale_status: 'RESOLVED' };
  const history = [{ status: 'RESOLVED', reason: '第一轮问题', resolution: '第一轮处理', client_request_id: 'old', resolved_at: '2026-10-19T01:00:00Z' }, { status: 'OPEN', client_request_id: 'new' }];
  s.set(settled, { status: 'RESOLVED', reason: '第二轮问题', resolution: '第二轮处理', client_request_id: 'new', history }); await s.show();
  assert.equal(s.page.data.order.canAfterSale, true); assert.equal(s.page.data.aftersale.history.length, 1); assert.equal(s.page.data.aftersale.history[0].resolution, '第一轮处理');
  s.set({ ...settled, can_aftersale: false }, null); await s.page.load(); assert.equal(s.page.data.order.canAfterSale, false);
  s.set({ ...settled, payment_status: 'REFUNDED', aftersale_status: 'REFUNDED', refund_summary: { amount: 1000, created_at: '2026-10-20T02:00:00Z', reason: '结算后退款' } }, null); await s.page.load();
  assert.equal(s.page.data.order.canAfterSale, false); assert.equal(s.page.data.order.refundAmountText, '100'); assert.equal(s.page.data.order.refundCreatedText, '2026-10-20T02:00:00Z');
  s.page.onUnload();
});

test('native retries an application with the same UUID and blocks a hidden modal submission', async () => {
  const s = native('order-detail'); let fail = true;
  s.modal({ confirm: true, content: '本地问题' });
  s.respond((url, method) => { if (method === 'POST') { if (fail) throw Error('模拟断线'); return {}; } return baseOrder(); }); await s.show();
  await s.page.aftersale(); fail = false; await s.page.aftersale();
  const posts = s.calls.filter(row => row.method === 'POST'); assert.equal(posts.length, 2); assert.match(posts[0].data.client_request_id, /^[\da-f]{8}-(?:[\da-f]{4}-){3}[\da-f]{12}$/); assert.equal(posts[0].data.client_request_id, posts[1].data.client_request_id);
  await s.page.aftersale(); assert.notEqual(s.calls.filter(row => row.method === 'POST')[2].data.client_request_id, posts[0].data.client_request_id);
  s.modal(options => { s.page.onHide(); options.success({ confirm: true, content: '隐藏后禁止提交' }); }); await s.page.aftersale(); assert.equal(s.calls.filter(row => row.method === 'POST').length, 3); s.page.onUnload();
});

test('native legacy history with no request key recognizes current timestamps across ISO offset spellings', async () => {
  const s = native('order-detail'); s.set({ ...baseOrder(), aftersale_status: 'RESOLVED' }, { created_at: '2026-10-20T01:00:00Z', client_request_id: '', history: [
    { status: 'RESOLVED', created_at: '2026-10-19T01:00:00+00:00', reason: '前一轮' }, { status: 'RESOLVED', created_at: '2026-10-20T01:00:00+00:00', reason: '当前轮' },
  ] }); await s.show(); assert.equal(s.page.data.aftersale.history.length, 1); assert.equal(s.page.data.aftersale.history[0].reason, '前一轮'); s.page.onUnload();
});

const partialSummary = () => ({ amount: 3750, original_amount: 10000, remaining_amount: 6250, refund_count: 2, records: [
  { id: 8, amount: 2500, reason: '第一笔部分退款', created_at: '2026-10-20T01:00:00Z', client_request_id: 'refund-one' },
  { id: 9, amount: 1250, reason: '第二笔部分退款', created_at: '2026-10-20T02:00:00Z', client_request_id: 'refund-two' },
] });

test('native partial refunds preserve completed service, expose cumulative and individual amounts and permit a fresh remaining application', async () => {
  const s = native('order-detail'), order = { ...baseOrder(), amount: 10000, payment_status: 'PARTIALLY_REFUNDED', aftersale_status: 'PARTIALLY_REFUNDED', refund_summary: partialSummary(), consumption_confirmed_at: '2026-10-19T00:00:00Z' };
  s.set(order, { status: 'PARTIALLY_REFUNDED', reason: '第二轮申请', resolution: '部分退回', client_request_id: 'round-two', history: [
    { status: 'PARTIALLY_REFUNDED', reason: '第一轮申请', resolution: '首次处理保留', client_request_id: 'round-one' },
    { status: 'PARTIALLY_REFUNDED', client_request_id: 'round-two' },
  ] }); await s.show();
  assert.equal(s.page.data.order.status, 'COMPLETED'); assert.equal(s.page.data.order.statusText, '已部分退款'); assert.equal(s.page.data.order.canAfterSale, true);
  assert.equal(s.page.data.order.amountText, '1000'); assert.equal(s.page.data.order.refundAmountText, '375'); assert.equal(s.page.data.order.refundRemainingText, '625');
  assert.equal(s.page.data.order.refundRecords.length, 2); assert.equal(s.page.data.order.refundRecords[0].amountText, '250'); assert.equal(s.page.data.order.refundRecords[1].amountText, '125');
  assert.equal(s.page.data.order.aftersaleStatusText, '已部分退款'); assert.equal(s.page.data.aftersale.history[0].statusText, '已部分退款');
  s.modal({ confirm: true, content: '申请剩余金额售后' }); await s.page.aftersale(); const post = s.calls.find(row => row.method === 'POST');
  assert.equal(post.data.reason, '申请剩余金额售后'); assert.ok(post.data.client_request_id); assert.equal(post.data.amount, undefined, 'Only the admin selects the refund amount');
  s.set({ ...order, can_aftersale: false }, null); await s.page.load(); assert.equal(s.page.data.order.canAfterSale, false); s.page.onUnload();
});

function h5(kind) {
  const slots = [], timers = new Map(), events = new Map(), authEvents = new Set(), calls = [], writes = [];
  let cursor = 0, dirty = false, effects = [], tree, didShow, didHide, nextTimer = 0, revision = 1, owner = '10', order = baseOrder(), sale = null, reader, writer, requestNumber = 0;
  const same = (a, b) => a && b && a.length === b.length && a.every((x, i) => Object.is(x, b[i]));
  const react = { Fragment: 'Fragment', createElement: (type, props, ...children) => ({ type, props: { ...props, children } }),
    useState(initial) { const i = cursor++; slots[i] ||= { value: typeof initial === 'function' ? initial() : initial }; return [slots[i].value, value => { const next = typeof value === 'function' ? value(slots[i].value) : value; if (!Object.is(next, slots[i].value)) { slots[i].value = next; dirty = true; } }]; },
    useRef(initial) { return slots[cursor++] ||= { current: initial }; },
    useCallback(fn, deps) { const i = cursor++; if (!same(slots[i]?.deps, deps)) slots[i] = { deps, value: fn }; return slots[i].value; },
    useMemo(fn, deps) { const i = cursor++; if (!same(slots[i]?.deps, deps)) slots[i] = { deps, value: fn() }; return slots[i].value; },
    useEffect(fn, deps) { const i = cursor++; if (!same(slots[i]?.deps, deps)) effects.push(() => { slots[i]?.cleanup?.(); slots[i] = { deps, cleanup: fn() }; }); },
  };
  const clock = { setTimeout: fn => { timers.set(++nextTimer, fn); return nextTimer; }, clearTimeout: id => timers.delete(id) };
  const queue = { exports: {} }; vm.runInNewContext(compile('apps/boss-miniapp/src/utils/coalescedRefresh.ts'), { module: queue, exports: queue.exports, ...clock });
  const money = { exports: {} }; vm.runInNewContext(compile('packages/money/src/index.js'), { module: money, exports: money.exports });
  const wallet = { balance: 1234, setBalance: value => { if (wallet.balance !== value) { wallet.balance = value; dirty = true; } }, invalidate() {} };
  const useWalletStore = Object.assign(selector => selector(wallet), { getState: () => wallet });
  const read = async label => { calls.push(label); return reader ? reader(label) : label === 'orders' ? { code: 0, data: [{ ...order }] } : label === 'aftersale' ? { code: 0, data: sale }
    : { code: 0, data: { balance: wallet.balance, transactions: [{ id: 1, tx_type: 'PAY', amount: -1234, remark: '付款', created_at: '2026-10-20' }, { id: 2, tx_type: 'REFUND', amount: 1234, remark: '退款', created_at: '2026-10-20' }] } }; };
  const store = { useWalletStore, useOrderStore: { getState: () => ({ invalidate() {} }) }, useCatalogStore: { getState: () => ({ loadServices: async () => {} }) }, useServices: () => [] };
  const dependencies = { react: { default: react, ...react }, '@tarojs/components': Object.fromEntries(['View', 'Text', 'Image', 'ScrollView', 'Textarea', 'Button'].map(name => [name, name])),
    '@tarojs/taro': { default: { eventCenter: { on: (name, fn) => authEvents.add(fn), off: (name, fn) => authEvents.delete(fn) }, showToast() {}, stopPullDownRefresh() {}, navigateTo() {}, showModal: async () => ({ confirm: false }) },
      useDidShow: fn => { didShow = fn; }, useDidHide: fn => { didHide = fn; }, usePullDownRefresh() {}, useRouter: () => ({ params: { orderId: '1' } }) },
    '@/services/wallet': { fetchTransactions: () => read('transactions') }, '@/services/order': { fetchOrders: () => read('orders'), fetchAfterSale: () => read('aftersale'), evaluateOrder() {}, cancelOrder() {}, tipOrder() {},
      newAfterSaleRequestId: () => `00000000-0000-4000-8000-${String(++requestNumber).padStart(12, '0')}`,
      submitAfterSale: async (id, reason, clientRequestId) => { writes.push({ id, reason, client_request_id: clientRequestId }); if (writer) return writer(id, reason, clientRequestId); order.aftersale_status = 'OPEN'; sale = { id: 3, status: 'OPEN', reason, resolution: '', client_request_id: clientRequestId }; return { code: 0, data: sale }; } },
    '@/services/websocket': { wsService: { on: (name, fn) => { if (!events.has(name)) events.set(name, new Set()); events.get(name).add(fn); return () => events.get(name).delete(fn); } } },
    '@/utils/auth': { getSessionRevision: () => revision, getStoredToken: () => owner ? 'fixture' : '', getStoredUser: () => owner ? { id: owner } : null },
    '@/utils/coalescedRefresh': queue.exports, '@/services/support': { openWecomCustomerService: async () => ({ opened: false }) }, '@/store': store, '@xa/money': money.exports,
    '@/components': { Empty: 'Empty', Skeleton: 'Skeleton' }, '@/components/Icon': { default: 'Icon' }, '@/components/RouletteWheel': { default: 'RouletteWheel' }, '@/utils/media': { resolveImageUrl: value => value || '' },
    '@/hooks/useLoginGuard': { useLoginGuard: () => ({ ensureLogin: fn => fn(), loginSheet: null }) }, './index.module.scss': { default: new Proxy({}, { get: (_, key) => String(key) }) },
  };
  const types = { exports: {} }; vm.runInNewContext(compile('apps/boss-miniapp/src/types/order.ts'), { module: types, exports: types.exports }); dependencies['@/types/order'] = types.exports;
  const module = { exports: {} }; const file = kind === 'wallet' ? 'customer/wallet' : 'orderList';
  vm.runInNewContext(compile(`apps/boss-miniapp/src/pages/${file}/index.tsx`), { module, exports: module.exports, ...clock, require: name => { if (!(name in dependencies)) throw Error('Unexpected import ' + name); return dependencies[name]; } });
  const render = () => { dirty = false; cursor = 0; effects = []; tree = module.exports.default(); for (const effect of effects) effect(); };
  const flush = async () => { for (let i = 0; i < 30; i++) { await tick(); if (!dirty) return; render(); } throw Error('Effects did not settle'); };
  const visit = (node, result = []) => { if (Array.isArray(node)) node.forEach(child => visit(child, result)); else if (node && typeof node === 'object') { result.push(node); visit(node.props?.children, result); } return result; };
  const text = node => Array.isArray(node) ? node.map(text).join('') : node && typeof node === 'object' ? text(node.props?.children) : node == null || node === false ? '' : String(node);
  const nodes = () => visit(tree); render();
  return { calls, writes, timers, wallet, nodes, text: () => text(tree), flush, button: label => nodes().find(node => node.type === 'Button' && text(node) === label),
    show: async () => { await didShow(); await flush(); }, hide: () => didHide(), read: fn => { reader = fn; }, write: fn => { writer = fn; },
    set: (nextOrder, nextSale) => { order = nextOrder; sale = nextSale; },
    emit: (name, data) => { for (const fn of events.get(name) || []) fn(data); },
    fire: async () => { assert.equal(timers.size, 1); const [id, fn] = timers.entries().next().value; timers.delete(id); await fn(); await flush(); },
    session: async nextOwner => { owner = nextOwner; revision++; for (const fn of authEvents) fn(); await flush(); },
    unmount: () => { for (const slot of slots) slot?.cleanup?.(); },
  };
}

test('H5 wallet displays signed payment/refund amounts and coalesces owner wallet invalidations', async () => {
  const s = h5('wallet'); await s.show(); assert.ok(s.text().includes('-123.4币')); assert.ok(s.text().includes('+123.4币')); assert.ok(s.text().includes('订单退款'));
  s.emit('wallet_update', { account_id: 99 }); assert.equal(s.timers.size, 0);
  for (let i = 0; i < 20; i++) s.emit('wallet_update', { account_id: 10 }); await s.fire(); assert.equal(s.calls.length, 2);
  s.hide(); s.emit('wallet_update', { account_id: 10 }); assert.equal(s.timers.size, 0); s.unmount();
});

test('H5 wallet discards old responses on session switch and cancels deferred reads on unmount', async () => {
  const s = h5('wallet'); await s.show(); const pending = deferred(); s.read(() => pending.promise); s.emit('wallet_update', { account_id: 10 }); const reading = s.fire();
  await s.session(null); pending.resolve({ code: 0, data: { balance: 999990, transactions: [] } }); await reading; assert.equal(s.wallet.balance, 0);
  s.unmount(); assert.equal(s.timers.size, 0);
});

test('H5 order detail submits an aftersale once and refreshes its resolved/refunded result through order events', async () => {
  const s = h5('orders'); await s.show(); s.button('提交售后问题').props.onClick(); await s.flush();
  s.nodes().find(node => node.type === 'Textarea' && node.props.maxlength === 1000).props.onInput({ detail: { value: '测试售后问题' } }); await s.flush();
  await s.button('提交售后').props.onClick(); await s.flush(); assert.deepEqual(s.writes, [{ id: 1, reason: '测试售后问题', client_request_id: '00000000-0000-4000-8000-000000000001' }]); assert.ok(s.text().includes('处理中'));
  s.set({ ...baseOrder(), status: 'CANCELLED', payment_status: 'REFUNDED', aftersale_status: 'REFUNDED', refunded_at: '2026-10-20T12:00:00Z' }, { status: 'REFUNDED', reason: '测试售后问题', resolution: '本地处理全额退回' });
  s.emit('order_status_update', { id: 1, status: 'CANCELLED' }); await s.fire();
  assert.ok(s.text().includes('售后服务 · 已退款')); assert.ok(s.text().includes('处理结果：本地处理全额退回')); assert.ok(s.text().includes('123.4 兴安币已退回钱包')); assert.equal(s.button('提交售后问题'), undefined); s.unmount();
});

test('H5 aftersale editor cannot submit after a session change or unmount', async () => {
  for (const stop of ['session', 'unmount']) {
    const s = h5('orders'); await s.show(); s.button('提交售后问题').props.onClick(); await s.flush();
    s.nodes().find(node => node.type === 'Textarea' && node.props.maxlength === 1000).props.onInput({ detail: { value: '测试售后问题' } }); await s.flush();
    const submit = s.button('提交售后').props.onClick;
    if (stop === 'session') await s.session(null); else s.unmount();
    await submit(); await s.flush(); assert.deepEqual(s.writes, []); s.unmount();
  }
});

test('H5 settled aftersale uses server eligibility, retains history and displays authoritative refund amount', async () => {
  const s = h5('orders'), settled = { ...baseOrder(), aftersale_status: 'RESOLVED', aftersale_deadline: '2020-01-01T00:00:00Z', consumption_confirmed_at: '2020-01-02T00:00:00Z' };
  s.set(settled, { status: 'RESOLVED', reason: '最新问题', resolution: '最新处理', client_request_id: 'current', history: [
    { status: 'RESOLVED', reason: '保留的历史问题', resolution: '保留的历史处理', client_request_id: 'previous', created_at: '2026-10-19T00:00:00Z', resolved_at: '2026-10-19T01:00:00Z' },
    { status: 'RESOLVED', reason: '当前快照勿重复', resolution: '当前结果勿重复', client_request_id: 'current' },
  ] }); await s.show();
  assert.ok(s.button('重新申请售后')); assert.ok(s.text().includes('保留的历史处理')); assert.ok(!s.text().includes('当前快照勿重复'));
  s.set({ ...settled, can_aftersale: false }, null); s.emit('order_status_update', { id: 1 }); await s.fire(); assert.equal(s.button('重新申请售后'), undefined);
  s.set({ ...settled, payment_status: 'REFUNDED', status: 'CANCELLED', aftersale_status: 'REFUNDED', refund_summary: { amount: 1000, reason: '结算后退款', created_at: '2026-10-20T02:00:00Z' } }, { status: 'REFUNDED', resolution: '退款成功' });
  s.emit('order_status_update', { id: 1 }); await s.fire(); assert.ok(s.text().includes('100 兴安币已退回钱包')); assert.ok(!s.text().includes('123.4 兴安币已退回钱包')); assert.ok(s.text().includes('退款说明：结算后退款')); assert.equal(s.button('重新申请售后'), undefined); s.unmount();
});

test('H5 aftersale retries one UUID and gives a later explicit application a new UUID', async () => {
  const s = h5('orders'); await s.show(); s.button('提交售后问题').props.onClick(); await s.flush();
  s.nodes().find(node => node.type === 'Textarea' && node.props.maxlength === 1000).props.onInput({ detail: { value: '本地售后问题' } }); await s.flush();
  let fail = true; s.write(async () => { if (fail) throw Error('模拟断线'); return { code: 0 }; });
  await s.button('提交售后').props.onClick(); await s.flush(); fail = false; await s.button('提交售后').props.onClick(); await s.flush();
  assert.equal(s.writes.length, 2); assert.equal(s.writes[0].client_request_id, s.writes[1].client_request_id);
  s.button('提交售后问题').props.onClick(); await s.flush(); s.nodes().find(node => node.type === 'Textarea' && node.props.maxlength === 1000).props.onInput({ detail: { value: '另一次问题' } }); await s.flush();
  await s.button('提交售后').props.onClick(); await s.flush(); assert.notEqual(s.writes[2].client_request_id, s.writes[0].client_request_id); s.unmount();
});

test('H5 legacy history excludes the current round without dropping earlier empty-key rounds', async () => {
  const s = h5('orders'); s.set({ ...baseOrder(), aftersale_status: 'RESOLVED' }, { reason: '当前申请', created_at: '2026-10-20T01:00:00Z', client_request_id: '', history: [
    { status: 'RESOLVED', created_at: '2026-10-19T01:00:00+00:00', reason: '应保留的旧轮次' }, { status: 'RESOLVED', created_at: '2026-10-20T01:00:00+00:00', reason: '勿重复的当前轮次' },
  ] }); await s.show(); assert.ok(s.text().includes('应保留的旧轮次')); assert.ok(!s.text().includes('勿重复的当前轮次')); s.unmount();
});

test('H5 partial refunds display authoritative cumulative balance/history and allow the next round without calling the service cancelled', async () => {
  const s = h5('orders'), order = { ...baseOrder(), amount: 10000, payment_status: 'PARTIALLY_REFUNDED', aftersale_status: 'PARTIALLY_REFUNDED', refund_summary: partialSummary() };
  s.set(order, { status: 'PARTIALLY_REFUNDED', client_request_id: 'round-two', reason: '第二轮', resolution: '第二次部分处理', history: [{ status: 'PARTIALLY_REFUNDED', client_request_id: 'round-one', reason: '第一轮', resolution: '第一次部分处理', created_at: '2026-10-20T00:00:00Z' }] }); await s.show();
  const text = s.text(); for (const expected of ['部分退款已入账', '累计已退 375 兴安币', '订单剩余金额：625 兴安币', '退款 #8 · 250 兴安币', '退款 #9 · 125 兴安币', '第一次部分处理', '625币']) assert.ok(text.includes(expected), expected);
  assert.ok(!text.includes('订单已取消')); assert.ok(!text.includes('1000 兴安币已退回钱包')); assert.ok(s.button('重新申请售后'));
  s.button('重新申请售后').props.onClick(); await s.flush(); s.nodes().find(node => node.type === 'Textarea' && node.props.maxlength === 1000).props.onInput({ detail: { value: '申请剩余部分' } }); await s.flush();
  await s.button('提交售后').props.onClick(); await s.flush(); assert.equal(s.writes.length, 1); assert.equal(s.writes[0].reason, '申请剩余部分'); assert.equal(s.writes[0].amount, undefined);
  s.set({ ...order, status: 'CANCELLED', payment_status: 'REFUNDED', aftersale_status: 'REFUNDED', refund_summary: { ...partialSummary(), amount: 10000, remaining_amount: 0 } }, { status: 'REFUNDED', resolution: '余款全部退回' });
  s.emit('order_status_update', { id: 1, status: 'CANCELLED' }); await s.fire(); assert.equal(s.button('重新申请售后'), undefined); assert.ok(s.text().includes('订单剩余金额：0 兴安币')); s.unmount();
});

test('partial refund status with a missing summary never invents zero or the original full payment as refunded', async () => {
  const order = { ...baseOrder(), payment_status: 'PARTIALLY_REFUNDED', refund_summary: null };
  const n = native('order-detail'); n.set(order, null); await n.show(); assert.equal(n.page.data.order.refundAmountText, ''); assert.equal(n.page.data.order.refundRemainingText, ''); n.page.onUnload();
  const s = h5('orders'); s.set(order, null); await s.show(); assert.ok(s.text().includes('退款金额以钱包流水为准')); assert.ok(!s.text().includes('累计已退 0')); assert.ok(!s.text().includes('123.4 兴安币已退回钱包')); s.unmount();
});

test('aftersale service generates UUID request keys and passes the selected key without regeneration', async () => {
  const calls = [], module = { exports: {} };
  vm.runInNewContext(compile('apps/boss-miniapp/src/services/order.ts'), { module, exports: module.exports, require: name => name === '@/utils/request' ? { request: (...args) => calls.push(args) } : {} });
  const key = module.exports.newAfterSaleRequestId(); assert.match(key, /^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/);
  module.exports.submitAfterSale(1, '本地问题', key); assert.equal(calls[0][2].client_request_id, key); assert.equal(calls[0][2].reason, '本地问题');
});
