const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const ts = createRequire(path.resolve('apps/provider-miniapp/package.json'))('typescript');
const tick = () => new Promise(resolve => setImmediate(resolve));

function setup(kind, defer = false) {
  let token = 'first', clock = 0, id = 0;
  const timers = new Map(), sockets = [], pending = [], events = new Map();
  const setTimer = (fn, ms, interval) => { timers.set(++id, { fn, at: clock + ms, interval }); return id; };
  class Socket {
    static OPEN = 1;
    constructor(url) { this.url = url; this.readyState = 0; this.closed = false; this.listeners = {}; sockets.push(this); }
    onOpen(fn) { this.listeners.open = fn; }
    onClose(fn) { this.listeners.close = fn; }
    onError(fn) { this.listeners.error = fn; }
    onMessage(fn) { this.listeners.message = fn; }
    emit(type, data) { if (type === 'open') this.readyState = 1; (this.listeners[type] || this['on' + type])?.(data); }
    close() { this.closed = true; this.readyState = 3; this.emit('close', {}); }
    send() { this.sent = (this.sent || 0) + 1; }
    message(data) { this.emit('message', { data: JSON.stringify(data) }); }
  }
  const auth = { get token() { return token; }, fetchProfile: async () => ({}), refreshAccessToken: async () => token };
  const taro = {
    eventCenter: { on: (name, fn) => events.set(name, fn) },
    connectSocket: ({ url }) => {
      const socket = new Socket(url);
      return defer ? new Promise(resolve => pending.push(() => resolve(socket))) : Promise.resolve(socket);
    },
  };
  const module = { exports: {} };
  const file = kind === 'native' ? 'apps/boss-miniapp/native/core/realtime.js'
    : kind === 'boss' ? 'apps/boss-miniapp/src/services/websocket.ts'
    : kind === 'provider' ? 'apps/provider-miniapp/src/services/websocket.ts' : 'apps/admin-web/src/utils/ws.ts';
  const code = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
  }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, WebSocket: Socket,
    wx: { connectSocket: ({ url }) => new Socket(url) },
    location: { protocol: 'http:', host: 'localhost:18081' }, window: { location: { origin: 'http://localhost:18082' } },
    process: { env: { TARO_ENV: 'h5' } }, Math, encodeURIComponent,
    setTimeout: (fn, ms) => setTimer(fn, ms, 0), clearTimeout: n => timers.delete(n),
    setInterval: (fn, ms) => setTimer(fn, ms, ms), clearInterval: n => timers.delete(n),
    require: name => {
      if (name === '@tarojs/taro') return { default: taro };
      if (name === '@/utils/auth') return { getStoredToken: () => token };
      if (name === '@/utils/request') return { BASE_URL: '/api', request: async () => ({ code: 0, data: {} }) };
      if (name === '@/utils/env') return { WS_URL: 'ws://localhost:18083' };
      if (name === '@/stores/auth') return { useAuthStore: () => auth };
      if (name === './auth') return { getSession: () => token ? { token, user: { id: '10' } } : null };
      if (name === './events') return { on: (name, fn) => events.set(name, fn), emit: (name, data) => events.get(name)?.(data) };
      if (name === './config') return { config: () => ({ socketBase: 'ws://localhost:18080' }) };
      if (name === './api') return { request: async () => ({}) };
      throw Error('Unexpected test dependency: ' + name);
    },
  });
  return {
    service: kind === 'native' ? { connect: module.exports.show, disconnect: module.exports.hide }
      : module.exports.wsService || module.exports.adminWs, sockets, pending, timers, events,
    setToken(value) { token = value; },
    async advance(ms) {
      const until = clock + ms;
      while (true) {
        const item = [...timers].filter(([, t]) => t.at <= until).sort((a, b) => a[1].at - b[1].at)[0];
        if (!item) break;
        const [key, timer] = item; clock = timer.at;
        if (timer.interval) timer.at += timer.interval; else timers.delete(key);
        timer.fn(); await tick();
      }
      clock = until; await tick();
    },
  };
}

for (const kind of ['provider', 'admin', 'boss']) {
  test(`${kind}: repeated page/app connection calls keep one connection`, async () => {
    const s = setup(kind); s.service.connect(); s.service.connect(); s.service.connect(); await tick();
    assert.equal(s.sockets.length, 1);
    s.sockets[0].emit('open'); s.service.connect(); await tick();
    assert.equal(s.sockets.length, 1);
    s.service.disconnect(); assert.equal(s.timers.size, 0);
  });
  test(`${kind}: delayed callbacks from the old socket cannot close or schedule over the replacement`, async () => {
    const s = setup(kind); s.service.connect(); await tick(); const old = s.sockets[0]; old.emit('open');
    s.setToken('second'); s.service.connect(); await tick(); const current = s.sockets[1]; current.emit('open');
    old.emit('error'); old.emit('close'); await s.advance(5000);
    assert.equal(s.sockets.length, 2); assert.equal(current.closed, false); assert.equal(old.closed, true);
    let delivered = 0; s.service.on('order_status_update', () => delivered++);
    old.message({ type: 'order_status_update', data: { id: 1 } });
    current.message({ type: 'order_status_update', data: { id: 2 } });
    assert.equal(delivered, 1); s.service.disconnect();
  });
  test(`${kind}: close/error coalesce, retry reads current token, and connected event allows HTTP resync`, async () => {
    const s = setup(kind); let connected = 0; s.service.on('connected', () => connected++);
    s.service.connect(); await tick(); const old = s.sockets[0]; old.emit('open'); old.emit('error'); old.emit('close');
    s.setToken('renewed'); await s.advance(5000);
    assert.equal(s.sockets.length, 2); assert.ok(s.sockets[1].url.endsWith('token=renewed'));
    s.sockets[1].emit('open'); assert.equal(connected, 2); s.service.disconnect();
  });
  test(`${kind}: missing heartbeat reply closes a half-open socket and reconnects`, async () => {
    const s = setup(kind); s.service.connect(); await tick(); const old = s.sockets[0]; old.emit('open');
    await s.advance(15000);
    assert.equal(old.closed, true); assert.equal(s.sockets.length, 2); s.service.disconnect();
  });
  test(`${kind}: heartbeat replies retain the connection, disconnect cancels recovery`, async () => {
    const s = setup(kind); s.service.connect(); await tick(); const socket = s.sockets[0]; socket.emit('open');
    socket.message({ type: 'pong' }); await s.advance(25000); socket.message({ type: 'pong' });
    await s.advance(12000); assert.equal(s.sockets.length, 1);
    socket.emit('error'); s.service.disconnect(); await s.advance(60000);
    assert.equal(s.sockets.length, 1); assert.equal(s.timers.size, 0);
  });
  test(`${kind}: no stored login stops the old connection`, async () => {
    const s = setup(kind); s.service.connect(); await tick(); const old = s.sockets[0]; old.emit('open');
    s.setToken(''); s.service.connect(); await s.advance(60000);
    assert.equal(old.closed, true); assert.equal(s.sockets.length, 1); assert.equal(s.timers.size, 0);
  });
}
test('provider: disconnect during asynchronous connect closes the late task without reviving it', async () => {
  const s = setup('provider', true); s.service.connect(); s.service.disconnect();
  s.pending[0](); await tick(); s.sockets[0].emit('open'); await s.advance(60000);
  assert.equal(s.sockets[0].closed, true); assert.equal(s.sockets.length, 1); assert.equal(s.timers.size, 0);
});

test('native: wallet invalidation is owner-scoped and reconnect announces a reconciliation', async () => {
  const s = setup('native'), updates = [];
  s.events.set('wallet.changed', data => updates.push(data));
  s.service.connect(); await tick(); const socket = s.sockets[0]; socket.emit('open');
  assert.equal(updates.length, 1); assert.equal(updates[0].account_id, '10');
  socket.message({ type: 'wallet_update', data: { account_id: 99 } }); assert.equal(updates.length, 1);
  socket.message({ type: 'wallet_update', data: { account_id: 10 } }); assert.equal(updates.length, 2);
  s.service.disconnect();
});
test('native: a missing pong recovers the connection; a hidden app never reconnects', async () => {
  const s = setup('native'); s.service.connect(); await tick(); const old = s.sockets[0]; old.emit('open');
  await s.advance(15000); assert.equal(old.closed, true); assert.equal(s.sockets.length, 2);
  s.service.disconnect(); await s.advance(60000); assert.equal(s.sockets.length, 2); assert.equal(s.timers.size, 0);
});
test('native: heartbeat replies preserve a healthy connection', async () => {
  const s = setup('native'); s.service.connect(); await tick(); s.sockets[0].emit('open');
  s.sockets[0].message({ type: 'pong' }); await s.advance(25000); s.sockets[0].message({ type: 'pong' });
  await s.advance(12000); assert.equal(s.sockets.length, 1); s.service.disconnect(); assert.equal(s.timers.size, 0);
});
