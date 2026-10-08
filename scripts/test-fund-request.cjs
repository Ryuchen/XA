// 在两端真实 TypeScript 实现上验证重试和重入，Taro 存储与网络由测试替身提供。
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');

for (const app of ['boss-miniapp', 'provider-miniapp']) {
  const appRoot = path.resolve(__dirname, '..', 'apps', app);
  const ts = createRequire(path.join(appRoot, 'package.json'))('typescript');
  const code = ts.transpileModule(fs.readFileSync(path.join(appRoot, 'src/utils/fundRequest.ts'), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true },
  }).outputText;

  function load({ storage = new Map(), send, owner = () => 'account-a', storageFails = false }) {
    const mod = { exports: {} };
    const taro = {
      getStorageSync: key => storage.get(key),
      setStorageSync: (key, value) => { if (storageFails) throw Error('full'); storage.set(key, value); },
      removeStorageSync: key => storage.delete(key),
    };
    vm.runInNewContext(code, {
      module: mod, exports: mod.exports,
      require: name => {
        if (name === '@tarojs/taro') return taro;
        if (name === './auth') return { getStoredUser: () => ({ id: owner() }) };
        if (name === './request') return { request: send };
        throw Error(name);
      },
    });
    return mod.exports.fundRequest;
  }

  test(`${app}: concurrent clicks share one network call`, async () => {
    let finish;
    let calls = 0;
    const storage = new Map();
    const fund = load({ storage, send: () => { calls++; return new Promise(resolve => { finish = resolve; }); } });
    const a = fund('/buy/', { amount: 100 });
    const b = fund('/buy/', { amount: 100 });
    assert.equal(a, b);
    assert.equal(calls, 1);
    finish({ code: 0 });
    await a;
    assert.equal(storage.size, 0);
  });

  test(`${app}: timeout and page reopen retain the original request id`, async () => {
    const storage = new Map();
    let firstId;
    const first = load({ storage, send: async (_url, _method, data) => { firstId = data.client_request_id; throw Error('timeout'); } });
    await assert.rejects(first('/buy/', { amount: 100 }), /timeout/);
    const reopened = load({ storage, send: async (_url, _method, data) => {
      assert.equal(data.client_request_id, firstId);
      return { code: 409 };
    } });
    await reopened('/buy/', { amount: 100 });
    assert.equal(storage.size, 1, 'an unknown duplicate result must not start a fresh purchase');
  });

  test(`${app}: recovered receipt finishes the original intent and next purchase gets a new id`, async () => {
    const storage = new Map(), ids = [];
    const fund = load({ storage, send: async (_url, _method, data) => {
      ids.push(data.client_request_id);
      if (ids.length === 1) throw Error('lost successful response');
      if (ids.length === 2) return { code: 409, errorCode: 'FUND_ALREADY_PROCESSED', fundResult: { code: 0, data: { id: 8 } } };
      return { code: 0, data: { id: 9 } };
    } });
    await assert.rejects(fund('/buy/', { amount: 100 }));
    assert.equal((await fund('/buy/', { amount: 100 })).data.id, 8);
    assert.equal(storage.size, 0);
    assert.equal((await fund('/buy/', { amount: 100 })).data.id, 9);
    assert.equal(ids[0], ids[1]); assert.notEqual(ids[1], ids[2]);
  });

  test(`${app}: wrong or incomplete receipt cannot end the transaction`, async () => {
    const storage = new Map();
    let response = { code: 409, errorCode: 'REQUEST_ID_CONFLICT', fundResult: { code: 0 } };
    const fund = load({ storage, send: async () => response });
    assert.equal((await fund('/buy/', {})).code, 409); assert.equal(storage.size, 1);
    response = { code: 409, errorCode: 'FUND_ALREADY_PROCESSED', fundResult: { code: 500 } };
    assert.equal((await fund('/buy/', {})).code, 409); assert.equal(storage.size, 1);
  });

  test(`${app}: account change while waiting rejects old success and retains its request`, async () => {
    const storage = new Map(); let owner = 'first', finish;
    const fund = load({ storage, owner: () => owner, send: () => new Promise(resolve => { finish = resolve; }) });
    const waiting = fund('/buy/', {}); owner = 'second'; finish({ code: 0 });
    await assert.rejects(waiting, /账号已切换/); assert.equal(storage.size, 1);
  });

  test(`${app}: server failure retains id; business rejection finishes attempt`, async () => {
    const storage = new Map();
    let code = 500;
    const fund = load({ storage, send: async () => ({ code }) });
    await fund('/buy/', { amount: 100 });
    assert.equal(storage.size, 1);
    code = 400;
    await fund('/buy/', { amount: 100 });
    assert.equal(storage.size, 0);
  });

  test(`${app}: account switch isolates unresolved transactions`, async () => {
    let owner = 'account-a';
    const ids = [];
    const fund = load({ owner: () => owner, send: async (_url, _method, data) => { ids.push(data.client_request_id); throw Error('timeout'); } });
    await assert.rejects(fund('/buy/', {}));
    owner = 'account-b';
    await assert.rejects(fund('/buy/', {}));
    assert.notEqual(ids[0], ids[1]);
  });

  test(`${app}: rate limiting retains the same transaction ID for the next retry`, async () => {
    const storage = new Map(), ids = [];
    let code = 429;
    const fund = load({ storage, send: async (_url, _method, data) => { ids.push(data.client_request_id); return { code }; } });
    await fund('/buy/', { amount: 100 }); assert.equal(storage.size, 1);
    code = 0; await fund('/buy/', { amount: 100 });
    assert.equal(ids[0], ids[1]); assert.equal(storage.size, 0);
  });

  test(`${app}: storage failure prevents untracked debit`, async () => {
    let calls = 0;
    const fund = load({ storageFails: true, send: async () => { calls++; return { code: 0 }; } });
    await assert.rejects(fund('/buy/', {}), /存储/);
    assert.equal(calls, 0);
  });
}
