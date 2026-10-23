const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const tick = () => new Promise(resolve => setImmediate(resolve));
const campaign = {
  id: 7, version: 2, name: '联调奖池', enabled: true, point_cost: 100, ten_draw_cost: 1000, draw_counts: [1, 10],
  ten_guarantee: { enabled: true, count: 10, min_non_points: 1, available: true, eligible_prize_ids: [3], pity_priority: true, unavailable_reason: '' },
  pity: { enabled: true, target_reward_code: 'lottery-experience-68', target_prize_id: 1, target_name: '68 体验单', limit: 240, count: 235, remaining: 5 },
  prizes: [{ id: 1, name: '68 体验单', kind: 'SERVICE', rarity: 'SSR', probability: 0 }, { id: 2, name: '5 积分', kind: 'POINTS', rarity: 'R', probability: 9000 }, { id: 3, name: '任选冠名 3 天', kind: 'NAMING', rarity: 'SR', probability: 1000 }]
};
function drawResponse(count = 10) {
  return { batch_id: 'test-batch', count, total_point_cost: 100 * count, points_balance: 5040,
    ten_guarantee: { ...campaign.ten_guarantee },
    pity: { ...campaign.pity, count: 5, remaining: 235 },
    draws: Array.from({ length: count }, (_, i) => ({ id: i + 1, prize_id: i === 4 ? 1 : 2, point_cost: 100,
      rarity: i === 4 ? 'SSR' : 'R', is_pity: i === 4, is_ten_guarantee: false, pity_before: i === 4 ? 239 : i,
      pity_after: i === 4 ? 0 : i + 1, claim: { id: 101 + i, name_snapshot: i === 4 ? '68 体验单' : '5 积分', status: i === 4 ? 'PENDING' : 'COMPLETED' } })) };
}
function environment(respond) {
  for (const key of Object.keys(require.cache)) if (key.startsWith(root + path.sep)) delete require.cache[key];
  const storage = new Map(), calls = [], navigation = [], toasts = [];
  global.wx = {
    getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, structuredClone(value)), removeStorageSync: key => storage.delete(key),
    getSystemInfoSync: () => ({ windowWidth: 375 }), getWindowInfo: () => ({ windowWidth: 375 }),
    getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
    showToast: options => toasts.push(options.title), stopPullDownRefresh() {},
    createCanvasContext: () => new Proxy({}, { get: () => () => {} }),
    navigateTo: options => navigation.push(options.url),
    request(options) {
      calls.push(options);
      Promise.resolve().then(() => respond(options)).then(result => {
        if (result instanceof Error) options.fail(result);
        else options.success(result && 'statusCode' in result ? result : { statusCode: 200, data: { code: 0, data: result } });
      }).catch(options.fail);
    }
  };
  const auth = require(path.join(root, 'core/auth'));
  function login(id = '111') { auth.setSession({ token: 'unit-test', refresh: 'unit-refresh', user: { id, role: 'customer' } }); }
  function page() {
    let definition; global.Page = value => { definition = value; };
    delete require.cache[require.resolve(path.join(root, 'pages/lottery/index.js'))];
    require(path.join(root, 'pages/lottery/index.js'));
    const instance = { ...definition, data: structuredClone(definition.data), setData(patch, callback) { Object.assign(this.data, patch); callback && callback(); } };
    for (const [key, value] of Object.entries(instance)) if (typeof value === 'function') instance[key] = value.bind(instance);
    instance.onLoad(); instance._visible = true;
    return instance;
  }
  return { calls, storage, auth, login, page, navigation, toasts, api: require(path.join(root, 'core/api')) };
}
const fixtureReply = options => options.method === 'POST' ? drawResponse(options.data.count) : options.url.endsWith('/club/points/') ? { points: 6000 } : campaign;
const posts = env => env.calls.filter(row => row.method === 'POST');

test('native ten draw sends one fund transaction, uses the server points/pity and reveals ten persisted claims', async () => {
  const env = environment(fixtureReply); env.login(); const page = env.page(); await page.load();
  let timers = []; page.later = (fn, delay) => timers.push({ fn, delay });
  await page.ten();
  assert.equal(posts(env).length, 1); assert.equal(posts(env)[0].data.count, 10); assert.ok(posts(env)[0].data.client_request_id);
  assert.equal(page.data.points, 5040); assert.equal(page.data.campaign.pity.count, 5); assert.equal(page.data.campaign.pity.remaining, 235);
  assert.equal(page.data.phase, 'summon'); assert.equal(page.data.results.length, 10); assert.equal(page.data.revealed, 0);
  assert.equal(page.data.resultPity, true); assert.equal(page.data.focusRarity, 'SSR');
  assert.equal(page.data.resultNonPoints, 1); assert.equal(page.data.resultTenGuarantee, false, 'a hard pity is sufficient without pretending the tenth draw was forced');
  await page.ten(); assert.equal(posts(env).length, 1, 'repeated tap during animation must not submit');
  timers.shift().fn(); assert.equal(page.data.phase, 'reveal'); assert.equal(page.data.revealed, 1);
  while (timers.length) timers.shift().fn();
  assert.equal(page.data.phase, 'done'); assert.equal(page.data.revealed, 10); assert.equal(page.data.busy, false);
  assert.equal(page.data.results[4].is_pity, true); assert.equal(page.data.results[4].claim_id, 105);
  page.claim({ currentTarget: { dataset: { id: '105' } } });
  assert.equal(env.navigation.at(-1), '/pages/reward-claim/index?id=105');
  assert.equal(env.calls.filter(row => row.url.endsWith('/club/points/')).length, 1, 'no secondary points fetch may overwrite the committed reply');
  page.onUnload();
});

test('skip and simplified motion change only presentation; hiding a page reveals every server result', async () => {
  const env = environment(fixtureReply); env.login(); const page = env.page(); await page.load();
  page.later = () => {};
  await page.ten(); page.skipAnimation(); page.skipAnimation();
  assert.equal(page.data.revealed, 10); assert.equal(posts(env).length, 1);
  page.toggleMotion({ detail: { value: true } });
  await page.ten(); assert.equal(page.data.phase, 'done'); assert.equal(page.data.revealed, 10); assert.equal(posts(env).length, 2);
  page.toggleMotion({ detail: { value: false } });
  await page.ten(); page.onHide(); assert.equal(page.data.phase, 'done'); assert.equal(page.data.revealed, 10); assert.equal(posts(env).length, 3);
  page.records(); assert.equal(env.navigation.at(-1), '/pages/points/index?tab=claims'); page.onUnload();
});

test('a result arriving while the page is hidden finishes immediately without a discarded claim', async () => {
  let release;
  const env = environment(options => options.method === 'POST' ? new Promise(resolve => { release = resolve; }) : fixtureReply(options));
  env.login(); const page = env.page(); await page.load();
  const spin = page.ten(); await tick(); page.onHide(); release(drawResponse()); await spin;
  assert.equal(page.data.phase, 'done'); assert.equal(page.data.revealed, 10); assert.equal(page.data.busy, false); page.onUnload();
});

test('uncertain network outcomes keep one retry intent and one fund id even after page re-entry and campaign update', async () => {
  let attempts = 0;
  const env = environment(options => options.method === 'POST' ? (++attempts === 1 ? new Error('offline') : drawResponse()) : fixtureReply(options));
  env.login(); let page = env.page(); await page.load(); await page.ten();
  assert.equal(page.data.retryPending, true); assert.equal(page.data.retryCount, 10); assert.match(page.data.drawError, /重试同一笔/);
  const original = posts(env)[0].data; await page.spin(); assert.equal(posts(env).length, 1);
  page.onUnload(); page = env.page(); await page.load(); page.setData({ campaign: { ...page.data.campaign, id: 99, version: 9 }, points: 0, simpleMotion: true });
  await page.retryDraw();
  assert.deepEqual(posts(env)[1].data, original, 'retry must preserve count, campaign, version and idempotency id despite the new pool');
  assert.equal(page.data.campaign.id, 99); assert.equal(page.data.campaign.pity.count, campaign.pity.count, 'an old pool receipt must not replace the new pool pity');
  assert.equal(page.data.retryPending, false); assert.equal(page.data.results.length, 10); assert.equal(page.data.phase, 'done'); page.onUnload();
});

test('business rejections clear pending intent; insufficient initial points and a disabled pool never submit', async () => {
  const env = environment(options => options.method === 'POST' ? { statusCode: 400, data: { code: 400, msg: '当日次数不足' } } : fixtureReply(options));
  env.login(); const page = env.page(); await page.load();
  page.setData({ points: 999 }); await page.ten(); assert.equal(posts(env).length, 0); assert.equal(env.toasts.at(-1), '积分不足');
  page.setData({ points: 6000, campaign: { ...page.data.campaign, enabled: false } }); await page.ten(); assert.equal(posts(env).length, 0);
  page.setData({ campaign: { ...page.data.campaign, enabled: true } }); await page.ten();
  assert.equal(posts(env).length, 1); assert.equal(page.data.retryPending, false); assert.match(page.data.drawError, /当日次数不足/);
  assert.equal(env.storage.has('xa:native:lottery-pending:111'), false); page.onUnload();
});

test('malformed successful batches cannot be repaired by an implicit second draw or leak partial cards', async () => {
  const env = environment(options => options.method === 'POST' ? { ...drawResponse(), draws: drawResponse().draws.slice(0, 9) } : fixtureReply(options));
  env.login(); const page = env.page(); await page.load(); await page.ten();
  assert.equal(posts(env).length, 1); assert.equal(page.data.results.length, 0); assert.equal(page.data.retryPending, false);
  assert.match(page.data.drawError, /已受理.*中奖记录/); await page.retryDraw(); assert.equal(posts(env).length, 1); page.onUnload();
});

test('an account switch cancels all animation and never displays another boss awards or pity', async () => {
  let release;
  const env = environment(options => options.method === 'POST' ? new Promise(resolve => { release = resolve; }) : fixtureReply(options));
  env.login(); const page = env.page(); await page.load(); const spin = page.ten(); await tick();
  env.login('222'); release(drawResponse()); await spin; await tick();
  assert.equal(page.data.results.length, 0); assert.equal(page.data.busy, false); assert.equal(page.data.resultCount, 0);
  assert.equal(page.data.retryPending, false); page.onUnload();
});

test('an accepted result after unload re-enters with the same explicit request id even though fund storage was consumed', async () => {
  let release, commits = 0;
  const receipts = new Map();
  const env = environment(options => {
    if (options.method !== 'POST') return fixtureReply(options);
    const key = options.data.client_request_id;
    if (receipts.has(key)) return { statusCode: 409, data: { code: 409, errorCode: 'FUND_ALREADY_PROCESSED', fundResult: { code: 0, data: receipts.get(key) } } };
    commits++; const result = drawResponse(); receipts.set(key, result);
    return new Promise(resolve => { release = () => resolve(result); });
  });
  env.login(); let page = env.page(); await page.load();
  const originalSpin = page.ten(); await tick(); const originalId = posts(env)[0].data.client_request_id;
  page.onUnload(); release(); await originalSpin;
  assert.equal([...env.storage.keys()].filter(key => key.startsWith('xa:native:fund:')).length, 0, 'fund should continue removing ordinary completed request receipts');
  page = env.page(); await page.load(); page.setData({ simpleMotion: true });
  assert.equal(page.data.retryPending, true); await page.retryDraw();
  assert.equal(posts(env)[1].data.client_request_id, originalId); assert.equal(commits, 1);
  assert.equal(page.data.results.length, 10); assert.equal(page.data.revealed, 10); page.onUnload();
});

test('a failed pending-intent removal can only replay its original receipt, while a new intentional draw gets a fresh id', async () => {
  const receipts = new Map(); let commits = 0;
  const env = environment(options => {
    if (options.method !== 'POST') return fixtureReply(options);
    const key = options.data.client_request_id;
    if (!receipts.has(key)) { commits++; receipts.set(key, drawResponse()); }
    return receipts.get(key);
  });
  env.login();
  const remove = wx.removeStorageSync;
  wx.removeStorageSync = key => { if (key.startsWith('xa:native:lottery-pending:')) throw new Error('disk remove failure'); remove(key); };
  let page = env.page(); await page.load(); page.setData({ simpleMotion: true }); await page.ten();
  const firstId = posts(env)[0].data.client_request_id; page.onUnload();
  page = env.page(); await page.load(); page.setData({ simpleMotion: true }); await page.retryDraw();
  assert.equal(posts(env)[1].data.client_request_id, firstId); assert.equal(commits, 1);
  await page.ten();
  assert.notEqual(posts(env)[2].data.client_request_id, firstId); assert.equal(commits, 2); page.onUnload();
});

test('explicit fund ids separate equal payload intents, reject invalid ids and retain default fund behavior', async () => {
  const env = environment(() => ({ ok: true })); env.login();
  const body = { campaign_id: 7, version: 2, count: 10 };
  await env.api.fund('/club/lottery/', body, { clientRequestId: 'native-test-first' });
  await env.api.fund('/club/lottery/', body, { clientRequestId: 'native-test-second' });
  assert.deepEqual(posts(env).map(row => row.data.client_request_id), ['native-test-first', 'native-test-second']);
  await assert.rejects(env.api.fund('/club/lottery/', body, { clientRequestId: 'bad id' }), /无效的交易凭证/);
  assert.equal(posts(env).length, 2);
  await env.api.fund('/club/redeem/', { reward_id: 1 });
  assert.match(posts(env)[2].data.client_request_id, /^native-/);
});

test('daily limits prevent impossible ten draws and server remaining counts update immediately after a committed batch', async () => {
  const env = environment(options => options.method === 'POST' ? { ...drawResponse(), daily_remaining: 0 } : fixtureReply(options));
  env.login(); const page = env.page(); await page.load();
  page.setData({ campaign: { ...page.data.campaign, daily_remaining: 9 } }); await page.ten();
  assert.equal(posts(env).length, 0); assert.match(env.toasts.at(-1), /今日剩余 9 次/);
  page.setData({ campaign: { ...page.data.campaign, daily_remaining: 10 }, simpleMotion: true }); await page.ten();
  assert.equal(page.data.campaign.daily_remaining, 0); await page.spin();
  assert.equal(posts(env).length, 1); assert.match(env.toasts.at(-1), /今日剩余 0 次/); page.onUnload();
});

test('unreadable or unwritable draw credentials block a new purchase until their original intent can be read', async () => {
  const env = environment(fixtureReply); env.login();
  const key = 'xa:native:lottery-pending:111';
  env.storage.set(key, { campaign_id: 7, version: 2, count: 10, request_id: 'native-original-intent' });
  const read = wx.getStorageSync;
  wx.getStorageSync = storageKey => { if (storageKey === key) throw new Error('disk read failure'); return read(storageKey); };
  const page = env.page(); await page.load();
  assert.equal(page.data.credentialError, true); await page.ten(); assert.equal(posts(env).length, 0);
  wx.getStorageSync = read; page.reloadCredential();
  assert.equal(page.data.credentialError, false); assert.equal(page.data.retryPending, true);
  page.setData({ simpleMotion: true }); await page.retryDraw();
  assert.equal(posts(env)[0].data.client_request_id, 'native-original-intent');
  const write = wx.setStorageSync;
  wx.setStorageSync = (storageKey, value) => { if (storageKey === key) throw new Error('disk write failure'); write(storageKey, value); };
  await page.ten(); assert.equal(posts(env).length, 1); assert.match(env.toasts.at(-1), /无法保存抽奖凭证/); page.onUnload();
});

test('a pool without an eligible non-points prize blocks ten draws without blocking one draw', async () => {
  const guarantee = { ...campaign.ten_guarantee, available: false, eligible_prize_ids: [], unavailable_reason: '十连奖池缺少正权重非积分奖品' };
  const env = environment(options => options.method === 'POST' ? drawResponse(options.data.count) : options.url.endsWith('/club/points/') ? { points: 6000 } : { ...campaign, ten_guarantee: guarantee });
  env.login(); const page = env.page(); await page.load(); page.setData({ simpleMotion: true });
  await page.ten(); assert.equal(posts(env).length, 0); assert.equal(env.toasts.at(-1), guarantee.unavailable_reason);
  await page.spin(); assert.equal(posts(env).length, 1); assert.equal(posts(env)[0].data.count, 1);
  assert.equal(page.data.results.length, 1); assert.equal(page.data.phase, 'done'); page.onUnload();
});

test('native ten guarantee tags only the actual server-marked final draw and counts non-points awards', async () => {
  const result = drawResponse();
  result.pity = { ...campaign.pity, count: 10, remaining: 230 };
  result.draws = result.draws.map((row, index) => ({ ...row, prize_id: index === 9 ? 3 : 2,
    rarity: index === 9 ? 'SR' : 'R', is_pity: false, is_ten_guarantee: index === 9,
    pity_before: index, pity_after: index + 1,
    claim: { ...row.claim, name_snapshot: index === 9 ? '任选冠名 3 天' : '5 积分', status: index === 9 ? 'PENDING' : 'COMPLETED' } }));
  const env = environment(options => options.method === 'POST' ? result : fixtureReply(options));
  env.login(); const page = env.page(); await page.load(); page.setData({ simpleMotion: true }); await page.ten();
  assert.equal(page.data.resultTenGuarantee, true); assert.equal(page.data.resultPity, false); assert.equal(page.data.resultNonPoints, 1);
  assert.equal(page.data.focusRarity, 'SR'); assert.equal(page.data.results[9].is_ten_guarantee, true);
  assert.equal(page.data.results.slice(0, 9).some(row => row.is_ten_guarantee), false);
  assert.equal(page.data.results[9].claimLabel, '领取奖励'); assert.equal(page.data.campaign.pity.count, 10); page.onUnload();
});

test('a naturally drawn SR does not get a false ten-guarantee badge, and updated server availability applies immediately', async () => {
  const result = drawResponse();
  result.ten_guarantee = { ...campaign.ten_guarantee, available: false, unavailable_reason: '当前十连奖池暂不可用' };
  result.draws[4] = { ...result.draws[4], prize_id: 3, rarity: 'SR', is_pity: false,
    claim: { ...result.draws[4].claim, name_snapshot: '任选冠名 3 天' } };
  const env = environment(options => options.method === 'POST' ? result : fixtureReply(options));
  env.login(); const page = env.page(); await page.load(); page.setData({ simpleMotion: true }); await page.ten();
  assert.equal(page.data.resultNonPoints, 1); assert.equal(page.data.resultTenGuarantee, false); assert.equal(page.data.resultPity, false);
  assert.equal(page.data.results.every(row => row.is_ten_guarantee === false), true);
  assert.equal(page.data.campaign.ten_guarantee.available, false); await page.ten();
  assert.equal(posts(env).length, 1); assert.equal(env.toasts.at(-1), result.ten_guarantee.unavailable_reason); page.onUnload();
});

test('pending ten draws still replay their exact receipt when current ten availability becomes false', async () => {
  let attempts = 0;
  const env = environment(options => options.method === 'POST' ? (++attempts === 1 ? new Error('offline') : drawResponse()) : fixtureReply(options));
  env.login(); const page = env.page(); await page.load(); await page.ten();
  const originalId = posts(env)[0].data.client_request_id;
  page.setData({ campaign: { ...page.data.campaign, enabled: false, ten_guarantee: { ...page.data.campaign.ten_guarantee, available: false } }, simpleMotion: true });
  await page.retryDraw();
  assert.equal(posts(env).length, 2); assert.equal(posts(env)[1].data.client_request_id, originalId);
  assert.equal(page.data.results.length, 10); assert.equal(page.data.retryPending, false); page.onUnload();
});
