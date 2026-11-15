const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
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
  const ui = { money: value => String(value / 10), date: value => value || '', image: value => value || '', toast() {}, go() {},
    modal: options => typeof modal === 'function' ? modal(options) : options.success(modal) };
  const orderModule = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'apps/boss-miniapp/native/core/orders.js'), 'utf8'), { module: orderModule, exports: orderModule.exports, require: () => ui });
  const pageModule = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'apps/boss-miniapp/native/core/page.js'), 'utf8'), { module: pageModule, exports: pageModule.exports, require: value => value === './auth' ? auth : { on, emit } });
  vm.runInNewContext(fs.readFileSync(path.join(root, `apps/boss-miniapp/native/pages/${name}/index.js`), 'utf8'), {
    Page: value => { definition = value; }, wx: { stopPullDownRefresh() {}, showModal: options => typeof modal === 'function' ? modal(options) : options.success(modal) },
    setTimeout: fn => { timers.set(++next, fn); return next; }, clearTimeout: id => timers.delete(id),
    require: value => value.endsWith('/auth') ? auth : value.endsWith('/api') ? { request } : value.endsWith('/events') ? { on, emit } : value.endsWith('/orders') ? orderModule.exports : value.endsWith('/page') ? pageModule.exports : value.endsWith('/brand-assets') ? { brandAssets: () => ({}) } : value.endsWith('/navigation') ? { syncTabBar() {} } : ui,
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

test('native wallet describes gift credits and gift debits without guessing their source', async () => {
  const s = native('wallet');
  s.respond(() => ({ balance: 12500, frozen_amount: 0, total: 2, transactions: [
    { id: 1, tx_type: 'GIFT', amount: 1000, balance_after: 13000, remark: '客服赠送兴安币' },
    { id: 2, tx_type: 'GIFT', amount: -500, balance_after: 12500, remark: '礼物打赏' },
  ] }));
  await s.show();
  assert.equal(s.page.data.items[0].title, '赠送入账');
  assert.equal(s.page.data.items[1].title, '赠送支出');
  assert.equal(s.page.data.items[0].displayAmount, '+100');
  assert.equal(s.page.data.items[1].displayAmount, '-50');
  s.page.filter({ currentTarget: { dataset: { value: 'TOPUP' } } }); await tick();
  assert.match(s.calls.at(-1).url, /tx_type=TOPUP$/);
  s.page.onUnload();
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

test('native defers order update during an operation and reloads even when it fails', async () => {
  const s = native('order-detail'), pending = deferred();
  s.set(baseOrder(), null); await s.show();
  const operation = s.page.operate(async () => { await pending.promise; throw Error('订单状态已变化'); });
  assert.equal(s.page.data.busy, true);
  s.set({ ...baseOrder(), status: 'CANCELLED', payment_status: 'REFUNDED', can_aftersale: false,
    refund_summary: { amount: 1234, remaining_amount: 0 } }, null);
  s.emit('order.changed');
  assert.equal(s.page.data.order.payment_status, 'PAID');
  pending.resolve(); await operation;
  assert.equal(s.page.data.busy, false);
  assert.equal(s.page.data.order.payment_status, 'REFUNDED');
  assert.equal(s.page.data.order.refundAmountText, '123.4');
  assert.equal(s.page.data.order.canAfterSale, false);
  s.page.onUnload();
});

test('native consumption clock distinguishes a pending milestone from confirmation while aftersale is open', async () => {
  const s = native('order-detail');
  const clock = '2026-10-23T10:00:00Z';
  s.set({ ...baseOrder(), consumption_ready_at: clock }, null); await s.show();
  assert.equal(s.page.data.order.consumptionLabel, '实销计时节点');
  assert.match(s.page.data.order.consumptionNote, /仍需满足报单审核及售后/);
  s.set({ ...baseOrder(), consumption_ready_at: clock, aftersale_status: 'OPEN' }, { status: 'OPEN', reason: '服务问题' });
  s.emit('order.changed'); await tick();
  assert.equal(s.page.data.order.consumptionReadyText, clock);
  assert.match(s.page.data.order.consumptionNote, /售后处理中，实销暂不确认；处理结束后仍需满足确认条件/);
  s.set({ ...baseOrder(), consumption_ready_at: null, aftersale_status: 'OPEN' }, { status: 'OPEN' }); await s.page.load();
  assert.equal(s.page.data.order.consumptionLabel, ''); assert.match(s.page.data.order.consumptionNote, /实销暂不确认/);
  s.set({ ...baseOrder(), consumption_ready_at: clock, consumption_confirmed_at: '2026-10-23T12:00:00Z', aftersale_status: 'OPEN' }, { status: 'OPEN' }); await s.page.load();
  assert.equal(s.page.data.order.consumptionLabel, '实销确认时间'); assert.doesNotMatch(s.page.data.order.consumptionNote, /暂不确认/);
  s.set({ ...baseOrder(), status: 'CANCELLED', payment_status: 'REFUNDED', consumption_ready_at: clock }, null); await s.page.load();
  assert.equal(s.page.data.order.consumptionLabel, ''); assert.equal(s.page.data.order.consumptionNote, '');
  const template = fs.readFileSync(path.join(root, 'apps/boss-miniapp/native/pages/order-detail/index.wxml'), 'utf8');
  assert.match(template, /order\.consumptionLabel/); assert.match(template, /order\.consumptionNote/); assert.doesNotMatch(template, /实销预计确认/);
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
  assert.equal(s.page.data.order.status, 'COMPLETED'); assert.equal(s.page.data.order.statusText, '已完成'); assert.equal(s.page.data.order.canAfterSale, true);
  assert.equal(s.page.data.order.amountText, '1000'); assert.equal(s.page.data.order.refundAmountText, '375'); assert.equal(s.page.data.order.refundRemainingText, '625');
  assert.equal(s.page.data.order.refundRecords.length, 2); assert.equal(s.page.data.order.refundRecords[0].amountText, '250'); assert.equal(s.page.data.order.refundRecords[1].amountText, '125');
  assert.equal(s.page.data.order.aftersaleStatusText, '已部分退款'); assert.equal(s.page.data.aftersale.history[0].statusText, '已部分退款');
  s.modal({ confirm: true, content: '申请剩余金额售后' }); await s.page.aftersale(); const post = s.calls.find(row => row.method === 'POST');
  assert.equal(post.data.reason, '申请剩余金额售后'); assert.ok(post.data.client_request_id); assert.equal(post.data.amount, undefined, 'Only the admin selects the refund amount');
  s.set({ ...order, can_aftersale: false }, null); await s.page.load(); assert.equal(s.page.data.order.canAfterSale, false); s.page.onUnload();
});

test('partial refund status with a missing summary never invents zero or the original full payment as refunded', async () => {
  const order = { ...baseOrder(), payment_status: 'PARTIALLY_REFUNDED', refund_summary: null };
  const n = native('order-detail'); n.set(order, null); await n.show(); assert.equal(n.page.data.order.refundAmountText, ''); assert.equal(n.page.data.order.refundRemainingText, ''); n.page.onUnload();
});
