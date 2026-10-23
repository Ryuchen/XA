const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const { tabs, selectedTab } = require(path.join(root, 'core/navigation'));

function navigation(route) {
  let definition;
  global.Component = value => { definition = value; };
  global.getCurrentPages = () => [{ route }];
  const componentFile = path.join(root, 'components/bottom-navigation/index.js');
  delete require.cache[require.resolve(componentFile)];
  require(componentFile);
  const component = {
    ...definition.methods,
    data: structuredClone(definition.data),
    setData(patch) { Object.assign(this.data, patch); },
  };
  definition.lifetimes.attached.call(component);
  return { component, show: () => definition.pageLifetimes.show.call(component) };
}
const tap = key => ({ currentTarget: { dataset: { key } } });

test('all registered pages provide native tabs or one persistent navigation outside conditional content', () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'));
  assert.deepEqual(manifest.tabBar.list.map(tab => tab.pagePath), tabs.map(tab => 'pages/' + tab.key + '/index'));
  assert.equal(manifest.usingComponents['bottom-navigation'], '/components/bottom-navigation/index');
  for (const route of manifest.pages) {
    const template = fs.readFileSync(path.join(root, route + '.wxml'), 'utf8').trim();
    const nativeTab = manifest.tabBar.list.some(tab => tab.pagePath === route);
    assert.equal((template.match(/<bottom-navigation\b/g) || []).length, nativeTab ? 0 : 1, route);
    if (!nativeTab) assert.match(template, /<bottom-navigation\s*\/>$/, route + ' navigation must survive loading, errors, and login states');
  }
});

test('nested pages highlight their primary destination and refresh it on page show', () => {
  for (const [route, expected] of [
    ['pages/catalog/index', 'home'], ['pages/service/index', 'home'], ['pages/checkout/index', 'home'],
    ['pages/companion-detail/index', 'companions'], ['pages/order-detail/index', 'orders'],
    ['pages/chat/index', 'messages'], ['pages/message-detail/index', 'messages'],
    ['pages/customer/wallet/index', 'mine'], ['pages/coupon/mine/index', 'mine'],
    ['pages/profile/index', 'mine'],
  ]) assert.equal(selectedTab(route), expected, route);
  const { component, show } = navigation('pages/service/index');
  assert.equal(component.data.selected, 'home');
  global.getCurrentPages = () => [{ route: 'pages/companion-picker/index' }];
  show();
  assert.equal(component.data.selected, 'companions');
});

test('tapping a highlighted parent from a detail returns to its tab and ignores repeated or invalid taps', () => {
  const calls = [];
  global.wx = { switchTab: options => calls.push(options), showToast() {} };
  const { component } = navigation('pages/order-detail/index');
  component.openTab(tap('orders'));
  component.openTab(tap('orders'));
  component.openTab(tap('../login'));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/pages/orders/index');
  calls[0].complete();
  component.openTab(tap('messages'));
  assert.equal(calls[1].url, '/pages/messages/index');
});

test('navigation failures remain retryable and do not change the selected destination', () => {
  const calls = [], notices = [];
  global.wx = { switchTab: options => calls.push(options), showToast: options => notices.push(options.title) };
  const { component } = navigation('pages/wallet/index');
  component.openTab(tap('home'));
  calls[0].fail({ errMsg: 'offline' });
  calls[0].complete();
  assert.equal(component.data.selected, 'mine');
  assert.equal(notices.length, 1);
  component.openTab(tap('home'));
  assert.equal(calls.length, 2);
  calls[1].complete();
  wx.switchTab = () => { throw new Error('bridge unavailable'); };
  component.openTab(tap('home'));
  assert.equal(component._switching, false);
  assert.equal(notices.length, 2);
});
