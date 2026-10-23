/* 60 independent providers claim one order through the local HTTP gateway.
 * Uses only the isolated fixtures; retains audit records and restores balances.
 */
const { spawn } = require('node:child_process');
const { readFileSync, writeFileSync, mkdirSync } = require('node:fs');
const { randomBytes } = require('node:crypto');
const assert = require('node:assert/strict');
const run = randomBytes(6).toString('hex');
const fixtureCode = readFileSync('scripts/local-capacity-fixtures.py', 'utf8');
function fixture(mode) {
  return new Promise((resolve, reject) => {
    const child = spawn('wsl.exe', ['-d', 'XA-Ubuntu-24.04', '-u', 'root', '--cd', '/mnt/g/Github/XA', '--exec',
      'docker', 'compose', '-p', 'xa-local-validation', '--env-file', '.tools/local-validation.env',
      '-f', 'docker-compose.remote.yml', '-f', 'deploy/compose.local-validation.yml',
      'exec', '-T', 'web', 'python', '-', mode, run],
    { stdio: ['pipe', 'pipe', 'ignore'], windowsHide: true });
    let output = '';
    const timer = setTimeout(() => { child.kill(); reject(Error('Local fixture helper timed out')); }, 60000);
    child.stdout.on('data', data => output += data);
    child.on('error', () => { clearTimeout(timer); reject(Error('Local fixture helper could not start')); });
    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0) return reject(Error(`Local fixture helper failed: ${mode}`));
      try { resolve(JSON.parse(output)); } catch { reject(Error('Invalid local fixture response')); }
    });
    child.stdin.end(fixtureCode);
  });
}
async function request(person, path, payload) {
  const started = performance.now();
  const response = await fetch('http://127.0.0.1:18080' + path, {
    method: 'POST', headers: { Authorization: 'Bearer ' + person.token, 'Content-Type': 'application/json' },
    body: JSON.stringify(payload), signal: AbortSignal.timeout(30000),
  });
  const result = await response.json();
  assert(response.status < 500, `HTTP ${response.status}`);
  return { person: person.id, code: result.code, data: result.data, ms: Math.round(performance.now() - started) };
}
(async () => {
  let data, state, audit, error, responses = [];
  const started = new Date().toISOString();
  try {
    data = await fixture('prepare');
    const boss = data.people.find(person => person.kind === 'boss');
    const created = await request(boss, '/api/orders/create/', {
      service_id: data.service_id, support_contact_id: data.support_id, client_request_id: `race-${run}`,
    });
    assert.equal(created.code, 0, 'Local order creation failed');
    const providers = data.people.filter(person => person.kind === 'provider');
    assert.equal(providers.length, 60);
    // Wait for every attempt, including failures, before reading state/cleaning up.
    const settled = await Promise.allSettled(providers.map(person =>
      request(person, `/api/orders/orders/${created.data.id}/grab/`, {})));
    responses = settled.filter(item => item.status === 'fulfilled').map(item => item.value);
    assert.equal(responses.length, 60, 'Some grab attempts failed at the HTTP/transport layer');
    const winners = responses.filter(result => result.code === 0);
    assert.equal(winners.length, 1, 'Exactly one provider must win');
    assert.equal(responses.filter(result => result.code === 400).length, 59, 'Other providers must receive a business conflict');
    const claims = await fixture('claims');
    assert.equal(claims.length, 1);
    state = claims[0];
    assert.equal(state.status, 'GRABBED');
    assert.equal(state.provider_account_id, winners[0].person);
    assert.equal(state.active_shares, 1);
    assert.equal(state.grab_logs, 1);
  } catch (caught) { error = caught.message; }
  finally {
    if (data) {
      try {
        for (const claim of await fixture('claims')) {
          if (claim.status === 'GRABBED') {
            const provider = data.people.find(person => person.id === claim.provider_account_id);
            assert(provider, 'Unexpected provider on local race order');
            assert.equal((await request(provider, `/api/orders/orders/${claim.id}/reject/`, {
              reason: '本地并发抢单验证完成，回池后取消并退还测试余额',
            })).code, 0);
          }
        }
        for (const pending of await fixture('pending')) {
          const boss = data.people.find(person => person.id === pending.customer_account_id);
          assert.equal((await request(boss, `/api/orders/orders/${pending.id}/cancel/`, {})).code, 0);
        }
        audit = await fixture('audit');
        await fixture('retire');
      } catch (caught) { error = [error, 'Cleanup: ' + caught.message].filter(Boolean).join('; '); }
    }
    mkdirSync('.tools/capacity', { recursive: true });
    const report = { run, started, passed: !error, error, state, audit,
      attempts: responses.map(({person, code, ms}) => ({person, code, ms})) };
    const path = `.tools/capacity/grab-${run}.json`;
    writeFileSync(path, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ report: path, passed: !error, attempts: responses.length,
      winners: responses.filter(result => result.code === 0).length, state, audit, error }));
    if (error) process.exitCode = 1;
  }
})().catch(() => { console.error('Local race probe failed before reporting'); process.exitCode = 1; });
