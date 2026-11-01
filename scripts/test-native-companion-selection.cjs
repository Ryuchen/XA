const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const tick = () => new Promise(resolve => setImmediate(resolve));
const tap = id => ({ currentTarget: { dataset: { id } } });
const rows = [
  { id: 11, nickname: '松子', can_order: true, gender: 'MALE', monthly_order_amount: 900, pricePerHour: 200 },
  { id: 12, nickname: '橙子', can_order: true, gender: 'FEMALE', monthly_order_amount: 1200, pricePerHour: 300 },
  { id: 13, nickname: '梨子', can_order: true, gender: 'FEMALE', monthly_order_amount: 1000, pricePerHour: 100 }
];

function environment(options = {}) {
  for (const key of Object.keys(require.cache)) if (key.startsWith(root + path.sep)) delete require.cache[key];
  const storage = new Map(), calls = [], navigation = [], toasts = [];
  const state = { limit: options.limit || 2, rows: structuredClone(rows), omitted: [] };
  global.wx = {
    getStorageSync: key => storage.get(key), getStorageInfoSync: () => ({ keys: [...storage.keys()] }), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key),
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    showToast: options => toasts.push(options.title), stopPullDownRefresh() {},
    navigateTo: value => navigation.push(value), navigateBack: value => navigation.push({ back: true, ...value }),
    switchTab: value => navigation.push(value), redirectTo: value => navigation.push(value), showModal() {},
    request(value) {
      calls.push(value);
      Promise.resolve().then(async () => {
        let response = options.respond && await options.respond(value, state);
        if (response === undefined) {
          const detail = /\/users\/escorts\/(\d+)\//.exec(value.url);
          if (detail) response = state.rows.find(row => row.id === Number(detail[1])) || { statusCode: 404, data: { code: 404, msg: '陪玩不可查看' } };
          else if (value.url.includes('/users/escorts/')) response = state.rows.filter(row => !state.omitted.includes(row.id));
          else if (value.url.includes('/orders/services/')) response = { id: 91, name: '测试套餐', price: 1360, billing_provider_count: state.limit };
          else response = [];
        }
        if (response instanceof Error) value.fail(response);
        else value.success(response && 'statusCode' in response ? response : { statusCode: 200, data: { code: 0, data: response } });
      }).catch(value.fail);
    }
  };
  const auth = require(path.join(root, 'core/auth'));
  const login = (id = '1') => auth.setSession({ token: 'test-' + id, refresh: 'refresh-' + id, user: { id, role: 'customer' } });
  function page(route, params = {}) {
    let definition; global.Page = value => { definition = value; };
    const entry = path.join(root, 'pages', route, 'index.js'); delete require.cache[require.resolve(entry)]; require(entry);
    const instance = { ...definition, data: structuredClone(definition.data), setData: patch => Object.assign(instance.data, patch) };
    instance.onLoad(params); return instance;
  }
  login();
  return { state, calls, navigation, toasts, storage, auth, login, page };
}

test('selection IDs are unique positive safe integers and service data controls the limit', () => {
  const { normalizeProviderIds, providerLimit } = require(path.join(root, 'core/provider-selection'));
  assert.deepEqual(normalizeProviderIds('11,12,11,0,-1,NaN,9007199254740992'), [11, 12]);
  assert.deepEqual(normalizeProviderIds([{ id: 12 }, 11, '12']), [12, 11]);
  assert.equal(providerLimit({ billing_provider_count: '2' }), 2);
  assert.equal(providerLimit({ provider_count: 2 }), 1);
  assert.equal(providerLimit(null), 1);
});

test('double selection restores highlighted cards and survives search, sorting, refresh, and re-entry', async () => {
  const env = environment(), page = env.page('companion-picker', { service_id: '91', from_checkout: '1', selected_provider_ids: '11,12,11' });
  await page.load(); assert.deepEqual(page.data.selectedProviderIds, [11, 12]); assert.equal(page.data.selectionLimit, 2);
  assert.deepEqual(page.data.visible.filter(row => row.selected).map(row => row.id), [11, 12]);
  page.search({ detail: { value: '梨子' } }); assert.equal(page.data.visible[0].selected, false);
  page.search({ detail: { value: '' } }); page.chooseSort({ detail: { value: 1 } });
  assert.deepEqual(page.data.selectedProviderIds, [11, 12]); assert.equal(page.data.visible.find(row => row.id === 12).selected, true);
  await page.load(); assert.deepEqual(page.data.selectedProviderIds, [11, 12]);
  const reopened = env.page('companion-picker', { service_id: '91', selected_provider_ids: page.data.selectedProviderIds.join(',') });
  await reopened.load(); assert.deepEqual(reopened.data.selectedProviderIds, [11, 12]);
  page.onUnload(); reopened.onUnload();
});

test('single selection replaces the previous card and tapping the selected card clears it', async () => {
  const env = environment({ limit: 1 }), page = env.page('companion-picker', { service_id: '91', selection_limit: '2', selected_provider_ids: '11' });
  await page.load(); assert.equal(page.data.selectionLimit, 1);
  page.chooseProvider(tap(12)); assert.deepEqual(page.data.selectedProviderIds, [12]);
  assert.equal(page.data.visible.find(row => row.id === 11).selected, false);
  page.chooseProvider(tap(12)); assert.deepEqual(page.data.selectedProviderIds, []); assert.equal(page.data.selectionReady, true); page.onUnload();
});

test('double selection needs exactly two specified people and never silently chooses a third', async () => {
  const env = environment(), page = env.page('companion-picker', { service_id: '91', from_checkout: '1' });
  const events = []; page.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await page.load(); page.chooseProvider(tap(11)); assert.equal(page.data.selectionReady, false);
  await page.confirmSelection(); assert.equal(events.length, 0); assert.match(env.toasts.at(-1), /还需选择1位/);
  page.chooseProvider(tap(12)); page.chooseProvider(tap(13));
  assert.deepEqual(page.data.selectedProviderIds, [11, 12]); assert.match(env.toasts.at(-1), /最多选择2位/);
  page.chooseProvider(tap(11)); page.chooseProvider(tap(13)); await page.confirmSelection();
  assert.equal(events.length, 1); assert.equal(events[0][0], 'providersSelected'); assert.deepEqual(events[0][1].map(row => row.id), [12, 13]);
  assert.equal(env.navigation.at(-1).back, true); page.onUnload();
});

test('detail selection returns a draft to the picker and waits for final confirmation', async () => {
  const env = environment(), page = env.page('companion-picker', { service_id: '91', from_checkout: '1', selected_provider_ids: '11' });
  const events = []; page.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await page.load(); page.openDetail(tap(12)); const opened = env.navigation.at(-1);
  assert.match(opened.url, /picker_draft=1&selected_provider_ids=11$/);
  const detail = env.page('companion-detail', { id: '12', service_id: '91', from_checkout: '1', picker_draft: '1', selected_provider_ids: '11' });
  detail.getOpenerEventChannel = () => ({ emit: (event, provider) => opened.events[event](provider) });
  await detail.load(); detail.select(); assert.equal(env.navigation.at(-1).delta, 1);
  assert.deepEqual(page.data.selectedProviderIds, [11, 12]); assert.equal(events.length, 0);
  await page.confirmSelection(); assert.equal(events.length, 1); detail.onUnload(); page.onUnload();
});

test('viewing and returning from an already selected profile does not deselect it', async () => {
  const env = environment(), detail = env.page('companion-detail', { id: '11', service_id: '91', picker_draft: '1', selected_provider_ids: '11,12' });
  const events = []; detail.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await detail.load(); assert.equal(detail.data.draftSelected, true); detail.select(); assert.equal(events.length, 0);
  assert.equal(env.navigation.at(-1).back, true); detail.onUnload();
});

test('a full double draft cannot add a third person from their detail page', async () => {
  const env = environment(), detail = env.page('companion-detail', { id: '13', service_id: '91', picker_draft: '1', selected_provider_ids: '11,12' });
  const events = []; detail.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await detail.load(); detail.select(); assert.equal(events.length, 0); assert.match(env.toasts.at(-1), /已选齐2位/); detail.onUnload();
});

test('selected people absent from recommended results retain names and are checked individually', async () => {
  const env = environment(); env.state.omitted = [11];
  const page = env.page('companion-picker', { service_id: '91', selected_provider_ids: '11,12' });
  await page.load(); assert.deepEqual(page.data.selectedProviderIds, [11, 12]); assert.equal(page.data.selectedProviders[0].nickname, '松子');
  assert.equal(page.data.selectionReady, true); assert.ok(env.calls.some(call => call.url.includes('/users/escorts/11/'))); page.onUnload();
});

test('unavailable selected people remain cancellable and cannot be confirmed', async () => {
  const env = environment(); env.state.rows[0].can_order = false;
  const page = env.page('companion-picker', { service_id: '91', from_checkout: '1', selected_provider_ids: '11,12' });
  const events = []; page.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await page.load(); assert.equal(page.data.selectionReady, false); await page.confirmSelection(); assert.equal(events.length, 0);
  page.chooseProvider(tap(11)); assert.deepEqual(page.data.selectedProviderIds, [12]);
  page.chooseProvider(tap(11)); assert.deepEqual(page.data.selectedProviderIds, [12]);
  page.chooseProvider(tap(13)); assert.equal(page.data.selectionReady, true); page.onUnload();
});

test('final confirmation checks current availability and catches a removed selected account', async () => {
  const env = environment(), page = env.page('companion-picker', { service_id: '91', from_checkout: '1', selected_provider_ids: '11,12' });
  const events = []; page.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await page.load(); env.state.rows = env.state.rows.filter(row => row.id !== 12);
  await page.confirmSelection(); assert.equal(events.length, 0); assert.equal(page.data.selectionReady, false);
  assert.deepEqual(page.data.selectedProviderIds, [11, 12]); page.removeSelected(tap(12)); assert.deepEqual(page.data.selectedProviderIds, [11]); page.onUnload();
});

test('a service headcount change before confirmation blocks an obsolete selection', async () => {
  const env = environment(), page = env.page('companion-picker', { service_id: '91', from_checkout: '1', selected_provider_ids: '11,12' });
  const events = []; page.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await page.load(); env.state.limit = 1; await page.confirmSelection();
  assert.equal(events.length, 0); assert.equal(page.data.selectionLimit, 1); assert.equal(page.data.selectionReady, false); assert.match(env.toasts.at(-1), /人数已变化/); page.onUnload();
});

test('platform arrangement clears all specified people and emits one empty array', async () => {
  const env = environment(), page = env.page('companion-picker', { service_id: '91', from_checkout: '1', selected_provider_ids: '11,12' });
  const events = []; page.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await page.load(); await page.platform(); assert.deepEqual(events, [['providersSelected', []]]); assert.deepEqual(page.data.selectedProviderIds, []); page.onUnload();
});

test('service picker opens checkout with both IDs, without changing the service or price', async () => {
  const env = environment(), page = env.page('companion-picker', { service_id: '91', selected_provider_ids: '11,12', provider_gender: 'ANY' });
  await page.load(); await page.confirmSelection(); assert.equal(env.navigation.at(-1).url, '/pages/checkout/index?service_id=91&provider_gender=ANY&provider_ids=11,12'); page.onUnload();
});

test('a double-package direct detail selection continues in picker with the first person selected', async () => {
  const env = environment(), detail = env.page('companion-detail', { id: '11', service_id: '91' });
  await detail.load(); detail.select(); assert.equal(env.navigation.at(-1).url, '/pages/companion-picker/index?service_id=91&provider_gender=ANY&selected_provider_ids=11'); detail.onUnload();
});

test('session changes clear the draft and reject old detail and checkout events', async () => {
  const env = environment(), page = env.page('companion-picker', { service_id: '91', from_checkout: '1', selected_provider_ids: '11,12' });
  const events = []; page.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await page.load(); page.openDetail(tap(13)); const detailNavigation = env.navigation.at(-1);
  env.login('2'); await page.load(); detailNavigation.events.providerSelected(rows[2]); assert.deepEqual(page.data.selectedProviderIds, []);
  page.chooseProvider(tap(11)); await page.platform(); assert.equal(events.length, 0); assert.match(env.toasts.at(-1), /账号已变更/); page.onUnload();
});

test('a session switch during final validation cannot send the old draft', async () => {
  let release, hold = false;
  const env = environment({ respond: async value => {
    if (hold && value.url.includes('/orders/services/')) await new Promise(resolve => { release = resolve; });
  } });
  const page = env.page('companion-picker', { service_id: '91', from_checkout: '1', selected_provider_ids: '11,12' });
  const events = []; page.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await page.load(); hold = true; const validation = page.confirmSelection(); await tick();
  env.login('2'); release(); await validation; assert.equal(events.length, 0); assert.deepEqual(page.data.selectedProviderIds, []); assert.equal(page.data.validating, false); page.onUnload();
});

test('rapid repeated confirmation sends a single selection and duplicate detail events do not toggle it', async () => {
  const env = environment(), page = env.page('companion-picker', { service_id: '91', from_checkout: '1', selected_provider_ids: '11,12' });
  const events = []; page.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await page.load(); page.openDetail(tap(12)); const opened = env.navigation.at(-1);
  opened.events.providerSelected(rows[1]); opened.events.providerSelected(rows[1]); assert.deepEqual(page.data.selectedProviderIds, [11, 12]);
  await Promise.all([page.confirmSelection(), page.confirmSelection()]); await page.confirmSelection();
  assert.equal(events.length, 1); page.onUnload();
});

test('a failed final service validation keeps the draft and can be retried', async () => {
  let fail = false;
  const env = environment({ respond: value => fail && value.url.includes('/orders/services/') ? new Error('offline') : undefined });
  const page = env.page('companion-picker', { service_id: '91', from_checkout: '1', selected_provider_ids: '11,12' });
  const events = []; page.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await page.load(); fail = true; await page.confirmSelection(); assert.equal(events.length, 0); assert.deepEqual(page.data.selectedProviderIds, [11, 12]);
  assert.equal(page.data.validating, false); fail = false; await page.confirmSelection(); assert.equal(events.length, 1); page.onUnload();
});

test('leaving the picker while validation is pending cannot navigate or emit later', async () => {
  let release, hold = false;
  const env = environment({ respond: async value => {
    if (hold && value.url.includes('/orders/services/')) await new Promise(resolve => { release = resolve; });
  } });
  const page = env.page('companion-picker', { service_id: '91', from_checkout: '1', selected_provider_ids: '11,12' });
  const events = []; page.getOpenerEventChannel = () => ({ emit: (...event) => events.push(event) });
  await page.load(); hold = true; const validation = page.confirmSelection(); await tick();
  page.onHide(); release(); await validation;
  assert.equal(events.length, 0); assert.equal(env.navigation.length, 0); assert.equal(page.data.validating, false); page.onUnload();
});

test('checkout submits the selected route while preserving public matching and chosen-provider behavior', async () => {
  const env = environment({ limit: 1, respond: value => {
    if (value.url.includes('/orders/quote/')) return { amount: 120, wallet_balance: 200, balance_sufficient: true, pending_timeout_minutes: 30 };
    if (value.url.includes('/orders/create/')) return { id: 45 };
  } });
  const page = env.page('checkout', { service_id: '91' });
  page.setData({ service: { name: '测试套餐', billing_provider_count: 1 }, selectionLimit: 1, nickname: '玩家', region: '一区', quote: { amount: 120, pending_timeout_minutes: 30 } });
  page.refreshQuote = () => {};
  assert.equal(page.payload().dispatch_route, 'PUBLIC');
  page.dispatch({ detail: { value: '1' } });
  assert.equal(page.payload().dispatch_mode, 'AUTO_GRAB');
  page.selectDispatchRoute({ currentTarget: { dataset: { route: 'STAFF' } } });
  assert.equal(page.payload().dispatch_route, 'STAFF');
  assert.equal(page.payload().dispatch_mode, 'BOSS_SELECT');
  let confirmation = '';
  global.wx.showModal = options => { confirmation = options.content; options.success({ confirm: true }); };
  await page.submit();
  const create = env.calls.find(call => call.url.includes('/orders/create/'));
  assert.equal(create.data.dispatch_route, 'STAFF');
  assert.equal(create.data.dispatch_mode, 'BOSS_SELECT');
  assert.match(confirmation, /客服指定陪玩，不进入公开接单池/);
  assert.match(env.navigation.at(-1).url, /order-detail\/index\?id=45/);
  page.setProviders([rows[0]]);
  assert.equal(page.data.dispatchRoute, 'STAFF');
  assert.equal(page.payload().dispatch_route, 'PUBLIC');
  assert.equal(page.payload().provider_id, 11);
  page.setProviders([]);
  assert.equal(page.payload().dispatch_route, 'STAFF');
  assert.equal(page.payload().dispatch_mode, 'BOSS_SELECT');
  page.onUnload();
});

test('double checkout permits staff arrangement without allowing direct grab or a partial selection', () => {
  const env = environment(), page = env.page('checkout', { service_id: '91' });
  page.setData({ service: { name: '测试套餐', billing_provider_count: 2 }, selectionLimit: 2 });
  page.refreshQuote = () => {};
  page.selectDispatchRoute({ currentTarget: { dataset: { route: 'STAFF' } } });
  assert.equal(page.data.dispatchRoute, 'STAFF');
  assert.equal(page.payload().dispatch_route, 'STAFF');
  assert.equal(page.payload().dispatch_mode, 'BOSS_SELECT');
  page.setProviders([rows[0]]);
  assert.equal(page.data.selectionComplete, false);
  assert.equal(page.payload().dispatch_route, 'PUBLIC');
  assert.equal(page.data.dispatchRoute, 'STAFF');
  page.setProviders([]);
  assert.equal(page.payload().dispatch_route, 'STAFF');
  page.setData({ service: { name: '测试套餐', billing_provider_count: 1 }, selectionLimit: 1, dispatchRoute: 'PUBLIC', submitting: true });
  page.selectDispatchRoute({ currentTarget: { dataset: { route: 'STAFF' } } });
  assert.equal(page.data.dispatchRoute, 'PUBLIC');
  page.onUnload();
});

test('checkout preserves its route while companions are selected or changed and resets on account switch', () => {
  for (const limit of [1, 2]) {
    const env = environment({ limit }), page = env.page('checkout', { service_id: '91' });
    page.setData({ service: { name: '测试套餐', billing_provider_count: limit }, selectionLimit: limit });
    page.refreshQuote = () => {};
    page.selectDispatchRoute({ currentTarget: { dataset: { route: 'STAFF' } } });
    page.setProviders(limit === 1 ? [rows[0]] : [rows[0], rows[1]]);
    assert.equal(page.data.dispatchRoute, 'STAFF');
    assert.equal(page.payload().dispatch_route, 'PUBLIC');
    page.setProviders(limit === 1 ? [rows[1]] : [rows[1], rows[2]]);
    assert.equal(page.payload().dispatch_route, 'PUBLIC');
    page.setProviders([]);
    assert.equal(page.payload().dispatch_route, 'STAFF');
    assert.equal(page.payload().provider_id, undefined);
    assert.equal(page.payload().provider_ids, undefined);
    page.selectDispatchRoute({ currentTarget: { dataset: { route: 'PUBLIC' } } });
    page.setProviders(limit === 1 ? [rows[0]] : [rows[0], rows[1]]);
    page.setProviders([]);
    assert.equal(page.payload().dispatch_route, 'PUBLIC');
    page.selectDispatchRoute({ currentTarget: { dataset: { route: 'STAFF' } } });
    env.login('2');
    assert.equal(page.data.dispatchRoute, 'PUBLIC');
    assert.deepEqual(page.data.selectedProviderIds, []);
    page.onUnload();
  }
});

test('quote and create agree on restored staff route after companion selection is cleared', async () => {
  for (const limit of [1, 2]) {
    const env = environment({ limit, respond: value => {
      if (value.url.includes('/orders/quote/')) return { amount: 120, wallet_balance: 200, balance_sufficient: true, pending_timeout_minutes: 30 };
      if (value.url.includes('/orders/create/')) return { id: 45 };
    } });
    const page = env.page('checkout', { service_id: '91' });
    page.setData({ service: { name: '测试套餐', billing_provider_count: limit }, selectionLimit: limit, nickname: '玩家', region: '一区' });
    page.selectDispatchRoute({ currentTarget: { dataset: { route: 'STAFF' } } });
    page.setProviders(limit === 1 ? [rows[0]] : [rows[0], rows[1]]);
    await page.refreshQuote();
    let quote = env.calls.filter(call => call.url.includes('/orders/quote/')).at(-1);
    assert.equal(quote.data.dispatch_route, 'PUBLIC');
    assert.ok(limit === 1 ? quote.data.provider_id : quote.data.provider_ids.length === 2);
    page.setProviders([]);
    await page.refreshQuote();
    quote = env.calls.filter(call => call.url.includes('/orders/quote/')).at(-1);
    assert.equal(quote.data.dispatch_route, 'STAFF');
    assert.equal(quote.data.provider_id, undefined);
    assert.equal(quote.data.provider_ids, undefined);
    global.wx.showModal = options => options.success({ confirm: true });
    await page.submit();
    const create = env.calls.find(call => call.url.includes('/orders/create/'));
    assert.equal(create.data.dispatch_route, 'STAFF');
    assert.equal(create.data.provider_id, undefined);
    assert.equal(create.data.provider_ids, undefined);
    page.onUnload();
  }
});

test('late selected-companion quote cannot overwrite a restored staff quote', async () => {
  let release;
  const env = environment({ limit: 1, respond: async value => {
    if (value.url.includes('/orders/quote/')) {
      if (value.data.provider_id) {
        await new Promise(resolve => { release = resolve; });
        return { amount: 121, wallet_balance: 200, balance_sufficient: true, pending_timeout_minutes: 30 };
      }
      return { amount: 120, wallet_balance: 200, balance_sufficient: true, pending_timeout_minutes: 30 };
    }
    if (value.url.includes('/orders/create/')) return { id: 45 };
  } });
  const page = env.page('checkout', { service_id: '91' });
  page.setData({ service: { name: '测试套餐', billing_provider_count: 1 }, selectionLimit: 1, dispatchRoute: 'STAFF', nickname: '玩家', region: '一区' });
  page.setProviders([rows[0]]);
  const olderQuote = page.refreshQuote();
  await tick();
  page.setProviders([]);
  await page.refreshQuote();
  assert.equal(page.data.quote.amount, 120);
  release();
  await olderQuote;
  assert.equal(page.data.quote.amount, 120);
  assert.equal(page.payload().dispatch_route, 'STAFF');
  global.wx.showModal = options => options.success({ confirm: true });
  await page.submit();
  const create = env.calls.find(call => call.url.includes('/orders/create/'));
  assert.equal(create.data.dispatch_route, 'STAFF');
  assert.equal(create.data.provider_id, undefined);
  page.onUnload();
});

test('double checkout confirms and submits staff arrangement for two people', async () => {
  const env = environment({ respond: value => {
    if (value.url.includes('/orders/quote/')) return { amount: 240, wallet_balance: 1000, balance_sufficient: true, pending_timeout_minutes: 30 };
    if (value.url.includes('/orders/create/')) return { id: 46 };
  } });
  let confirmation = '';
  global.wx.showModal = options => { confirmation = options.content; options.success({ confirm: true }); };
  const page = env.page('checkout', { service_id: '91' });
  page.setData({ service: { name: '测试套餐', billing_provider_count: 2 }, selectionLimit: 2, nickname: '玩家', region: '一区', quote: { amount: 240, pending_timeout_minutes: 30 } });
  page.refreshQuote = () => {};
  page.selectDispatchRoute({ currentTarget: { dataset: { route: 'STAFF' } } });
  await page.submit();
  const create = env.calls.find(call => call.url.includes('/orders/create/'));
  assert.equal(create.data.dispatch_route, 'STAFF'); assert.equal(create.data.dispatch_mode, 'BOSS_SELECT');
  assert.equal(create.data.provider_ids, undefined); assert.match(confirmation, /客服指定两名陪玩/);
  page.onUnload();
});

test('public double checkout explains team confirmation and boss selection before payment', async () => {
  const env = environment({ respond: value => {
    if (value.url.includes('/orders/quote/')) return { amount: 240, wallet_balance: 1000, balance_sufficient: true, pending_timeout_minutes: 30 };
    if (value.url.includes('/orders/create/')) return { id: 46 };
  } });
  let confirmation = '';
  global.wx.showModal = options => { confirmation = options.content; options.success({ confirm: true }); };
  const page = env.page('checkout', { service_id: '91' });
  page.setData({ service: { name: '测试套餐', billing_provider_count: 2 }, selectionLimit: 2, nickname: '玩家', region: '一区', quote: { amount: 240, pending_timeout_minutes: 30 } });
  await page.submit();
  assert.match(confirmation, /组队报名/);
  assert.match(confirmation, /队友确认后请你在订单详情选人/);
  const create = env.calls.find(call => call.url.includes('/orders/create/'));
  assert.equal(create.data.dispatch_route, 'PUBLIC');
  assert.equal(create.data.dispatch_mode, 'BOSS_SELECT');
  const source = require('node:fs').readFileSync(path.join(root, 'pages/checkout/index.wxml'), 'utf8');
  assert.match(source, /陪玩需组队报名，队友确认后，请你在订单详情选择接单的两名陪玩/);
  page.onUnload();
});

test('checkout discloses and freezes the confirmed wait limit for each unassigned route', async () => {
  for (const route of ['PUBLIC', 'STAFF']) {
    const env = environment({ limit: 1, respond: value => {
      if (value.url.includes('/orders/quote/')) return { amount: 120, wallet_balance: 200, balance_sufficient: true, pending_timeout_minutes: 45 };
      if (value.url.includes('/orders/create/')) return { id: 46 };
    } });
    let confirmation = '';
    global.wx.showModal = options => { confirmation = options.content; options.success({ confirm: true }); };
    const page = env.page('checkout', { service_id: '91' });
    page.setData({ service: { name: '测试套餐', billing_provider_count: 1 }, selectionLimit: 1, nickname: '玩家', region: '一区', dispatchRoute: route });
    await page.refreshQuote();
    assert.equal(page.data.quote.pending_timeout_minutes, 45);
    await page.submit();
    const create = env.calls.find(call => call.url.includes('/orders/create/'));
    assert.equal(create.data.expected_timeout_minutes, 45);
    assert.match(confirmation, /待接单时限为 45 分钟/);
    assert.match(confirmation, route === 'STAFF' ? /客服指定陪玩/ : /由你在订单详情选人/);
    page.onUnload();
  }
  const source = require('node:fs').readFileSync(path.join(root, 'pages/checkout/index.wxml'), 'utf8');
  assert.match(source, /quote\.pending_timeout_minutes/);
});

test('changed or invalid wait-limit quotes require a fresh confirmation and never create an order', async () => {
  for (const latest of [45, null, 0, 1441, '30']) {
    const env = environment({ limit: 1, respond: value => value.url.includes('/orders/quote/')
      ? { amount: 120, wallet_balance: 200, balance_sufficient: true, pending_timeout_minutes: latest }
      : undefined });
    const page = env.page('checkout', { service_id: '91' });
    page.setData({ service: { name: '测试套餐', billing_provider_count: 1 }, selectionLimit: 1, nickname: '玩家', region: '一区', quote: { amount: 120, pending_timeout_minutes: 30 } });
    let modalCount = 0;
    global.wx.showModal = () => { modalCount++; };
    await page.submit();
    assert.equal(env.calls.filter(call => call.url.includes('/orders/create/')).length, 0);
    assert.equal(modalCount, 0);
    assert.match(page.data.quoteError, /待接单时限/);
    page.onUnload();
  }
  const env = environment({ limit: 1, respond: value => value.url.includes('/orders/quote/')
    ? { amount: 120, wallet_balance: 200, balance_sufficient: true }
    : undefined });
  const page = env.page('checkout', { service_id: '91' });
  page.setData({ service: { name: '测试套餐', billing_provider_count: 1 }, selectionLimit: 1 });
  await page.refreshQuote();
  assert.equal(page.data.quote, null);
  assert.match(page.data.quoteError, /待接单时限/);
  page.onUnload();
});

test('server-expired pending orders never offer boss selection or promise refund', () => {
  const env = environment();
  const { present } = require(path.join(root, 'core/orders'));
  const order = { status: 'PENDING', payment_status: 'PAID', dispatch_route: 'PUBLIC', requires_boss_selection: true,
                  pending_deadline_expired: true, auto_cancel_at: '2099-10-27T05:00:00Z', allowed_actions: ['CANCEL', 'SELECT_PROVIDER'], service: {} };
  for (const route of ['PUBLIC', 'STAFF']) {
    const row = present({ ...order, dispatch_route: route });
    assert.equal(row.statusText, '已到接单期限，等待处理');
    assert.equal(row.canSelectProvider, false);
    assert.equal(row.canCancel, true);
    assert.match(row.deadlineDescription, /请联系客服/);
    assert.doesNotMatch(row.deadlineDescription, /退款|退回|余额/);
  }
  const stillValid = present({ ...order, pending_deadline_expired: false, auto_cancel_at: '2001-01-01T00:00:00Z' });
  assert.equal(stillValid.canSelectProvider, true);
  assert.equal(present({ ...order, status: 'GRABBED' }).statusText, '已接单');
  assert.equal(present({ ...order, payment_status: 'UNPAID' }).statusText, '支付待确认');
});

test('simulated direct intent review never claims payment, dispatch, cancellation, or refund', () => {
  environment();
  const { present } = require(path.join(root, 'core/orders'));
  const order = { status: 'CANCELLED', payment_status: 'PAID', dispatch_route: 'STAFF',
    payment_display: { kind: 'DIRECT_TEST', state: 'REVIEW' }, auto_cancel_at: '2099-10-27T05:00:00Z',
    pending_deadline_expired: true, allowed_actions: ['CANCEL', 'SELECT_PROVIDER', 'EVALUATE', 'TIP'],
    service: {}, created_at: '2026-10-27T05:00:00Z', cancelled_at: '2026-10-27T06:00:00Z' };
  for (const state of ['REVIEW', 'UNCONFIRMED']) {
    const row = present({ ...order, payment_display: { ...order.payment_display, state } });
    assert.equal(row.statusText, '支付状态待核对');
    assert.equal(row.paymentUnconfirmed, true);
    assert.equal(row.deadlineDescription, '');
    assert.match(row.fulfillment, /支付状态待核对/);
    assert.equal(row.canCancel, false); assert.equal(row.canSelectProvider, false);
    assert.equal(row.canEvaluate, false); assert.equal(row.canTip, false);
    assert.deepEqual(row.timeline.map(item => item.title), ['订单已创建']);
  }
  const balance = present({ ...order, payment_display: null });
  assert.equal(balance.statusText, '已取消');
  const fs = require('node:fs');
  for (const file of ['pages/orders/index.wxml', 'pages/order-detail/index.wxml']) {
    const template = fs.readFileSync(path.join(root, file), 'utf8');
    assert.match(template, /!\w+\.directTest && \(\w+\.partialRefund/);
  }
});

test('staff pending presentation hides selection and uses the current deadline, including after rejection', () => {
  const env = environment();
  const { present } = require(path.join(root, 'core/orders'));
  const order = { status: 'PENDING', payment_status: 'PAID', dispatch_route: 'STAFF', requires_boss_selection: true,
    allowed_actions: ['SELECT_PROVIDER', 'CANCEL'], auto_cancel_at: '2026-10-27T04:30:00Z', service: { name: '服务' } };
  const first = present(order);
  assert.equal(first.statusText, '等待客服安排');
  assert.equal(first.providerDisplay, '等待客服指定陪玩');
  assert.equal(first.canSelectProvider, false);
  assert.match(first.fulfillment, /未进入公开接单池/);
  assert.match(first.deadlineDescription, /2026-10-27 12:30/);
  const rejected = present({ ...order, auto_cancel_at: '2026-10-27T05:00:00Z' });
  assert.match(rejected.deadlineDescription, /2026-10-27 13:00/);
  const doublePending = present({ ...order, required_team_size: 2 });
  assert.equal(doublePending.providerDisplay, '等待客服指定两名陪玩');
  assert.match(doublePending.fulfillment, /等待客服指定两名陪玩/);
  const assigned = present({ ...order, status: 'GRABBED', provider_name_snapshot: '松子' });
  assert.equal(assigned.statusText, '已接单');
  assert.equal(assigned.providerDisplay, '松子');
  const doubleAssigned = present({ ...order, status: 'GRABBED', required_team_size: 2, provider_name_snapshot: '松子', participants: [{ nickname: '松子' }, { nickname: '橙子' }] });
  assert.equal(doubleAssigned.providerDisplay, '松子、橙子');
  assert.equal(present({ ...order, dispatch_route: 'PUBLIC' }).canSelectProvider, true);
  assert.match(present({ ...order, dispatch_route: 'PUBLIC' }).deadlineDescription, /2026-10-27 12:30/);
  assert.equal(present({ ...order, auto_cancel_at: null }).deadlineDescription, '');
  assert.equal(present({ ...order, status: 'GRABBED' }).deadlineDescription, '');
});

test('unknown payment is restored with its original route and request ID across edits, re-entry and account switches', async () => {
  let saved, fail = true;
  const env = environment({ limit: 1, respond: value => {
    if (value.url.includes('/orders/quote/')) return { amount: 120, wallet_balance: 1000, balance_sufficient: true, pending_timeout_minutes: 30 };
    if (value.url.includes('/orders/create/')) {
      if (fail) { saved = structuredClone(value.data); return new Error('response lost'); }
      assert.deepEqual(value.data, saved);
      return { statusCode: 409, data: { code: 409, errorCode: 'FUND_ALREADY_PROCESSED', fundResult: { code: 0, data: { id: 55 } } } };
    }
  } });
  global.wx.showModal = options => { if (options.success) options.success({ confirm: true }); };
  const setup = page => { page.setData({ service: { name: '测试套餐', billing_provider_count: 1 }, selectionLimit: 1, nickname: '玩家', region: '一区', quote: { amount: 120, pending_timeout_minutes: 30 } }); page.refreshQuote = () => {}; };
  const first = env.page('checkout', { service_id: '91' }); setup(first);
  first.selectDispatchRoute({ currentTarget: { dataset: { route: 'STAFF' } } });
  await first.submit(); assert.equal(first.data.pendingOrder, true); assert.equal(saved.dispatch_route, 'STAFF');
  first.selectDispatchRoute({ currentTarget: { dataset: { route: 'PUBLIC' } } });
  await first.submit(); assert.equal(env.calls.filter(call => call.url.includes('/orders/create/')).length, 1);
  first.onUnload();
  const reopened = env.page('checkout', { service_id: '999' }); setup(reopened);
  assert.ok(reopened.refreshPending()); await reopened.submit();
  assert.equal(env.calls.filter(call => call.url.includes('/orders/create/')).length, 1);
  env.login('2'); assert.equal(reopened.refreshPending(), null);
  env.login('1'); assert.ok(reopened.refreshPending()); fail = false;
  await reopened.recoverPending();
  assert.equal(env.calls.filter(call => call.url.includes('/orders/create/')).length, 2);
  assert.match(env.navigation.at(-1).url, /order-detail\/index\?id=55/);
  assert.equal(reopened.refreshPending(), null); reopened.onUnload();
});

test('legacy payload and ID are recovered unchanged; multiple legacy intents are processed separately', async () => {
  let replay;
  const env = environment({ respond: value => {
    if (value.url.includes('/orders/create/')) { replay = value.data; return { id: 66 }; }
  } });
  const body = { expected_amount: 100, service_id: 3 };
  const key = 'xa:native:fund:1:/orders/create/:' + JSON.stringify(body);
  env.storage.set(key, 'native-old-request');
  const page = env.page('checkout', { service_id: '5' });
  assert.equal(page.refreshPending().requestId, 'native-old-request'); await page.recoverPending();
  assert.deepEqual(replay, { ...body, client_request_id: 'native-old-request' });
  assert.equal(env.storage.has(key), false); assert.equal(page.refreshPending(), null);
  env.storage.set(key, 'native-old-request');
  env.storage.set('xa:native:fund:1:/orders/create/:' + JSON.stringify({ expected_amount: 200, service_id: 4 }), 'native-other-request');
  assert.equal(page.refreshPending().requestId, 'native-old-request');
  await page.recoverPending(); assert.equal(page.refreshPending().requestId, 'native-other-request');
  await page.recoverPending(); assert.equal(page.refreshPending(), null);
  page.onUnload();
});

test('resolve safely seals a pending request or opens its committed order using the exact saved payload', async () => {
  let resolution = 'SEALED';
  const env = environment({ respond: value => {
    if (value.url.includes('/orders/create/resolve/')) {
      assert.equal(value.data.client_request_id, 'native-previous');
      assert.deepEqual(value.data.payload, { service_id: 2, expected_amount: 100, client_request_id: 'native-previous' });
      return resolution === 'SEALED' ? { status: 'SEALED' } : { status: 'COMMITTED', fundResult: { code: 0, data: { id: 99 } } };
    }
  } });
  global.wx.showModal = options => options.success({ confirm: true });
  const page = env.page('checkout', { service_id: '91' });
  const intent = { requestId: 'native-previous', payload: { service_id: 2, expected_amount: 100 }, attempted: true };
  const legacy = 'xa:native:fund:1:/orders/create/:' + JSON.stringify(intent.payload) + ':request:native-previous';
  env.storage.set('xa:native:order-intent:v1:1', intent); env.storage.set(legacy, intent.requestId);
  await page.resolvePending(); assert.equal(page.refreshPending(), null); assert.equal(env.storage.has(legacy), false);
  env.storage.set('xa:native:order-intent:v1:1', intent); resolution = 'COMMITTED';
  await page.resolvePending(); assert.equal(page.refreshPending(), null);
  assert.match(env.navigation.at(-1).url, /order-detail\/index\?id=99/); page.onUnload();
});

test('resolve conflict or unavailable receipt keeps the pending request and does not submit a new order', async () => {
  const env = environment({ respond: value => value.url.includes('/orders/create/resolve/') ? { statusCode: 200, data: { code: 409, errorCode: 'REQUEST_ID_CONFLICT', msg: '请求编号冲突' } } : undefined });
  global.wx.showModal = options => { if (options.success) options.success({ confirm: true }); };
  const page = env.page('checkout', { service_id: '91' });
  env.storage.set('xa:native:order-intent:v1:1', { requestId: 'native-previous', payload: { service_id: 2 }, attempted: true, blocked: true });
  await page.resolvePending(); assert.equal(page.data.pendingBlocked, true);
  assert.equal(env.calls.filter(call => call.url.endsWith('/orders/create/')).length, 0); page.onUnload();
});

test('resolution locks before confirmation and delayed cleanup preserves a replacement intent', async () => {
  let confirm, release;
  const env = environment({ respond: value => value.url.includes('/orders/create/resolve/') ? new Promise(resolve => { release = resolve; }) : undefined });
  let modalCount = 0;
  global.wx.showModal = options => { modalCount++; confirm = options.success; };
  const page = env.page('checkout', { service_id: '91' });
  const old = { requestId: 'native-previous', payload: { service_id: 2 }, attempted: true };
  const replacement = { requestId: 'native-new-order', payload: { service_id: 3 }, attempted: true };
  env.storage.set('xa:native:order-intent:v1:1', old);
  const first = page.resolvePending(); const second = page.resolvePending();
  assert.equal(modalCount, 1); assert.equal(page.data.submitting, true);
  confirm({ confirm: true }); await tick();
  env.storage.set('xa:native:order-intent:v1:1', replacement);
  release({ status: 'SEALED' }); await Promise.all([first, second]);
  assert.equal(page.refreshPending().requestId, replacement.requestId);
  assert.deepEqual(env.storage.get('xa:native:order-intent:v1:1'), replacement);
  page.onUnload();
});

test('late successful payment cannot clear an independently replaced intent', async () => {
  let release;
  const env = environment({ respond: value => value.url.includes('/orders/create/') ? new Promise(resolve => { release = resolve; }) : undefined });
  const page = env.page('checkout', { service_id: '91' });
  const old = { requestId: 'native-previous', payload: { service_id: 2 }, attempted: true };
  const replacement = { requestId: 'native-new-order', payload: { service_id: 3 }, attempted: true };
  env.storage.set('xa:native:order-intent:v1:1', old);
  const pending = page.recoverPending(); await tick();
  env.storage.set('xa:native:order-intent:v1:1', replacement);
  release({ id: 45 }); await pending;
  assert.equal(page.refreshPending().requestId, replacement.requestId);
  assert.deepEqual(env.storage.get('xa:native:order-intent:v1:1'), replacement);
  page.onUnload();
});

test('late rejected recovery cannot overwrite a replacement intent', async () => {
  let release;
  const env = environment({ respond: value => value.url.includes('/orders/create/') ? new Promise(resolve => { release = resolve; }) : undefined });
  global.wx.showModal = options => { if (options.success) options.success({ confirm: true }); };
  const page = env.page('checkout', { service_id: '91' });
  const old = { requestId: 'native-previous', payload: { service_id: 2 }, attempted: true };
  const replacement = { requestId: 'native-new-order', payload: { service_id: 3 }, attempted: true };
  env.storage.set('xa:native:order-intent:v1:1', old);
  const pending = page.recoverPending(); await tick();
  env.storage.set('xa:native:order-intent:v1:1', replacement);
  release({ statusCode: 200, data: { code: 400, msg: '订单不可用' } }); await pending;
  assert.deepEqual(env.storage.get('xa:native:order-intent:v1:1'), replacement);
  assert.equal(page.refreshPending().requestId, replacement.requestId); page.onUnload();
});

test('two checkout instances preserve a new intent when an old response arrives after same-account relogin', async () => {
  for (const kind of ['success', 'failure', 'resolve']) {
    let releaseOld, resolutionCalls = 0;
    const env = environment({ limit: 1, respond: value => {
      if (value.url.includes('/orders/quote/')) return { amount: 120, wallet_balance: 1000, balance_sufficient: true, pending_timeout_minutes: 30 };
      if (value.url.includes('/orders/create/resolve/')) {
        resolutionCalls++;
        if (kind === 'resolve' && resolutionCalls === 1) return new Promise(resolve => { releaseOld = resolve; });
        return kind === 'success' ? { status: 'COMMITTED', fundResult: { code: 0, data: { id: 45 } } } : { status: 'SEALED' };
      }
      if (value.url.includes('/orders/create/')) {
        if (value.data.service_id === 2) return new Promise(resolve => { releaseOld = resolve; });
        return new Error('new order response lost');
      }
    } });
    global.wx.showModal = options => { if (options.success) options.success({ confirm: true }); };
    const oldPage = env.page('checkout', { service_id: '2' });
    env.storage.set('xa:native:order-intent:v1:1', { requestId: 'native-old-order', payload: { service_id: 2 }, attempted: true });
    const oldOperation = kind === 'resolve' ? oldPage.resolvePending() : oldPage.recoverPending();
    await tick();
    env.login('1');
    const newPage = env.page('checkout', { service_id: '91' });
    await newPage.resolvePending();
    assert.equal(newPage.refreshPending(), null, kind);
    newPage.setData({ service: { name: '测试套餐', billing_provider_count: 1 }, selectionLimit: 1, nickname: '玩家', region: '一区', quote: { amount: 120, pending_timeout_minutes: 30 } });
    await newPage.submit(); const newIntent = structuredClone(env.storage.get('xa:native:order-intent:v1:1'));
    assert.equal(newPage.data.pendingOrder, true, kind);
    const navigationBefore = env.navigation.length;
    if (kind === 'success') releaseOld({ id: 45 });
    else if (kind === 'failure') releaseOld({ statusCode: 200, data: { code: 400, msg: '订单不可用' } });
    else releaseOld({ status: 'SEALED' });
    await oldOperation;
    assert.deepEqual(env.storage.get('xa:native:order-intent:v1:1'), newIntent, kind);
    assert.equal(env.navigation.length, navigationBefore, kind);
    oldPage.onUnload(); newPage.onUnload();
  }
});

test('a first definite rejection clears the intent, but a rejection after an uncertain attempt stays blocked', async () => {
  let offline = false;
  const env = environment({ limit: 1, respond: value => {
    if (value.url.includes('/orders/quote/')) return { amount: 120, wallet_balance: 1000, balance_sufficient: true, pending_timeout_minutes: 30 };
    if (value.url.includes('/orders/traffic-status/')) return { self_order_enabled: true };
    if (value.url.includes('/orders/create/')) return offline ? new Error('offline') : { statusCode: 200, data: { code: 400, msg: '不能下单' } };
  } });
  global.wx.showModal = options => { if (options.success) options.success({ confirm: true }); };
  const page = env.page('checkout', { service_id: '91' });
  page.setData({ service: { name: '测试套餐', billing_provider_count: 1 }, selectionLimit: 1, nickname: '玩家', region: '一区', quote: { amount: 120, pending_timeout_minutes: 30 } });
  await page.submit(); assert.equal(page.refreshPending(), null);
  assert.equal(page.data.quote, null);
  page._visible = true; await page.refreshAvailability();
  assert.equal(page.data.quote.amount, 120);
  offline = true; await page.submit(); assert.equal(page.data.pendingOrder, true);
  offline = false; await page.recoverPending(); assert.equal(page.data.pendingBlocked, true);
  assert.match(page.data.pendingError, /联系客服/); page.onUnload();
});

test('switching accounts during payment confirmation cannot submit the previous account payload', async () => {
  const env = environment({ limit: 1, respond: value => value.url.includes('/orders/quote/') ? { amount: 120, wallet_balance: 1000, balance_sufficient: true, pending_timeout_minutes: 30 } : undefined });
  const page = env.page('checkout', { service_id: '91' });
  page.setData({ service: { name: '测试套餐', billing_provider_count: 1 }, selectionLimit: 1, nickname: '玩家', region: '一区', quote: { amount: 120, pending_timeout_minutes: 30 } });
  global.wx.showModal = options => { env.login('2'); options.success({ confirm: true }); };
  await page.submit(); assert.equal(env.calls.filter(call => call.url.includes('/orders/create/')).length, 0);
  assert.equal(page.data.submitting, false); page.onUnload();
});
