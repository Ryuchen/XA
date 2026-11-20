const assert = require('node:assert/strict')

// Only the browser sees these store responses. No store state is changed.
module.exports = async function checkStoreClosed(browser, origin = 'http://sl.esports.club') {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 } })
  try {
    const page = await context.newPage()
    const errors = [], businessCalls = []
    page.on('pageerror', error => errors.push(error.message))
    let response = { status: 403, json: { code: 403, msg: '门店尚未开放或已停用，请联系门店管理员' } }
    let hold, reads = 0
    await page.route('**/api/**', async route => {
      const request = route.request()
      if (request.method() !== 'GET' || new URL(request.url()).pathname !== '/api/stores/current/') {
        businessCalls.push(request.method() + ' ' + new URL(request.url()).pathname)
        return route.abort()
      }
      reads++
      if (hold) await hold.promise
      return route.fulfill(response)
    })
    const url = origin + '/boss/#/login'
    await page.goto(url)
    const closed = page.getByRole('heading', { name: '门店暂未开放', exact: true })
    await closed.waitFor()
    const retry = page.getByRole('button', { name: '重新检查状态', exact: true })
    const before = reads
    hold = Promise.withResolvers()
    await retry.focus()
    await page.keyboard.press('Enter')
    const pending = page.getByRole('button', { name: '正在检查…', exact: true })
    await pending.waitFor()
    assert.equal(await pending.getAttribute('aria-disabled'), 'true')
    await page.keyboard.press('Enter')
    response = { status: 503, json: { code: 503, msg: '门店服务暂不可用，请稍后重试' } }
    hold.resolve()
    await page.getByRole('alert').filter({ hasText: response.json.msg }).waitFor()
    assert.equal(reads, before + 1)
    assert.equal(await closed.count(), 0)

    response = { status: 403, json: { code: 403, msg: '门店数据库尚未配置，请联系平台管理员' } }
    await page.getByRole('button', { name: '重试', exact: true }).click()
    await page.getByRole('alert').filter({ hasText: response.json.msg }).waitFor()
    assert.equal(await closed.count(), 0)
    assert.deepEqual(businessCalls, [])
    response = { status: 200, json: { code: 0, data: {
      id: 1, slug: 'shenglang', name: '契约验证门店', currency_name: '浪花',
      status: 'ACTIVE', is_active: true, boss_url: '/boss/', provider_url: '/player/', admin_url: '/',
    } } }
    await page.getByRole('button', { name: '重试', exact: true }).click()
    await page.getByRole('heading', { name: '老板账号登录', exact: true }).waitFor()
    assert.equal(page.url(), url)
    assert.deepEqual(errors, [])
  } finally {
    await context.close()
  }
}
