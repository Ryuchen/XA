// Client behavior only; API and socket correctness are covered by backend integration tests.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.join(__dirname, '..', 'apps', 'boss-miniapp', 'native');
const tick = () => new Promise(resolve => setImmediate(resolve));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };

function setup() {
  const handlers = new Map();
  const events = {
    on(name, fn) { if (!handlers.has(name)) handlers.set(name, new Set()); handlers.get(name).add(fn); return () => handlers.get(name).delete(fn); },
    emit(name, data) { for (const fn of handlers.get(name) || []) fn(data); },
  };
  let revision = 1, user = 10, fetch = async () => ({ unread: 0 });
  const auth = { getSession: () => user ? { user: { id: user }, token: 'test' } : null, revision: () => revision };
  const badges = [], requests = [];
  const module = { exports: {} };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'core/message-badge.js'), 'utf8'), {
    module, exports: module.exports,
    require(name) { return name === './auth' ? auth : name === './events' ? events : { request: url => { requests.push(url); return fetch(); } }; },
    wx: { setTabBarBadge: data => badges.push(data.text), removeTabBarBadge: () => badges.push(null) },
  });
  return { service: module.exports, events, badges, requests, setFetch(value) { fetch = value; },
    account(value) { user = value; revision++; events.emit('session.changed'); } };
}

test('unread badge refreshes on foreground, live invalidation and reconnect', async () => {
  const s = setup(); let count = 2; s.setFetch(async () => ({ unread: count }));
  s.service.show(); await tick(); assert.equal(s.badges.at(-1), '2');
  count = 3; s.events.emit('site-message.changed'); await tick(); assert.equal(s.badges.at(-1), '3');
  s.service.hide(); s.events.emit('site-message.changed'); await tick(); assert.equal(s.badges.at(-1), '3');
  count = 1; s.service.show(); await tick(); assert.equal(s.badges.at(-1), '1');
  count = 0; s.events.emit('site-message.changed'); await tick(); assert.equal(s.badges.at(-1), null);
  assert.ok(s.requests.every(url => url === '/messages/unread-count/'));
});

test('late responses never restore a switched or hidden account badge', async () => {
  const s = setup(), old = deferred(); s.setFetch(() => old.promise); s.service.show();
  s.setFetch(async () => ({ unread: 4 })); s.account(11); await tick(); assert.equal(s.badges.at(-1), '4');
  old.resolve({ unread: 88 }); await tick(); assert.equal(s.badges.at(-1), '4');
  const pending = deferred(); s.setFetch(() => pending.promise); s.events.emit('site-message.changed');
  s.service.hide(); pending.resolve({ unread: 99 }); await tick(); assert.equal(s.badges.at(-1), '4');
  s.account(null); assert.equal(s.badges.at(-1), null);
});

test('visible message page reloads when a new notice arrives and on return', async () => {
  const handlers = new Map(), requests = []; let pageDefinition, rows = [];
  const events = {
    on(name, fn) { if (!handlers.has(name)) handlers.set(name, new Set()); handlers.get(name).add(fn); return () => handlers.get(name).delete(fn); },
    emit(name, data) { for (const fn of handlers.get(name) || []) fn(data); },
  };
  const api = { request: async url => {
    requests.push(url);
    return url.startsWith('/messages/') ? { messages: rows, total: rows.length, unread: rows.length } : { items: [], has_more: false };
  } };
  vm.runInNewContext(fs.readFileSync(path.join(root, 'pages/messages/index.js'), 'utf8'), {
    Page: value => { pageDefinition = value; },
    require(name) { return name.endsWith('/api') ? api : name.endsWith('/auth') ? { getSession: () => ({ user: { id: 1 } }) } : name.endsWith('/events') ? events : { list: value => value || [], date: value => value, go() {} }; },
    wx: {}, setInterval: () => 1, clearInterval() {},
  });
  const page = { ...pageDefinition, data: structuredClone(pageDefinition.data), setData(data) { Object.assign(this.data, data); } };
  page.onLoad(); page.onShow(); await tick(); assert.equal(page.data.unread, 0);
  rows = [{ id: 7, type: 'ORDER', title: '订单超时已取消', created_at: 'now', is_read: false }];
  events.emit('site-message.changed'); await tick();
  assert.equal(page.data.unread, 1); assert.equal(page.data.items[0].title, '订单超时已取消');
  page.onHide(); const before = requests.length; events.emit('site-message.changed'); await tick(); assert.equal(requests.length, before);
  page.onShow(); await tick(); assert.equal(page.data.unread, 1); page.onUnload();
});
