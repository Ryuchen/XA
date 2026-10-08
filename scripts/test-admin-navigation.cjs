const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const adminRequire = createRequire(path.resolve('apps/admin-web/package.json'));
const ts = adminRequire('typescript');

function loadNavigation() {
  const cache = new Map();
  let config, guard;
  const auth = { token: 'test-session', profile: {}, permissions: [],
    hasPerm(code) { return !code || (Array.isArray(code) ? code : [code]).some(item => this.permissions.includes(item)); } };
  function load(file) {
    file = path.resolve(file);
    if (cache.has(file)) return cache.get(file).exports;
    const module = { exports: {} }; cache.set(file, module);
    const source = fs.readFileSync(file, 'utf8');
    const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
    function requireModule(name) {
      if (name === '@/stores/auth') return { useAuthStore: () => auth };
      if (name === 'vue-router') return { createWebHistory: () => ({}), createRouter(options) {
        config = options; return { beforeEach(callback) { guard = callback; } };
      } };
      if (name.startsWith('.')) return load(path.resolve(path.dirname(file), name + '.ts'));
      throw Error('Unexpected import ' + name);
    }
    vm.runInNewContext(code, { require: requireModule, module, exports: module.exports }, { filename: file });
    return module.exports;
  }
  const nav = load('apps/admin-web/src/router/navigation.ts');
  load('apps/admin-web/src/router/index.ts');
  const routes = config.routes.find(route => route.path === '/').children;
  const allowed = (...permissions) => {
    auth.permissions = permissions;
    return nav.buildNavigation(routes, code => auth.hasPerm(code));
  };
  return { nav, routes, auth, allowed, guard: to => guard(to) };
}

test('every business URL belongs to exactly one center, with its original permission', () => {
  const { nav, routes } = loadNavigation();
  const business = routes.filter(route => route.path !== '403');
  const paths = nav.navigationDomains.flatMap(domain => domain.centers.flatMap(center => center.features.map(feature => feature.path)));
  assert.equal(paths.length, 35);
  assert.equal(new Set(paths).size, paths.length);
  assert.deepEqual([...paths].sort(), [...business.map(route => route.path)].sort());
  for (const route of business) {
    assert(route.meta.perm, route.path + ' must retain authorization');
    assert(route.meta.center && route.meta.title && route.meta.group);
  }
  assert.equal(routes.find(route => route.path === 'escorts').meta.perm, 'escort:view');
  assert.equal(routes.find(route => route.path === 'provider-registrations').meta.perm, 'registration:view');
});

test('full access produces 7 categories, 15 centers and 35 reachable features', () => {
  const { nav, routes } = loadNavigation();
  const result = nav.buildNavigation(routes, () => true);
  assert.equal(result.length, 7);
  assert.equal(result.flatMap(domain => domain.centers).length, 15);
  assert.equal(nav.flattenNavigation(result).length, 35);
});

test('each individual permission reveals only authorized pages, including search results', () => {
  const { nav, routes, auth, allowed } = loadNavigation();
  const permissions = new Set(routes.flatMap(route => route.meta?.perm || []));
  for (const permission of permissions) {
    const domains = allowed(permission);
    for (const feature of nav.searchNavigation(domains, '')) {
      assert(auth.hasPerm(routes.find(route => route.path === feature.path).meta.perm), feature.path);
    }
    assert(domains.every(domain => domain.centers.every(center => center.features.length > 0)));
  }
});

test('registration-only reviewer enters its own page without requiring escort permission', async () => {
  const { nav, allowed, guard } = loadNavigation();
  const domains = allowed('registration:view');
  assert.equal(nav.flattenNavigation(domains)[0].path, 'provider-registrations');
  assert.equal(domains[0].centers[0].title, '陪玩中心');
  assert.equal(nav.searchNavigation(domains, '通行证').length, 0);
  assert.equal((await guard({ meta: { perm: 'dashboard:view' }, path: '/dashboard' })).path, '/provider-registrations');
  assert.equal((await guard({ meta: { perm: 'escort:view' }, path: '/escorts' })).path, '/403');
});

test('finance-only staff have a useful entry and no unrelated menus', async () => {
  const { allowed, guard } = loadNavigation();
  const domains = allowed('withdraw:view');
  assert.equal(domains.length, 1);
  assert.equal(domains[0].title, '财务结算');
  assert.equal(domains[0].centers[0].features.length, 1);
  assert.equal((await guard({ meta: { perm: 'dashboard:view' }, path: '/dashboard' })).path, '/withdrawals');
});

test('group dispatch view OR edit permission works even without order permission', async () => {
  const { nav, routes, allowed, guard } = loadNavigation();
  for (const permission of ['dispatch_config:view', 'dispatch_config:edit']) {
    const domains = allowed(permission);
    assert.equal(nav.flattenNavigation(domains).length, 1);
    assert.equal(nav.flattenNavigation(domains)[0].path, 'kook-dispatch');
    assert.equal((await guard({ meta: { perm: 'dashboard:view' }, path: '/dashboard' })).path, '/kook-dispatch');
    assert.equal(await guard({ meta: routes.find(route => route.path === 'kook-dispatch').meta, path: '/kook-dispatch' }), true);
  }
});

test('empty permissions leave no empty categories or accessible search results', async () => {
  const { nav, allowed, guard } = loadNavigation();
  const domains = allowed();
  assert.equal(domains.length, 0);
  assert.equal(nav.searchNavigation(domains, '').length, 0);
  assert.equal((await guard({ meta: { perm: 'dashboard:view' }, path: '/dashboard' })).path, '/403');
});

test('old names and operational keywords find renamed features, with exact matches first', () => {
  const { nav, routes } = loadNavigation();
  const domains = nav.buildNavigation(routes, () => true);
  for (const [query, target] of [['声卡', 'audition-links'], ['账目记录', 'recharge-records'],
    ['职位权限管理', 'roles'], ['单抵', 'deposit-rules'], ['KOOK 派单', 'kook-dispatch'], ['注册审核', 'provider-registrations']]) {
    assert.equal(nav.searchNavigation(domains, query)[0]?.path, target, query);
  }
  assert.equal(nav.searchNavigation(domains, '不存在的功能').length, 0);
  assert.equal(nav.searchNavigation(domains, '   ').length, 35);
});

test('a hidden feature cannot leak into navigation or search', () => {
  const { nav, routes } = loadNavigation();
  routes.find(route => route.path === 'audit-logs').meta.hidden = true;
  const domains = nav.buildNavigation(routes, () => true);
  assert.equal(nav.searchNavigation(domains, '审计').length, 0);
});
