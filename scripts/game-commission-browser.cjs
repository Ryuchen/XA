// Real admin Vite screen and isolated ASGI, using only synthetic test data.
const fs = require('node:fs')
const assert = require('node:assert/strict')
const { chromium } = require('../.tools/club-source-20261003/node_modules/playwright')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
;(async () => {
  const browser = await chromium.launch({ executablePath: 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    headless: true, args: ['--no-proxy-server'] })
  try {
    const context = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    await context.addInitScript(identity => {
      sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
      localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({
        token: identity.token, user: { id: identity.id, role: 'staff', permissions: identity.permissions },
        sessionId: 'game-commission-browser',
      }))
    }, input.staff)
    const page = await context.newPage(), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({ url: input.api + target.pathname + target.search,
        headers: { ...route.request().headers(), host: 'admin.esports.club' } })
      await route.fulfill({ response })
    })
    await page.goto('http://127.0.0.1:14103/#/dictionaries')
    await page.getByRole('button', { name: '游戏抽成草稿' }).click()
    const directory = page.getByRole('dialog').first()
    await directory.getByText('当前草稿尚未用于订单计算。', { exact: false }).waitFor()
    await directory.getByText('界面隔离游戏', { exact: true }).locator('../..').getByRole('button', { name: '查看或填写' }).click()
    let editor = page.getByRole('dialog').last()
    await editor.getByLabel('游戏分类').click()
    await page.getByRole('option', { name: '瓦罗兰特', exact: true }).click()
    await editor.getByLabel('陪玩抽成（%）').fill('0')
    await editor.getByLabel('代肝及上分抽成（%）').fill('25')
    await editor.getByRole('button', { name: '保存草稿' }).click()
    await page.getByText('抽成草稿已保存，尚未生效').waitFor()
    await directory.getByText('瓦罗兰特 · 草稿').waitFor()
    await directory.getByText('界面隔离游戏', { exact: true }).locator('../..').getByRole('button', { name: '查看或填写' }).click()
    editor = page.getByRole('dialog').last()
    assert.equal(await editor.getByLabel('陪玩抽成（%）').inputValue(), '0')
    assert.equal(await editor.getByLabel('代肝及上分抽成（%）').inputValue(), '25')
    const concurrent = await page.evaluate(async ({ gameId, token }) => {
      const response = await fetch(`/api/admin/config/game-commissions/${gameId}/`, {
        method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
        body: JSON.stringify({ game: 'VALORANT', version: 1, rules: { companion: 0, grind: 35 } }),
      })
      return response.status
    }, { gameId: input.game_id, token: input.staff.token })
    assert.equal(concurrent, 200)
    await editor.getByLabel('代肝及上分抽成（%）').fill('40')
    await editor.getByRole('button', { name: '保存草稿' }).click()
    await editor.getByText('保存结果或版本需要核对，请重新读取后再操作。').waitFor()
    assert.equal(await editor.getByRole('button', { name: '保存草稿' }).isDisabled(), true)
    page.once('dialog', dialog => dialog.accept())
    await editor.getByRole('button', { name: '重新读取草稿' }).click()
    await editor.getByLabel('代肝及上分抽成（%）').waitFor()
    await page.waitForFunction(() => document.querySelector('#field-commission-grind')?.value === '35')
    await editor.getByRole('button', { name: '返回', exact: true }).click()
    await page.getByRole('button', { name: '放弃并关闭' }).click()
    await directory.getByText('界面三角洲测试', { exact: true }).locator('../..').getByRole('button', { name: '查看或填写' }).click()
    editor = page.getByRole('dialog').last()
    await editor.getByLabel('游戏分类').click()
    await page.getByRole('option', { name: '三角洲行动', exact: true }).click()
    await editor.getByRole('button', { name: '添加范围' }).click()
    for (const [label, value] of [['服务方向', '护航'], ['端', '端游'], ['难度', '绝密'], ['档位', '大师']]) {
      await editor.getByLabel(label, { exact: true }).click()
      await page.getByRole('option', { name: value, exact: true }).click()
    }
    await editor.getByLabel('抽成（%）', { exact: true }).fill('22')
    await editor.getByRole('button', { name: '保存草稿' }).click()
    await directory.getByText('三角洲行动 · 草稿').waitFor()
    await directory.getByRole('button', { name: '核对服务覆盖' }).click()
    const coverage = page.getByRole('dialog').last()
    await coverage.getByText('启用服务 4 · 匹配草稿 2 · 待处理 1 · 特殊规则 1').waitFor()
    await coverage.getByText('界面陪玩服务').waitFor()
    await coverage.getByText('门店抽成 0% · 草稿版本 2').waitFor()
    await coverage.getByText('界面三角洲护航').waitFor()
    await coverage.getByLabel('核对结果').click()
    await page.getByRole('option', { name: '待处理', exact: true }).click()
    await coverage.getByText('界面未映射方向').waitFor()
    assert.equal(await coverage.getByText('界面陪玩服务').count(), 0)
    await coverage.getByLabel('游戏类目').click()
    await page.getByRole('option', { name: '界面隔离游戏', exact: true }).click()
    await coverage.getByText('启用服务 2 · 匹配草稿 1 · 待处理 1 · 特殊规则 0').waitFor()
    await page.keyboard.press('Escape')
    const viewer = await browser.newContext({ viewport: { width: 1366, height: 1024 } })
    await viewer.addInitScript(identity => {
      sessionStorage.setItem('xa:fronted:admin:active-store:v1', 'shenglang')
      localStorage.setItem('xa:store:shenglang:xa:fronted:admin:session:v1', JSON.stringify({
        token: identity.token, user: { id: identity.id, role: 'staff', permissions: identity.permissions }, sessionId: 'game-reader',
      }))
    }, input.reader)
    const viewPage = await viewer.newPage()
    viewPage.on('pageerror', error => errors.push(error.message))
    await viewPage.route(url => url.pathname.startsWith('/api/'), async route => {
      const target = new URL(route.request().url())
      const response = await route.fetch({ url: input.api + target.pathname + target.search,
        headers: { ...route.request().headers(), host: 'admin.esports.club' } })
      await route.fulfill({ response })
    })
    await viewPage.goto('http://127.0.0.1:14103/#/dictionaries')
    await viewPage.getByRole('button', { name: '游戏抽成草稿' }).click()
    const viewDirectory = viewPage.getByRole('dialog').first()
    await viewDirectory.getByText('界面隔离游戏', { exact: true }).locator('../..').getByRole('button', { name: '查看或填写' }).click()
    const viewEditor = viewPage.getByRole('dialog').last()
    await viewEditor.getByLabel('陪玩抽成（%）').waitFor()
    assert.equal(await viewEditor.getByLabel('陪玩抽成（%）').isDisabled(), true)
    assert.equal(await viewEditor.getByRole('button', { name: '保存草稿' }).isDisabled(), true)
    await viewer.close()
    assert.deepEqual(errors, [])
    process.stdout.write('game commission draft browser passed\n')
  } finally { await browser.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
