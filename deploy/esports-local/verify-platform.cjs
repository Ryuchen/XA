// Real local browser + isolated MySQL acceptance. External transports are test substitutes.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const { chromium } = require('../../node_modules/playwright');

const root = path.resolve(__dirname, '../..');
const python = path.join(root, '.venv', 'Scripts', 'python.exe');
const fixtures = path.join(__dirname, 'scripts', 'platform-acceptance-fixtures.py');
const delivery = path.join(root, '.tools', 'fronted-acceptance', 'sms-delivery.private.json');
let fixture, browser, phase = '启动';
const runFixture = (...args) => JSON.parse(execFileSync(python, [fixtures, ...args], { encoding: 'utf8', cwd: root }));
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

async function codeFor(phone) {
  for (let attempt = 0; attempt < 50; attempt++) {
    try {
      const record = JSON.parse(fs.readFileSync(delivery, 'utf8'))[phone];
      if (record?.external_substitute && Date.now() / 1000 - record.sent_at < 60) return record.code;
    } catch { /* A concurrent test write may briefly be incomplete. */ }
    await delay(100);
  }
  throw new Error('隔离短信替身未收到测试验证码');
}
function otp(secret, offset = 0) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const character of secret) bits += alphabet.indexOf(character).toString(2).padStart(5, '0');
  const key = Buffer.from((bits.match(/.{8}/g) || []).map(item => parseInt(item, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000) + offset));
  const digest = crypto.createHmac('sha1', key).update(counter).digest();
  const at = digest[digest.length - 1] & 15;
  return String((digest.readUInt32BE(at) & 0x7fffffff) % 1000000).padStart(6, '0');
}
async function newPage() {
  const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } });
  await context.route('**/*', route => {
    const request = route.request(), url = new URL(request.url());
    if (url.hostname !== 'admin.esports.club') return route.abort('blockedbyclient');
    if (!['GET', 'HEAD', 'OPTIONS'].includes(request.method()) && ![
      '/api/admin/auth/password-login', '/api/admin/platform/auth/otp-setup', '/api/admin/platform/accounts',
    ].includes(url.pathname)) return route.abort('blockedbyclient');
    return route.continue();
  });
  return { context, page: await context.newPage() };
}
async function passwordStep(page, username, password) {
  await page.goto('http://admin.esports.club/#/login', { waitUntil: 'domcontentloaded' });
  await page.getByLabel('管理员账号').fill(username);
  await page.getByLabel('登录密码').fill(password);
  await page.getByRole('button', { name: '下一步' }).click();
}
async function smsLogin(page, username, password, phone) {
  await passwordStep(page, username, password);
  await page.getByLabel('短信验证码').waitFor();
  await page.getByLabel('短信验证码').fill(await codeFor(phone));
  await page.getByRole('button', { name: '验证并继续' }).click();
  await page.getByRole('heading', { name: '平台管理' }).waitFor();
}

(async () => {
  try {
    fixture = runFixture('create');
    phase = '启动浏览器';
    browser = await chromium.launch({ headless: true, args: ['--no-proxy-server'] });
    const rootSession = await newPage();
    phase = '平台短信登录与聚合';
    await smsLogin(rootSession.page, fixture.username, fixture.password, fixture.phone);
    await rootSession.page.getByRole('heading', { name: '门店汇总' }).waitFor();
    await rootSession.page.getByRole('heading', { name: '平台人员' }).waitFor();
    await rootSession.page.getByRole('heading', { name: '通行证退款待补资金' }).waitFor();
    await rootSession.page.getByRole('heading', { name: '平台提现审核' }).waitFor();
    await rootSession.page.getByText('声浪电竞', { exact: true }).first().waitFor();
    await rootSession.page.getByText('兴安电竞', { exact: true }).first().waitFor();
    const token = await rootSession.page.evaluate(() => JSON.parse(sessionStorage.getItem('xa:fronted:platform:session:v1')).token);
    const crossing = await rootSession.context.request.get('http://admin.esports.club/api/admin/auth/profile', { headers: { Authorization: `Bearer ${token}` } });
    assert.equal(crossing.status(), 401);

    phase = '创建指定门店财务与审计';
    const finance = 'qa-finance-' + fixture.nonce;
    const financePassword = crypto.randomBytes(24).toString('base64url');
    await rootSession.page.getByRole('button', { name: '新增平台账号' }).click();
    await rootSession.page.getByLabel('账号', { exact: true }).fill(finance);
    await rootSession.page.getByLabel('姓名', { exact: true }).fill('隔离验收财务');
    await rootSession.page.getByLabel('手机号', { exact: true }).fill(fixture.finance_phone);
    await rootSession.page.getByLabel('初始密码').fill(financePassword);
    await rootSession.page.getByLabel('全部门店（含以后新增的门店）').uncheck();
    await rootSession.page.getByLabel('兴安电竞', { exact: true }).check();
    await rootSession.page.getByRole('button', { name: '保存', exact: true }).click();
    await rootSession.page.getByText('平台账号已保存。').waitFor();
    const financeRow = rootSession.page.getByRole('row').filter({ hasText: finance });
    await financeRow.getByRole('button', { name: '记录' }).click();
    await rootSession.page.getByText('创建账号', { exact: false }).first().waitFor();

    phase = '绑定验证器和恢复码';
    const setup = rootSession.page.locator('section[aria-label="平台验证器设置"]');
    await setup.getByRole('button', { name: '绑定验证器' }).click();
    const secret = await setup.locator('p code').first().textContent();
    await setup.getByLabel('动态验证码').fill(otp(secret));
    await setup.getByRole('button', { name: '确认绑定' }).click();
    await setup.getByText('请安全保存以下一次性恢复码', { exact: false }).waitFor();
    assert.equal(await setup.locator('ul li code').count(), 8);
    await setup.getByRole('button', { name: '已保存' }).click();
    await rootSession.page.getByRole('button', { name: '退出登录' }).click();
    phase = '平台验证器登录';
    await passwordStep(rootSession.page, fixture.username, fixture.password);
    await rootSession.page.getByLabel('动态验证码或恢复码').fill(otp(secret, 1));
    await rootSession.page.getByRole('button', { name: '验证并继续' }).click();
    await rootSession.page.getByRole('heading', { name: '平台管理' }).waitFor();

    phase = '财务授权门店隔离';
    const financeSession = await newPage();
    await smsLogin(financeSession.page, finance, financePassword, fixture.finance_phone);
    await financeSession.page.getByRole('heading', { name: '授权门店' }).waitFor();
    await financeSession.page.getByText('兴安电竞', { exact: false }).first().waitFor();
    const shortfalls = financeSession.page.locator('section[aria-labelledby="pass-shortfalls-title"]');
    const withdrawals = financeSession.page.locator('section[aria-labelledby="platform-withdraw-title"]');
    await withdrawals.getByRole('heading', { name: '平台提现审核' }).waitFor();
    await withdrawals.getByRole('button', { name: /兴安电竞/ }).waitFor();
    assert.equal(await withdrawals.getByRole('button', { name: /声浪电竞/ }).count(), 0);
    await withdrawals.getByRole('button', { name: /兴安电竞/ }).click();
    await withdrawals.getByRole('button', { name: '处理记录' }).waitFor();
    await withdrawals.getByRole('button', { name: '历史待核实', exact: true }).click();
    await withdrawals.getByText('历史申请仅供核对', { exact: false }).waitFor();
    await shortfalls.getByRole('heading', { name: '通行证退款待补资金' }).waitFor();
    await shortfalls.getByText('兴安电竞', { exact: true }).waitFor();
    assert.equal(await shortfalls.getByText('声浪电竞', { exact: true }).count(), 0);
    await shortfalls.getByRole('button', { name: '查看明细' }).click();
    await shortfalls.getByRole('heading', { name: '兴安电竞 · 退款缺口明细' }).waitFor();
    assert.equal(await financeSession.page.getByRole('heading', { name: '平台人员' }).count(), 0);
    const financeToken = await financeSession.page.evaluate(() => JSON.parse(sessionStorage.getItem('xa:fronted:platform:session:v1')).token);
    const denied = await financeSession.context.request.get('http://admin.esports.club/api/admin/platform/accounts', { headers: { Authorization: `Bearer ${financeToken}` } });
    assert.equal(denied.status(), 403);
    const otherStore = await financeSession.context.request.get('http://admin.esports.club/api/admin/platform/pass-refund-shortfalls/shenglang', { headers: { Authorization: `Bearer ${financeToken}` } });
    assert.equal(otherStore.status(), 404);
    const otherWithdrawals = await financeSession.context.request.get('http://admin.esports.club/api/admin/platform/withdrawals/shenglang', { headers: { Authorization: `Bearer ${financeToken}` } });
    assert.equal(otherWithdrawals.status(), 404);
    const otherLegacy = await financeSession.context.request.get('http://admin.esports.club/api/admin/platform/withdrawals/shenglang?scope=legacy', { headers: { Authorization: `Bearer ${financeToken}` } });
    assert.equal(otherLegacy.status(), 404);
    console.log(JSON.stringify({ isolatedMysql: true, sms: 'test-substitute', platformLogin: true, dashboard: true,
      scopedFinance: true, passRefundShortfalls: true, withdrawalQueue: true, legacyInspection: true, accountAudit: true, authenticator: true, storeTokenIsolation: true }));
  } catch (error) {
    // Do not print test credentials, OTP secrets, recovery codes or request bodies.
    console.error(`平台隔离验收失败：${phase}（${error.name}）`);
    process.exitCode = 1;
  } finally {
    if (browser) await browser.close();
    if (fixture) {
      try { runFixture('finish', fixture.nonce); }
      catch { console.error('测试账号停用失败，请检查隔离开发库的 qa-platform / qa-finance 账号'); process.exitCode = 1; }
    }
  }
})();
