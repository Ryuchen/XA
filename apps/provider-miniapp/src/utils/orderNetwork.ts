export interface NetworkSample { at: number; ms: number; failed: boolean; status: number; id: number }
const PERCENT_SCALE = 100;
const samples: NetworkSample[] = [];
const cooldowns = new Map<string, number>();
let session = -1, serial = 0, reported = 0;
export const scopeFor = (url: string) => /\/orders\/orders\/[^/]+\/grab\//.test(url) ? 'grab'
  : url.startsWith('/orders/orders/?') && url.includes('status=pending') ? 'pool' : '';
const ensure = (revision: number) => {
  if (session !== revision) { session = revision; samples.length = 0; cooldowns.clear(); reported = serial; }
};
export const cooldownRemaining = (scope: string, revision: number, now = Date.now()) => {
  ensure(revision); return Math.max(0, Math.ceil(((cooldowns.get(scope) || 0) - now) / 1000));
};
export const recordOrderRequest = (url: string, ms: number, status: number, revision: number, retryAfter = 0, now = Date.now()) => {
  ensure(revision); const scope = scopeFor(url); if (!scope || status === 401) return;
  samples.push({ id: ++serial, at: now, ms: Math.min(15000, Math.max(0, ms)), failed: status === 0, status });
  if (samples.length > 60) samples.shift();
  if (status === 429) cooldowns.set(scope, now + Math.min(300, Math.max(1, retryAfter)) * 1000);
};
export const summarize = (rows: NetworkSample[]) => {
  const timings = rows.filter(item => !item.failed).sort((a, b) => a.id - b.id).map(item => item.ms);
  const sorted = [...timings].sort((a, b) => a - b);
  const jitter = timings.slice(1).reduce((sum, ms, i) => sum + Math.abs(ms - timings[i]), 0);
  return { samples: rows.length, failures: rows.filter(item => item.failed).length,
    average_ms: timings.length ? Math.round(timings.reduce((sum, ms) => sum + ms, 0) / timings.length) : 0,
    jitter_ms: timings.length > 1 ? Math.round(jitter / (timings.length - 1)) : 0,
    p95_ms: sorted.length ? Math.round(sorted[Math.ceil(sorted.length * .95) - 1]) : 0,
    limited: rows.filter(item => item.status === 429).length,
    server_errors: rows.filter(item => item.status >= 500).length };
};
export const networkSnapshot = (revision: number, now = Date.now()) => {
  ensure(revision); const rows = samples.filter(item => now - item.at <= 60000);
  const summary = summarize(rows);
  return { ...summary, stale: !rows.length || now - rows[rows.length - 1].at > 30000,
    failure_percent: rows.length ? Math.round(summary.failures * PERCENT_SCALE / rows.length) : 0,
    grab_wait: cooldownRemaining('grab', revision, now), pool_wait: cooldownRemaining('pool', revision, now) };
};
export const pendingNetworkReport = (revision: number, now = Date.now()) => {
  ensure(revision); const rows = samples.filter(item => item.id > reported && now - item.at <= 60000);
  return { ...summarize(rows), cursor: rows.length ? rows[rows.length - 1].id : reported };
};
export const acknowledgeNetworkReport = (cursor: number, revision: number) => { if (revision === session) reported = Math.max(reported, cursor); };
