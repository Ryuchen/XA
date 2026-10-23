const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const providerRequire = createRequire(path.resolve('apps/provider-miniapp/package.json'));
const adminRequire = createRequire(path.resolve('apps/admin-web/package.json'));
const ts = providerRequire('typescript');
const tick = () => new Promise(resolve => setImmediate(resolve));
const watch = promise => promise.then(value => ({ value }), error => ({ error }));

function environment(kind) {
  const root = path.resolve(`apps/${kind === 'provider' ? 'provider-miniapp' : 'admin-web'}/src`);
  const modules = new Map(), storage = new Map(), calls = [], notices = [], navigation = [], events = [], timers = [];
  const localStorage = { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) };
  const deferred = (config, upload = false) => new Promise((resolve, reject) => calls.push({ config, upload, resolve, reject }));
  const taro = { getStorageSync: key => storage.get(key), setStorageSync: localStorage.setItem, removeStorageSync: localStorage.removeItem,
    request: config => deferred(config), uploadFile: config => deferred(config, true),
    eventCenter: { trigger: () => events.push({ token: storage.get('token'), refresh: storage.get('xa_provider_refresh_token'), user: storage.get('xa_provider_user') }) },
    showToast: notice => notices.push(notice.title), reLaunch: value => navigation.push(value.url) };
  const axios = adminRequire('axios');
  const pinia = adminRequire('pinia');
  pinia.setActivePinia(pinia.createPinia());
  const router = { replace: url => navigation.push(url), beforeEach: guard => { router.guard = guard; } };
  function load(file) {
    file = path.resolve(file);
    if (modules.has(file)) return modules.get(file).exports;
    const module = { exports: {} }; modules.set(file, module);
    const source = fs.readFileSync(file, 'utf8').replace('import.meta.env.VITE_API_BASE', "'/api/admin'");
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true } }).outputText;
    const requireModule = name => {
      if (name === '@tarojs/taro') return taro;
      if (name === 'axios') return axios;
      if (name === 'pinia') return pinia;
      if (name === 'vue-router') return { createRouter: () => router, createWebHistory: () => ({}) };
      if (name === 'element-plus') return { ElMessage: { error: message => notices.push(message) } };
      if (name === '@/router') return router;
      if (name === './env') return { BASE_URL: '/api' };
      if (name.startsWith('@/')) return load(path.join(root, name.slice(2) + '.ts'));
      if (name.startsWith('.')) return load(path.resolve(path.dirname(file), name + '.ts'));
      throw Error('Unexpected dependency: ' + name);
    };
    vm.runInNewContext(code, { module, exports: module.exports, require: requireModule, localStorage,
      setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout() {} }, { filename: file });
    return module.exports;
  }
  const auth = kind === 'provider' ? load(path.join(root, 'utils/auth.ts')) : load(path.join(root, 'stores/auth.ts')).useAuthStore();
  const api = load(path.join(root, kind === 'provider' ? 'utils/request.ts' : 'api/request.ts'));
  if (kind === 'admin') api.default.defaults.adapter = config => deferred(config);
  const loginData = id => kind === 'provider'
    ? { token: `token-${id}`, refreshToken: `refresh-${id}`, userInfo: { id, username: id, role: 'provider' } }
    : { token: `token-${id}`, refresh: `refresh-${id}`, profile: { id, username: id, permissions: [] } };
  function respond(call, status = 200, body = { code: 0, data: {} }) {
    assert(call, 'Expected an issued request');
    if (kind === 'provider') call.resolve({ statusCode: status, data: call.upload ? JSON.stringify(body) : body });
    else {
      const response = { status, data: body, config: call.config, headers: {}, statusText: String(status) };
      if (status >= 400) call.reject(new axios.AxiosError('HTTP ' + status, undefined, call.config, undefined, response));
      else call.resolve(response);
    }
  }
  return { auth, api, calls, notices, navigation, events, loginData, respond,
    async issued(index) { await tick(); assert(calls[index], 'Expected request ' + index); return calls[index]; },
    read: () => kind === 'provider' ? api.request('/orders/') : api.request({ url: '/orders/', method: 'get' }),
    upload: () => kind === 'provider' ? api.uploadFile('/orders/report/', '/tmp/proof.png', {}, 'proof_image')
      : api.request({ url: '/orders/report/', method: 'post', data: 'multipart body' }),
    login(id) { const data = loginData(id); if (kind === 'provider') auth.persistLoginData(data); else { auth.setToken(data.token, data.refresh); auth.profile = data.profile; } },
    clear: () => kind === 'provider' ? auth.clearStoredUser() : auth.clear(),
    token: () => kind === 'provider' ? auth.getStoredToken() : auth.token,
    refresh: () => kind === 'provider' ? auth.getStoredRefreshToken() : auth.refresh,
    loginRequest: id => kind === 'provider' ? auth.accountLogin({ username: id, password: 'test-only' }) : auth.login(id, 'test-only'),
    runTimers() { timers.splice(0).forEach(fn => fn()); },
    routeGuard: () => { load(path.join(root, 'router/index.ts')); return router.guard; },
  };
}

module.exports = { environment, watch, tick };

if (require.main === module) {
for (const kind of ['provider', 'admin']) {
  for (const status of [200, 401]) test(`${kind}: old ${status} response cannot affect the next account`, async () => {
    const s = environment(kind); s.login('A'); const pending = watch(s.read()); const call = await s.issued(0);
    s.clear(); s.login('B'); s.respond(call, status, { code: status === 200 ? 0 : 401, data: { owner: 'A' } });
    await tick();
    assert.equal(s.calls.length, 1, 'Old request must not refresh or replay under B');
    assert((await pending).error, 'Old-account data must be rejected');
    assert.equal(s.token(), 'token-B'); assert.equal(s.refresh(), 'refresh-B');
    assert.equal(s.notices.length, 0); assert.equal(s.navigation.length, 0);
  });

  for (const after of ['logout', 'switch']) test(`${kind}: late refresh success after ${after} cannot restore A`, async () => {
    const s = environment(kind); s.login('A'); const pending = watch(s.read());
    s.respond(await s.issued(0), 401); const refresh = await s.issued(1);
    s.clear(); if (after === 'switch') s.login('B');
    s.respond(refresh, 200, { code: 0, data: { token: 'late-A' } }); await tick();
    assert.equal(s.calls.length, 2, 'Stale refresh must not retry the original request');
    assert((await pending).error); assert.equal(s.token(), after === 'switch' ? 'token-B' : '');
    assert.equal(s.navigation.length, 0);
  });

  test(`${kind}: late refresh rejection cannot clear B`, async () => {
    const s = environment(kind); s.login('A'); const pending = watch(s.read());
    s.respond(await s.issued(0), 401); const refresh = await s.issued(1);
    s.clear(); s.login('B'); s.respond(refresh, 401, { code: 401 });
    assert((await pending).error); s.runTimers(); assert.equal(s.token(), 'token-B');
    assert.equal(s.notices.length, 0); assert.equal(s.navigation.length, 0);
  });

  for (const failure of ['network', 'server']) test(`${kind}: ${failure} failure during refresh preserves the session`, async () => {
    const s = environment(kind); s.login('A'); const pending = watch(s.read());
    s.respond(await s.issued(0), 401); const refresh = await s.issued(1);
    if (failure === 'network') refresh.reject(new Error('temporary outage'));
    else s.respond(refresh, 503, { code: 503, msg: 'temporary outage' });
    assert((await pending).error); s.runTimers();
    assert.equal(s.token(), 'token-A'); assert.equal(s.refresh(), 'refresh-A'); assert.equal(s.navigation.length, 0);
  });

  test(`${kind}: simultaneous 401s share one refresh and both retry`, async () => {
    const s = environment(kind); s.login('A'); const one = watch(s.read()), two = watch(s.read());
    s.respond(await s.issued(0), 401); s.respond(await s.issued(1), 401);
    const refresh = await s.issued(2); assert.equal(s.calls.length, 3);
    s.respond(refresh, 200, { code: 0, data: { token: 'renewed-A' } });
    for (const i of [3, 4]) {
      const retry = await s.issued(i);
      assert.equal((retry.config.header || retry.config.headers).Authorization, 'Bearer renewed-A');
      s.respond(retry, 200, { code: 0, data: { ok: true } });
    }
    assert(!(await one).error); assert(!(await two).error); assert.equal(s.calls.length, 5);
  });

  test(`${kind}: delayed 401 uses already refreshed token without a second refresh`, async () => {
    const s = environment(kind); s.login('A'); const one = watch(s.read()), two = watch(s.read());
    s.respond(await s.issued(0), 401); await s.issued(1);
    s.respond(await s.issued(2), 200, { code: 0, data: { token: 'renewed-A' } });
    s.respond(await s.issued(3)); assert(!(await one).error);
    s.respond(s.calls[1], 401); const retry = await s.issued(4);
    assert(!retry.config.url.includes('refresh'), 'Delayed 401 should reuse the current access token');
    s.respond(retry); assert(!(await two).error); assert.equal(s.calls.length, 5);
  });

  test(`${kind}: definitive refresh 401 clears once without a retry loop`, async () => {
    const s = environment(kind); s.login('A'); const one = watch(s.read()), two = watch(s.read());
    s.respond(await s.issued(0), 401); s.respond(await s.issued(1), 401);
    s.respond(await s.issued(2), 401, { code: 401 });
    assert((await one).error); assert((await two).error); s.runTimers();
    assert.equal(s.token(), ''); assert.equal(s.navigation.length, 1); assert.equal(s.notices.length, 1);
  });

  test(`${kind}: upload completion from A is discarded after B login`, async () => {
    const s = environment(kind); s.login('A'); const pending = watch(s.upload()); const call = await s.issued(0);
    s.clear(); s.login('B'); s.respond(call, 200, { code: 0, data: { image: 'private-A.png' } });
    assert((await pending).error); assert.equal(s.token(), 'token-B');
  });

  test(`${kind}: a later login attempt wins even if the first returns last`, async () => {
    const s = environment(kind); const one = watch(s.loginRequest('A')), two = watch(s.loginRequest('B'));
    await s.issued(1); s.respond(s.calls[1], 200, { code: 0, data: s.loginData('B') }); assert(!(await two).error);
    s.respond(s.calls[0], 200, { code: 0, data: s.loginData('A') }); assert((await one).error);
    assert.equal(s.token(), 'token-B');
  });

  test(`${kind}: logout cancels an outstanding login result`, async () => {
    const s = environment(kind); const pending = watch(s.loginRequest('A')); const call = await s.issued(0);
    s.clear(); s.respond(call, 200, { code: 0, data: s.loginData('A') });
    assert((await pending).error); assert.equal(s.token(), '');
  });

  test(`${kind}: invalid refreshed token ends the session without repeated refresh`, async () => {
    const s = environment(kind); s.login('A'); const pending = watch(s.read());
    s.respond(await s.issued(0), 401);
    s.respond(await s.issued(1), 200, { code: 0, data: { token: 'revoked-A' } });
    s.respond(await s.issued(2), 401, { code: 401 });
    assert((await pending).error); s.runTimers();
    assert.equal(s.token(), ''); assert.equal(s.calls.length, 3); assert.equal(s.navigation.length, 1);
  });

  test(`${kind}: B refresh is independent of A's unfinished refresh`, async () => {
    const s = environment(kind); s.login('A'); const old = watch(s.read());
    s.respond(await s.issued(0), 401); const oldRefresh = await s.issued(1);
    s.clear(); s.login('B'); const current = watch(s.read()); s.respond(await s.issued(2), 401);
    const newRefresh = await s.issued(3);
    s.respond(oldRefresh, 200, { code: 0, data: { token: 'late-A' } }); assert((await old).error);
    // The old operation's finally must not clear B's single-flight refresh.
    const concurrent = watch(s.read()); s.respond(await s.issued(4), 401); await tick();
    assert.equal(s.calls.length, 5);
    s.respond(newRefresh, 200, { code: 0, data: { token: 'renewed-B' } });
    s.respond(await s.issued(5)); s.respond(await s.issued(6));
    assert(!(await current).error); assert(!(await concurrent).error); assert.equal(s.token(), 'renewed-B');
  });
}

test('provider: login publishes a complete session and removes the previous refresh token', () => {
  const s = environment('provider'); s.login('A');
  const data = s.loginData('B'); delete data.refreshToken; s.auth.persistLoginData(data);
  assert.equal(s.refresh(), '');
  const event = s.events.at(-1); assert.equal(event.token, 'token-B'); assert.equal(event.user.id, 'B'); assert(!event.refresh);
});

test('provider: old login-expiry navigation cannot redirect a new login', async () => {
  const s = environment('provider'); s.login('A'); const pending = watch(s.read());
  s.respond(await s.issued(0), 401); s.respond(await s.issued(1), 401, { code: 401 });
  assert((await pending).error); s.login('B'); s.runTimers();
  assert.equal(s.navigation.length, 0); assert.equal(s.token(), 'token-B');
});

test('admin: late profile cannot replace the newly logged-in profile', async () => {
  const s = environment('admin'); s.login('A'); const pending = watch(s.auth.fetchProfile());
  const call = await s.issued(0); s.clear(); s.login('B');
  s.respond(call, 200, { code: 0, data: { id: 'A', permissions: ['all'] } });
  assert((await pending).error); assert.equal(s.auth.profile.id, 'B');
});

test('admin: failed login does not refresh or clear an existing session', async () => {
  const s = environment('admin'); s.login('A'); const pending = watch(s.loginRequest('wrong'));
  s.respond(await s.issued(0), 401, { code: 401, msg: '账号或密码错误' }); await tick();
  assert.equal(s.calls.length, 1); assert((await pending).error); assert.equal(s.token(), 'token-A');
  assert.equal(s.navigation.length, 0);
});

for (const action of ['login', 'refresh']) test(`admin: ${action} does not attach an expired access token`, async () => {
  const s = environment('admin'); s.login('A');
  const pending = watch(action === 'login' ? s.loginRequest('B') : s.auth.refreshAccessToken());
  const call = await s.issued(0); assert(!call.config.headers.Authorization);
  s.respond(call, 200, { code: 0, data: action === 'login' ? s.loginData('B') : { token: 'renewed-A' } });
  assert(!(await pending).error);
});

test('admin: a request queued before account switching cannot be sent as the new account', async () => {
  const s = environment('admin'); s.login('A'); const pending = watch(s.read()); s.clear(); s.login('B');
  assert((await pending).error); assert.equal(s.calls.length, 0);
});

for (const situation of ['network', 'switch']) test(`admin: route profile ${situation} failure preserves current credentials`, async () => {
  const s = environment('admin'); s.login('A'); s.auth.profile = null;
  const guard = s.routeGuard(); const pending = watch(guard({ meta: {}, path: '/orders', fullPath: '/orders' }));
  const call = await s.issued(0);
  if (situation === 'switch') { s.clear(); s.login('B'); }
  call.reject(new Error('temporary failure'));
  assert.equal((await pending).value, false); assert.equal(s.token(), situation === 'switch' ? 'token-B' : 'token-A');
});

test('provider: upload 401 refreshes once and preserves the upload payload', async () => {
  const s = environment('provider'); s.login('A'); const pending = watch(s.upload());
  const original = await s.issued(0); s.respond(original, 401);
  s.respond(await s.issued(1), 200, { code: 0, data: { token: 'renewed-A' } });
  const retry = await s.issued(2);
  assert.equal(retry.config.header.Authorization, 'Bearer renewed-A');
  for (const key of ['filePath', 'name', 'formData']) assert.deepEqual(retry.config[key], original.config[key]);
  s.respond(retry, 200, { code: 0, data: { image: 'proof.png' } });
  assert.equal((await pending).value.data.image, 'proof.png');
});
}
