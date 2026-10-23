const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const appRoot = path.resolve(__dirname, '../apps/provider-miniapp');
const ts = createRequire(path.join(appRoot, 'package.json'))('typescript');
const tick = () => new Promise(resolve => setImmediate(resolve));
const pending = id => ({ id, status: 'PENDING' });

function setup() {
  let revision = 1, token = 'provider-1', hidden = false, cooldown = 0, nextTimer = 0;
  const timers = new Map(), events = new Map(), socket = new Map(), requests = [], sounds = { played: 0, stopped: 0 };
  const document = {
    get hidden() { return hidden; },
    addEventListener: (name, fn) => events.set(name, fn),
    removeEventListener: name => events.delete(name),
  };
  const dependencies = {
    '@tarojs/taro': { default: { eventCenter: {
      on: (name, fn) => events.set(name, fn), off: name => events.delete(name),
    } } },
    '@/utils/auth': { getSessionRevision: () => revision, getStoredToken: () => token },
    '@/utils/orderNetwork': { cooldownRemaining: () => cooldown },
    '@/utils/orderAlertSound': { orderAlertSound: {
      prepare() {}, play() { sounds.played++; }, stop() { sounds.stopped++; }, dispose() {},
    } },
    '@/utils/request': { request: url => new Promise((resolve, reject) => requests.push({ url, resolve, reject })) },
    './websocket': { wsService: { on: (name, fn) => { socket.set(name, fn); return () => socket.delete(name); } } },
  };
  function load(file) {
    const module = { exports: {} };
    const code = ts.transpileModule(fs.readFileSync(path.join(appRoot, file), 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
    }).outputText;
    vm.runInNewContext(code, {
      module, exports: module.exports, process: { env: { TARO_ENV: 'h5' } }, document, Math,
      require: name => { if (!(name in dependencies)) throw Error('Unexpected import ' + name); return dependencies[name]; },
      setInterval: (fn, ms) => { timers.set(++nextTimer, { fn, ms, interval: true }); return nextTimer; },
      clearInterval: id => timers.delete(id),
      setTimeout: (fn, ms) => { timers.set(++nextTimer, { fn, ms }); return nextTimer; },
      clearTimeout: id => timers.delete(id),
    });
    return module.exports;
  }
  dependencies['@/utils/coalescedRefresh'] = load('src/utils/coalescedRefresh.ts');
  const { orderAlerts } = load('src/services/orderAlerts.ts');
  dependencies['./orderAlerts'] = { orderAlerts };
  const { fetchOrders } = load('src/services/order.ts');
  return {
    service: orderAlerts, fetchOrders, timers, requests, sounds, events, socket,
    async reply(orders, code = 0, index = requests.length - 1) { requests[index].resolve({ code, data: orders }); await tick(); },
    async poll() { [...timers.values()].find(t => t.interval).fn(); await tick(); },
    async push(type = 'order_status_update') {
      socket.get(type)?.({ id: 99, status: 'PENDING' });
      const item = [...timers].find(([, t]) => !t.interval);
      if (item) { timers.delete(item[0]); await item[1].fn(); }
      await tick();
    },
    account(value) { revision++; token = value; events.get('xa:auth.changed')?.(); },
    visibility(value) { hidden = value; events.get('visibilitychange')?.(); },
    cooldown(value) { cooldown = value; },
  };
}

test('initial snapshot is silent; only unseen pending IDs ring once per refresh', async () => {
  const s = setup(); s.service.show(); await s.reply([pending(1)]);
  assert.equal(s.sounds.played, 0);
  await s.poll(); await s.reply([pending(1)]); assert.equal(s.sounds.played, 0);
  await s.poll(); await s.reply([pending(1), pending(2), pending(3)]); assert.equal(s.sounds.played, 1);
  await s.poll(); await s.reply([]);
  await s.poll(); await s.reply([pending(2), { id: 4, status: 'GRABBED' }]);
  assert.equal(s.sounds.played, 1, 'removal, returned orders and status changes do not repeat the bell');
  s.service.dispose();
});

test('an initially empty pool establishes a baseline before its first new order', async () => {
  const s = setup(); s.service.show(); await s.reply([]);
  await s.poll(); await s.reply([pending(1)]); assert.equal(s.sounds.played, 1);
  s.service.dispose();
});

test('workbench and notification monitor share a single in-flight pool request', async () => {
  const s = setup(); s.service.show();
  let updates = 0; const off = s.service.onPoolUpdate(() => updates++);
  const a = s.fetchOrders({ status: 'pending', role: 'provider' });
  const b = s.service.loadPending();
  assert.equal(a, b); assert.equal(s.requests.length, 1);
  await s.reply([pending(1)]); await a;
  assert.equal(updates, 1); off();
  await s.poll(); await s.reply([pending(2)]); assert.equal(updates, 1);
  s.service.dispose();
});

test('failed reads preserve the baseline and recover without false alerts', async () => {
  const s = setup(); s.service.show(); await s.reply([pending(1)]);
  await s.poll(); await s.reply([pending(2)], 1); assert.equal(s.sounds.played, 0);
  await s.poll(); s.requests.at(-1).reject(Error('offline')); await tick();
  await s.poll(); await s.reply([pending(1), pending(2)]); assert.equal(s.sounds.played, 1);
  s.service.dispose();
});

test('polling discovers pass-delayed orders and observes the server cooldown', async () => {
  const s = setup(); s.service.show(); await s.reply([]);
  s.cooldown(5); await s.poll(); assert.equal(s.requests.length, 1);
  s.cooldown(0); await s.poll(); await s.reply([pending(7)]); assert.equal(s.sounds.played, 1);
  assert.equal(s.requests[0].url, '/orders/orders/?status=pending&role=provider');
  s.service.dispose();
});

test('a socket burst schedules one pool read; reconnect resync does not repeat alerts', async () => {
  const s = setup(); s.service.show(); await s.reply([pending(1)]);
  for (let i = 0; i < 50; i++) s.socket.get('order_status_update')({ id: 2, status: 'PENDING' });
  assert.equal([...s.timers.values()].filter(t => !t.interval).length, 1);
  // Do not await the refresh while its mocked HTTP request is still pending.
  const update = s.push(); await tick(); await s.reply([pending(1), pending(2)]); await update;
  assert.equal(s.requests.length, 2); assert.equal(s.sounds.played, 1);
  const reconnect = s.push('connected'); await tick(); await s.reply([pending(1), pending(2)]); await reconnect;
  assert.equal(s.sounds.played, 1); s.service.dispose();
});

test('hidden apps stop polling and discard late responses; resume preserves deduplication', async () => {
  const s = setup(); s.service.show(); await s.reply([pending(1)]);
  await s.poll(); const late = s.requests.length - 1; s.service.hide();
  assert.equal(s.timers.size, 0); assert.equal(s.socket.size, 0);
  await s.reply([pending(2)], 0, late); assert.equal(s.sounds.played, 0);
  s.service.show(); await s.reply([pending(1), pending(2)]); assert.equal(s.sounds.played, 1);
  s.service.hide(); s.service.show(); await s.reply([pending(1), pending(2)]); assert.equal(s.sounds.played, 1);
  s.service.dispose(); assert.equal(s.events.size, 0);
});

test('browser tab switches keep new-order alerts active and do not repeat them on return', async () => {
  const s = setup(); s.service.show(); await s.reply([pending(1)]);
  await s.poll(); s.visibility(true); await s.reply([pending(2)]);
  assert.equal(s.sounds.played, 1);
  await s.poll(); await s.reply([pending(2), pending(3)]); assert.equal(s.sounds.played, 2);
  s.visibility(false); await s.reply([pending(2), pending(3)]); assert.equal(s.sounds.played, 2);
  s.service.dispose();
});

test('logout invalidates outstanding reads and another account gets a silent baseline', async () => {
  const s = setup(); s.service.show(); await s.reply([pending(1)]);
  await s.poll(); const old = s.requests.length - 1;
  s.account(''); assert.equal(s.timers.size, 0);
  s.account('provider-2'); await s.reply([pending(20)]); assert.equal(s.sounds.played, 0);
  await s.reply([pending(2)], 0, old); assert.equal(s.sounds.played, 0);
  await s.poll(); await s.reply([pending(20), pending(21)]); assert.equal(s.sounds.played, 1);
  s.service.dispose();
});

test('packaged bell is a short, nonempty mono PCM WAV', () => {
  const wav = fs.readFileSync(path.join(appRoot, 'src/assets/new-order-bell.wav'));
  assert.equal(wav.toString('ascii', 0, 4), 'RIFF');
  assert.equal(wav.toString('ascii', 8, 12), 'WAVE');
  assert.equal(wav.readUInt16LE(20), 1); assert.equal(wav.readUInt16LE(22), 1);
  const duration = wav.readUInt32LE(40) / wav.readUInt32LE(28);
  assert.ok(duration > 0.6 && duration < 0.7);
  assert.ok(wav.length < 30000);
});

function setupSound(target) {
  const events = new Map(), contexts = [], native = [], tones = [];
  class WebAudio {
    constructor() { this.state = 'suspended'; this.currentTime = 10; contexts.push(this); }
    resume() { this.state = 'running'; return Promise.resolve(); }
    close() { this.state = 'closed'; return Promise.resolve(); }
    createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {}, exponentialRampToValueAtTime() {} }, connect() {}, disconnect() {} }; }
    createOscillator() {
      const tone = { frequency: {}, connect() {}, disconnect() {}, start(time) { this.started = time; }, stop(time) { this.stopped = time; } };
      tones.push(tone); return tone;
    }
  }
  const taro = { createInnerAudioContext() {
    const audio = { played: 0, stopped: 0, destroyed: false, play() { this.played++; }, stop() { this.stopped++; }, destroy() { this.destroyed = true; }, onError(fn) { this.error = fn; } };
    native.push(audio); return audio;
  } };
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(appRoot, 'src/utils/orderAlertSound.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, process: { env: { TARO_ENV: target } },
    window: { AudioContext: WebAudio }, document: {
      addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name),
    }, require: name => name === '@tarojs/taro' ? { default: taro } : { default: '/assets/new-order-bell.wav' },
  });
  return { sound: module.exports.orderAlertSound, contexts, native, tones, events };
}

test('H5 unlock is silent; one alert ends after 650ms with no loop or deferred sound', async () => {
  const s = setupSound('h5'); s.sound.prepare(); s.sound.prepare();
  assert.equal(s.events.size, 3);
  s.sound.play(); assert.equal(s.tones.length, 0);
  s.events.get('click')(); await tick(); assert.equal(s.contexts[0].state, 'running');
  assert.equal(s.tones.length, 0, 'unlock does not replay alerts received before the gesture');
  s.sound.play(); assert.equal(s.tones.length, 3);
  for (const tone of s.tones) { assert.equal(tone.started, 10); assert.equal(tone.stopped, 10.65); }
  s.sound.dispose(); assert.equal(s.contexts[0].state, 'closed'); assert.equal(s.events.size, 0);
});

test('miniapp uses a reusable non-looping packaged bell and recovers after audio errors', () => {
  const s = setupSound('weapp'); s.sound.prepare(); s.sound.play(); s.sound.play();
  assert.equal(s.native.length, 1); assert.equal(s.native[0].played, 2);
  assert.equal(s.native[0].loop, false); assert.equal(s.native[0].autoplay, false);
  assert.equal(s.native[0].src, '/assets/new-order-bell.wav');
  s.native[0].error(); assert.equal(s.native[0].destroyed, true);
  s.sound.play(); assert.equal(s.native.length, 2); assert.equal(s.native[1].played, 1);
  s.sound.dispose(); assert.equal(s.native[1].destroyed, true);
});
