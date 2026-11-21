const assert = require('node:assert/strict')
const { chromium } = require('playwright')

const origin = process.env.XA_ADMIN_WEB || 'http://127.0.0.1:14103'
const host = new URL(origin).hostname
assert.ok(['127.0.0.1', 'localhost'].includes(host), 'Use the registered local admin frontend')
const actor = { id: 7, username: 'fixture-operator', role: 'admin', permissions: ['order:view', 'order:refund'] }
const order = { id: 42, order_no: 'UI-REFUND-42', service_name: '隔离验证订单', status: 'IN_SERVICE', payment_status: 'PAID', amount: 480 }
const ticket = { id: 19, status: 'OPEN', affected_provider_account_ids: [8], affected_provider_names: ['隔离陪玩'], reason: '隔离协商工单' }
const execution = '/api/admin/aftersales/19/negotiation-execution/'
const funding = '/api/admin/aftersales/19/negotiation-funding/'
const intentKey = 'xa:negotiation-refund:shenglang:7:19'

async function exercise(browser) {
  const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
  const page = await context.newPage()
  const paths = []
  try {
    await context.addInitScript(user => {
      sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
      localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({
        token: 'ui-fixture-only', user, sessionId: 'refund-fixture',
      }))
    }, actor)
    page.setDefaultTimeout(8000)
    await page.routeWebSocket('**/*', socket => socket.close())
    const errors = [], mutations = [], queries = []
    page.on('pageerror', error => errors.push(error.message))
    let amount = 300, points = true, estimate, held, lost = false, receipt = null, fundingReads = 0

    function estimateFor(overrides = {}, msg) {
      return { code: 0, msg, data: {
        refund_units: amount,
        book_amounts_sufficient: true,
        executable: false,
        providers: [{ account_id: 8, pending_income_units: 0, wallet_units: 0, deposit_units: 0, shortfall_units: 0 }],
        ...overrides,
      } }
    }

    await context.route(url => url.pathname.startsWith('/api/'), async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname
      paths.push(path)
      if (req.method() !== 'GET') {
        assert.equal(path, execution, 'Only the simulated refund can be submitted')
        const body = req.postDataJSON()
        mutations.push(body)
        receipt = { status: 'COMPLETED', execution_id: 31, refund_units: amount === 0 ? 0 : 240 }
        if (lost) return route.abort('failed')
        return route.fulfill({ json: { code: 0, data: receipt } })
      }
      if (path === execution) {
        if (url.searchParams.has('client_request_id')) {
          queries.push(Object.fromEntries(url.searchParams))
          return route.fulfill({ json: { code: 0, data: receipt ?? { status: 'NOT_FOUND' } } })
        }
        return route.fulfill({ json: { code: 0, data: {
          proposal: { id: 26, version: 1, refund_units: amount, status: 'ACCEPTED', provider_terms: [],
            explanation: '隔离验证确认框', ...(points && { refund_coin_units: 240, refund_points_hundredths: 200 }) },
          execution_enabled: true,
          zero_execution_enabled: true,
        } } })
      }
      if (path === funding) {
        fundingReads++
        assert.equal(url.searchParams.get('refund_units'), String(amount))
        if (held) await held.promise
        return route.fulfill(estimate)
      }
      const data = {
        '/api/stores/current/': { id: 1, slug: 'shenglang', name: '隔离验证门店', currency_name: '浪花', status: 'ACTIVE' },
        '/api/admin/auth/profile': actor,
        '/api/admin/orders/': { list: [order], total: 1, page: 1, page_size: 20 },
        '/api/admin/orders/42/': order,
        '/api/admin/orders/42/logs/': [],
        '/api/admin/aftersales/': { list: [ticket], total: 1 },
      }[path]
      if (data === undefined) return route.fulfill({ status: 404, json: { code: 404, msg: '隔离用例未配置该接口' } })
      return route.fulfill({ json: { code: 0, data } })
    })

    const visit = () => page.goto(origin + '/#/orders?detail=42')
    const open = async (name = '执行协商退款') => {
      await page.getByRole('button', { name, exact: true }).click()
      const dialog = page.getByRole('dialog').last()
      await dialog.getByRole('checkbox', { name: /已核对当事人/ }).waitFor()
      return dialog
    }
    async function review(dialog) {
      const checkbox = dialog.getByRole('checkbox', { name: /已核对当事人/ })
      await checkbox.focus()
      await page.keyboard.press('Space')
      assert.equal(await checkbox.isChecked(), true)
    }
    const confirm = (dialog, name = '确认退款') => dialog.getByRole('button', { name, exact: true })
    async function close(dialog) {
      await dialog.getByRole('button', { name: '返回', exact: true }).click()
    }
    async function retry(dialog, reply) {
      estimate = reply
      await Promise.all([
        page.waitForResponse(response => new URL(response.url()).pathname === funding),
        dialog.getByRole('button', { name: '重试', exact: true }).click(),
      ])
    }

    const depositReason = '本次需要扣减押金，当前押金扣款或后续补差尚未启用'
    estimate = { json: estimateFor({ execution_check_required: true, executable: false }, depositReason) }
    held = Promise.withResolvers()
    await visit()
    let dialog = await open()
    await dialog.getByText('退回 24 浪花、2 积分', { exact: true }).waitFor()
    await dialog.getByText('正在核对资金来源…', { exact: true }).waitFor()
    await review(dialog)
    assert.equal(await confirm(dialog).isDisabled(), true)
    held.resolve()
    held = null
    await dialog.getByText('缺口', { exact: false }).waitFor()
    assert.equal(await confirm(dialog).isDisabled(), true, 'The marked preview blocks a new refund even with sufficient book funds')
    await dialog.getByRole('status').filter({ hasText: depositReason }).waitFor()
    assert.equal(await page.evaluate(key => localStorage.getItem(key), intentKey), null)
    assert.equal(mutations.length, 0)
    await close(dialog)

    estimate = { status: 503, json: { code: 503, msg: '资金接口暂时无法核对' } }
    await page.reload()
    dialog = await open()
    await dialog.getByRole('alert').filter({ hasText: '资金接口暂时无法核对' }).waitFor()
    assert.equal(await confirm(dialog).isDisabled(), true)
    await retry(dialog, { json: { code: 0, data: null } })
    await dialog.getByRole('alert').filter({ hasText: '当前方案的资金核对结果不可用' }).waitFor()
    assert.equal(await confirm(dialog).isDisabled(), true)
    await retry(dialog, { json: estimateFor({ refund_units: 280, execution_check_required: true, executable: true }) })
    await dialog.getByRole('alert').filter({ hasText: '当前方案的资金核对结果不可用' }).waitFor()
    assert.equal(await confirm(dialog).isDisabled(), true)
    for (const invalid of [{ execution_check_required: null }, { execution_check_required: true, providers: [null] }]) {
      await retry(dialog, { json: estimateFor(invalid) })
      await dialog.getByRole('alert').filter({ hasText: '当前方案的资金核对结果不可用' }).waitFor()
      assert.equal(await confirm(dialog).isDisabled(), true)
    }
    await retry(dialog, { json: estimateFor({ book_amounts_sufficient: false, execution_check_required: true }) })
    await dialog.getByRole('status').filter({ hasText: '账面金额不足，补足后才能退款。' }).waitFor()
    assert.equal(await confirm(dialog).isDisabled(), true)
    assert.equal(mutations.length, 0)
    await close(dialog)

    estimate = { json: estimateFor({ execution_check_required: true, executable: true }) }
    lost = true
    await page.reload()
    dialog = await open()
    await dialog.getByRole('status').filter({ hasText: '账面金额可覆盖' }).waitFor()
    await review(dialog)
    await confirm(dialog).click()
    await dialog.getByRole('button', { name: '查询并重试原请求', exact: true }).waitFor()
    assert.equal(mutations.length, 1)
    const original = mutations[0]
    assert.equal(original.proposal_id, 26)
    assert.ok(original.client_request_id)
    await close(dialog)
    const priorReads = fundingReads
    await page.reload()
    dialog = await open('核对协商处理')
    await review(dialog)
    await confirm(dialog, '查询并重试原请求').click()
    await page.getByText('协商退款已完成', { exact: true }).waitFor()
    assert.equal(mutations.length, 1)
    assert.equal(fundingReads, priorReads)
    assert.deepEqual(queries[0], { proposal_id: String(original.proposal_id), client_request_id: original.client_request_id })
    assert.equal(await page.evaluate(key => localStorage.getItem(key), intentKey), null)

    const pending = { proposal_id: 26, client_request_id: 'fixture-absent-receipt' }
    await page.evaluate(([key, original]) => localStorage.setItem(key, JSON.stringify(original)), [intentKey, pending])
    receipt = null
    lost = false
    await page.reload()
    dialog = await open('核对协商处理')
    await review(dialog)
    await confirm(dialog, '查询并重试原请求').click()
    await dialog.getByRole('checkbox', { name: /已核对当事人/ }).waitFor({ state: 'hidden' })
    assert.equal(fundingReads, priorReads)
    assert.deepEqual(queries[1], { proposal_id: String(pending.proposal_id), client_request_id: pending.client_request_id })
    assert.deepEqual(mutations[1], pending)

    for (const legacyAmount of [300, 0]) {
      amount = legacyAmount
      points = false
      estimate = { json: estimateFor() }
      await page.reload()
      dialog = await open(legacyAmount ? '执行协商退款' : '执行协商终止')
      await dialog.getByRole('status').filter({ hasText: '账面金额可覆盖' }).waitFor()
      await review(dialog)
      assert.equal(await confirm(dialog, legacyAmount ? '确认退款' : '确认终止').isEnabled(), true,
        'An unmarked preview retains the ordinary or zero-refund behavior')
      await close(dialog)
    }
    assert.equal(mutations.length, 2)
    assert.deepEqual(errors, [])
    console.log('协商退款弹窗验收通过，已覆盖四种资金读态、原号查单、相同编号重试和普通及零元兼容。')
  } catch (error) {
    console.error(page.url(), [...new Set(paths)], (await page.locator('body').innerText()).slice(0, 1400))
    throw error
  } finally {
    await context.unrouteAll({ behavior: 'ignoreErrors' })
    await context.close()
  }
}

;(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.XA_BROWSER_CHANNEL })
  try { await exercise(browser) }
  finally { await browser.close() }
})().catch(error => { console.error(error.stack); process.exitCode = 1 })
