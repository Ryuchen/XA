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
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key),
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    showToast: options => toasts.push(options.title), stopPullDownRefresh() {},
    navigateTo: value => navigation.push(value), navigateBack: value => navigation.push({ back: true, ...value }),
    switchTab: value => navigation.push(value), showModal() {},
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
  return { state, calls, navigation, toasts, auth, login, page };
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
