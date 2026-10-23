import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const deployScript = fileURLToPath(new URL('../deploy/deploy-release.sh', import.meta.url));
const bash = process.env.XA_TEST_BASH || (process.platform === 'win32' ? 'C:/Program Files/Git/bin/bash.exe' : 'bash');
const shellPath = value => value.replaceAll('\\', '/').replace(/^([A-Za-z]):/, (_, drive) => `/${drive.toLowerCase()}`);

function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'xa-deploy-test-'));
  t.after(() => {
    const absolute = path.resolve(root);
    if (path.dirname(absolute) !== path.resolve(os.tmpdir()) || !path.basename(absolute).startsWith('xa-deploy-test-')) throw new Error('Unexpected fixture cleanup path');
    fs.rmSync(absolute, { recursive: true, force: true });
  });
  const source = path.join(root, 'origin'), server = path.join(root, 'server'), bin = path.join(root, 'bin');
  fs.mkdirSync(source); fs.mkdirSync(bin);
  function git(cwd, ...args) {
    const result = spawnSync('git', ['-c', 'core.autocrlf=false', '-c', 'commit.gpgsign=false', '-c', 'user.name=XA Test', '-c', 'user.email=test@invalid', ...args], { cwd, encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr || result.error?.message);
    return result.stdout.trim();
  }
  git(source, 'init', '-q', '-b', 'main');
  fs.writeFileSync(path.join(source, '.gitignore'), '.env.release\n');
  fs.writeFileSync(path.join(source, 'docker-compose.remote.yml'), 'services: {}\n');
  fs.writeFileSync(path.join(source, 'docker-compose.release.yml'), 'services: {}\n');
  fs.writeFileSync(path.join(source, 'docker-compose.managed.yml'), 'services: {}\n');
  fs.writeFileSync(path.join(source, 'version.txt'), 'previous\n');
  git(source, 'add', '.'); git(source, 'commit', '-q', '-m', 'previous');
  const previous = git(source, 'rev-parse', 'HEAD');
  fs.writeFileSync(path.join(source, 'version.txt'), 'target\n');
  git(source, 'add', '.'); git(source, 'commit', '-q', '-m', 'target');
  const target = git(source, 'rev-parse', 'HEAD');
  git(source, 'tag', 'v1.2.3');
  git(root, 'clone', '-q', source, server);
  git(server, 'checkout', '-q', '--detach', previous);
  const envFile = path.join(server, '.env.release');
  fs.writeFileSync(envFile, 'UNCHANGED_CONFIGURATION=yes\n');
  const log = path.join(root, 'docker.log');
  fs.writeFileSync(path.join(bin, 'docker'), `#!/usr/bin/env bash
printf 'tag=%s namespace=%s %s\\n' "$XA_IMAGE_TAG" "$DOCKERHUB_NAMESPACE" "$*" >> "$XA_COMMAND_LOG"
if [[ -n "$XA_FAKE_FAIL" && " $* " == *" $XA_FAKE_FAIL "* ]]; then exit 19; fi
`, { mode: 0o755 });
  function run(overrides = {}) {
    return spawnSync(bash, ['-c', 'export PATH="$XA_FAKE_BIN:$PATH"; exec bash "$XA_SCRIPT"'], {
      cwd: server, encoding: 'utf8', timeout: 30000,
      env: { ...process.env, DEPLOY_COMMIT: target, XA_IMAGE_TAG: `sha-${target}`, DOCKERHUB_NAMESPACE: 'xa-test',
        XA_DEPLOY_DIR: shellPath(server), XA_SCRIPT: shellPath(deployScript), XA_FAKE_BIN: shellPath(bin), XA_COMMAND_LOG: shellPath(log),
        XA_FAKE_FAIL: '', XA_DATABASE_MODE: 'embedded', ...overrides },
    });
  }
  return { server, previous, target, envFile, git, run, commands: () => fs.existsSync(log) ? fs.readFileSync(log, 'utf8') : '' };
}

test('tagged commit deploys exact source and images using release overlay without editing environment', t => {
  const f = fixture(t);
  const result = f.run();
  assert.equal(result.status, 0, result.stderr || result.error?.message);
  assert.equal(f.git(f.server, 'rev-parse', 'HEAD'), f.target);
  assert.equal(fs.readFileSync(f.envFile, 'utf8'), 'UNCHANGED_CONFIGURATION=yes\n');
  const lines = f.commands().trim().split('\n');
  assert.equal(lines.length, 6);
  for (const line of lines) {
    assert.ok(line.includes(`tag=sha-${f.target}`));
    assert.ok(line.includes('--env-file .env.release -f docker-compose.remote.yml -f docker-compose.release.yml'));
  }
  assert.match(f.commands(), /up -d --no-build --remove-orphans --wait --wait-timeout 180/);
  assert.match(f.commands(), /manage.py check_mysql_readiness/);
});

test('tracked server changes survive and prevent deployment', t => {
  const f = fixture(t);
  fs.writeFileSync(path.join(f.server, 'version.txt'), 'operator change\n');
  const result = f.run();
  assert.notEqual(result.status, 0);
  assert.match(result.stderr, /Tracked server files have changes/);
  assert.equal(f.git(f.server, 'rev-parse', 'HEAD'), f.previous);
  assert.equal(fs.readFileSync(path.join(f.server, 'version.txt'), 'utf8'), 'operator change\n');
  assert.equal(f.commands(), '');
});

test('missing release environment never falls back to remote development settings', t => {
  const f = fixture(t);
  fs.unlinkSync(f.envFile);
  fs.writeFileSync(path.join(f.server, '.env.remote'), 'WECHAT_MOCK_LOGIN=true\n');
  assert.notEqual(f.run().status, 0);
  assert.equal(f.git(f.server, 'rev-parse', 'HEAD'), f.previous);
  assert.equal(f.commands(), '');
});

test('mismatched image tag or malformed commit fails before checkout or Docker', t => {
  const f = fixture(t);
  assert.notEqual(f.run({ XA_IMAGE_TAG: `sha-${f.target.slice(0, 7)}` }).status, 0);
  assert.notEqual(f.run({ DEPLOY_COMMIT: 'main' }).status, 0);
  assert.equal(f.git(f.server, 'rev-parse', 'HEAD'), f.previous);
  assert.equal(f.commands(), '');
});

test('failed image pull stops before changing running services', t => {
  const f = fixture(t);
  const result = f.run({ XA_FAKE_FAIL: 'pull' });
  assert.equal(result.status, 19, result.stderr);
  assert.equal(f.commands().trim().split('\n').length, 2);
  assert.ok(!f.commands().includes(' up '));
});

test('managed deployment selects only its standalone compose file and preserves configuration', t => {
  const f = fixture(t);
  const result = f.run({ XA_DATABASE_MODE: 'managed' });
  assert.equal(result.status, 0, result.stderr);
  const lines = f.commands().trim().split('\n');
  assert.equal(lines.length, 6);
  for (const line of lines) {
    assert.match(line, /--env-file .env.release -f docker-compose.managed.yml/);
    assert.ok(!line.includes('docker-compose.remote.yml'));
    assert.ok(!line.includes('docker-compose.release.yml'));
  }
  assert.equal(fs.readFileSync(f.envFile, 'utf8'), 'UNCHANGED_CONFIGURATION=yes\n');
});

test('unknown database deployment mode stops before checkout or Docker', t => {
  const f = fixture(t);
  assert.notEqual(f.run({ XA_DATABASE_MODE: 'typo' }).status, 0);
  assert.equal(f.git(f.server, 'rev-parse', 'HEAD'), f.previous);
  assert.equal(f.commands(), '');
});
