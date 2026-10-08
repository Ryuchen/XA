/* Read-only production smoke. Credentials and bearer tokens never printed. */
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { performance } = require('node:perf_hooks');
let lastRoute = 'initialization';

(async () => {
  const out = path.resolve(__dirname, '../.tools/tencent-deploy');
  const access = JSON.parse(fs.readFileSync(path.join(out, 'admin-access.json'), 'utf8'));
  const base = 'https://admin.xaesports.club';
  let token;
  const checks = [];
  async function json(route, body, authenticated = true) {
    lastRoute = route;
    const start = performance.now();
    const response = await fetch(base + route, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json', ...(authenticated && token ? { Authorization: 'Bearer ' + token } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(20000),
    });
    const data = await response.json();
    checks.push({route, http:response.status, code:data.code, ms:Math.round(performance.now()-start)});
    return { response, data };
  }
  const login = await json('/api/admin/auth/login', {username:access.username,password:access.password}, false);
  assert.equal(login.data.code,0,'Administrator login failed');
  token = login.data.data.token;
  const permissionCount = login.data.data.profile.permissions.length;
  assert(permissionCount > 50);
  const resources = ['users','escorts','orders','evaluations','reports','withdrawals','wallets',
    'recharge-records','transactions','coupons','user-coupons','announcements','banners',
    'achievements','checkin-gifts','service-items','game-categories','service-categories',
    'boss-types','escort-levels','support-cards','audition-links','audition-signups',
    'chat-sessions','messages','roles','admins','audit-logs','bans','gameplays',
    'gift-benefits','club-rewards','club-claims','broadcast-rules','broadcasts',
    'provider-namings','club-content','vip-tiers','recharge-packages','lotteries'];
  for (const route of ['/api/admin/auth/profile','/api/admin/dashboard/stats',
    '/api/admin/dashboard/player','/api/admin/permissions','/api/admin/config/commission',
    '/api/admin/config/withdraw','/api/admin/config/checkin','/api/admin/config/provider-pass',
    '/api/admin/club-config/',...resources.map(r=>'/api/admin/'+r+'/')]) {
    const result = await json(route);
    assert(result.response.ok && result.data.code === 0, `Read failed: ${route} HTTP ${result.response.status} code ${result.data.code}`);
  }
  const unauthorized = await json('/api/admin/wallets/',undefined,false);
  assert([401,403].includes(unauthorized.response.status));
  const refresh = await json('/api/admin/auth/refresh',{refresh:login.data.data.refresh},false);
  assert.equal(refresh.data.code,0);
  for (const origin of ['https://api.xaesports.club','https://admin.xaesports.club','https://provider.xaesports.club']) {
    const response = await fetch(origin+'/api/health/',{signal:AbortSignal.timeout(15000)});
    assert(response.ok && (await response.json()).status==='ok');
    const privateResponse = await fetch(origin+'/media/wallet/proofs/nonexistent.jpg');
    assert.equal(privateResponse.status,404);
  }
  const root = await fetch('https://xaesports.club/',{redirect:'manual'});
  assert([301,308].includes(root.status));
  assert(root.headers.get('location').startsWith('https://provider.xaesports.club'));
  const cors = await fetch('https://api.xaesports.club/api/health/',{headers:{Origin:base}});
  assert.equal(cors.headers.get('access-control-allow-origin'),base);
  const alien = await fetch('https://api.xaesports.club/api/health/',{headers:{Origin:'https://untrusted.example'}});
  assert.equal(alien.headers.get('access-control-allow-origin'),null);
  await new Promise((resolve,reject) => {
    const socket = new WebSocket('wss://api.xaesports.club/ws/orders/?token='+encodeURIComponent(token));
    const timer = setTimeout(()=>{socket.close();reject(Error('WSS heartbeat timed out'));},15000);
    socket.addEventListener('open',()=>socket.send('{"type":"ping"}'));
    socket.addEventListener('message',event=>{
      if(JSON.parse(event.data).type==='pong'){clearTimeout(timer);socket.close();resolve();}
    });
    socket.addEventListener('error',()=>{clearTimeout(timer);reject(Error('WSS connection failed'));});
  });
  const report = {administratorLogin:true,permissionCount,adminReadEndpoints:49,
    anonymousRejected:true,tokenRefresh:true,threeOriginsHealthy:true,
    privateMediaBlocked:true,corsRestricted:true,rootRedirect:true,wssHeartbeat:true,checks};
  fs.writeFileSync(path.join(out,'cloud-web-report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({...report,checks:checks.length}));
})().catch(error=>{console.error(`${lastRoute}: ${error.message}; ${error.cause?.code || ''}`);process.exitCode=1;});
