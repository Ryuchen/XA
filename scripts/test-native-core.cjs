const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const tick = () => new Promise(resolve => setImmediate(resolve));

function environment(respond = () => ({ code: 0, data: {} })) {
  for (const key of Object.keys(require.cache)) if (key.startsWith(root + path.sep)) delete require.cache[key];
  const storage = new Map(), calls = [], navigation = [];
  global.wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value), removeStorageSync: key => storage.delete(key),
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    getSystemInfoSync: () => ({ windowWidth: 375 }),
    getFileSystemManager: () => ({ getFileInfo: options => options.success({ size: 1000 }) }),
    showToast() {}, stopPullDownRefresh() {}, setTabBarBadge() {}, removeTabBarBadge() {},
    navigateTo(options) { navigation.push(options.url); if (options.complete) options.complete(); },
    navigateBack() { navigation.push('back'); }, switchTab(options) { navigation.push(options.url); },
    redirectTo(options) { navigation.push(options.url); },
    showModal(options) { options.success({ confirm: true }); },
    login(options) { options.success({ code: 'fresh-' + calls.length }); },
    request(options) { calls.push(options); Promise.resolve().then(() => respond(options)).then(response => {
      if (response instanceof Error) options.fail(response);
      else options.success({ statusCode: 200, ...(response && 'statusCode' in response ? response : { data: response }) });
    }).catch(options.fail); },
  };
  const auth = require(path.join(root, 'core/auth'));
  const api = require(path.join(root, 'core/api'));
  function login(id = '1') { auth.setSession({ token: 'old-' + id, refresh: 'refresh-' + id, user: { id, role: 'customer', nickname: '老板' + id } }); }
  function page(name, options = {}) {
    let definition;
    global.Page = value => { definition = value; };
    require(path.join(root, 'pages', name, 'index.js'));
    const instance = { ...definition, data: structuredClone(definition.data), setData(patch) {
      for (const [key, value] of Object.entries(patch)) {
        const pieces = key.replace(/\[(\d+)\]/g, '.$1').split('.');
        let target = this.data;
        for (const piece of pieces.slice(0, -1)) target = target[piece] || (target[piece] = {});
        target[pieces.at(-1)] = value;
      }
    } };
    for (const [key, value] of Object.entries(instance)) if (typeof value === 'function') instance[key] = value.bind(instance);
    instance.onLoad && instance.onLoad(options);
    return instance;
  }
  return { storage, calls, navigation, auth, api, login, page };
}

const wecomCard = { id: 1, name: '测试客服', wecom_corp_id: 'wwtest', wecom_service_url: 'https://work.weixin.qq.com/kfid/kfc_test' };
test('guest support resolves the public card and opens exactly one native customer service chat', async () => {
  const env = environment(() => ({ code: 0, data: wecomCard }));
  const page = env.page('support'); await page.load();
  assert.ok(env.calls[0].url.endsWith('/support/public-contact/'));
  assert.equal(env.calls[0].header.Authorization, undefined);
  const sent = []; wx.openCustomerServiceChat = options => sent.push(options);
  page.contact(); page.contact(); assert.equal(sent.length, 1);
  assert.equal(sent[0].corpId, wecomCard.wecom_corp_id);
  assert.equal(sent[0].extInfo.url, wecomCard.wecom_service_url);
  assert.equal(sent[0].sendMessagePath, 'pages/home/index');
  sent[0].complete(); assert.equal(page.data.opening, false);
});
test('order support preserves its reference and order context, and can copy it for the operator', async () => {
  const env = environment(() => ({ code: 0, data: { ...wecomCard, customer_reference: 'XA100001', order_no: 'ORDER-1' } }));
  env.login(); const page = env.page('support', { order_id: '25' }); await page.load();
  assert.ok(env.calls[0].url.endsWith('/support/contact-card/?order_id=25'));
  let options, copied; wx.openCustomerServiceChat = value => { options = value; }; wx.setClipboardData = value => { copied = value.data; };
  page.contact(); assert.equal(options.sendMessagePath, 'pages/order-detail/index?id=25');
  assert.match(options.sendMessageTitle, /XA100001.*ORDER-1/);
  page.copyReference(); assert.match(copied, /XA100001.*ORDER-1/);
  page.onUnload(); options.complete();
});
test('recharge support uses the wallet card while old WeChat and synchronous failures stay usable', async () => {
  const env = environment(() => ({ code: 0, data: wecomCard }));
  const page = env.page('support', { scene: 'recharge' }); await page.load();
  page.contact(); assert.match(page.data.contactError, /更新微信/); assert.equal(page.data.opening, false);
  let options; wx.openCustomerServiceChat = value => { options = value; }; page.contact();
  assert.equal(options.sendMessagePath, 'pages/wallet/index'); assert.match(options.sendMessageTitle, /充值/);
  options.fail({ errMsg: 'openCustomerServiceChat:fail cancel' }); assert.equal(page.data.contactError, '');
  wx.openCustomerServiceChat = () => { throw { errMsg: 'invalid corpId' }; };
  page.contact(); assert.equal(page.data.opening, false); assert.match(page.data.contactError, /平台客服/);
});
test('support does not retain a stale card after a failed refresh or open chat during loading', async () => {
  let failed = false;
  const env = environment(() => failed ? new Error('offline') : { code: 0, data: wecomCard });
  const page = env.page('support'); await page.load(); let openings = 0; wx.openCustomerServiceChat = () => { openings++; };
  failed = true; const refresh = page.load(); page.contact(); await refresh;
  assert.equal(page.data.card, null); assert.ok(page.data.error); assert.equal(openings, 0);
});
test('support clears the old account reference before opening for a switched session', async () => {
  const env = environment(() => ({ code: 0, data: { ...wecomCard, customer_reference: 'XA100001' } }));
  env.login(); const page = env.page('support'); await page.load();
  env.login('other'); let openings = 0; wx.openCustomerServiceChat = () => { openings++; };
  page.contact(); assert.equal(openings, 0); assert.equal(page.data.card, null);
  await tick(); page.onUnload();
});

test('customer service failures remain visible after returning to the page, until an explicit retry', async () => {
  const env = environment(() => ({ code: 0, data: wecomCard }));
  const page = env.page('support'); await page.load();
  let options; wx.openCustomerServiceChat = value => { options = value; };
  page.contact(); options.fail({ errCode: 12, errMsg: 'openCustomerServiceChat:fail invalid corpId' }); options.complete();
  const detail = page.data.contactDetail;
  assert.match(detail, /12.*invalid corpId/);
  await page.onShow();
  assert.match(page.data.contactError, /平台客服/); assert.equal(page.data.contactDetail, detail);
  page.contact(); assert.equal(page.data.contactError, ''); assert.equal(page.data.contactDetail, '');
  options.fail({ errMsg: 'openCustomerServiceChat:fail invalid corpId' });
  await page.onPullDownRefresh(); assert.equal(page.data.contactError, ''); assert.equal(page.data.contactDetail, '');
});

test('release customer service failures hide SDK diagnostics and a switched account clears previous failures', async () => {
  const env = environment(() => ({ code: 0, data: wecomCard }));
  wx.getAccountInfoSync = () => ({ miniProgram: { envVersion: 'release' } });
  const page = env.page('support'); await page.load();
  wx.openCustomerServiceChat = options => options.fail({ errMsg: 'openCustomerServiceChat:fail invalid corpId' });
  page.contact(); assert.match(page.data.contactError, /平台客服/); assert.equal(page.data.contactDetail, '');
  env.login('other'); await page.onShow(); assert.equal(page.data.contactError, '');
});

test('a failed support menu navigation reports the native cause instead of silently staying on mine', () => {
  const env = environment(); const page = env.page('mine');
  let dialog;
  wx.navigateTo = options => options.fail({ errMsg: 'navigateTo:fail page not found' });
  wx.showModal = options => { dialog = options; };
  page.open({ currentTarget: { dataset: { page: 'support' } } });
  assert.equal(dialog.title, '页面打开失败'); assert.match(dialog.content, /page not found/);
  wx.getAccountInfoSync = () => ({ miniProgram: { envVersion: 'release' } });
  page.open({ currentTarget: { dataset: { page: 'support' } } });
  assert.doesNotMatch(dialog.content, /page not found/); page.onUnload();
});

test('money boundary matches ledger units rather than yuan cents', () => {
  environment(); const { money } = require(path.join(root, 'core/ui'));
  assert.equal(money(125), '12.5'); assert.equal(money(10000), '1000'); assert.equal(money(-125), '-12.5');
});

test('large catalogs stay below the view bridge limit while paging, filtering and switching accounts', async () => {
  const rows = Array.from({ length: 500 }, (_, i) => ({ id: i + 1, name: i % 2 ? '银行套餐' : '常规套餐', price: 1880,
    price_unit: '份', platform: 'PC', rules: '履约规则'.repeat(1200), game_category: 1 }));
  const env = environment(options => ({ code: 0, data: options.url.endsWith('/orders/services/') ? rows : [] })); env.login();
  const page = env.page('catalog', { tab: 'regular' }), apply = page.setData; const batches = [];
  page.setData = patch => { batches.push(Buffer.byteLength(JSON.stringify(patch))); apply(patch); };
  await page.load();
  assert.equal(page.data.total, 500); assert.equal(page.data.displayed.length, 20);
  while (page.data.displayed.length < page.data.total) page.more();
  assert.equal(new Set(page.data.displayed.map(row => row.id)).size, 500);
  assert.ok(batches.every(bytes => bytes < 64 * 1024), 'full rules or all items must not cross the view bridge');
  page.search({ detail: { value: '银行' } });
  assert.equal(page.data.total, 250); assert.equal(page.data.displayed.length, 20);
  assert.ok(page.data.displayed.every(row => row.name === '银行套餐'));
  env.login('other');
  assert.equal(page.data.total, 0); assert.deepEqual(page.data.displayed, []);
  page.more(); assert.deepEqual(page.data.displayed, [], 'old-account rows cannot reappear on scroll');
  page.onUnload();
});

test('catalog groups explicit families and filters their prices without treating addons as packages', async () => {
  const base = { catalog_family: '物资银行单', game_category: 1, gameplay: 8, platform: 'PC', security_level: 'TOP_SECRET', price_unit: '份' };
  const rows = [{ ...base, id: 1, name: '银行一档', price: 2880 }, { ...base, id: 2, name: '银行二档', price: 4880 },
    { ...base, id: 3, name: '手游一档', platform: 'MOBILE', price: 1880 }, { ...base, id: 4, name: '银行加仓', is_addon: true, price: 880 }];
  const env = environment(options => ({ code: 0, data: options.url.endsWith('/orders/services/') ? rows : [] })); env.login();
  const page = env.page('catalog', { tab: 'regular' }); await page.load();
  assert.equal(page.data.total, 1); assert.equal(page.data.displayed[0].id, 3); assert.equal(page.data.displayed[0].variantCount, 3);
  assert.equal(page.data.displayed[0].displayPrice, '188'); assert.equal(page.data.displayed[0].name, '物资银行单');
  page.choose({ currentTarget: { dataset: { field: 'platform', id: 'PC' } } });
  assert.equal(page.data.displayed[0].displayPrice, '288'); assert.equal(page.data.displayed[0].variantCount, 2);
  page.search({ detail: { value: '加仓' } }); assert.equal(page.data.total, 1);
  const groups = require(path.join(root, 'core/catalog')).groupServices([...rows, { ...rows[0], id: 5, game_category: 2 }]);
  assert.equal(groups.length, 2, 'same labels from different games must not merge');
  page.onUnload();
});

test('companion price sorting retains checkout service and combines voice/search filters', async () => {
  const rows = [
    { id: 11, nickname: '松子', pricePerHour: 205, voice_card_url: '/media/voice.wav', city: '杭州' },
    { id: 12, nickname: '橙子', pricePerHour: 500, voice_card_url: '', city: '杭州' },
    { id: 13, nickname: '梨子', pricePerHour: 0, voice_card_url: '/media/pear.mp3', city: '上海' },
  ];
  const env = environment(options => ({ code: 0, data: options.url.includes('/users/escorts/') ? rows
    : options.url.includes('/orders/services/') ? { id: 91, name: '独立套餐', price: 9900 } : [] }));
  env.login();
  const page = env.page('companions', { service_id: '91' });
  await page.load();
  assert.equal(page.data.escorts[0].hourlyPriceText, '20.5');
  assert.equal(page.data.escorts[2].hasHourlyPrice, false);
  assert.ok(env.calls.some(call => call.url.endsWith('/users/escorts/?service_item=91')));
  page.chooseSort({ detail: { value: '2' } });
  assert.deepEqual(page.data.visible.map(item => item.id), [12, 11, 13]);
  page.chooseSort({ detail: { value: '1' } });
  assert.deepEqual(page.data.visible.map(item => item.id), [11, 12, 13]);
  page.chooseSort({ detail: { value: '0' } });
  assert.deepEqual(page.data.visible.map(item => item.id), [11, 12, 13]);
  page.toggleVoice(); page.search({ detail: { value: '杭州' } });
  assert.deepEqual(page.data.visible.map(item => item.id), [11]);
  page.openDetail({ currentTarget: { dataset: { id: 11 } } });
  assert.equal(env.navigation.at(-1), '/pages/companion-detail/index?id=11&service_id=91&provider_gender=ANY');
  page.resetFilters();
  assert.equal(page.data.serviceId, 91); assert.equal(page.data.keyword, '');
  assert.equal(page.data.voiceOnly, false); assert.equal(page.data.sort, 0);
  let stopped = 0; page._audio = { stop() { stopped++; } }; page.setData({ playingId: 11 });
  page.search({ detail: { value: '上海' } });
  assert.deepEqual(page.data.visible.map(item => item.id), [13]);
  assert.equal(page.data.playingId, 0); assert.equal(stopped, 1);
  page.resetFilters(); page.toggleVoice();
  assert.deepEqual(page.data.visible.map(item => item.id), [11, 13]);
  rows.push({ id: 14, nickname: '零时价', pricePerHour: 0, hasReferenceHourlyPrice: true });
  page.resetFilters(); await page.load();
  page.chooseSort({ detail: { value: '1' } });
  assert.deepEqual(page.data.visible.map(item => item.id), [14, 11, 12, 13]);
  page.chooseSort({ detail: { value: '2' } });
  assert.deepEqual(page.data.visible.map(item => item.id), [12, 11, 14, 13]);
});

test('service picker requests current service ranking and preserves it through search and checkout', async () => {
  const rows = [
    { id: 31, nickname: '小满', level_name: '初级', picker_group: 'recommended', can_order: true },
    { id: 12, nickname: '小禾', level_name: '高级', picker_group: 'popular', can_order: true },
    { id: 23, nickname: '听风', level_name: '高级', picker_group: '', can_order: true },
  ];
  const env = environment(options => ({code:0,data:options.url.includes('/users/escorts/') ? rows
    : options.url.includes('/orders/services/') ? {id:91,name:'当前服务'} : []}));
  env.login(); const page = env.page('companion-picker', {service_id:'91'}); await page.load();
  assert.ok(env.calls.some(call => call.url.endsWith('/users/escorts/?service_item=91&picker=1')));
  assert.deepEqual(page.data.visible.map(item => item.id), [31,12,23]);
  page.search({detail:{value:'小'}}); assert.deepEqual(page.data.visible.map(item => item.id), [31,12]);
  page.openDetail({currentTarget:{dataset:{id:12}}});
  assert.equal(env.navigation.at(-1), '/pages/companion-detail/index?id=12&service_id=91&provider_gender=ANY&picker_draft=1&selected_provider_ids=');
  page.onUnload();
});

test('service picker refresh replaces stale people and rejects selection during loading or failure', async () => {
  let rows = [{id:1,nickname:'旧陪玩',can_order:true}], failed = false;
  const env = environment(options => options.url.includes('/users/escorts/') && failed ? new Error('加载失败')
    : {code:0,data:options.url.includes('/users/escorts/') ? rows : []});
  env.login(); const page = env.page('companion-picker', {service_id:'91'}); await page.load();
  rows = [{id:2,nickname:'当前陪玩',can_order:true}]; const refreshing = page.load();
  page.openDetail({currentTarget:{dataset:{id:1}}}); assert.equal(env.navigation.length, 0);
  await refreshing; assert.deepEqual(page.data.visible.map(item => item.id), [2]);
  failed = true; await page.load(); assert.equal(page.data.visible.length, 0); assert.ok(page.data.error);
  page.openDetail({currentTarget:{dataset:{id:2}}}); assert.equal(env.navigation.length, 0);
  failed = false; await page.load(); assert.deepEqual(page.data.visible.map(item => item.id), [2]);
  page.onUnload();
});

test('checkout selection confirms a detail-page draft before filling the checkout provider and quote', async () => {
  const rows = [{id:12,nickname:'小禾',gender:'FEMALE',can_order:true}];
  const env = environment(options => ({code:0,data:options.url.includes('/users/escorts/12/') ? {...rows[0],nickname:'详情最新昵称',monthly_order_amount:205}
    : options.url.includes('/users/escorts/') ? rows
    : options.url.includes('/orders/services/') ? {id:91,name:'单陪套餐',service_type:'COMPANION',billing_provider_count:1}
    : options.url.includes('/orders/quote/') ? {original_amount:9000,amount:9000,wallet_balance:100000,balance_sufficient:true} : []}));
  env.login();
  const checkout = env.page('checkout', {service_id:'91',provider_gender:'FEMALE'});
  checkout.setData({service:{id:91,service_type:'COMPANION'}});
  let navigation, back;
  wx.navigateTo = options => { navigation = options; };
  wx.navigateBack = options => { back = options; };
  checkout.chooseProvider();
  const pickerNavigation = navigation;
  assert.equal(pickerNavigation.url, '/pages/companion-picker/index?service_id=91&from_checkout=1&provider_gender=FEMALE');
  const page = env.page('companion-picker', {service_id:'91',from_checkout:'1',provider_gender:'FEMALE'});
  const selected = [];
  page.getOpenerEventChannel = () => ({emit:(event,provider)=>{
    selected.push({event,provider}); pickerNavigation.events[event](provider);
  }});
  await page.load();
  assert.ok(env.calls.some(call => call.url.endsWith('/users/escorts/?service_item=91&picker=1&gender=FEMALE')));
  page.chooseGender({detail:{value:1}}); assert.equal(page.data.genderIndex, 2);
  page.openDetail({currentTarget:{dataset:{id:12}}});
  const detailNavigation = navigation;
  assert.equal(detailNavigation.url, '/pages/companion-detail/index?id=12&service_id=91&provider_gender=FEMALE&from_checkout=1&picker_draft=1&selected_provider_ids=');
  assert.equal(selected.length, 0, 'opening details must not select yet');
  const detail = env.page('companion-detail', {id:'12',service_id:'91',provider_gender:'FEMALE',from_checkout:'1',picker_draft:'1'});
  detail.getOpenerEventChannel = () => ({emit:(event,provider)=>detailNavigation.events[event](provider)});
  await detail.load(); detail.select(); detail.select();
  assert.equal(selected.length, 0, 'detail selection updates only the picker draft');
  assert.deepEqual(page.data.selectedProviderIds, [12]); assert.equal(checkout.data.provider, null);
  assert.equal(back.delta || 1, 1);
  await page.confirmSelection();
  assert.equal(selected.length, 1); assert.equal(selected[0].event, 'providersSelected'); assert.equal(selected[0].provider[0].id, 12);
  assert.equal(selected[0].provider[0].nickname, '详情最新昵称'); assert.equal(selected[0].provider[0].monthlyAmountText, '20.5');
  assert.equal(back.delta || 1, 1);
  assert.equal(checkout.data.provider.nickname, '详情最新昵称');
  assert.equal(checkout._providerId, 12); assert.equal(checkout.payload().provider_id, 12);
  checkout.onShow(); await tick();
  assert.equal(checkout.data.provider.nickname, '详情最新昵称', 'returning to checkout must preserve the selected name');
  assert.equal(env.calls.filter(call => call.url.includes('/orders/quote/')).at(-1).data.provider_id, 12);
  checkout.onHide();
  env.login('other'); detailNavigation.events.providerSelected(rows[0]); assert.equal(selected.length, 1, 'old session events must be ignored');
  assert.equal(checkout.data.provider, null); assert.equal(checkout._providerId, 0);
  detail.onUnload(); page.onUnload(); checkout.onUnload();
});

test('checkout keeps selection on cancel, replaces it on selection, and clears it for platform or account change', async () => {
  const env = environment(options => ({code:0,data:options.url.includes('/orders/quote/')
    ? {original_amount:9000,amount:9000,wallet_balance:100000,balance_sufficient:true} : []}));
  env.login();
  const checkout = env.page('checkout', {service_id:'91'});
  checkout.setData({service:{id:91,service_type:'COMPANION'}});
  let selectionNavigation;
  wx.navigateTo = options => { selectionNavigation = options; };
  const first = {id:12,nickname:'第一位陪玩',gender:'FEMALE',can_order:true,avatar:'/media/first.png'};
  const second = {id:13,nickname:'重新选择的陪玩',gender:'MALE',can_order:true};
  checkout.chooseProvider(); selectionNavigation.events.providersSelected([first]); await tick();
  assert.equal(checkout.data.provider.nickname, first.nickname); assert.equal(checkout.payload().provider_id, 12);
  assert.match(checkout.data.provider.avatarUrl, /\/media\/first\.png$/);

  checkout.chooseProvider();
  wx.navigateBack(); checkout.onShow(); await tick(); checkout.onHide();
  assert.equal(checkout.data.provider.nickname, first.nickname, 'returning without confirming a different person must preserve the selection');
  assert.equal(checkout.payload().provider_id, 12);

  checkout.chooseProvider(); selectionNavigation.events.providersSelected([second]); await tick();
  assert.equal(checkout.data.provider.nickname, second.nickname); assert.equal(checkout._providerId, 13);
  assert.equal(checkout.payload().provider_id, 13);
  assert.equal(env.calls.filter(call => call.url.includes('/orders/quote/')).at(-1).data.provider_id, 13);

  checkout.chooseProvider();
  const picker = env.page('companion-picker', {service_id:'91',from_checkout:'1'});
  picker.getOpenerEventChannel = () => ({emit:(event,provider)=>selectionNavigation.events[event](provider)});
  await picker.load(); await picker.platform(); await tick();
  assert.equal(checkout.data.provider, null); assert.equal(checkout._providerId, 0);
  assert.equal(checkout.payload().provider_id, undefined);
  assert.equal(env.calls.filter(call => call.url.includes('/orders/quote/')).at(-1).data.provider_id, undefined);

  checkout.chooseProvider(); selectionNavigation.events.providersSelected([first]); await tick();
  checkout.chooseProvider(); const staleNavigation = selectionNavigation;
  env.login('other');
  assert.equal(checkout.data.provider, null); assert.equal(checkout._providerId, 0);
  staleNavigation.events.providersSelected([second]); await tick();
  assert.equal(checkout.data.provider, null, 'a previous account selection event must not fill the new account checkout');
  assert.equal(checkout._providerId, 0); assert.equal(checkout.payload().provider_id, undefined);
  picker.onUnload(); checkout.onUnload();
});

test('directory cards can open unavailable profiles; details block selection but permit gifts', async () => {
  const provider = {id:12,nickname:'听风',can_order:false,order_unavailable_reason:'陪玩当前处于离线状态'};
  const env = environment(options => ({code:0,data:options.url.includes('/users/escorts/12/') ? provider : options.url.includes('/users/escorts/') ? [provider] : []}));
  env.login(); const directory = env.page('companions'); await directory.load(); directory.openDetail({currentTarget:{dataset:{id:12}}});
  assert.equal(env.navigation.at(-1), '/pages/companion-detail/index?id=12&provider_gender=ANY');
  const detail = env.page('companion-detail', {id:'12'}); await detail.load();
  detail.select(); assert.equal(env.navigation.length, 1);
  detail.gift(); assert.equal(env.navigation.at(-1), '/pages/gifts/index?provider_id=12');
  detail.onUnload(); directory.onUnload();
});

test('detail selection preserves service gender or filters the directory catalog to this provider', async () => {
  for (const options of [{id:'12',service_id:'91',provider_gender:'FEMALE'}, {id:'12'}]) {
    const env = environment(() => ({code:0,data:{id:12,nickname:'听风',can_order:true}})); env.login();
    const detail = env.page('companion-detail', options); await detail.load(); detail.select();
    if (options.service_id) assert.equal(env.navigation.at(-1), '/pages/checkout/index?service_id=91&provider_id=12&provider_gender=FEMALE');
    else { assert.equal(env.navigation.at(-1), '/pages/catalog/index'); assert.deepEqual(env.storage.get('xa:native:catalog-intent'), {provider_id:12,provider_name:'听风'}); }
    assert.ok(!detail.onShareAppMessage().path.includes('from_checkout')); detail.onUnload();
  }
});

test('detail refresh failures and account changes clear profiles and prevent stale actions', async () => {
  let failed = false;
  const env = environment(() => failed ? new Error('网络错误') : {code:0,data:{id:12,nickname:'听风',can_order:true}}); env.login();
  const detail = env.page('companion-detail', {id:'12'}); await detail.load();
  failed = true; const refresh = detail.load(); detail.select(); detail.gift(); await refresh;
  assert.equal(env.navigation.length, 0); assert.equal(detail.data.provider, null); assert.ok(detail.data.error);
  failed = false; await detail.load(); env.login('other'); detail.select(); detail.gift();
  assert.equal(detail.data.provider, null); assert.equal(env.navigation.length, 0); detail.onUnload();
});

test('selecting from companion details opens only that provider’s supported catalog services', async () => {
  const provider = {id:12,nickname:'听风',can_order:true,service_item_ids:[3]};
  const env = environment(options => ({code:0,data:options.url.includes('/users/escorts/12/') ? provider : options.url.includes('/users/escorts/') ? [provider]
    : options.url.endsWith('/orders/services/') ? [{id:3,name:'支持的服务',price:1250},{id:4,name:'其他服务',price:990}] : []})); env.login();
  const detail = env.page('companion-detail', {id:'12'}); await detail.load(); detail.select();
  const catalog = env.page('catalog'); catalog.onShow(); await tick();
  assert.equal(catalog.data.providerId,12); assert.equal(catalog.data.providerName,'听风');
  assert.deepEqual(catalog.data.displayed.map(row=>row.id),[3]);
  assert.equal(env.storage.has('xa:native:catalog-intent'),false);
  detail.onUnload(); catalog.onUnload();
});

test('plain catalog navigation preserves prepared intent and failed detail navigation can be retried', async () => {
  const env = environment(() => ({code:0,data:{id:12,nickname:'听风',can_order:true}})); env.login();
  env.storage.set('xa:native:catalog-intent',{provider_id:12});
  require(path.join(root,'core/ui')).go('/pages/catalog/index'); assert.deepEqual(env.storage.get('xa:native:catalog-intent'),{provider_id:12});
  const detail = env.page('companion-detail',{id:'12',service_id:'91'}); await detail.load();
  let attempts=0; wx.navigateTo = options => {attempts++;options.fail({errMsg:'failed'});}; detail.select(); detail.select();
  assert.equal(attempts,2); detail.onUnload();
});

test('detail voice playback stops on hide and unload and handles errors', async () => {
  const env = environment(() => ({code:0,data:{id:12,nickname:'听风',voice_card_url:'/media/voice.mp3'}})); env.login();
  let stopped=0,destroyed=0,ended,error;
  wx.createInnerAudioContext = () => ({stop(){stopped++;},destroy(){destroyed++;},play(){},onEnded(cb){ended=cb;},onError(cb){error=cb;}});
  const detail = env.page('companion-detail', {id:'12'}); await detail.load();
  detail.voice(); assert.equal(detail.data.playing,true); ended(); assert.equal(detail.data.playing,false);
  detail.voice(); error(); assert.equal(detail.data.playing,false);
  detail.voice(); detail.onHide(); assert.equal(detail.data.playing,false); assert.equal(stopped,1);
  detail.onUnload(); assert.equal(stopped,2); assert.equal(destroyed,1);
});

test('companion cards show monthly order amounts in XA coins and refresh them without review data', async () => {
  for (const route of ['companions', 'companion-picker']) {
    let amount = 12975;
    const env = environment(options => ({code:0,data:options.url.includes('/users/escorts/')
      ? [{id:1,nickname:'听风',monthly_order_amount:amount},{id:2,nickname:'小满',monthly_order_amount:0}] : []}));
    env.login(); const page = env.page(route, {service_id:'91'}); await page.load();
    assert.equal(page.data.visible[0].monthlyAmountText, '1297.5');
    assert.equal(page.data.visible[1].monthlyAmountText, '0');
    amount = 20500; await page.load(); assert.equal(page.data.visible[0].monthlyAmountText, '2050');
    page.onUnload();
  }
});

test('catalog defaults and resets to price order and publishes loaded results atomically', async () => {
  const rows = [
    { id: 1, name: 'A 高档', price: 10800, catalog_family: 'A' },
    { id: 2, name: 'B', price: 6800, catalog_family: 'B' },
    { id: 3, name: 'A 入门', price: 990, catalog_family: 'A' },
    { id: 4, name: 'C', price: 18800, catalog_family: 'C' },
  ];
  let finish;
  const env = environment(options => options.url.endsWith('/orders/services/')
    ? new Promise(resolve => { finish = () => resolve({ code: 0, data: rows }); }) : { code: 0, data: [] });
  env.login();
  const page = env.page('catalog', { tab: 'regular' }), apply = page.setData, patches = [];
  page.setData = patch => { patches.push(patch); apply(patch); };
  const initial = page.load(); await tick();
  assert.equal(page.data.loading, true); assert.equal(page.data.displayed.length, 0);
  finish(); await initial;
  assert.equal(page.data.sort, 'price'); assert.deepEqual(page.data.displayed.map(row => row.id), [3, 2, 4]);
  const complete = patches.filter(patch => patch.loading === false);
  assert.equal(complete.length, 1); assert.equal(complete[0].displayed.length, 3);
  const refresh = page.load(); await tick();
  assert.equal(page.data.loading, true); assert.equal(page.data.displayed.length, 3, 'refresh retains existing content');
  finish(); await refresh; assert.equal(page.data.loading, false);
  page.choose({ currentTarget: { dataset: { field: 'sort', id: 'default' } } });
  page.search({ detail: { value: 'C' } }); assert.equal(page.data.total, 1);
  page.reset(); assert.equal(page.data.sort, 'price'); assert.deepEqual(page.data.displayed.map(row => row.id), [3, 2, 4]);
  page.onUnload();
});

test('service tabs cover all catalog groups and translate existing homepage links', async () => {
  const codes=['experience','supplies','bank','extraction','mission','companion','fun','custom','roulette'];
  const plays=codes.map((code,i)=>({id:i+1,code:'xa-catalog-'+code,name:code}));
  const services=plays.map(play=>({id:play.id,gameplay:play.id,name:play.name,price:1880}));
  const env=environment(options=>({code:0,data:options.url.endsWith('/orders/services/')?services:options.url.endsWith('/orders/gameplays/')?plays:[]})); env.login();
  const page=env.page('catalog',{gameplay:3}); await page.load();
  assert.deepEqual(page.data.serviceTabs.map(item=>item.name),['体验单','常规单','陪玩单','趣味单','其他']);
  assert.equal(page.data.tab,'regular'); assert.equal(page.data.total,1);
  assert.equal(page.data.displayed[0].gameplay,3,'home gameplay entry must retain the exact gameplay');
  await page.load(); assert.equal(page.data.total,1,'refresh must retain gameplay context');
  const ids=new Set();
  for(const [id,total] of [['experience',1],['regular',4],['companion',1],['fun',2],['other',1]]) {
    page.chooseTab({currentTarget:{dataset:{id}}}); assert.equal(page.data.total,total);
    page.data.displayed.forEach(item=>ids.add(item.id));
  }
  assert.equal(ids.size,9,'no service group disappears after grouping');
  page.search({detail:{value:'missing'}}); assert.equal(page.data.total,0);
  page.reset(); assert.equal(page.data.tab,'other'); assert.equal(page.data.total,1);
  page.onUnload();
});

test('tier selection refreshes rules and checkout identity and ignores a late previous tier response', async () => {
  const base = { catalog_family: '常规物资单', game_category: 1, gameplay: 8, platform: 'PC', security_level: 'TOP_SECRET', price_unit: '份' };
  const rows = [{ ...base, id: 1, catalog_variant: '一档', name: '套餐1', price: 1880 }, { ...base, id: 2, catalog_variant: '二档', name: '套餐2', price: 2680 },
    { ...base, id: 3, catalog_variant: '三档', name: '套餐3', platform: 'MOBILE', price: 1680 }];
  let finishSecond;
  const env = environment(options => {
    if (options.url.endsWith('/evaluations/')) return { code: 0, data: { list: [], total: 0 } };
    const id = Number(options.url.match(/\/services\/(\d+)\//)?.[1]);
    const result = { code: 0, data: { ...rows.find(row => row.id === id), rules: '规则' + id, variants: rows } };
    return id === 2 ? new Promise(resolve => { finishSecond = () => resolve(result); }) : result;
  }); env.login();
  const page = env.page('service', { id: 1 }); await page.load();
  assert.equal(page.data.variantChoices.length, 2);
  const visible = page.data.service, reviews = page.data.reviews, updates = [], apply = page.setData;
  page.setData = patch => { updates.push(patch); apply(patch); };
  const pending = page.selectVariant(2); await tick(); page.checkout(); assert.equal(env.navigation.length, 0, 'loading tier must not be purchasable');
  assert.equal(page.data.service, visible, 'retain visible content while loading the next tier');
  assert.equal(page.data.reviews, reviews); assert.equal(page.data.loading, false);
  assert.equal(page.data.refreshing, true); assert.equal(page.data.pendingVariantId, 2);
  assert.equal(page.onShareAppMessage().path, '/pages/service/index?id=1', 'share must match the currently displayed detail');
  await page.selectVariant(3); finishSecond(); await pending;
  assert.ok(updates.every(patch => patch.service !== null && patch.loading !== true), 'switching must never unmount content or show full-page loading');
  assert.equal(page.data.refreshing, false); assert.equal(page.data.pendingVariantId, 0);
  assert.equal(page.data.service.id, 3); assert.equal(page.data.service.displayPrice, '168'); assert.equal(page.data.service.rules, '规则3');
  page.checkout(); assert.equal(env.navigation.at(-1), '/pages/checkout/index?service_id=3');
  assert.equal(page.onShareAppMessage().path, '/pages/service/index?id=3');
  env.auth.clearSession(); assert.deepEqual(page.data.variantChoices, []); assert.equal(page.data.service, null);
  page.onUnload();
});

test('failed tier switch retains the page and blocks stale checkout until the same tier is retried', async () => {
  const rows = [{ id: 1, name: '套餐1', price: 1880 }, { id: 2, name: '套餐2', price: 2680 }];
  let failed = true;
  const env = environment(options => {
    if (options.url.endsWith('/evaluations/')) return { code: 0, data: { list: [], total: 0 } };
    const id = Number(options.url.match(/\/services\/(\d+)\//)?.[1]);
    if (id === 2 && failed) return { code: 503, msg: '套餐暂时无法加载' };
    return { code: 0, data: { ...rows.find(row => row.id === id), variants: rows } };
  }); env.login();
  const page = env.page('service', { id: 1 }); await page.load();
  await page.selectVariant(2);
  assert.equal(page.data.service.id, 1); assert.equal(page.data.loading, false);
  assert.equal(page.data.error, ''); assert.equal(page.data.variantError, '套餐暂时无法加载');
  page.checkout(); page.companions(); await page.favorite(); assert.equal(env.navigation.length, 0);
  assert.ok(!env.calls.some(call => call.url.endsWith('/favorites/toggle/')));
  failed = false; await page.selectVariant(2);
  assert.equal(page.data.service.id, 2); assert.equal(page.data.variantError, '');
  page.checkout(); assert.equal(env.navigation.at(-1), '/pages/checkout/index?service_id=2');
  page.onUnload();
});

test('favorite feedback follows confirmed add/remove results and never replays on load or account change', async () => {
  const service = { id: 1, name: '常规套餐', price: 1880, is_favorited: false };
  let finishToggle;
  const env = environment(options => {
    if (options.url.endsWith('/favorites/toggle/')) return new Promise(resolve => {
      finishToggle = favorited => { service.is_favorited = favorited; resolve({ code: 0, data: { favorited } }); };
    });
    return { code: 0, data: options.url.endsWith('/evaluations/') ? { list: [], total: 0 } : { ...service } };
  }); env.login();
  const page = env.page('service', { id: 1 }); await page.load();
  assert.equal(page.data.favoriteMotion, '');
  const adding = page.favorite(); await tick(); await page.favorite();
  assert.equal(env.calls.filter(call => call.url.endsWith('/favorites/toggle/')).length, 1, 'a pending toggle cannot be submitted twice');
  assert.equal(page.data.favoriteBusy, true); assert.equal(page.data.service.is_favorited, false);
  assert.equal(page.data.favoriteMotion, '', 'an unconfirmed request must not animate success');
  finishToggle(true); await adding;
  assert.equal(page.data.service.is_favorited, true); assert.equal(page.data.favoriteMotion, 'added');
  assert.equal(page.data.favoriteBusy, false);
  const reload = page.load(); assert.equal(page.data.favoriteMotion, ''); await reload;
  assert.equal(page.data.service.is_favorited, true, 'the confirmed favorite survives a detail reload');
  assert.equal(page.data.favoriteMotion, '', 'loading a saved favorite must not animate it again');
  const removing = page.favorite(); await tick();
  assert.equal(page.data.service.is_favorited, true); assert.equal(page.data.favoriteMotion, '');
  finishToggle(false); await removing;
  assert.equal(page.data.service.is_favorited, false); assert.equal(page.data.favoriteMotion, 'removed');
  assert.equal(page.data.favoriteBusy, false);
  env.login('other'); assert.equal(page.data.service, null); assert.equal(page.data.favoriteMotion, '');
  page.onUnload();
});

test('failed or unchanged favorite responses do not show a false state transition', async () => {
  let failToggle = true;
  const service = { id: 1, name: '已收藏套餐', price: 1880, is_favorited: true };
  const env = environment(options => options.url.endsWith('/favorites/toggle/')
    ? failToggle ? new Error('offline') : { code: 0, data: { favorited: true } }
    : { code: 0, data: options.url.endsWith('/evaluations/') ? { list: [], total: 0 } : service });
  env.login(); const page = env.page('service', { id: 1 }); await page.load();
  page.setData({ favoriteMotion: 'added' }); await page.favorite();
  assert.equal(page.data.service.is_favorited, true); assert.equal(page.data.favoriteMotion, '');
  assert.equal(page.data.favoriteBusy, false, 'failed toggles release the interaction lock');
  failToggle = false; await page.favorite();
  assert.equal(page.data.service.is_favorited, true); assert.equal(page.data.favoriteMotion, '');
  assert.equal(page.data.favoriteBusy, false);
  page.onUnload();
});

test('a late favorite response cannot animate the previous tier while its replacement is loading', async () => {
  const rows = [{ id: 1, name: '套餐1', price: 1880, is_favorited: false }, { id: 2, name: '套餐2', price: 2680, is_favorited: false }];
  let finishToggle, finishTier;
  const env = environment(options => {
    if (options.url.endsWith('/favorites/toggle/')) return new Promise(resolve => { finishToggle = () => resolve({ code: 0, data: { favorited: true } }); });
    if (options.url.endsWith('/evaluations/')) return { code: 0, data: { list: [], total: 0 } };
    const id = Number(options.url.match(/\/services\/(\d+)\//)?.[1]);
    const response = { code: 0, data: { ...rows.find(row => row.id === id), variants: rows } };
    return id === 2 ? new Promise(resolve => { finishTier = () => resolve(response); }) : response;
  }); env.login();
  const page = env.page('service', { id: 1 }); await page.load();
  const toggle = page.favorite(); await tick();
  const switching = page.selectVariant(2); await tick();
  assert.equal(page.data.service.id, 1, 'the old tier remains visible until its replacement arrives');
  assert.equal(page.data.refreshing, true);
  finishToggle(); await toggle;
  assert.equal(page.data.service.is_favorited, false, 'a toggle for the previous tier cannot update the retained view');
  assert.equal(page.data.favoriteMotion, ''); assert.equal(page.data.favoriteBusy, false);
  finishTier(); await switching;
  assert.equal(page.data.service.id, 2); assert.equal(page.data.service.is_favorited, false);
  assert.equal(page.data.favoriteMotion, '');
  page.onUnload();
});

test('a selected companion only exposes its supported price tiers', async () => {
  const rows=[{ id: 1, platform:'PC', security_level:'ANY', price:600, name:'娱乐' },{ id:2, platform:'PC', security_level:'ANY', price:1000, name:'技术' }];
  const env=environment(options=>({code:0,data:options.url.endsWith('/users/escorts/')?[{id:7,service_item_ids:[1]}]:options.url.endsWith('/evaluations/')?{list:[],total:0}:{...rows[0],variants:rows}})); env.login();
  const page=env.page('service',{id:1,provider_id:7}); await page.load();
  assert.equal(page.data.variantTotal,1); await page.selectVariant(2); assert.equal(page.data.service.id,1);
  page.checkout(); assert.equal(env.navigation.at(-1),'/pages/checkout/index?service_id=1&provider_id=7');
  page.onUnload();
});

test('mark-all-read finishing after leaving messages cannot reload or write to the destroyed page', async () => {
  let finish;
  const env = environment(() => new Promise(resolve => { finish = resolve; })); env.login();
  const page = env.page('messages'); page.setData({ unread: 1 });
  const marking = page.markRead(); await tick();
  page.onUnload(); const afterUnload = structuredClone(page.data);
  finish({ code: 0, data: {} }); await marking;
  assert.equal(env.calls.length, 1, 'no request is issued by a destroyed page');
  assert.deepEqual(page.data, afterUnload, 'late request callbacks do not update destroyed page');
});

test('avatar upload preserves unsaved profile fields and updates cached user without switching identity', async () => {
  const env = environment(); env.login();
  const uploads = [];
  wx.uploadFile = options => { uploads.push(options); options.success({ statusCode: 200,
    data: JSON.stringify({ code: 0, data: { avatar: 'https://media.xa.test/avatar.png' } }) }); };
  const page = env.page('profile'); page._visible = true;
  const profiles = [{ game_category: 7, nickname: '草稿游戏昵称', region: '微信区', uid: '123' }];
  page.setData({ logged: true, nickname: '尚未保存的昵称', profiles });
  const revision = env.auth.revision();
  await page.chooseAvatar({ detail: { avatarUrl: 'wxfile://chosen-avatar' } });
  await tick();
  assert.equal(uploads.length, 1); assert.equal(uploads[0].name, 'avatar');
  assert.equal(uploads[0].header.Authorization, 'Bearer old-1');
  assert.equal(page.data.nickname, '尚未保存的昵称'); assert.deepEqual(page.data.profiles, profiles);
  assert.equal(env.auth.revision(), revision);
  assert.equal(page.data.avatar, env.auth.getSession().user.avatar);
  await page.chooseAvatar({ detail: {} }); assert.equal(uploads.length, 1);
  page.onUnload();
});

test('upload clears a definitively rejected session after refresh, but ignores stale account responses', async () => {
  const env = environment(() => ({ code: 0, data: { token: 'new-token' } })); env.login();
  let uploads = 0;
  wx.uploadFile = options => { uploads++; options.success({ statusCode: 401, data: JSON.stringify({ code: 401 }) }); };
  await assert.rejects(env.api.upload('/users/me/avatar/', 'wxfile://avatar', 'avatar'), /登录已过期/);
  assert.equal(uploads, 2); assert.equal(env.auth.getSession(), null);
  env.login('1'); let pending;
  wx.uploadFile = options => { pending = options; };
  const upload = env.api.upload('/users/me/avatar/', 'wxfile://avatar', 'avatar');
  await tick();
  env.login('2'); pending.success({ statusCode: 200, data: JSON.stringify({ code: 0, data: { avatar: 'old' } }) });
  await assert.rejects(upload, /账号已切换/); assert.equal(env.auth.getSession().user.id, '2');
});

test('account switch during image compression prevents uploading under the new account', async () => {
  const env = environment(); env.login();
  let inspect; let uploads = 0;
  wx.getFileSystemManager = () => ({ getFileInfo: options => { inspect = options; } });
  wx.uploadFile = () => { uploads++; };
  const pending = env.api.upload('/users/me/avatar/', 'wxfile://avatar', 'avatar');
  env.login('2');
  inspect.success({ size: 1000 });
  await assert.rejects(pending, /账号已切换/);
  assert.equal(uploads, 0);
});

test('fresh wx.login code is used for discovery and phone binding; discovery does not create a session', async () => {
  const env = environment(options => options.data.checkOnly ? { code: 0, data: { requiresBinding: true } } : { code: 0, data: { token: 'bound', refreshToken: 'refresh', userInfo: { id: '8', role: 'customer', isWechatBound: true, isPhoneVerified: true } } });
  const page = env.page('login');
  await page.quickLogin(); assert.equal(env.calls.length, 0, 'consent required');
  page.setData({ agreed: true }); await page.quickLogin();
  assert.equal(page.data.requiresBinding, true); assert.equal(env.auth.getSession(), null);
  await page.phone({ detail: { code: 'phone-authorization' } });
  assert.notEqual(env.calls[0].data.code, env.calls[1].data.code);
  assert.equal(env.calls[1].data.phoneCode, 'phone-authorization');
  assert.equal(env.auth.getSession().refresh, 'refresh'); assert.equal(env.auth.getSession().user.id, '8');
});

test('rejected phone authorization does not create a new account', async () => {
  const env = environment(); const page = env.page('login');
  page.setData({ agreed: true }); await page.phone({ detail: { errMsg: 'getPhoneNumber:fail user deny' } });
  assert.equal(env.calls.length, 0); assert.equal(env.auth.getSession(), null);
});

test('concurrent 401 responses share one refresh, then retry with new access token', async () => {
  const env = environment(options => {
    if (options.url.endsWith('/users/refresh/')) return { code: 0, data: { token: 'new-token' } };
    if (options.header.Authorization === 'Bearer new-token') return { code: 0, data: { ok: true } };
    return { statusCode: 401, data: { code: 401 } };
  });
  env.login();
  const results = await Promise.all([env.api.request('/wallet/info/'), env.api.request('/orders/stats/')]);
  assert.equal(results.every(item => item.ok), true);
  assert.equal(env.calls.filter(item => item.url.endsWith('/users/refresh/')).length, 1);
});

test('temporary refresh outage keeps session; definitive token failure clears it', async () => {
  let outage = true;
  const env = environment(options => options.url.endsWith('/users/refresh/') && outage ? new Error('offline') : { statusCode: 401, data: { code: 401, msg: 'expired' } });
  env.login(); await assert.rejects(env.api.request('/wallet/info/')); assert.ok(env.auth.getSession());
  outage = false; await assert.rejects(env.api.request('/wallet/info/')); assert.equal(env.auth.getSession(), null);
});

test('responses from an old account cannot overwrite a new login', async () => {
  let resolveOld;
  const env = environment(() => new Promise(resolve => { resolveOld = resolve; }));
  env.login('1'); const oldRequest = env.api.request('/users/me/'); await tick();
  env.login('2'); resolveOld({ code: 0, data: { id: '1' } });
  await assert.rejects(oldRequest, /账号已切换/); assert.equal(env.auth.getSession().user.id, '2');
});

test('fund requests deduplicate rapid taps and preserve idempotency across uncertain retries', async () => {
  let offline = true;
  const env = environment(() => offline ? new Error('lost response') : { code: 0, data: { id: 7 } });
  env.login(); const a = env.api.fund('/orders/create/', { service_id: 1 });
  const b = env.api.fund('/orders/create/', { service_id: 1 }); assert.equal(a, b);
  await assert.rejects(a); const firstId = env.calls[0].data.client_request_id;
  assert.ok([...env.storage.keys()].some(key => key.startsWith('xa:native:fund:')));
  offline = false; await env.api.fund('/orders/create/', { service_id: 1 });
  assert.equal(env.calls[1].data.client_request_id, firstId);
  assert.equal([...env.storage.keys()].some(key => key.startsWith('xa:native:fund:')), false);
});

test('duplicate-fund response retains transaction identity, preventing another purchase on next tap', async () => {
  const env = environment(() => ({ code: 409, msg: 'already processed' })); env.login();
  await assert.rejects(env.api.fund('/orders/create/', { service_id: 1 }));
  await assert.rejects(env.api.fund('/orders/create/', { service_id: 1 }));
  assert.equal(env.calls[0].data.client_request_id, env.calls[1].data.client_request_id);
});

test('storage failure blocks money writes', async () => {
  const env = environment(); env.login(); wx.setStorageSync = () => { throw new Error('full'); };
  await assert.rejects(env.api.fund('/orders/create/', {}), /无法保存交易/); assert.equal(env.calls.length, 0);
});

test('fund replay restores the original result then permits a separate confirmed purchase', async () => {
  let attempts = 0;
  const env = environment(() => {
    attempts++;
    if (attempts === 1) return new Error('lost successful response');
    if (attempts === 2) return { code: 409, errorCode: 'FUND_ALREADY_PROCESSED', fundResult: { code: 0, data: { id: 7 } } };
    return { code: 0, data: { id: 8 } };
  });
  env.login();
  await assert.rejects(env.api.fund('/orders/create/', { service_id: 1 }));
  assert.equal((await env.api.fund('/orders/create/', { service_id: 1 })).id, 7);
  assert.equal((await env.api.fund('/orders/create/', { service_id: 1 })).id, 8);
  assert.equal(env.calls[0].data.client_request_id, env.calls[1].data.client_request_id);
  assert.notEqual(env.calls[1].data.client_request_id, env.calls[2].data.client_request_id);
});

test('fund replay refuses mismatched and incomplete receipts', async () => {
  let errorCode = 'REQUEST_ID_CONFLICT', fundResult = { code: 0, data: { id: 7 } };
  const env = environment(() => ({ code: 409, errorCode, fundResult })); env.login();
  await assert.rejects(env.api.fund('/orders/create/', {}));
  errorCode = 'FUND_ALREADY_PROCESSED'; fundResult = { code: 500 };
  await assert.rejects(env.api.fund('/orders/create/', {}));
  assert.equal(env.calls[0].data.client_request_id, env.calls[1].data.client_request_id);
});

test('checkout never posts a purchase when the latest quote differs from the displayed amount', async () => {
  const env = environment(() => ({ code: 0, data: { amount: 200, balance_sufficient: true } })); env.login();
  const page = env.page('checkout', { service_id: '1' });
  page.setData({ service: { id: 1 }, quote: { amount: 100 }, nickname: '游戏昵称', region: '微信区' });
  await page.submit();
  assert.equal(env.calls.length, 1); assert.match(env.calls[0].url, /\/quote\/$/);
  assert.match(page.data.quoteError, /价格或优惠已变化/);
});

test('private read screen discards a late result after unload', async () => {
  let finish; const env = environment(() => new Promise(resolve => { finish = resolve; })); env.login();
  const page = env.page('achievements'); const reading = page.load(); await tick(); page.onUnload();
  finish({ code: 0, data: [{ code: 'old', current: 10, target: 20 }] }); await reading;
  assert.deepEqual(page.data.items, []);
});

test('guest private screens show a login action without forcing a navigation loop', async () => {
  for (const name of ['catalog','orders','messages','mine','service','checkout','order-detail','companions','companion-picker','companion-detail','wallet','profile','security','achievements','rankings','coupons','favorites','checkin','announcements','announcement-detail','message-detail']) {
    const env = environment(); const page = env.page(name, { id: '1', service_id: '1' });
    await page.load(); assert.equal(env.calls.length, 0, name); assert.equal(env.navigation.length, 0, name);
    assert.equal(typeof page.login, 'function', name); page.onUnload && page.onUnload();
  }
});

test('release cannot silently use the developer localhost API', async () => {
  const env = environment(); wx.getAccountInfoSync = () => ({ miniProgram: { envVersion: 'release' } });
  const environments = require(path.join(root, 'config'));
  const original = environments.release;
  try {
    for (const apiBase of ['', 'http://127.0.0.1:18080/api']) {
      environments.release = { ...original, apiBase };
      await assert.rejects(env.api.request('/orders/gameplays/'), /服务地址尚未配置/);
      assert.equal(env.calls.length, 0);
    }
  } finally { environments.release = original; }
});

test('platform and difficulty switch the advert, compressed poster, price and checkout together', async () => {
  const rows = [['PC','CONFIDENTIAL'],['PC','TOP_SECRET'],['MOBILE','CONFIDENTIAL'],['MOBILE','TOP_SECRET']].map(([platform,security_level],i)=>({id:i+1,name:'当前档位',catalog_family:'联调项目',catalog_variant:'基础档',platform,security_level,price:1000+i*100,cover_url:'https://example.com/cover.webp'}));
  const env = environment(options => {
    if(options.url.endsWith('/evaluations/')) return {code:0,data:{list:[],total:0}};
    const id=Number(options.url.match(/\/services\/(\d+)\//)?.[1]);
    return {code:0,data:{...rows[id-1],variants:rows,introduction_images:[`https://example.com/advert-${id}.webp`],details_html:`<img src="https://example.com/price-${id}-display.webp" />`,details_images:[`https://example.com/price-${id}.png`]}};
  });env.login();const page=env.page('service',{id:1});await page.load();
  await page.chooseSpec({currentTarget:{dataset:{field:'platform',id:'MOBILE'}}});
  await page.chooseSpec({currentTarget:{dataset:{field:'security_level',id:'TOP_SECRET'}}});
  assert.equal(page.data.service.id,4);
  assert.deepEqual(page.data.service.introductionImages,['https://example.com/advert-4.webp']);
  assert.match(page.data.service.detailsHtml,/price-4-display\.webp/);
  assert.deepEqual(page.data.service.detailsImages,['https://example.com/price-4.png']);
  assert.deepEqual(page.data.service.gallery,[],'cover remains outside the project landing page');
  page.checkout();assert.equal(env.navigation.at(-1),'/pages/checkout/index?service_id=4');page.onUnload();
});

test('every native template event resolves to an actual page method', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'));
  for (const route of manifest.pages) {
    const env = environment();
    const page = env.page(route.replace(/^pages\//, '').replace(/\/index$/, ''), { id: '1' });
    const template = fs.readFileSync(path.join(root, route + '.wxml'), 'utf8');
    for (const match of template.matchAll(/(?:bind|catch):?[a-z]+="([a-zA-Z_$][\w$]*)"/g)) {
      assert.equal(typeof page[match[1]], 'function', route + ' -> ' + match[1]);
    }
    page.onUnload && page.onUnload();
  }
});

test('legacy product, order, wallet, and gameplay links preserve identity and filters', () => {
  for (const [name, options, target] of [
    ['productDetail', { id: '42' }, '/pages/service/index?id=42'],
    ['orderList', { orderId: '19' }, '/pages/order-detail/index?id=19'],
    ['customer/wallet', {}, '/pages/wallet/index'],
    ['coupon/mine', {}, '/pages/coupons/index?tab=mine'],
  ]) {
    const env = environment(); env.page(name, options); assert.equal(env.navigation[0], target);
  }
  const env = environment(); env.page('gameplay', { id: '4', platform: 'PC', level: 'TOP_SECRET' });
  assert.equal(env.navigation[0], '/pages/catalog/index');
  assert.deepEqual(env.storage.get('xa:native:catalog-intent'), { gameplay: '4', platform: 'PC', level: 'TOP_SECRET' });
});

test('bound coupon routes to only eligible price tiers and clears on account switch', async () => {
  const rows = [1,2,3].map(id=>({id,name:'陪玩 '+id,price:id*1000,game_category:2,game_category_name:'英雄联盟',catalog_family:'同款陪玩',price_unit:'小时'}));
  const env=environment(options=>({code:0,data:options.url.endsWith('/orders/services/')?rows:options.url.endsWith('/orders/game-categories/')?[{id:1,name:'三角洲行动'},{id:2,name:'英雄联盟'}]:[]}));env.login();
  env.storage.set('xa:native:catalog-intent',{service_ids:[2],coupon_name:'VIP 陪玩券'});
  const page=env.page('catalog');page.onShow();await tick();await tick();
  assert.equal(page.data.couponName,'VIP 陪玩券');assert.equal(page.data.category,'2');assert.equal(page.data.tab,'companion');
  assert.equal(page.data.displayed.length,1);assert.equal(page.data.displayed[0].id,2);assert.equal(page.data.displayed[0].variantCount,1);
  env.auth.clearSession();assert.equal(page.data.couponServiceIds,null);assert.equal(page.data.couponName,'');page.onUnload();
});

test('new companion tab keeps contextual checkout picker on the navigation stack', () => {
  const env = environment(); env.login();
  const ui = require(path.join(root, 'core/ui'));
  ui.go('/pages/orders/index?status=in_progress');
  assert.equal(env.storage.get('xa:native:orders-intent').status, 'in_progress');
  const orders = env.page('orders'); orders.onShow(); assert.equal(orders.data.filter, 'in_progress'); orders.onUnload();
  const checkout = env.page('checkout', { service_id: '9' }); checkout.chooseProvider();
  assert.ok(env.navigation.includes('/pages/companion-picker/index?service_id=9&from_checkout=1&provider_gender=ANY'));
  const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'));
  assert.equal(app.tabBar.list.length, 5);
  assert.ok(app.tabBar.list.some(item => item.pagePath === 'pages/companions/index'));
  assert.ok(!app.tabBar.list.some(item => item.pagePath === 'pages/catalog/index'));
  checkout.onUnload();
});

test('brand artwork uses the configured asset origin on every affected page', () => {
  const env = environment();
  global.wx.getAccountInfoSync = () => ({ miniProgram: { envVersion: 'release' } });
  for (const name of ['home', 'login', 'mine', 'gifts']) {
    const page = env.page(name);
    for (const key of ['companionshipArt', 'giftMomentArt']) {
      assert.match(page.data[key], /^https:\/\/api\.xaesports\.club\/static\/miniapp\/brand\/[a-z-]+\.[a-f0-9]{12}\.png$/);
    }
    page.onUnload && page.onUnload();
  }
});

test('gift basket combines quantities and sends only selected recipient, prices and naming text', async () => {
  const context = { gifts: [{ id: 1, name: '月饼', price: 2100, gift_naming_days: 2 }, { id: 2, name: '桂花糕', price: 5200, gift_naming_days: 3 }], recipients: [{ id: 9, nickname: '听风' }], balance: 100000, wallet_active: true, max_quantity: 999 };
  const env = environment(options => ({ code: 0, data: options.url.includes('/context/') ? context : { orders: [{ id: 1 }, { id: 2 }], amount: 9400, quantity: 3, naming_days: 7 } })); env.login();
  const page = env.page('gifts'); await page.load();
  assert.equal(page.data.recipientIndex, -1, 'browsing must not silently pick a recipient');
  page.recipient({ detail: { value: 0 } });
  const event = id => ({ currentTarget: { dataset: { id } } });
  page.select(event(1)); page.select(event(1)); page.select(event(2));
  assert.equal(page.data.total, 9400); assert.equal(page.data.namingDays, 7);
  page.naming({ detail: { value: '我的默契搭子' } }); await page.send();
  const sent = env.calls.find(call => call.url.endsWith('/orders/gifts/send/'));
  assert.deepEqual(sent.data.items, [{ gift_service_id: 1, quantity: 2 }, { gift_service_id: 2, quantity: 1 }]);
  assert.equal(sent.data.provider_id, 9); assert.equal(sent.data.expected_amount, 9400);
  assert.equal(sent.data.naming_title, '我的默契搭子'); assert.equal(sent.data.naming_days, undefined);
  assert.equal(page.data.receipt.quantity, 3); assert.deepEqual(page.data.basket, {});
  page.onUnload();
});

test('club feed is separate from private unread notifications and rejects old-session feed responses', async () => {
  let finish;
  const env = environment(options => options.url.includes('/club/feed/') ? new Promise(resolve => { finish = resolve; }) : { code: 0, data: { messages: [], unread: 0, total: 0 } }); env.login();
  const page = env.page('messages');
  const loading = page.loadFeed(); await tick();
  env.auth.clearSession(); finish({ code: 0, data: { items: [{ id: 1, kind: 'GIFT', content: '旧账号动态' }], has_more: false } });
  await loading;
  assert.deepEqual(page.data.feed, []);
  assert.equal(page.data.feedLoading, false);
  assert.equal(page.data.section, 'feed');
  page.onUnload();
});


test('native 429 retains the payment identity and cools down only the affected transaction scope', async () => {
  let attempts = 0;
  const env = environment(options => options.url.endsWith('/orders/create/') && ++attempts === 1
    ? { statusCode: 429, data: { code: 429, msg: '繁忙', retry_after: 1, errorCode: 'ORDER_RATE_LIMITED' } }
    : { code: 0, data: { id: 77 } });
  env.login();
  const original = Date.now; let now = original(); Date.now = () => now;
  try {
    await assert.rejects(env.api.fund('/orders/create/', { service_id: 1 }), error => error.code === 429);
    const first = env.calls[0].data.client_request_id;
    await assert.rejects(env.api.fund('/orders/create/', { service_id: 1 }), error => error.code === 429);
    assert.equal(env.calls.length, 1, 'cooldown must not send another purchase');
    await env.api.request('/orders/quote/', 'POST', { service_id: 1 });
    now += 1100;
    assert.equal((await env.api.fund('/orders/create/', { service_id: 1 })).id, 77);
    assert.equal(env.calls.at(-1).data.client_request_id, first, 'retry retains the original receipt key');
  } finally { Date.now = original; }
});

test('home retains five working quick entries and retired self-order routes cannot be opened', () => {
  const env = environment();
  const home = env.page('home');
  const template = fs.readFileSync(path.join(root, 'pages/home/index.wxml'), 'utf8');
  const quickGrid = template.match(/<view class="quick-grid">([\s\S]*?)\n  <\/view>/)[1];
  const actions = Array.from(quickGrid.matchAll(/bindtap="([^"]+)"/g), match => match[1]);
  assert.deepEqual(actions, ['checkin', 'companions', 'coupons', 'rankings', 'gifts']);
  for (const action of actions) {
    home[action]();
    assert.equal(env.navigation.at(-1), '/pages/' + action + '/index');
  }
  assert.equal(home.selfOrder, undefined);
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'));
  assert.ok(!manifest.pages.some(route => /self-?order/i.test(route)));
  const { go } = require(path.join(root, 'core/ui'));
  const before = env.navigation.length;
  go('/pages/self-order/index'); go('/pages/selfOrder/index');
  assert.equal(env.navigation.length, before);
  env.storage.set('xa:native:self-order-mode', 'AUTO_GRAB');
  const checkout = env.page('checkout', { service_id: 12 });
  assert.equal(checkout.payload().dispatch_mode, 'BOSS_SELECT');
  checkout.dispatch({ detail: { value: 1 } });
  assert.equal(checkout.payload().dispatch_mode, 'AUTO_GRAB');
  home.onUnload(); checkout.onUnload();
});

test('catalog shows game icons, starts with all games, and deselects a game without losing service filters', async () => {
  const categories = [{ id: 1, name: '三角洲行动', icon_url: '/media/games/delta.png' }, { id: 2, name: '英雄联盟' }];
  const services = categories.map(item => ({ id: item.id, name: item.name + '陪玩', game_category: item.id, price: 1000, platform: 'PC' }));
  const env = environment(options => ({ code: 0, data: options.url.endsWith('/orders/services/') ? services : options.url.endsWith('/orders/game-categories/') ? categories : [] })); env.login();
  const page = env.page('catalog', { tab: 'regular' }); await page.load();
  assert.equal(page.data.category, ''); assert.equal(page.data.total, 2);
  assert.match(page.data.categories[0].iconUrl, /^https?:\/\/.*\/media\/games\/delta\.png$/);
  assert.equal(page.data.categories[1].iconUrl, '/assets/icons/headphones.png');
  const choose = (field, id) => page.choose({ currentTarget: { dataset: { field, id } } });
  choose('category', '1'); assert.equal(page.data.category, '1'); assert.equal(page.data.total, 1);
  choose('platform', 'PC');
  choose('category', '1'); assert.equal(page.data.category, ''); assert.equal(page.data.platform, ''); assert.equal(page.data.total, 2);
  assert.equal(page.data.tab, 'regular');
  const template = fs.readFileSync(path.join(root, 'pages/catalog/index.wxml'), 'utf8');
  assert.doesNotMatch(template, /全部游戏/); assert.match(template, /src="{{item.iconUrl}}"/);
  page.onUnload();
});

test('companion game filters use icons and deselect back to the full directory for both picker pages', async () => {
  const categories = [{ id: 1, name: '三角洲行动', icon_url: 'https://assets.example.com/delta.png' }, { id: 2, name: '英雄联盟' }];
  const escorts = categories.map(item => ({ id: item.id, nickname: item.name + '陪玩', pricePerHour: 100 }));
  const env = environment(options => ({ code: 0, data: options.url.endsWith('/orders/game-categories/') ? categories
    : options.url.includes('game_category=1') ? escorts.slice(0, 1) : escorts })); env.login();
  for (const name of ['companions', 'companion-picker']) {
    const page = env.page(name); await page.load();
    assert.equal(page.data.category, ''); assert.equal(page.data.visible.length, 2);
    assert.equal(page.data.categories[0].iconUrl, 'https://assets.example.com/delta.png');
    assert.equal(page.data.categories[1].iconUrl, '/assets/icons/headphones.png');
    page.setData({ voiceOnly: false, sort: 1 });
    const event = { currentTarget: { dataset: { id: '1' } } };
    page.chooseCategory(event); await tick();
    assert.equal(page.data.category, '1'); assert.equal(page.data.visible.length, 1);
    assert.ok(env.calls.filter(c=>c.url.includes('/users/escorts/')).at(-1).url.endsWith('/users/escorts/?directory=1&game_category=1'));
    page.chooseCategory(event); await tick();
    assert.equal(page.data.category, ''); assert.equal(page.data.visible.length, 2);
    assert.ok(env.calls.filter(c=>c.url.includes('/users/escorts/')).at(-1).url.endsWith('/users/escorts/?directory=1'));
    assert.equal(page.data.sort, 1);
    page.onUnload();
  }
  const template = fs.readFileSync(path.join(root, 'pages/companions/index.wxml'), 'utf8');
  assert.doesNotMatch(template, /全部游戏/); assert.match(template, /src="{{item.iconUrl}}"/);
});


test('service details never expose original preview and deduplicate the same poster', async () => {
  const poster='https://api.xaesports.club/media/club-catalog/thumbnails/v3/price.jpg';
  const env=environment(options => options.url.endsWith('/evaluations/') ? {code:0,data:{list:[],total:0}} :
    {code:0,data:{id:43,name:'双陪技术',catalog_family:'双陪',platform:'PC',platform_name:'端游',
      service_type:'COMPANION',billing_provider_count:2,price_unit:'小时',price:16000,purchase_mode:'CONSULT',
      details_html:`<p>每单1小时</p><img src="${poster}" data-original="https://example.com/original.png" />`,
      details_images:[poster],rules_images:[poster]}});
  env.login();const page=env.page('service',{id:43});await page.load();
  assert.equal(page.data.service.isDoubleHour,true);
  assert.deepEqual(page.data.service.rulesImages,[]);
  assert.doesNotMatch(page.data.service.detailsHtml,/data-original/);
  assert.equal(page.previewDetails,undefined); assert.equal(page.previewRule,undefined);
  page.checkout(); assert.equal(env.navigation.at(-1),'/pages/checkout/index?service_id=43');
  page.companions(); assert.equal(env.navigation.at(-1),'/pages/companion-picker/index?service_id=43');
  page.onUnload();
});

test('companion checkout selects recent and recommended people and clears a mismatched gender', async () => {
  const male = {id:11,nickname:'男陪',gender:'MALE',can_order:true};
  const female = {id:12,nickname:'女陪',gender:'FEMALE',can_order:true};
  const env = environment(options => ({code:0,data:options.url.includes('shortcuts=1') ? {recent:[male],recommended:[female]} : {original_amount:9000,amount:9000,wallet_balance:100000,balance_sufficient:true}}));
  env.login(); const page = env.page('checkout',{service_id:14});
  page.setData({service:{id:14,service_type:'COMPANION'}}); await page.loadShortcuts();
  assert.equal(page.data.recent[0].id,11); assert.equal(page.data.recommended[0].id,12);
  page.selectShortcut({currentTarget:{dataset:{id:11}}}); await tick();
  assert.equal(page.payload().provider_id,11);
  await page.gender({detail:{value:2}});
  assert.equal(page.data.provider,null); assert.equal(page.payload().provider_id,undefined);
  assert.equal(page.payload().provider_gender,'FEMALE');
  page.selectShortcut({currentTarget:{dataset:{id:12}}}); await tick();
  assert.equal(page.payload().provider_id,12);
  page.onUnload();
});
test('shortcut failure preserves a valid quote and unavailable companions cannot be selected', async () => {
  const env=environment(options=>options.url.includes('shortcuts=1') ? new Error('推荐加载失败') : {code:0,data:{original_amount:9000,amount:9000,wallet_balance:100000,balance_sufficient:true}});
  env.login();const page=env.page('checkout',{service_id:14});page.setData({service:{id:14,service_type:'COMPANION'}});
  await Promise.all([page.refreshQuote(),page.loadShortcuts()]);
  assert.equal(page.data.quote.amount,9000);assert.ok(page.data.shortcutsError);
  page.setData({recent:[{id:11,can_order:false}]});page.selectShortcut({currentTarget:{dataset:{id:11}}});
  assert.equal(page.data.provider,null);page.onUnload();
});

test('double companion checkout restores both people and quotes and creates the original package with the same selection', async () => {
  const providers = [
    { id: 12, nickname: '小禾', gender: 'FEMALE', can_order: true },
    { id: 13, nickname: '听风', gender: 'MALE', can_order: true },
    { id: 14, nickname: '小满', gender: 'FEMALE', can_order: true },
  ];
  const packagePrice = 16000;
  const quote = { original_amount: packagePrice, amount: packagePrice, wallet_balance: 100000, balance_sufficient: true };
  const env = environment(options => ({ code: 0, data:
    options.url.includes('/orders/services/') ? { id: 43, name: '双陪套餐', service_type: 'COMPANION', billing_provider_count: 2, price: packagePrice, purchase_mode: 'CONSULT', game_category: 7 }
      : options.url.includes('/users/me/') ? { game_profiles: [{ game_category: 7, nickname: '测试玩家', region: '微信区' }] }
      : options.url.includes('shortcuts=1') ? { recent: [], recommended: providers }
      : options.url.includes('/users/escorts/') ? providers
      : options.url.includes('/orders/traffic-status/') ? { self_order_enabled: true, mode: 'normal' }
      : options.url.includes('/orders/quote/') ? quote
      : options.url.includes('/orders/create/') ? { id: 991 }
      : [] }));
  env.login();
  const page = env.page('checkout', { service_id: '43', provider_ids: '12,13' }); await page.load();
  assert.equal(page.data.error, ''); assert.equal(page.data.selectionLimit, 2);
  assert.deepEqual(page.data.providers.map(item => item.nickname), ['小禾', '听风']);
  assert.deepEqual(page.data.selectedProviderIds, [12, 13]); assert.equal(page.data.provider.id, 12);
  assert.equal(page.data.quote.amount, packagePrice, 'choosing two people must retain the package price');
  assert.deepEqual(page.payload().provider_ids, [12, 13]); assert.equal(page.payload().provider_id, undefined);
  const initialQuote = env.calls.filter(call => call.url.includes('/orders/quote/')).at(-1);
  assert.deepEqual(initialQuote.data.provider_ids, [12, 13]);

  let pickerNavigation; wx.navigateTo = options => { pickerNavigation = options; };
  page.chooseProvider();
  const query = new URL(pickerNavigation.url, 'http://native.test').searchParams;
  assert.equal(query.get('selected_provider_ids'), '12,13'); assert.equal(query.get('from_checkout'), '1');
  page.onShow(); await tick(); page.onHide();
  assert.deepEqual(page.data.selectedProviderIds, [12, 13], 'canceling the picker preserves the confirmed selection');
  pickerNavigation.events.providersSelected([providers[0], providers[2]]); await tick();
  assert.deepEqual(page.data.providers.map(item => item.nickname), ['小禾', '小满']);
  assert.deepEqual(page.payload().provider_ids, [12, 14]); assert.equal(page.data.quote.amount, packagePrice);
  page.chooseProvider();
  assert.equal(new URL(pickerNavigation.url, 'http://native.test').searchParams.get('selected_provider_ids'), '12,14');

  await page.submit();
  const submittedQuote = env.calls.filter(call => call.url.includes('/orders/quote/')).at(-1);
  const created = env.calls.find(call => call.url.includes('/orders/create/'));
  assert.ok(created, 'a complete pair with the confirmed quote can submit');
  assert.deepEqual(submittedQuote.data.provider_ids, [12, 14]);
  assert.deepEqual(created.data.provider_ids, submittedQuote.data.provider_ids);
  assert.equal(created.data.provider_id, undefined); assert.equal(created.data.expected_amount, packagePrice);
  assert.equal(env.navigation.at(-1), '/pages/order-detail/index?id=991'); page.onUnload();
});

test('double companion checkout blocks a partial selection and permits a fully cleared platform order', async () => {
  const female = { id: 12, nickname: '小禾', gender: 'FEMALE', can_order: true };
  const quote = { original_amount: 16000, amount: 16000, wallet_balance: 100000, balance_sufficient: true };
  const env = environment(options => ({ code: 0, data: options.url.includes('/orders/create/') ? { id: 992 } : quote }));
  env.login(); const page = env.page('checkout', { service_id: '43' });
  page.setData({ service: { id: 43, service_type: 'COMPANION', billing_provider_count: 2 }, nickname: '测试玩家', region: '微信区' });
  let pickerNavigation; wx.navigateTo = options => { pickerNavigation = options; };
  page.chooseProvider(); pickerNavigation.events.providersSelected([female]); await tick();
  assert.deepEqual(page.data.selectedProviderIds, [12]); assert.equal(page.data.quote, null);
  assert.match(page.data.quoteError, /2|两/);
  assert.equal(env.calls.length, 0, 'a partial pair is not sent to the quote endpoint');
  page.setData({ quote }); await page.submit();
  assert.equal(env.calls.length, 0, 'a stale displayed quote cannot bypass the incomplete pair guard');

  page.chooseProvider(); pickerNavigation.events.providersSelected([]); await tick();
  assert.deepEqual(page.data.selectedProviderIds, []); assert.equal(page.data.provider, null);
  assert.equal(page.payload().provider_id, undefined); assert.equal(page.payload().provider_ids, undefined);
  assert.equal(page.payload().dispatch_mode, 'BOSS_SELECT'); assert.equal(page.data.quote.amount, quote.amount);
  await page.submit();
  const create = env.calls.find(call => call.url.includes('/orders/create/'));
  assert.ok(create); assert.equal(create.data.dispatch_mode, 'BOSS_SELECT');
  assert.equal(create.data.provider_id, undefined); assert.equal(create.data.provider_ids, undefined);
  assert.equal(create.data.expected_amount, 16000); page.onUnload();
});

test('double companion shortcuts cap and toggle people, and gender changes retain compatible selections', async () => {
  const providers = [
    { id: 12, nickname: '女陪甲', gender: 'FEMALE', can_order: true },
    { id: 13, nickname: '男陪', gender: 'MALE', can_order: true },
    { id: 14, nickname: '女陪乙', gender: 'FEMALE', can_order: true },
  ];
  const env = environment(options => ({ code: 0, data: options.url.includes('shortcuts=1') ? { recent: [], recommended: providers.filter(item => !options.url.endsWith('FEMALE') || item.gender === 'FEMALE') }
    : { original_amount: 16000, amount: 16000, wallet_balance: 100000, balance_sufficient: true } }));
  env.login(); const page = env.page('checkout', { service_id: '43' });
  page.setData({ service: { id: 43, service_type: 'COMPANION', billing_provider_count: 2 }, recommended: providers });
  const choose = id => page.selectShortcut({ currentTarget: { dataset: { id } } });
  choose(12); await tick(); assert.deepEqual(page.data.selectedProviderIds, [12]);
  assert.equal(env.calls.filter(call => call.url.includes('/orders/quote/')).length, 0);
  choose(13); await tick(); assert.deepEqual(page.data.selectedProviderIds, [12, 13]);
  const quoteCount = env.calls.filter(call => call.url.includes('/orders/quote/')).length;
  choose(14); await tick(); assert.deepEqual(page.data.selectedProviderIds, [12, 13]);
  assert.equal(env.calls.filter(call => call.url.includes('/orders/quote/')).length, quoteCount, 'a third person must not alter the pair or quote');
  choose(13); await tick(); assert.deepEqual(page.data.selectedProviderIds, [12]); assert.equal(page.data.quote, null);
  choose(13); await tick(); assert.deepEqual(page.data.selectedProviderIds, [12, 13]);
  await page.gender({ detail: { value: 2 } });
  assert.deepEqual(page.data.selectedProviderIds, [12]); assert.equal(page.data.providers[0].nickname, '女陪甲');
  assert.equal(page.payload().provider_gender, 'FEMALE'); assert.equal(page.data.quote, null);
  choose(14); await tick(); assert.deepEqual(page.data.selectedProviderIds, [12, 14]);
  assert.equal(page.data.quote.amount, 16000); page.onUnload();
});

test('multi-selection callbacks from a previous session cannot repopulate checkout', async () => {
  const env = environment(() => ({ code: 0, data: { original_amount: 16000, amount: 16000, wallet_balance: 100000, balance_sufficient: true } }));
  env.login(); const page = env.page('checkout', { service_id: '43' });
  page.setData({ service: { id: 43, service_type: 'COMPANION', billing_provider_count: 2 } });
  let pickerNavigation; wx.navigateTo = options => { pickerNavigation = options; };
  const pair = [{ id: 12, nickname: '小禾', can_order: true }, { id: 13, nickname: '听风', can_order: true }];
  page.chooseProvider(); pickerNavigation.events.providersSelected(pair); await tick();
  assert.deepEqual(page.data.selectedProviderIds, [12, 13]);
  page.chooseProvider(); const staleNavigation = pickerNavigation; env.login('other');
  assert.deepEqual(page.data.selectedProviderIds, []); assert.deepEqual(page.data.providers, []);
  staleNavigation.events.providersSelected(pair); await tick();
  assert.deepEqual(page.data.selectedProviderIds, []); assert.equal(page.data.provider, null);
  assert.equal(page.payload().provider_id, undefined); assert.equal(page.payload().provider_ids, undefined);
  page.onUnload();
});

test('single companion links and legacy selection callbacks retain the provider_id contract', async () => {
  const provider = { id: 12, nickname: '小禾', can_order: true, gender: 'FEMALE' };
  const env = environment(options => ({ code: 0, data:
    options.url.includes('/orders/services/') ? { id: 91, name: '单陪', billing_provider_count: 1, service_type: 'COMPANION' }
      : options.url.includes('/users/escorts/') ? options.url.includes('shortcuts=1') ? { recent: [], recommended: [] } : [provider]
      : options.url.includes('/orders/quote/') ? { original_amount: 9000, amount: 9000, wallet_balance: 100000, balance_sufficient: true }
      : options.url.includes('/orders/traffic-status/') ? { self_order_enabled: true } : [] }));
  env.login(); const page = env.page('checkout', { service_id: '91', provider_id: '12' }); await page.load();
  assert.deepEqual(page.data.selectedProviderIds, [12]); assert.equal(page.payload().provider_id, 12);
  assert.equal(page.payload().provider_ids, undefined);
  let pickerNavigation; wx.navigateTo = options => { pickerNavigation = options; }; page.chooseProvider();
  pickerNavigation.events.providerSelected({ ...provider, id: 13, nickname: '新陪玩' }); await tick();
  assert.equal(page.data.provider.nickname, '新陪玩'); assert.equal(page.payload().provider_id, 13);
  assert.equal(page.payload().provider_ids, undefined); page.onUnload();
});

test('non-double consulting services and addons keep their customer-service entry', async () => {
  for (const service of [{ id: 43, purchase_mode: 'CONSULT', service_type: 'COMPANION', billing_provider_count: 1 },
    { id: 44, purchase_mode: 'DIRECT', is_addon: true, billing_provider_count: 2 }]) {
    const env = environment(options => ({ code: 0, data: options.url.endsWith('/evaluations/') ? { list: [], total: 0 } : service }));
    env.login(); const page = env.page('service', { id: String(service.id) }); await page.load();
    page.checkout(); assert.equal(env.navigation.at(-1), '/pages/support/index');
    page.companions(); assert.equal(env.navigation.at(-1), '/pages/support/index'); page.onUnload();
  }
});

test('qualification filters reset dependent grades and keep minimum tier scoped for both picker pages', async()=>{
  const meta={kinds:[{value:'GUARD',label:'护航'},{value:'COMPANION',label:'陪玩'}],platforms:[{value:'PC',label:'端游'},{value:'MOBILE',label:'手游'}],scopes:[{provider_kind:'GUARD',platform:'MOBILE',security_level:'CONFIDENTIAL',grades:[{value:'MASTER',label:'大师'},{value:'LEGEND',label:'传奇'}]},{provider_kind:'COMPANION',platform:'MOBILE',security_level:'ANY',grades:[{value:'ENTERTAINMENT',label:'娱乐'}]}]};
  for(const name of ['companions','companion-picker']){
    const env=environment(o=>({code:0,data:o.url.includes('qualification-options') ? meta:[]}));env.login();const page=env.page(name);await page.load();
    const change=(key,value)=>page.chooseScope({currentTarget:{dataset:{key}},detail:{value}});
    change('provider_kind',1);await tick();change('platform',2);await tick();change('security_level',1);await tick();change('grade',2);await tick();
    const query=env.calls.filter(c=>c.url.includes('/users/escorts/')).at(-1).url;
    assert.match(query,/provider_kind=GUARD/);assert.match(query,/platform=MOBILE/);assert.match(query,/security_level=CONFIDENTIAL/);assert.match(query,/grade=LEGEND/);
    change('provider_kind',2);await tick();assert.equal(page.data.scope.security_level,'ANY');assert.equal(page.data.scope.grade,'');
    page.resetFilters();await tick();assert.deepEqual(page.data.scope,{provider_kind:'',platform:'',security_level:'',grade:''});page.onUnload();
  }
});

test('reset clears directory gender but preserves checkout gender locked by the order', async()=>{
 for(const locked of [false,true]){const env=environment(o=>({code:0,data:[]}));env.login();const page=env.page('companion-picker',{from_checkout:locked?'1':'0',provider_gender:'FEMALE'});page.setData({sort:2,voiceOnly:true,keyword:'听风'});page.resetFilters();await tick();assert.equal(page.data.genderIndex,locked?2:0);assert.equal(page.data.sort,0);assert.equal(page.data.keyword,'');page.onUnload();}
});
