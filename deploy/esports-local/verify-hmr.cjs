// GET-only browser validation. A CSS mtime touch emits real Vite HMR without changing source contents.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');
const { chromium } = require('../../node_modules/playwright');
const repo = path.resolve(__dirname, '../..');
const output = path.join(__dirname, '.runtime');
const settings = JSON.parse(fs.readFileSync(path.join(__dirname, 'settings.json'), 'utf8'));
const distribution = process.env.XA_WSL_DISTRIBUTION || settings.distribution;
fs.mkdirSync(output, { recursive: true });
const hash = file => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(condition, timeout = 20000) {
  const end = Date.now() + timeout;
  while (!condition()) { if (Date.now() > end) throw new Error('Expected real HMR notification was not received'); await delay(100); }
}
(async () => {
  const names = settings.services.map(name => `${settings.project}-${name}-1`);
  const mounts = names.map(name => ({ name, mounts: JSON.parse(execFileSync('wsl.exe', ['-d', distribution, '-u', 'root', '--', 'docker', 'inspect', '--format', '{{json .Mounts}}', name], { encoding: 'utf8' })) }));
  fs.writeFileSync(path.join(output, 'mounts.json'), JSON.stringify(mounts, null, 2));
  const browser = await chromium.launch({ headless: true });
  const results = [];
  try {
    for (const [host, base, product] of [
      ['sl.esports.club', '/boss/', 'boss'], ['sl.esports.club', '/player/', 'provider'],
      ['xa.esports.club', '/boss/', 'boss'], ['xa.esports.club', '/player/', 'provider'],
      ['admin.esports.club', '/', 'admin'],
    ]) {
      const context = await browser.newContext();
      const page = await context.newPage();
      const sockets = [], frames = [], writes = [];
      page.on('websocket', socket => {
        const u = new URL(socket.url());
        sockets.push(u.origin + u.pathname); // exclude the Vite token and all business credentials
        socket.on('framereceived', ({ payload }) => {
          try {
            const event = JSON.parse(payload.toString());
            if (['connected', 'update', 'full-reload'].includes(event.type)) frames.push({
              type: event.type,
              paths: event.updates?.map(item => item.path) || (event.path ? [event.path] : []),
            });
          } catch {}
        });
      });
      await page.route('**/api/**', route => {
        if (['GET', 'HEAD', 'OPTIONS'].includes(route.request().method())) return route.continue();
        writes.push(route.request().method());
        return route.abort('blockedbyclient');
      });
      const response = await page.goto(`http://${host}${base}`, { waitUntil: 'domcontentloaded' });
      assert.equal(response.status(), 200);
      await until(() => frames.some(event => event.type === 'connected'));
      const source = path.join(repo, 'fronted', product, 'src', 'index.css');
      const before = hash(source), stat = fs.statSync(source);
      const start = frames.length;
      fs.utimesSync(source, stat.atime, new Date());
      await until(() => frames.slice(start).some(event => event.type === 'update' && event.paths.some(p => p.includes('src/index.css'))));
      const unchanged = before === hash(source);
      assert.equal(unchanged, true, 'Probe must not change or restore source contents');
      assert.equal(writes.length, 0);
      const result = { host, base, product, status: response.status(), sockets, notifications: frames.slice(start), sourceContentUnchanged: unchanged, writes, passed: true };
      results.push(result);
      fs.writeFileSync(path.join(output, 'hmr-results.json'), JSON.stringify({ scope: 'Real local DNS, source bind mounts, actual Vite WebSocket HMR after mtime touch; no source-content edit or business write', results }, null, 2));
      console.log(JSON.stringify({ host, base, realHmr: true, sourceContentUnchanged: unchanged }));
      await context.close();
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error.message); process.exitCode = 1; });
