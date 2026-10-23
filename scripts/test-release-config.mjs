import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, readFileSync, rmdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import { validateReleaseConfig } from './check-release-config.mjs';

function fixture() {
  return {
    env: { WECHAT_APPID: 'wx1234567890abcdef', TARO_APP_API: '/api' },
    project: { appid: 'wx1234567890abcdef', miniprogramRoot: 'native/', compileType: 'miniprogram', setting: { urlCheck: true } },
    nativeConfig: { release: { apiBase: 'https://api.xa-club.com/api', socketBase: '' }, trial: { apiBase: '', socketBase: '' } },
  };
}

test('native public API and same-origin H5 are valid without legacy Taro socket/media variables', () => {
  const input = fixture();
  assert.deepEqual(validateReleaseConfig(input), []);
  input.env.TARO_APP_API = 'https://api.xa-club.com/api';
  input.nativeConfig.release.socketBase = 'wss://socket.xa-club.com/';
  assert.deepEqual(validateReleaseConfig(input), []);
});

test('native API rejects blank, relative, plaintext, IP, placeholder and malformed addresses', () => {
  for (const value of ['', '/api', 'http://api.xa-club.com/api', 'https://127.0.0.1/api', 'https://192.168.1.2/api',
    'https://8.8.8.8/api', 'https://[::1]/api', 'https://api.example.com/api', 'https://api.local/api',
    'https://api.internal/api', 'https://api.test/api', 'https://localhost/api',
    'https://user:secret@api.xa-club.com/api', 'https://api.xa-club.com/api?key=x',
    'https://api.xa-club.com/api#fragment', 'https://api.xa-club.com', ' https://api.xa-club.com/api']) {
    const input = fixture();
    input.nativeConfig.release.apiBase = value;
    assert.ok(validateReleaseConfig(input).some(error => error.includes('release.apiBase')), value || '(empty)');
  }
});

test('explicit native socket address must be secure and contain no application path', () => {
  for (const value of ['ws://socket.xa-club.com', 'wss://socket.xa-club.com/api', 'wss://socket.xa-club.com/ws/orders/',
    'wss://127.0.0.1', 'wss://socket.example.com', 'wss://socket.xa-club.com?token=secret']) {
    const input = fixture();
    input.nativeConfig.release.socketBase = value;
    assert.ok(validateReleaseConfig(input).some(error => error.includes('release.socketBase')), value);
  }
});

test('trial validation selects trial configuration and does not fall back to release', () => {
  const input = { ...fixture(), target: 'trial' };
  assert.ok(validateReleaseConfig(input).some(error => error.includes('trial.apiBase')));
  input.nativeConfig.trial.apiBase = 'https://trial.xa-club.com/api';
  input.nativeConfig.release.apiBase = '';
  assert.deepEqual(validateReleaseConfig(input), []);
  input.target = 'develop';
  assert.ok(validateReleaseConfig(input).some(error => error.includes('目标环境')));
});

test('legacy project roots and disabled domain validation block release', () => {
  const input = fixture();
  input.project.miniprogramRoot = 'dist/';
  assert.ok(validateReleaseConfig(input).some(error => error.includes('native/')));
  input.project.miniprogramRoot = 'native/';
  input.privateProject = { setting: { urlCheck: false } };
  assert.ok(validateReleaseConfig(input).some(error => error.includes('urlCheck')));
  input.project.setting.urlCheck = false;
  input.privateProject.setting.urlCheck = true;
  assert.deepEqual(validateReleaseConfig(input), [], 'effective private setting takes precedence');
});

test('missing or mismatched AppID and H5 API fail without exposing credential values', () => {
  const input = fixture();
  input.env.WECHAT_APPID = 'wx0000000000000000';
  assert.ok(validateReleaseConfig(input).some(error => error.includes('不一致')));
  input.env.WECHAT_APPID = '';
  input.env.TARO_APP_API = 'https://operator:sensitive-password@api.xa-club.com/api';
  const errors = validateReleaseConfig(input).join('\n');
  assert.match(errors, /WECHAT_APPID/);
  assert.match(errors, /TARO_APP_API/);
  assert.ok(!errors.includes('sensitive-password'));
  for (const value of ['', 'http://127.0.0.1:8000/api', '//api.xa-club.com/api', '/other/api']) {
    input.env.TARO_APP_API = value;
    assert.ok(validateReleaseConfig(input).some(error => error.includes('TARO_APP_API')));
  }
});

test('CLI exits unsuccessfully for missing build configuration and unsupported arguments', () => {
  const script = fileURLToPath(new URL('./check-release-config.mjs', import.meta.url));
  const emptyEnv = { ...process.env, WECHAT_APPID: '', TARO_APP_API: '' };
  const missing = spawnSync(process.execPath, [script], { encoding: 'utf8', env: emptyEnv });
  assert.equal(missing.status, 1);
  assert.match(missing.stderr, /WECHAT_APPID/);
  const badArg = spawnSync(process.execPath, [script, '--skip-checks'], { encoding: 'utf8', env: emptyEnv });
  assert.equal(badArg.status, 1);
  assert.match(badArg.stderr, /用法/);
});

test('native runtime removes socket trailing slash and derives a single WebSocket order path', () => {
  const source = readFileSync(new URL('../apps/boss-miniapp/native/core/config.js', import.meta.url), 'utf8');
  const environments = fixture().nativeConfig;
  environments.release.socketBase = 'wss://socket.xa-club.com/';
  const context = { module: { exports: {} }, require: () => environments,
    wx: { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'release' } }) } };
  vm.runInNewContext(source, context);
  assert.equal(context.module.exports.config().socketBase + '/ws/orders/', 'wss://socket.xa-club.com/ws/orders/');
  environments.release.socketBase = '';
  assert.equal(context.module.exports.config().socketBase, 'wss://api.xa-club.com');
  environments.release.apiBase = '';
  assert.throws(() => context.module.exports.config(), /服务地址尚未配置/);
});

test('required official compiler cannot silently fall back to syntax-only validation', () => {
  const emptyDirectory = mkdtempSync(join(tmpdir(), 'xa-compiler-test-'));
  try {
    const script = fileURLToPath(new URL('./check-native.mjs', import.meta.url));
    const result = spawnSync(process.execPath, [script, '--require-compiler'], {
      encoding: 'utf8', env: { ...process.env, WECHAT_COMPILER_DIR: emptyDirectory },
    });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Official WeChat compiler is required/);
    assert.ok(!result.stdout.includes('templates passed'));
  } finally {
    rmdirSync(emptyDirectory);
  }
});


test('admin release API keeps the admin namespace and rejects the provider API root', () => {
  const input = fixture();
  for (const value of ['/api/admin', '/api/admin/', 'https://api.xa-club.com/api/admin']) {
    input.env.VITE_API_BASE = value;
    assert.deepEqual(validateReleaseConfig(input), []);
  }
  for (const value of ['/api', 'https://api.xa-club.com/api']) {
    input.env.VITE_API_BASE = value;
    assert.ok(validateReleaseConfig(input).some(error => error.includes('VITE_API_BASE')));
  }
});
