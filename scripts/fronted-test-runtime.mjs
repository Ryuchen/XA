/** Node transport harness for the current React controllers, never a browser. */
import assert from 'node:assert/strict';
import { registerHooks } from 'node:module';

class MemoryStorage {
  values = new Map();
  get length() { return this.values.size; }
  key(index) { return [...this.values.keys()][index] ?? null; }
  getItem(key) { return this.values.get(String(key)) ?? null; }
  setItem(key, value) { this.values.set(String(key), String(value)); }
  removeItem(key) { this.values.delete(String(key)); }
  clear() { this.values.clear(); }
}
globalThis.localStorage = new MemoryStorage();
globalThis.sessionStorage = new MemoryStorage();
globalThis.location = new URL('http://localhost/?store=shenglang');
const transport = globalThis.fetch.bind(globalThis);
let origin = '', traps = [];
globalThis.fetch = async (target, options = {}) => {
  const url = new URL(String(target), origin || location.href);
  const early = traps.findIndex(trap => trap.before && trap.match(url, options));
  if (early >= 0) {
    const [trap] = traps.splice(early, 1);
    trap.arrived?.({ before: true });
    if (trap.gate) await trap.gate;
  }
  const response = await transport(url, options);
  const index = traps.findIndex(trap => trap.match(url, options));
  if (index >= 0) {
    const [trap] = traps.splice(index, 1);
    const body = response.status === 204 ? { code: 0 } : await response.clone().json().catch(() => ({ code: response.status }));
    trap.arrived?.({ body, status: response.status, ok: response.ok });
    if (trap.gate) await trap.gate;
    if (trap.truncate && response.ok && body.code === 0)
      return new Response('{"code":', { status: 200, headers: { 'Content-Type': 'application/json' } });
    if (trap.lose && response.ok && body.code === 0)
      throw new TypeError('隔离测试：服务器已提交，响应在传输中丢失');
  }
  return response;
};

export const shared = await import('../fronted/shared/src/client.ts');
export const sessions = await import('../fronted/shared/src/session.ts');
export const business = await import('../fronted/shared/src/business.ts');
export const clients = Object.fromEntries(['boss', 'provider', 'admin'].map(role => [role, shared.createApiClient(role)]));
export function setBase(value) {
  const target = new URL(value);
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(target.hostname))
    throw new Error('Contract fixtures must use an isolated loopback HTTP server');
  origin = value.replace(/\/$/, '');
}
export function login(token, role = 'provider') {
  const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url'));
  location.search = '?store=' + (claims.store_slug || 'shenglang');
  sessions.persistSession(role, { token, user: { id: claims.account_id,
    role: role === 'boss' ? 'customer' : role, username: `${role}-${claims.account_id}` } });
  return clients[role];
}
export function intercept(match, { lose = false, hold = false, truncate = false, before = false } = {}) {
  let arrived, release;
  const received = new Promise(resolve => { arrived = resolve; });
  const gate = hold ? new Promise(resolve => { release = resolve; }) : null;
  traps.push({ match, lose, truncate, gate, before, arrived });
  return { received, release: () => release?.() };
}
export function imageForm(encoded, field = 'image', data = {}) {
  const form = new FormData();
  for (const [key, value] of Object.entries(data)) form.append(key, String(value));
  form.append(field, new Blob([Buffer.from(encoded, 'base64')], { type: 'image/png' }), 'evidence.png');
  return form;
}
export async function raw(token, path, method = 'GET', data) {
  const multipart = data instanceof FormData;
  const claims = JSON.parse(Buffer.from(token.split('.')[1], 'base64url'));
  const response = await transport(origin + (path.startsWith('/api/') ? path : '/api' + path), {
    method, headers: { Authorization: 'Bearer ' + token, 'X-Store-Slug': claims.store_slug || 'shenglang',
      ...(data === undefined || multipart ? {} : { 'Content-Type': 'application/json' }) },
    body: data === undefined ? undefined : multipart ? data : JSON.stringify(data),
  });
  const body = await response.json();
  if (!response.ok || body.code !== 0) throw new shared.ApiError(body.msg || '请求失败', response.status, body);
  return body.data;
}
export async function lost(client, path, data, read, method = 'POST', role = 'provider', { truncate = false } = {}) {
  const trap = intercept((url, options) => url.pathname === '/api' + path && options.method === method, { lose: !truncate, truncate });
  let writes = 0;
  const result = await business.mutateAndReload({
    role,
    mutate: () => { writes++; return client.request(path, { method, data }); },
    reload: read, isCurrent: () => true,
  });
  const captured = await trap.received;
  assert.equal(captured.ok, true, JSON.stringify(captured));
  assert.equal(captured.body.code, 0, JSON.stringify(captured));
  assert.equal(writes, 1, 'uncertain writes must never repeat automatically');
  assert.ok(result.error, 'an unknown response cannot be announced as confirmed');
  assert.equal(result.stale, false);
  assert.equal(result.reloadError, undefined, 'the recovery read must succeed');
  return result;
}
export const rows = value => Array.isArray(value) ? value : value?.results || value?.list || value?.reservations || value?.messages || value?.items || [];
export function deferred() {
  let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve };
}
export const readInput = async () => {
  let source = ''; for await (const chunk of process.stdin) source += chunk;
  return JSON.parse(source || '{}');
};

/** Subscribe with the actual React hub; only DOM event surfaces are provided. */
export async function contractRealtime(role, wsBase, onMessage) {
  globalThis.document ||= Object.assign(new EventTarget(), { hidden: false });
  globalThis.window ||= new EventTarget();
  const address = new URL(wsBase);
  location.host = address.host;
  location.protocol = address.protocol === 'https:' ? 'https:' : 'http:';
  const { subscribeRealtime } = await import('../fronted/shared/src/realtime.ts');
  let resolveConnected;
  const connected = new Promise(resolve => { resolveConnected = resolve; });
  const unsubscribe = subscribeRealtime(role, message => {
    if (message.type === 'connected') resolveConnected();
    onMessage(message);
  });
  let timeout;
  try {
    await Promise.race([connected, new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('isolated React WebSocket did not connect')), 15000); })]);
  } finally { clearTimeout(timeout); }
  return unsubscribe;
}

export async function adminControllers() {
  globalThis.window ||= new EventTarget();
  registerHooks({ resolve(specifier, context, nextResolve) {
    if (specifier === '@xa/shared') return { url: new URL('./fronted-shared-node.mjs', import.meta.url).href, shortCircuit: true };
    if (specifier.startsWith('@/')) return { url: new URL('../fronted/admin/src/' + specifier.slice(2) + '.ts', import.meta.url).href, shortCircuit: true };
    return nextResolve(specifier, context);
  } });
  return { reviews: await import('../fronted/admin/src/lib/reviews.ts'), api: await import('../fronted/admin/src/api/request.ts') };
}
