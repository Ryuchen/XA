const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('../node_modules/typescript');

function environment({ type = 'image/png', suppliedName, target = 'h5', firstUnauthorized = false, onRead } = {}) {
  let revision = 1, token = 'original-token';
  const calls = [], reads = [];
  const dependencies = {
    '@tarojs/taro': { __esModule: true, default: {
      uploadFile: async config => { calls.push(config); return { statusCode: firstUnauthorized && calls.length === 1 ? 401 : 200, data: JSON.stringify({ code: 0, data: { stored: true } }) }; },
      request: async () => ({ statusCode: 200, data: { code: 0, data: { token: 'renewed-token' } } }),
      showToast() {}, reLaunch() {},
    } },
    './auth': { getSessionRevision: () => revision, getStoredToken: () => token, getStoredRefreshToken: () => 'refresh-token', setStoredToken: value => { token = value; }, sessionChangedError: () => new Error('SESSION_CHANGED'), clearStoredUser() {} },
    './orderNetwork': { scopeFor: () => null, cooldownRemaining: () => 0, recordOrderRequest() {} },
    './env': { BASE_URL: '/api' },
  };
  const module = { exports: {} };
  const code = ts.transpileModule(fs.readFileSync(path.join(__dirname, '../src/utils/request.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
  vm.runInNewContext(code, { module, exports: module.exports, process: { env: { TARO_ENV: target } }, setTimeout, clearTimeout,
    require: name => { if (!(name in dependencies)) throw new Error('Unexpected import ' + name); return dependencies[name]; },
    fetch: async (url, options) => { reads.push({ url, options }); onRead?.(() => { revision++; }); return { ok: true, blob: async () => ({ type }) }; },
  });
  return { calls, reads, upload: (key = 'image') => module.exports.uploadFile('/wallet/reports/3/images/', 'blob:http://127.0.0.1/test-local-file', { kind: 'ENTRY' }, key, false, suppliedName) };
}

for (const [type, extension] of [['image/png', 'png'], ['image/jpeg', 'jpg'], ['image/webp', 'webp']]) test(`anonymous H5 ${type} retains a validator-compatible file extension`, async () => {
  const env = environment({ type }); const response = await env.upload();
  assert.equal(response.code, 0); assert.equal(env.calls[0].fileName, 'upload.' + extension);
  assert.equal(env.calls[0].name, 'image'); assert.equal(env.calls[0].formData.kind, 'ENTRY');
  assert.equal(env.calls[0].header.Authorization, 'Bearer original-token'); assert.equal(env.calls[0].withCredentials, false);
  assert.equal(env.reads[0].options.credentials, 'omit');
});

test('explicit original names from profile or audio selection are preserved without extra Blob reads', async () => {
  for (const suppliedName of ['头像.png', 'profile.jpeg', 'voice-card.mp3']) {
    const env = environment({ suppliedName }); await env.upload('avatar');
    assert.equal(env.calls[0].fileName, suppliedName); assert.equal(env.calls[0].name, 'avatar'); assert.equal(env.reads.length, 0);
  }
});

test('native upload stays on the existing Taro path', async () => {
  const env = environment({ target: 'weapp' }); await env.upload('entry_image');
  assert.equal(env.calls[0].fileName, undefined); assert.equal(env.reads.length, 0);
});

test('401 retry preserves the recovered name, file path, form fields and new authorization', async () => {
  const env = environment({ type: 'image/jpeg', firstUnauthorized: true }); await env.upload('completion_image');
  assert.equal(env.calls.length, 2); assert.equal(env.reads.length, 1);
  assert.equal(env.calls[1].fileName, 'upload.jpg'); assert.equal(env.calls[1].filePath, env.calls[0].filePath);
  assert.equal(env.calls[1].name, 'completion_image'); assert.deepEqual(env.calls[1].formData, env.calls[0].formData);
  assert.equal(env.calls[1].header.Authorization, 'Bearer renewed-token');
});

test('a session change while reading the Blob cannot send an upload under the next account', async () => {
  const env = environment({ onRead: changeSession => changeSession() });
  await assert.rejects(env.upload(), /SESSION_CHANGED/); assert.equal(env.calls.length, 0);
});

test('unknown file MIME is not disguised as a valid image format', async () => {
  const env = environment({ type: 'application/octet-stream' }); await env.upload();
  assert.equal(env.calls[0].fileName, undefined);
});
