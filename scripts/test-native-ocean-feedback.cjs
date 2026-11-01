const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Exercise actual component lifetimes with delayed native layout callbacks.
function component(name, initial = {}) {
  let definition, now = 0, nextId = 0;
  const timers = new Map(), ticks = [], measurements = [], events = [], updates = [];
  const context = {
    Component(value) { definition = value; }, wx: { nextTick(fn) { ticks.push(fn); } },
    setTimeout(fn, delay) { const id = ++nextId; timers.set(id, { at: now + delay, fn }); return id; },
    clearTimeout(id) { timers.delete(id); },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../apps/boss-miniapp/native/components', name, 'index.js'), 'utf8'), context);
  const host = { data: { ...definition.data }, properties: { ...initial },
    setData(patch, callback) { updates.push(patch); Object.assign(this.data, patch); callback?.(); },
    triggerEvent(type, detail) { events.push({ type, ...detail }); },
    createSelectorQuery() { const query = { select() { return query; }, boundingClientRect(callback) { measurements.push(callback); return query; }, exec() {} }; return query; },
  };
  for (const [key, fn] of Object.entries(definition.methods)) host[key] = fn.bind(host);
  const api = { host, events, updates,
    ready() { definition.lifetimes.attached?.call(host); definition.lifetimes.ready?.call(host); },
    detach() { definition.lifetimes.detached.call(host); },
    expanded(value) { host.properties.expanded = value; definition.properties.expanded.observer.call(host); },
    measure(height) { measurements.shift()?.({ height }); },
    flush() { while (ticks.length) ticks.shift()(); },
    tick(ms) { const target = now + ms; while (true) { const entry = [...timers].filter(([, t]) => t.at <= target).sort((a,b) => a[1].at-b[1].at)[0]; if (!entry) break; timers.delete(entry[0]); now = entry[1].at; entry[1].fn(); } now = target; },
  };
  api.ready(); return api;
}
test('filter expansion ignores stale measurements and restores auto height for dynamic content', () => {
  const x = component('expand-region', { expanded: false });
  x.expanded(true); x.measure(100); assert.equal(x.host.data.height, '0px');
  x.measure(240); assert.equal(x.host.data.height, '240px');
  x.tick(350); assert.equal(x.host.data.height, 'auto');
  x.expanded(false); x.measure(320); x.tick(20); assert.equal(x.host.data.height, '0px');
});
test('rapid close and reopen cannot collapse the latest filter state', () => {
  const x = component('expand-region', { expanded: true }); x.measure(180);
  x.expanded(false); x.measure(180); x.expanded(true); x.measure(260);
  x.tick(400); assert.equal(x.host.data.height, 'auto');
  x.expanded(false); const count = x.updates.length; x.detach(); x.measure(200); x.tick(500);
  assert.equal(x.updates.length, count);
});
test('a help sheet closed before the native nextTick cannot reopen itself', () => {
  const x = component('info-sheet'); x.host.show(); x.host.hide(); x.flush();
  assert.equal(x.host.data.open, false); x.tick(350); assert.equal(x.host.data.visible, false);
  assert.equal(x.events.at(-1).open, false);
});
test('reopening a closing sheet cancels stale dismissal and detaching clears queued work', () => {
  const x = component('info-sheet'); x.host.show(); x.flush(); x.host.hide();
  x.tick(100); x.host.show(); x.flush(); x.tick(500);
  assert.equal(x.host.data.open, true); assert.equal(x.host.data.visible, true);
  x.host.hide(); x.detach(); const count = x.updates.length; x.tick(500); x.flush();
  assert.equal(x.updates.length, count);
});
test('leaving the page releases its sheet lock and cancels pending open and close callbacks', () => {
  const x = component('info-sheet');
  x.host.show(); x.host.closeNow(); x.flush(); x.tick(500);
  assert.equal(x.host.data.visible, false); assert.equal(x.host.data.open, false);
  assert.equal(x.events.at(-1).open, false);
  x.host.show(); x.flush(); x.host.hide(); x.host.closeNow();
  const eventCount = x.events.length; x.tick(500);
  assert.equal(x.host.data.visible, false); assert.equal(x.events.length, eventCount);
});
