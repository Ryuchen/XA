/* Bounded Windows -> WSL gateway probe. No external traffic or real payments.
 * Node >=22. Run from the repository root with local Compose already running.
 * Simulates HTTP/WS protocols, not 160 rendered browsers or cloud/mobile latency.
 */
const { spawn } = require('node:child_process');
const { readFileSync, writeFileSync, mkdirSync } = require('node:fs');
const { randomBytes } = require('node:crypto');
const { performance } = require('node:perf_hooks');
const { createRequire } = require('node:module');
const path = require('node:path');
const vm = require('node:vm');
const appRoot = path.resolve('apps/provider-miniapp');
const ts = createRequire(path.join(appRoot, 'package.json'))('typescript');
const compiled = ts.transpileModule(readFileSync(path.join(appRoot, 'src/utils/coalescedRefresh.ts'), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 },
}).outputText;
const helper = { exports: {} };
vm.runInNewContext(compiled, { module: helper, exports: helper.exports, setTimeout, clearTimeout, Math });
const legacyPush = process.argv.includes('--legacy-push');
const fixtureCode = readFileSync('scripts/local-capacity-fixtures.py', 'utf8');
const run = randomBytes(6).toString('hex');
const base = 'http://127.0.0.1:18080';
const compose = ['docker', 'compose', '-p', 'xa-local-validation', '--env-file', '.tools/local-validation.env',
  '-f', 'docker-compose.remote.yml', '-f', 'deploy/compose.local-validation.yml'];
const wsl = ['-d', 'XA-Ubuntu-24.04', '-u', 'root', '--cd', '/mnt/g/Github/XA', '--exec'];
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function command(args, input = '', timeout = 60000) {
  return new Promise((resolve, reject) => {
    const child = spawn('wsl.exe', [...wsl, ...args], { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
    let out = '', err = '';
    const timer = setTimeout(() => { child.kill(); reject(new Error('Local probe helper timed out')); }, timeout);
    child.stdout.on('data', x => out += x);
    child.stderr.on('data', x => err += x);
    child.on('error', () => { clearTimeout(timer); reject(new Error('Local helper could not start')); });
    child.on('close', code => {
      clearTimeout(timer);
      // Never echo helper output: prepare returns private JWTs, and server traces may contain URLs.
      code === 0 ? resolve(out.trim()) : reject(new Error(`Local helper failed (${code}); mode=${args.at(-1)}`));
    });
    child.stdin.end(input);
  });
}
async function fixture(mode) {
  return JSON.parse(await command([...compose, 'exec', '-T', 'web', 'python', '-', mode, run], fixtureCode));
}
const samples = [], failures = [], sockets = [], phases = [], writes = [];
let phase = 'setup', stopping = false, requestInFlight = 0, peakInFlight = 0;
const connections = new Map();
async function acquire(person) {
  let slot = connections.get(person.id);
  if (!slot) { slot = { active: 0, waiting: [] }; connections.set(person.id, slot); }
  if (slot.active >= 6) await new Promise(resolve => slot.waiting.push(resolve));
  else slot.active++;
  return () => { const next = slot.waiting.shift(); if (next) next(); else slot.active--; };
}
function fail(kind, detail) {
  failures.push({ phase, kind, detail });
  if (failures.length >= 20) stopping = true;
}
async function request(person, path, payload, accepted = [0], label = path) {
  const release = await acquire(person);
  if (requestInFlight >= 1200) { release(); fail('client_backpressure', label); return null; }
  requestInFlight++;
  peakInFlight = Math.max(peakInFlight, requestInFlight);
  const start = performance.now(), atPhase = phase;
  let ok = false;
  try {
    const response = await fetch(base + path, {
      method: payload === undefined ? 'GET' : 'POST',
      headers: { Authorization: 'Bearer ' + person.token, 'Content-Type': 'application/json' },
      body: payload === undefined ? undefined : JSON.stringify(payload), signal: AbortSignal.timeout(15000),
    });
    const text = await response.text();
    let value;
    try { value = JSON.parse(text); }
    catch { fail('http_non_json', `${label}: HTTP ${response.status}`); return null; }
    ok = accepted.includes(value.code) && response.status < 500;
    if (!ok) fail('http', `${label}: HTTP ${response.status}, code ${value.code}`);
    return value;
  } catch {
    fail('transport', label);
    return null;
  } finally {
    samples.push({ phase: atPhase, path: label, ms: performance.now() - start, ok });
    requestInFlight--;
    release();
  }
}
const extraRequests = new Set();
function track(promise) {
  extraRequests.add(promise);
  promise.finally(() => extraRequests.delete(promise));
}
async function connect(person) {
  const refresh = () => Promise.all([
    request(person, '/api/orders/orders/?status=pending&role=provider', undefined, [0], 'push:pool'),
    request(person, '/api/users/provider-stats/', undefined, [0], 'push:stats'),
  ]);
  const queue = helper.exports.createCoalescedRefresh(() => {
    const pending = request(person, '/api/orders/orders/?status=pending&role=provider', undefined, [0], 'push:pool');
    track(pending); return pending;
  });
  const statsQueue = helper.exports.createCoalescedRefresh(() => {
    const pending = request(person, '/api/users/provider-stats/', undefined, [0], 'push:stats');
    track(pending); return pending;
  });
  const record = { person, queue, statsQueue, socket: null, open: false, pongs: 0, notifications: new Set() };
  sockets.push(record);
  await new Promise(resolve => {
    const socket = new WebSocket(`ws://127.0.0.1:18080/ws/orders/?token=${encodeURIComponent(person.token)}`);
    record.socket = socket;
    const timer = setTimeout(() => { fail('ws_handshake', String(person.id)); socket.close(); resolve(); }, 15000);
    socket.addEventListener('open', () => {
      clearTimeout(timer); record.open = true; socket.send('{"type":"ping"}'); resolve();
    });
    socket.addEventListener('error', () => { clearTimeout(timer); fail('ws_transport', String(person.id)); resolve(); });
    socket.addEventListener('close', () => { if (!stopping && record.open) fail('ws_closed', String(person.id)); record.open = false; });
    socket.addEventListener('message', event => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'pong') { record.pongs++; return; }
        if (msg.type !== 'order_status_update' || !msg.data?.id) return;
        record.notifications.add(`${msg.data.id}:${msg.data.status}`);
        if (person.kind === 'provider' && !stopping) {
          // Use the page's actual scheduler; the flag retains the pre-fix control.
          if (legacyPush) track(refresh());
          else {
            queue.request();
            if (helper.exports.isPersonalOrderUpdate(msg.data)) statsQueue.request();
          }
        }
      } catch { fail('ws_payload', String(person.id)); }
    });
  });
}
const routes = {
  boss: ['/api/orders/gameplays/', '/api/orders/services/', '/api/users/escorts/', '/api/orders/orders/', '/api/wallet/info/'],
  provider: ['/api/orders/orders/?status=pending&role=provider', '/api/users/provider-stats/', '/api/wallet/info/', '/api/wallet/reports/'],
};
async function browse(person, until) {
  await sleep((person.index * 137) % 4500);
  let count = person.index;
  while (!stopping && performance.now() < until) {
    const path = routes[person.kind][count++ % routes[person.kind].length];
    await request(person, path);
    await sleep(5000);
  }
}
async function orderBurst(data, people) {
  await sleep(10000);
  if (stopping) return;
  await Promise.all(people.filter(p => p.kind === 'boss').slice(0, 10).map(async person => {
    const payload = { service_id: data.service_id, support_contact_id: data.support_id,
      client_request_id: `capacity-${run}-${person.id}` };
    const pair = await Promise.all([1, 2].map(() => request(person, '/api/orders/create/', payload, [0, 409], 'write:create')));
    const results = pair.map(value => value?.code === 0 ? value : value?.fundResult);
    if (!results[0]?.data?.id || results[0].data.id !== results[1]?.data?.id) {
      fail('idempotency', String(person.id)); return;
    }
    const id = results[0].data.id;
    writes.push({ id, boss: person.id });
    // Leave the pending order visible during the push/refresh burst.
    await sleep(8000);
    await request(person, `/api/orders/orders/${id}/cancel/`, {}, [0], 'write:cancel');
  }));
}
function percentiles(values) {
  const sorted = values.slice().sort((a, b) => a - b);
  const p = q => sorted.length ? Math.round(sorted[Math.min(sorted.length - 1, Math.ceil(sorted.length * q) - 1)]) : 0;
  return { count: sorted.length, p50_ms: p(.5), p95_ms: p(.95), p99_ms: p(.99), max_ms: p(1) };
}
async function metrics() {
  const [database, containers] = await Promise.all([
    fixture('metrics'), command(['docker', 'stats', '--no-stream', '--format', '{{json .}}']),
  ]);
  return { database, containers: containers.split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line)) };
}
let data, audit, error;
(async () => {
  const started = new Date().toISOString();
  mkdirSync('.tools/capacity', { recursive: true });
  try {
    data = await fixture('prepare');
    console.log(`Prepared 100 bosses and 60 providers; run ${run}. Credentials stay in memory.`);
    for (const config of [{ bosses: 12, providers: 8, seconds: 20 }, { bosses: 50, providers: 30, seconds: 20 }, { bosses: 100, providers: 60, seconds: 60 }]) {
      if (stopping) break;
      phase = `${config.bosses + config.providers}-online`;
      const people = data.people.filter(p => p.index < (p.kind === 'boss' ? config.bosses : config.providers));
      for (const person of people) {
        if (!sockets.some(s => s.person.id === person.id)) await connect(person);
        if (stopping) break;
      }
      const begin = performance.now(), until = begin + config.seconds * 1000;
      const initial = samples.length;
      console.log(`Starting ${phase}: ${config.bosses} bosses, ${config.providers} providers, ${config.seconds}s.`);
      const heartbeat = setInterval(() => sockets.filter(s => s.open).forEach(s => s.socket.send('{"type":"ping"}')), 10000);
      const measurement = (async () => {
        await sleep(people.length === 160 ? 13000 : 3000);
        try { return await metrics(); } catch { return { sampling_failed: true }; }
      })();
      await Promise.all([...people.map(p => browse(p, until)), ...(people.length === 160 ? [orderBurst(data, people)] : [])]);
      await Promise.all([...extraRequests]);
      clearInterval(heartbeat);
      const duration = (performance.now() - begin) / 1000;
      const records = samples.slice(initial);
      const summary = { ...config, phase, actual_seconds: Math.round(duration), requests: records.length,
        rps: Number((records.length / duration).toFixed(2)), errors: records.filter(x => !x.ok).length,
        latency: percentiles(records.map(x => x.ms)), open_sockets: sockets.filter(s => s.open).length,
        resources: await measurement };
      phases.push(summary);
      console.log(JSON.stringify({ phase, requests: summary.requests, errors: summary.errors, ...summary.latency }));
    }
    if (writes.length !== 10) fail('orders', `Expected 10 orders, got ${writes.length}`);
    for (const socket of sockets) {
      if (!socket.pongs) fail('ws_heartbeat', String(socket.person.id));
      if (socket.person.kind === 'provider') for (const order of writes) {
        for (const status of ['PENDING', 'CANCELLED']) {
          if (!socket.notifications.has(`${order.id}:${status}`)) fail('ws_delivery', `${socket.person.id}:${order.id}:${status}`);
        }
      }
    }
  } catch (caught) { error = caught.message; }
  finally {
    stopping = true;
    sockets.forEach(s => { s.queue.dispose(); s.statsQueue.dispose(); s.socket?.close(); });
    await Promise.allSettled([...extraRequests]);
    if (data) {
      try {
        // A timed-out response might still have committed; resolve it from owned records.
        for (const order of await fixture('pending')) {
          const person = data.people.find(p => p.id === order.customer_account_id);
          await request(person, `/api/orders/orders/${order.id}/cancel/`, {}, [0], 'cleanup:cancel');
        }
        audit = await fixture('audit');
        await fixture('retire');
      } catch (caught) { error = [error, caught.message].filter(Boolean).join('; '); }
    }
    const byPath = {};
    for (const path of new Set(samples.map(x => x.path))) {
      const records = samples.filter(x => x.path === path);
      byPath[path] = { ...percentiles(records.map(x => x.ms)), errors: records.filter(x => !x.ok).length };
    }
    const report = { started, run, target: base, push_policy: legacyPush ? 'legacy-per-event' : 'actual-page-coalescer',
      http_connections_per_person: 6, passed: !error && !failures.length && phases.length === 3,
      phases, by_path: byPath, peak_in_flight: peakInFlight, writes, audit, failures, error,
      socket_count: sockets.length, sockets_with_pong: sockets.filter(s => s.pongs).length };
    const path = `.tools/capacity/${run}.json`;
    writeFileSync(path, JSON.stringify(report, null, 2));
    console.log(JSON.stringify({ report: path, passed: report.passed, audit, failures: failures.length, error }));
    if (!report.passed) process.exitCode = 1;
  }
})().catch(() => { console.error('Capacity probe failed before reporting; inspect local state.'); process.exitCode = 1; });
