import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { spawnSync } from 'node:child_process';

const script = fileURLToPath(new URL('../deploy/deploy-remote.sh', import.meta.url));
const bash = process.env.XA_TEST_BASH || (process.platform === 'win32' ? 'C:/Program Files/Git/bin/bash.exe' : 'bash');
const shellPath = value => value.replaceAll('\\', '/').replace(/^([A-Za-z]):/, (_, drive) => `/${drive.toLowerCase()}`);
const commit = 'a'.repeat(40);
const token = 'test-only-registry-token';

function run(t, overrides = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'xa-ssh-test-'));
  t.after(() => fs.rmSync(root, { recursive: true, force: true }));
  fs.writeFileSync(path.join(root, 'ssh'), `#!/usr/bin/env bash
set -eu
count=0
[[ ! -f "$XA_TEST_ROOT/count" ]] || count=$(cat "$XA_TEST_ROOT/count")
count=$((count + 1))
printf '%s' "$count" > "$XA_TEST_ROOT/count"
printf '%s\\n' "$@" > "$XA_TEST_ROOT/args-$count"
cat > "$XA_TEST_ROOT/stdin-$count"
if [[ "$count" == "$XA_FAIL_CALL" ]]; then
  echo 'Permission denied (publickey).' >&2
  exit 255
fi
`, { mode: 0o755 });
  const result = spawnSync(bash, ['-c', 'export PATH="$XA_TEST_ROOT:$PATH"; exec bash "$XA_TEST_SCRIPT"'], {
    encoding: 'utf8', timeout: 10000,
    env: { ...process.env, XA_TEST_ROOT: shellPath(root), XA_TEST_SCRIPT: shellPath(script), XA_FAIL_CALL: '0',
      DEPLOY_HOST: 'deploy.example.invalid', DEPLOY_USER: 'deployer', DOCKERHUB_USERNAME: 'xa-test',
      DOCKERHUB_TOKEN: token, DEPLOY_COMMIT: commit, XA_DATABASE_MODE: 'embedded', ...overrides },
  });
  const read = name => fs.existsSync(path.join(root, name)) ? fs.readFileSync(path.join(root, name), 'utf8') : '';
  assert.ok(!`${result.stdout}${result.stderr}`.includes(token));
  for (let i = 1; i <= Number(read('count')); i++) assert.ok(!read(`args-${i}`).includes(token));
  return { result, read };
}

test('authentication failure stops before sending registry token or deployment script', t => {
  const { result, read } = run(t, { XA_FAIL_CALL: '1' });
  assert.equal(result.status, 255, result.stderr);
  assert.equal(read('count'), '1');
  assert.equal(read('stdin-1'), '');
  assert.match(read('args-1'), /deployer@deploy.example.invalid\ntrue\n$/);
  assert.match(result.stderr, /registry login and deployment were not attempted/);
  assert.match(result.stderr, /DEPLOY_SSH_PRIVATE_KEY/);
});

test('successful preflight precedes stdin-only login and exact commit deployment', t => {
  const { result, read } = run(t);
  assert.equal(result.status, 0, result.stderr);
  assert.equal(read('count'), '3');
  assert.equal(read('stdin-1'), '');
  assert.equal(read('stdin-2'), token);
  assert.match(read('args-2'), /docker login --username 'xa-test' --password-stdin/);
  assert.match(read('args-3'), new RegExp(`XA_IMAGE_TAG='sha-${commit}' DEPLOY_COMMIT='${commit}'`));
  assert.match(read('stdin-3'), /git checkout --detach/);
  for (let i = 1; i <= 3; i++) {
    assert.match(read(`args-${i}`), /BatchMode=yes/);
    assert.match(read(`args-${i}`), /ConnectTimeout=15/);
    assert.match(read(`args-${i}`), /StrictHostKeyChecking=accept-new/);
  }
});

test('registry login failure prevents deployment', t => {
  const { result, read } = run(t, { XA_FAIL_CALL: '2' });
  assert.equal(result.status, 255);
  assert.equal(read('count'), '2');
});

test('deployment failure is propagated rather than reported as success', t => {
  const { result, read } = run(t, { XA_FAIL_CALL: '3' });
  assert.equal(result.status, 255);
  assert.equal(read('count'), '3');
});

test('invalid configuration fails before connecting', t => {
  for (const overrides of [{ DEPLOY_HOST: '' }, { DEPLOY_USER: '' }, { DOCKERHUB_TOKEN: '' },
    { DOCKERHUB_USERNAME: "bad'user" }, { DEPLOY_COMMIT: 'main' }, { XA_DATABASE_MODE: 'typo' }]) {
    const { result, read } = run(t, overrides);
    assert.notEqual(result.status, 0);
    assert.equal(read('count'), '');
  }
});

test('managed database mode reaches the remote deployment unchanged', t => {
  const { result, read } = run(t, { XA_DATABASE_MODE: 'managed' });
  assert.equal(result.status, 0, result.stderr);
  assert.match(read('args-3'), /XA_DATABASE_MODE='managed'/);
});

test('workflow calls the tested runner-side script', () => {
  const workflow = fs.readFileSync(new URL('../.github/workflows/docker-publish.yml', import.meta.url), 'utf8');
  assert.match(workflow, /run: bash deploy\/deploy-remote.sh/);
  assert.ok(!workflow.includes('printf \'%s\' "$DOCKERHUB_TOKEN"'));
});
