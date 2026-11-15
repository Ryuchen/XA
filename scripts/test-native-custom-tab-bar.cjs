const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

test('custom tab bar follows shown route and preserves safe switch behavior', () => {
  const file = path.resolve(__dirname, '../apps/boss-miniapp/native/custom-tab-bar/index.js');
  let component, route = 'pages/home/index', request, warning = '';
  const old = { Component: global.Component, wx: global.wx, getCurrentPages: global.getCurrentPages };
  global.Component = definition => { component = definition; };
  global.getCurrentPages = () => [{ route }];
  global.wx = {
    switchTab(options) { request = options; },
    showToast(options) { warning = options.title; },
  };
  try {
    delete require.cache[require.resolve(file)];
    require(file);
    const bar = { ...component.methods, data: { ...component.data }, setData(patch) { Object.assign(this.data, patch); } };
    component.lifetimes.attached.call(bar);
    assert.equal(bar.data.selected, 'home');
    require('../apps/boss-miniapp/native/core/events').emit('site-message.badge.updated', 8);
    assert.equal(bar.data.unread, 8);
    route = 'pages/orders/index';
    component.pageLifetimes.show.call(bar);
    assert.equal(bar.data.selected, 'orders');
    bar.openTab({ currentTarget: { dataset: { key: 'orders' } } });
    assert.equal(request, undefined);
    bar.openTab({ currentTarget: { dataset: { key: 'unknown' } } });
    assert.equal(request, undefined);
    bar.openTab({ currentTarget: { dataset: { key: 'mine' } } });
    assert.equal(request.url, '/pages/mine/index');
    assert.equal(bar.data.selected, 'orders', 'selected state waits for actual page navigation');
    const first = request;
    bar.openTab({ currentTarget: { dataset: { key: 'messages' } } });
    assert.equal(request, first, 'prevent duplicate switches');
    request.fail();
    assert.equal(warning, '页面打开失败，请重试');
    request.complete();
    bar.openTab({ currentTarget: { dataset: { key: 'messages' } } });
    assert.equal(request.url, '/pages/messages/index');
    request.complete();
    route = 'pages/messages/index';
    component.pageLifetimes.show.call(bar);
    assert.equal(bar.data.selected, 'messages');
    component.lifetimes.detached.call(bar);
    require('../apps/boss-miniapp/native/core/events').emit('site-message.badge.updated', 0);
    assert.equal(bar.data.unread, 8, 'detached tab releases event listener');
  } finally {
    Object.assign(global, old);
    delete require.cache[require.resolve(file)];
  }
});

test('page show sync targets the real custom tab bar without requiring it elsewhere', () => {
  const { syncTabBar } = require('../apps/boss-miniapp/native/core/navigation');
  let result;
  assert.doesNotThrow(() => syncTabBar({}, 'mine'));
  syncTabBar({ getTabBar: () => ({ setData: patch => { result = patch; } }) }, 'mine');
  assert.deepEqual(result, { selected: 'mine' });
});

test('checkout and appointment pages belong to the orders navigation section', () => {
  const { selectedTab } = require('../apps/boss-miniapp/native/core/navigation');
  for (const page of ['checkout', 'reservations', 'reservation-create', 'reservation-detail']) {
    assert.equal(selectedTab('pages/' + page + '/index'), 'orders', page);
    assert.equal(selectedTab('/pages/' + page + '/index?id=1'), 'orders', page + ' query');
  }
  assert.equal(selectedTab('pages/companions/index'), 'companions');
  assert.equal(selectedTab('pages/home/index'), 'home');
});
