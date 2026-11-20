/* UI contract tests are deliberately separate from real read-only smoke checks.
 * New SaaS endpoints are not implemented by this frontend task. No money/order
 * write from this script may reach the development backend. Run from repo root. */
const { chromium } = require('playwright')
const fs = require('node:fs')
const path = require('node:path')
const assert = require('node:assert/strict')
const out = path.resolve('.tools/saas-frontend-20261111')
fs.mkdirSync(out, { recursive: true })
const report = { contractChecks: [], realReadOnlyChecks: [], errors: [] }
const branding = { display_name: '样例俱乐部', primary_color: '#2563EB', welcome: '为你安排每一局', support_label: '专属客服', density: 'comfortable' }
const store = { id: 1, slug: 'shenglang', name: '接口契约样例俱乐部', currency_name: '浪花', provider_name_prefix: 'SL', status: 'ACTIVE', is_active: true, boss_url: 'http://sl.esports.club/boss/', provider_url: 'http://sl.esports.club/player/', admin_url: 'http://admin.esports.club/', capabilities: ['SAAS_MODULES_V1', 'BOSS_H5_BRANDING_V1'] }
const profile = { id: 'contract-platform', username: 'contract-review', nickname: '接口契约样例 · 非真实业务验收', identity_type: 'platform', grants: [{ role: 'SUPER_ADMIN', scope_slug: '*' }], capabilities: ['SAAS_MODULES_V1', 'PLATFORM_ORDERS_V1', 'PLATFORM_DISPATCH_V1'] }
const contract = { store_slug: 'shenglang', version: 'v1', read_at: '2026-11-11T02:00:00Z', h5: { status: 'ACTIVE', can_create_order: true, can_customize: true }, branding, allowed_actions: ['REQUEST_PURCHASE', 'REQUEST_CUSTOMIZATION', 'SAVE_BRANDING'], requests: [] }
const order = { id: '42', store_slug: 'shenglang', store_name: store.name, order_no: 'CONTRACT-ONLY-42', service_name: '接口契约样例服务', customer_name: '样例老板', provider_names: [], currency_name: '浪花', status: 'PENDING', payment_status: 'PAID', dispatch_route: 'STAFF', order_source: 'BOSS_H5', version: 'v1', allowed_actions: ['ASSIGN'], created_at: '2026-11-11T02:00:00Z', settlement_status: 'WAITING' }
async function fixtureContext(browser, overrides = {}) {
  const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
  const writes = [], calls = []
  const who = overrides.profile || profile
  await context.addInitScript(value => {
    sessionStorage.setItem('xa:fronted:platform:session:v1', JSON.stringify({ token: 'contract-only', refresh: 'contract-refresh', sessionId: 'contract-session', profile: value }))
  }, who)
  await context.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), pathname = url.pathname
    if (!pathname.startsWith('/api/')) return route.continue()
    calls.push(pathname)
    if (request.method() !== 'GET') writes.push({ path: pathname, data: request.postDataJSON() })
    let data
    if (pathname.endsWith('/platform/profile')) data = who
    else if (pathname.endsWith('/platform/stores')) data = { results: [store] }
    else if (pathname.endsWith('/platform/dashboard')) data = { complete: false, generated_at: '2026-11-11T02:00:00Z', partial_totals: { orders: 1, bosses: 1, providers: 0 }, stores: [{ ...store, status: 'OK', read_at: '2026-11-11T02:00:00Z', counts: { orders: 1, bosses: 1, providers: 0 } }, { slug: 'xinan', name: '缺失俱乐部', status: 'UNAVAILABLE', counts: null }] }
    else if (/\/saas\/modules\/shenglang\/(requests|branding)$/.test(pathname)) {
      if (overrides.failWrite) return route.fulfill({ status: 503, json: { code: 503, msg: '接口契约测试：结果未确认' } })
      data = { ...contract, version: 'v2' }
    }
    else if (pathname.endsWith('/saas/modules/shenglang')) data = contract
    else if (pathname.endsWith('/platform/orders')) data = { results: [order], total: 1, complete: false, generated_at: '2026-11-11T02:00:00Z', missing_stores: ['xinan'] }
    else if (pathname.endsWith('/orders/shenglang/42/candidates')) data = { results: [{ id: '7', name: '样例陪玩' }] }
    else if (pathname.endsWith('/orders/shenglang/42/assign')) data = { ...order, status: 'GRABBED' }
    else if (pathname.endsWith('/orders/shenglang/42')) data = order
    else if (pathname.endsWith('/orders/shenglang/dispatch-options')) data = { customers: [{ id: '9', name: '样例老板' }], services: [{ id: '8', name: '样例服务' }] }
    else if (pathname.endsWith('/orders/shenglang/dispatch/quote')) data = { token: 'contract-quote', amount: 1000, currency_name: '浪花', summary: '接口契约报价', expires_at: '2099-11-11T02:00:00Z' }
    else if (pathname.endsWith('/orders/shenglang/dispatch')) data = order
    else return route.fulfill({ status: 404, json: { code: 404, msg: '接口契约测试未定义此调用' } })
    await route.fulfill({ json: { code: 0, data } })
  })
  return { context, writes, calls }
}
async function screenshot(page, name) {
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: path.join(out, `${name}.png`), fullPage: true })
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)
  assert.equal(overflow, false, `${name}: 页面横向溢出`)
}
async function contractTests(browser) {
  const fixture = await fixtureContext(browser), page = await fixture.context.newPage()
  page.on('pageerror', error => report.errors.push(error.message))
  const dashboardRoute = '**/api/admin/platform/dashboard'
  const summaryValues = page.locator('dl.saas-definition dd')
  await page.route(dashboardRoute, route => route.fulfill({ json: { code: 0, data: {
    complete: false,
    generated_at: '2026-11-11T02:00:00Z',
    partial_totals: null,
    stores: [{ slug: store.slug, name: store.name, status: 'NOT_ACTIVE', counts: null, read_at: null }],
  } } }))
  await page.goto('http://admin.esports.club/#/platform/overview')
  await page.getByRole('heading', { name: '平台总览', exact: true }).waitFor()
  await page.getByRole('cell', { name: '未营业或未初始化', exact: true }).waitFor()
  await page.getByText('尚未读取到俱乐部数据，暂时无法汇总。', { exact: true }).waitFor()
  assert.deepEqual(await summaryValues.allTextContents(), ['—', '—', '—'])
  assert.equal(await summaryValues.getByLabel('未读取', { exact: true }).count(), 3)

  await page.unroute(dashboardRoute)
  await page.reload()
  await page.getByText('部分俱乐部未取得数据', { exact: false }).waitFor()
  assert.deepEqual(await summaryValues.allTextContents(), ['1', '1', '0'])
  report.contractChecks.push('平台总览在无可读门店时标明未读取，部分门店可读时显示已知数量并保留零值')
  await screenshot(page, 'contract-platform-overview')
  await page.getByRole('link', { name: '模块与购买', exact: true }).click()
  await page.getByRole('combobox').selectOption('shenglang')
  await page.getByText('已开通', { exact: true }).waitFor()
  await page.getByLabel('客户侧品牌名称').fill('独立 H5 定制样例')
  await page.getByLabel('首页欢迎语').fill('只作用于客户侧品牌')
  assert.equal(await page.locator('.saas-preview-screen header').innerText(), '独立 H5 定制样例\n服务中心')
  await screenshot(page, 'contract-modules-brand-preview')
  const before = await page.locator('.platform-sidebar').evaluate(element => getComputedStyle(element).getPropertyValue('--primary'))
  await page.getByLabel('品牌颜色值').fill('#DD8800')
  assert.equal(await page.locator('.platform-sidebar').evaluate(element => getComputedStyle(element).getPropertyValue('--primary')), before)
  await page.getByRole('button', { name: '核对品牌配置', exact: true }).click()
  await page.getByRole('button', { name: '确认保存品牌配置', exact: true }).click()
  await page.getByText('品牌配置已保存，以发布结果为准。', { exact: true }).waitFor()
  const write = fixture.writes.find(entry => entry.path.endsWith('/branding'))
  assert.equal(write.data.branding.display_name, '独立 H5 定制样例')
  assert.equal(write.data.expected_version, 'v1')
  assert.ok(write.data.client_request_id)
  assert.deepEqual(Object.keys(write.data.branding).sort(), Object.keys(branding).sort())
  report.contractChecks.push('模块固定/可购/定制边界、品牌预览、样式隔离、版本与幂等请求')
  await page.getByRole('link', { name: '订单与派单', exact: true }).click()
  await page.getByText('CONTRACT-ONLY-42', { exact: true }).waitFor()
  await page.getByRole('button', { name: '详情与处理' }).click()
  await page.getByRole('heading', { name: '订单流转', exact: true }).waitFor()
  await page.getByText('老板 H5 下单', { exact: false }).last().waitFor()
  await page.getByLabel('样例陪玩').check()
  await page.getByLabel('派单备注').fill('接口契约验证，不执行真实派单')
  await page.getByRole('button', { name: '确认指派', exact: true }).click()
  await page.getByRole('button', { name: '确认指派', exact: true }).waitFor()
  const assign = fixture.writes.find(entry => entry.path.endsWith('/assign'))
  assert.deepEqual(assign.data.provider_ids, ['7'])
  assert.equal(assign.data.expected_version, 'v1')
  await screenshot(page, 'contract-platform-order-detail')
  report.contractChecks.push('统一订单列表、部分门店缺失、双维来源/派单路径、同店候选指派请求')
  await page.getByRole('button', { name: '关闭', exact: true }).last().click()
  await page.getByRole('button', { name: '新建客服代单', exact: true }).click()
  await page.getByLabel('所属俱乐部').selectOption('shenglang')
  await page.getByLabel('本店老板').selectOption('9')
  await page.getByLabel('本店服务').selectOption('8')
  await page.getByRole('button', { name: '核对报价', exact: true }).click()
  await page.getByLabel('老板确认记录').fill('仅验证接口契约，不创建真实订单')
  await page.getByRole('button', { name: '确认创建客服代单', exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  const created = fixture.writes.find(entry => entry.path.endsWith('/dispatch'))
  assert.equal(created.data.order_source, 'STAFF_MANUAL')
  assert.equal(created.data.quote_token, 'contract-quote')
  assert.ok(created.data.client_request_id)
  report.contractChecks.push('平台代单先选本店老板/服务、真实报价令牌与老板确认，再发出创建契约')
  await fixture.context.close()

  const pending = await fixtureContext(browser, { failWrite: true }), pendingPage = await pending.context.newPage()
  await pendingPage.goto('http://admin.esports.club/#/platform/modules')
  await pendingPage.getByRole('combobox').selectOption('shenglang')
  await pendingPage.getByText('已开通', { exact: true }).waitFor()
  await pendingPage.getByLabel('具体需求').fill('接口契约测试：不执行真实购买')
  await pendingPage.getByRole('button', { name: '提交需求', exact: true }).click()
  await pendingPage.getByText('有一笔操作结果待核对', { exact: false }).waitFor()
  await pendingPage.reload()
  await pendingPage.getByRole('combobox').selectOption('shenglang')
  await pendingPage.getByText('有一笔操作结果待核对', { exact: false }).waitFor()
  assert.equal(await pendingPage.getByRole('button', { name: '提交需求', exact: true }).isDisabled(), true)
  report.contractChecks.push('请求失败/重载后保留待核对标记，禁止重复购买提交')
  await pending.context.close()

  const finance = await fixtureContext(browser, { profile: { ...profile, grants: [{ role: 'FINANCE', scope_slug: 'shenglang' }] } }), financePage = await finance.context.newPage()
  await financePage.goto('http://admin.esports.club/#/platform/orders')
  await financePage.getByRole('heading', { name: '暂无访问权限' }).waitFor()
  assert.equal(await financePage.getByRole('link', { name: '模块与购买' }).count(), 0)
  assert.equal(finance.calls.some(call => call.endsWith('/platform/orders')), false)
  report.contractChecks.push('平台财务不能凭财务身份读取订单或模块管理')
  await finance.context.close()
  const unavailable = await fixtureContext(browser, { profile: { ...profile, capabilities: [] } }), unavailablePage = await unavailable.context.newPage()
  await unavailablePage.goto('http://admin.esports.club/#/platform/modules')
  await unavailablePage.getByRole('combobox').selectOption('shenglang')
  await unavailablePage.getByText('模块购买与开通服务尚未开放', { exact: false }).waitFor()
  assert.equal(await unavailablePage.getByRole('button', { name: '提交需求', exact: true }).isDisabled(), true)
  assert.equal(unavailable.calls.some(call => call.includes('/saas/modules/')), false)
  report.contractChecks.push('未声明能力时不调用待接入接口，不伪造授权或购买成功')
  await unavailable.context.close()
}

async function bossBrandTests(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
  const branded = { ...store, boss_h5: { entitlement: { status: 'NOT_PURCHASED', can_create_order: false, can_customize: false }, branding: { ...branding, display_name: '独立品牌样例', primary_color: '#8A3412' } } }
  await context.addInitScript(() => localStorage.setItem('xa:store:shenglang:xa:fronted:boss:session:v1', JSON.stringify({ token: 'contract-boss', refresh: 'contract-refresh', user: { id: 'contract-boss', role: 'customer' }, sessionId: 'contract-boss' })))
  await context.route('**/api/**', route => {
    const url = new URL(route.request().url())
    if (!url.pathname.startsWith('/api/')) return route.continue()
    const detail = { ...order, id: 42, amount: 1000, original_amount: 1000, service: { name: '接口契约样例服务', is_gift: false }, required_team_size: 1, escort_mode: 'SINGLE', game_rounds: 1, allowed_actions: [], participants: [], evaluation_targets: [] }
    return route.fulfill({ json: { code: 0, data: url.pathname.endsWith('/stores/current/') ? branded : url.pathname.endsWith('/orders/orders/42/') ? detail : [] } })
  })
  await context.routeWebSocket('**/ws/**', socket => socket.close())
  const page = await context.newPage()
  page.on('pageerror', error => report.errors.push(error.message))
  await page.goto('http://sl.esports.club/boss/#/checkout')
  await page.getByRole('heading', { name: '自主下单暂未开放' }).waitFor()
  await page.getByText('独立品牌样例', { exact: true }).waitFor()
  assert.equal(await page.locator('.boss-app').evaluate(element => getComputedStyle(element).getPropertyValue('--primary')), '#8A3412')
  assert.notEqual(await page.locator('html').evaluate(element => getComputedStyle(element).getPropertyValue('--primary')), '#8A3412')
  assert.equal(await page.getByRole('link', { name: '查看我的订单', exact: true }).count(), 1)
  await screenshot(page, 'contract-boss-module-gate-390')
  await page.goto('http://sl.esports.club/boss/#/order-detail?id=42')
  await page.getByRole('heading', { name: '订单流转', exact: true }).waitFor()
  await screenshot(page, 'contract-boss-order-journey-390')
  await page.setViewportSize({ width: 1366, height: 1024 })
  await screenshot(page, 'contract-boss-order-journey-1366')
  report.contractChecks.push('H5 独立品牌仅作用客户侧；未开通阻止新下单，保留既有订单入口')
  await context.close()
}

async function providerJourneyTests(browser) {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
  await context.addInitScript(() => localStorage.setItem('xa:store:shenglang:xa:fronted:provider:session:v1', JSON.stringify({ token: 'contract-provider', refresh: 'contract-refresh', user: { id: 'contract-provider', role: 'provider' }, sessionId: 'contract-provider' })))
  await context.routeWebSocket('**/ws/**', socket => socket.close())
  await context.route('**/api/**', route => {
    const pathname = new URL(route.request().url()).pathname
    if (!pathname.startsWith('/api/')) return route.continue()
    let data = []
    if (pathname.endsWith('/stores/current/')) data = store
    else if (pathname.endsWith('/users/me/')) data = { id: 'contract-provider', role: 'provider', nickname: '接口契约样例（非真实业务）' }
    else if (pathname.endsWith('/users/provider-stats/')) data = { escort_status: 'AVAILABLE', rating_avg: 0, rating_count: 0, today_orders: 0, serving: 0, completion_rate: 0, total_income: 0, active_orders: 0, max_concurrent_orders: 1 }
    else if (pathname.endsWith('/users/escorts/me/')) data = { is_verified: true }
    else if (pathname.endsWith('/orders/reservations/')) data = { items: [] }
    else if (pathname.endsWith('/orders/orders/42/')) data = { ...order, id: 42, status: 'GRABBED', amount: 1000, expected_income: 600, service_name_snapshot: '接口契约样例服务', service: { name: '接口契约样例服务' }, customer: { nickname: '接口契约样例老板' }, required_team_size: 1, allowed_actions: [], game_rounds: 1, participants: [], assignments: [] }
    return route.fulfill({ json: { code: 0, data } })
  })
  const page = await context.newPage()
  page.on('pageerror', error => report.errors.push(error.message))
  await page.goto('http://sl.esports.club/player/#/orders?order=42')
  await page.locator('.provider-order-detail-trigger').first().click()
  await page.getByRole('heading', { name: '订单流转', exact: true }).waitFor()
  await screenshot(page, 'contract-provider-order-journey-390')
  await page.setViewportSize({ width: 1366, height: 1024 })
  await screenshot(page, 'contract-provider-order-journey-1366')
  report.contractChecks.push('老板/陪玩订单详情在390与1366视口显示统一状态摘要，无页面横向溢出')
  await context.close()
}

async function realSmoke(browser) {
  for (const [app, url, viewport] of [
    ['admin', 'http://admin.esports.club/#/workspace', { width: 1366, height: 1024 }],
    ['boss', 'http://sl.esports.club/boss/#/orders', { width: 390, height: 844 }],
    ['provider', 'http://sl.esports.club/player/#/orders', { width: 390, height: 844 }],
  ]) {
    const state = `.tools/fronted-acceptance/ui-regression-${app}-storage-state.json`
    const context = await browser.newContext({ viewport, ...(fs.existsSync(state) ? { storageState: state } : {}) })
    if (app === 'admin' && fs.existsSync('.tools/fronted-acceptance/ui-regression-admin-context.json')) {
      const { activeStoreSlug } = JSON.parse(fs.readFileSync('.tools/fronted-acceptance/ui-regression-admin-context.json', 'utf8'))
      await context.addInitScript(slug => sessionStorage.setItem('xa:fronted:admin:active-store:v1', slug), activeStoreSlug)
    }
    await context.route('**/api/**', route => {
      const request = route.request()
      if (request.method() !== 'GET' && !/^\/api\/(?:admin\/auth\/refresh|users\/refresh\/)\/?$/.test(new URL(request.url()).pathname)) return route.abort()
      return route.continue()
    })
    const page = await context.newPage(), errors = [], apiFailures = []
    page.on('pageerror', error => errors.push(error.message))
    page.on('response', response => { const pathname = new URL(response.url()).pathname; if (pathname.startsWith('/api/') && response.status() >= 500) apiFailures.push({ path: pathname, status: response.status() }) })
    await page.goto(url)
    await page.getByRole('heading', { name: { admin: '我的工作台', boss: '我的订单', provider: '接单工作台' }[app], exact: true }).waitFor({ timeout: 25000 })
    const body = await page.locator('body').innerText()
    await screenshot(page, `real-${app}-${viewport.width}`)
    assert.ok(body.includes({ admin: '当前可用任务', boss: '查看接单', provider: '本人接单与履约' }[app]), `${app}业务页面未加载`)
    if (app !== 'admin') {
      await page.setViewportSize({ width: 1366, height: 1024 })
      await screenshot(page, `real-${app}-1366`)
    }
    if (app === 'boss') {
      const link = page.getByRole('link', { name: '查看详情', exact: false }).first()
      if (await link.count()) { await link.click(); await page.getByRole('heading', { name: '订单流转', exact: true }).waitFor(); await page.setViewportSize({ width: 390, height: 844 }); await screenshot(page, 'real-boss-order-detail-390') }
    }
    if (app === 'admin') {
      await page.goto('http://admin.esports.club/#/orders')
      await Promise.race([page.locator('tbody button.font-mono').first().waitFor(), page.getByRole('heading', { name: '暂时无法读取数据', exact: true }).waitFor()])
      if (await page.locator('tbody button.font-mono').count()) { await page.locator('tbody button.font-mono').first().click(); await page.getByRole('heading', { name: '订单流转', exact: true }).waitFor(); await screenshot(page, 'real-admin-order-detail-1366') }
      else { assert.ok(await page.getByRole('button', { name: '重新加载', exact: true }).count()); await screenshot(page, 'real-admin-order-error-1366') }
    }
    if (app === 'provider') {
      if (await page.locator('.provider-order-detail-trigger').count()) { await page.locator('.provider-order-detail-trigger').first().click(); await page.getByRole('heading', { name: '订单流转', exact: true }).waitFor(); await page.setViewportSize({ width: 390, height: 844 }); await screenshot(page, 'real-provider-order-detail-390') }
    }
    report.realReadOnlyChecks.push({ app, viewport, authenticated: true, status: apiFailures.length ? '业务页面与错误反馈已验证，订单数据受后端500阻塞' : '业务页面已读取', apiFailures, errors })
    await context.close()
  }
}
;(async () => {
  const browser = await chromium.launch({ headless: true })
  try { await contractTests(browser); await bossBrandTests(browser); await providerJourneyTests(browser); await realSmoke(browser); assert.deepEqual(report.errors, []) }
  finally { await browser.close(); fs.writeFileSync(path.join(out, 'ui-results.json'), JSON.stringify(report, null, 2)) }
  console.log(JSON.stringify(report, null, 2))
})().catch(error => { console.error(error.stack); process.exitCode = 1 })
