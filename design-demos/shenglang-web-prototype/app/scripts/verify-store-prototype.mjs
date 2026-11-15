import { spawn, spawnSync } from 'node:child_process'
import { mkdir, writeFile } from 'node:fs/promises'
import path from 'node:path'
import process from 'node:process'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const output = path.join(root, 'verification')
const base = 'http://127.0.0.1:4179/'
const vite = path.join(root, 'node_modules', 'vite', 'bin', 'vite.js')
const server = spawn(process.execPath, [vite, '--host', '127.0.0.1', '--port', '4179', '--strictPort'], { cwd: root, stdio: ['ignore', 'pipe', 'pipe'] })
let serverLog = ''
server.stdout.on('data', chunk => { serverLog += chunk.toString() })
server.stderr.on('data', chunk => { serverLog += chunk.toString() })
let browser
const results = { routes: {}, interactions: [], consoleErrors: [], pageErrors: [], screenshots: [], overflow: [] }

function assert(condition, message) {
  if (!condition) throw new Error(message)
}

async function stopServer() {
  if (server.killed || server.exitCode !== null) return
  if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(server.pid), '/t', '/f'], { stdio: 'ignore' })
  else server.kill('SIGTERM')
  await Promise.race([new Promise(resolve => server.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 3000))])
}

try {
  await mkdir(output, { recursive: true })
  for (let attempt = 0; attempt < 60; attempt++) {
    try {
      const response = await fetch(base)
      if (response.ok) break
    } catch { /* Vite is still starting. */ }
    if (attempt === 59) throw new Error(`Vite did not start:\n${serverLog}`)
    await new Promise(resolve => setTimeout(resolve, 250))
  }

  browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
  page.on('pageerror', error => results.pageErrors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') results.consoleErrors.push(message.text()) })

  function previewTarget(hash) {
    const productLabel = hash.startsWith('/admin') ? '管理后台' : hash.startsWith('/provider') ? '陪玩工作台' : '老板端 H5'
    return page.frameLocator(`iframe[title^="${productLabel}"]`)
  }

  async function open(hash, store = 'shenglang') {
    await page.goto(`${base}?store=${store}#${hash}`, { waitUntil: 'networkidle' })
    await previewTarget(hash).locator(`[data-store-scope="${store}"]`).waitFor({ state: 'attached', timeout: 5000 })
  }

  async function hashTo(hash) {
    await page.evaluate(next => { window.location.hash = next }, hash)
    const pageKey = hash.startsWith('/admin-') ? hash.slice('/admin-'.length) : hash.split('?')[0]
    await previewTarget(hash).locator(`[data-store-page="${pageKey}"]`).waitFor({ state: 'attached', timeout: 5000 })
  }

  async function textIncludes(text) {
    const hash = new URL(page.url()).hash.slice(1)
    const body = await previewTarget(hash).locator('body').innerText()
    assert(body.includes(text), `Expected ${hash} to include: ${text}`)
  }

  async function screenshot(name, fullPage = true) {
    const target = path.join(output, name)
    await page.screenshot({ path: target, fullPage })
    results.screenshots.push(path.relative(root, target))
  }

  const manualRefreshLabels = ['刷新快照', '刷新记录', '刷新数据', '刷新订单', '刷新钱包', '刷新陪玩订单', '刷新俱乐部动态', '刷新档案', '刷新申请', '刷新预约', '刷新']

  async function assertNoManualRefresh(target, label) {
    const matches = await target.locator('button, [role="button"]').evaluateAll((elements, labels) => elements.flatMap(element => {
      const name = element.getAttribute('aria-label')?.trim() || element.textContent?.trim() || ''
      return labels.includes(name) ? [name] : []
    }), manualRefreshLabels)
    assert(matches.length === 0, `${label} still contains a manual refresh entry: ${matches.join(', ')}`)
  }

  async function checkOverflow(label) {
    const size = await page.evaluate(() => ({ viewport: window.innerWidth, document: document.documentElement.scrollWidth }))
    results.overflow.push({ label, ...size })
    assert(size.document <= size.viewport + 1, `${label} has horizontal page overflow: ${size.document} > ${size.viewport}`)
  }

  async function checkProviderOverflow(label, publicPage = false) {
    const selector = publicPage ? '[data-testid="provider-public-scroll"]' : '[data-testid="provider-phone-scroll"]'
    const size = await provider.locator(selector).evaluate(element => ({ viewport: element.clientWidth, document: element.scrollWidth }))
    results.overflow.push({ label, ...size })
    assert(size.document <= size.viewport + 1, `${label} has horizontal content overflow: ${size.document} > ${size.viewport}`)
  }

  const bossPages = ['login', 'home', 'companions', 'companion-picker', 'companion-detail', 'catalog', 'service-detail', 'checkout', 'orders', 'order-detail', 'wallet', 'gifts', 'mine', 'messages', 'message-detail', 'chat', 'support', 'security', 'profile', 'reservations', 'reservation-detail', 'reservation-create', 'coupons', 'membership', 'points', 'lottery', 'reward-claim', 'favorites', 'checkin', 'achievements', 'rankings', 'announcements', 'announcement-detail', 'club-content', 'club-content-detail', 'activities', 'activity-detail', 'rights', 'rights-detail', 'point-rules', 'point-rules-detail', 'audition']
  const providerPages = ['/provider-login', '/provider-register', '/provider', '/provider/applications', '/provider/applications/105?application=9001&from=history', '/provider/reports', '/provider/reservations', '/provider/messages', '/provider/support-chat?order_no=SHENGLANG202611040105', '/provider/wallet', '/provider/withdraw-accounts', '/provider/mine', '/provider/profile', '/provider/security', '/provider/skills', '/provider/evaluations', '/provider/pass', '/provider/points', '/provider/support', '/provider/auditions', '/provider/audition?token=SL-AUDITION-202611&role=provider']
  const adminPages = ['login', 'stores', 'dashboard', 'orders', 'order-detail', 'reservations', 'chat', 'reports', 'commission', 'services', 'gameplays', 'banners', 'announcements', 'contents', 'broadcast-rules', 'broadcast-records', 'rewards', 'lotteries', 'claims', 'operation-config', 'bosses', 'providers', 'provider-detail', 'registrations', 'registration-detail', 'levels', 'passes', 'disposes', 'auditions', 'audition-signups', 'withdraw', 'wallets', 'provider-wallets', 'transactions', 'recharges', 'payment-fee']
  const adminSecondaryGroups = {
    orders: ['fulfillment', 4], 'order-detail': ['fulfillment', 4], reservations: ['fulfillment', 4], chat: ['fulfillment', 4], reports: ['fulfillment', 4],
    bosses: ['bosses', 1],
    providers: ['providers', 7], 'provider-detail': ['providers', 7], registrations: ['providers', 7], 'registration-detail': ['providers', 7], auditions: ['providers', 7], 'audition-signups': ['providers', 7], levels: ['providers', 7], passes: ['providers', 7], disposes: ['providers', 7],
    wallets: ['finance', 5], 'provider-wallets': ['finance', 5], withdraw: ['finance', 5], recharges: ['finance', 5], transactions: ['finance', 5],
    services: ['catalog', 2], gameplays: ['catalog', 2],
    banners: ['content', 5], announcements: ['content', 5], contents: ['content', 5], 'broadcast-rules': ['content', 5], 'broadcast-records': ['content', 5],
    rewards: ['marketing', 3], lotteries: ['marketing', 3], claims: ['marketing', 3],
    'operation-config': ['settings', 3], commission: ['settings', 3], 'payment-fee': ['settings', 3],
  }

  await open('/home')
  const boss = previewTarget('/home')
  results.routes.boss = []
  for (const key of bossPages) {
    await hashTo(`/${key}`)
    await boss.locator(`[data-store-page="/${key}"]`).waitFor({ state: 'attached' })
    assert(await page.locator(`[data-store-design-panel="${key}"]`).count() === 1, `Missing boss design panel: ${key}`)
    assert(await boss.locator('button[aria-label$="本页数据隔离说明"]').count() === 0, `Boss scope description is still visible: ${key}`)
    assert(await boss.locator('header div.ml-auto > span.rounded-full').count() === 0, `Boss top avatar is still visible: ${key}`)
    await assertNoManualRefresh(boss, `Boss ${key}`)
    if (key === 'orders') await screenshot('boss-orders-no-manual-refresh-pc.png', false)
    results.routes.boss.push(key)
  }
  const bossPcSize = await page.locator('iframe[title="老板端 H5PC 端预览"]').evaluate(frame => ({ logicalWidth: frame.getAttribute('data-logical-width'), clientWidth: frame.clientWidth }))
  assert(bossPcSize.logicalWidth === '1440' && bossPcSize.clientWidth === 1440, `Boss PC viewport must be 1440px: ${JSON.stringify(bossPcSize)}`)

  await open('/provider')
  const provider = previewTarget('/provider')
  results.routes.provider = []
  for (const key of providerPages) {
    const routeKey = key.split('?')[0]
    await hashTo(key)
    await provider.locator(`[data-store-page="${routeKey}"]`).waitFor({ state: 'attached' })
    assert(await page.locator(`[data-store-design-panel="${routeKey}"]`).count() === 1, `Missing provider design panel: ${routeKey}`)
    assert(await provider.locator('button[aria-label$="本页数据隔离说明"]').count() === 0, `Provider scope description is still visible: ${key}`)
    assert(await provider.locator('header div.ml-auto > span.rounded-full').count() === 0, `Provider top avatar is still visible: ${key}`)
    await assertNoManualRefresh(provider, `Provider ${key}`)
    if (key === '/provider') await screenshot('provider-workbench-no-manual-refresh-pc.png', false)
    results.routes.provider.push(key)
  }
  const providerPcSize = await page.locator('iframe[title="陪玩工作台PC 端预览"]').evaluate(frame => ({ logicalWidth: frame.getAttribute('data-logical-width'), clientWidth: frame.clientWidth }))
  assert(providerPcSize.logicalWidth === '1440' && providerPcSize.clientWidth === 1440, `Provider PC viewport must be 1440px: ${JSON.stringify(providerPcSize)}`)

  async function checkProviderDialog(label) {
    const dialog = page.getByRole('dialog')
    await dialog.waitFor({ state: 'visible' })
    await dialog.evaluate(element => Promise.all(element.getAnimations({ subtree: true }).map(animation => animation.finished.catch(() => undefined))))
    const heading = await dialog.locator('[data-layout="provider-dialog-heading"]').evaluate(element => {
      const icon = element.children[0]?.getBoundingClientRect()
      const copy = element.children[1]?.getBoundingClientRect()
      const title = element.querySelector('[data-slot="dialog-title"]')?.getBoundingClientRect()
      const description = element.querySelector('[data-slot="dialog-description"]')?.getBoundingClientRect()
      return {
        direction: getComputedStyle(element).flexDirection,
        iconBeforeCopy: Boolean(icon && copy && icon.right <= copy.left + 1),
        descriptionBelowTitle: Boolean(title && description && title.bottom <= description.top + 1),
      }
    })
    assert(heading.direction === 'row' && heading.iconBeforeCopy && heading.descriptionBelowTitle, `${label} heading layout is incorrect: ${JSON.stringify(heading)}`)
    const buttons = await dialog.locator('[data-slot="dialog-footer"] button').evaluateAll(elements => elements.map(element => {
      const style = getComputedStyle(element)
      return { height: element.getBoundingClientRect().height, radius: Number.parseFloat(style.borderTopLeftRadius) }
    }))
    assert(buttons.length > 0 && buttons.every(button => Math.round(button.height) === 40 && Math.round(button.radius) === 8), `${label} footer buttons must be medium with 8px corners: ${JSON.stringify(buttons)}`)
    const size = await dialog.evaluate(element => {
      const rect = element.getBoundingClientRect()
      return { clientWidth: element.clientWidth, scrollWidth: element.scrollWidth, left: rect.left, right: rect.right, bottom: rect.bottom, viewportWidth: element.ownerDocument.defaultView?.innerWidth || 0, viewportHeight: element.ownerDocument.defaultView?.innerHeight || 0 }
    })
    assert(size.scrollWidth <= size.clientWidth + 1 && size.left >= -1 && size.right <= size.viewportWidth + 1 && size.bottom <= size.viewportHeight + 1, `${label} dialog overflows its viewport: ${JSON.stringify(size)}`)
    results.overflow.push({ label, viewport: size.clientWidth, document: size.scrollWidth })
  }

  async function openProviderOrderDetail(label, image) {
    await hashTo('/provider')
    const firstCard = provider.locator('section[aria-label="陪玩订单列表"] article').first()
    assert(await firstCard.getByRole('button', { name: '不接此单', exact: true }).count() === 1, `${label} is missing the 不接此单 action`)
    assert(!(await firstCard.innerText()).includes('暂不接单'), `${label} still contains the previous reject label`)
    const cardButtons = await firstCard.locator('div.border-t').last().locator('button').evaluateAll(elements => elements.map(element => {
      const style = getComputedStyle(element)
      return { height: element.getBoundingClientRect().height, radius: Number.parseFloat(style.borderTopLeftRadius) }
    }))
    assert(cardButtons.length === 4 && cardButtons.every(button => Math.round(button.height) === 32 && Math.round(button.radius) === 6), `${label} card buttons must be small with 6px corners: ${JSON.stringify(cardButtons)}`)
    await firstCard.getByRole('button', { name: '订单详情', exact: true }).click()
    const poster = page.getByTestId('order-game-poster')
    await poster.waitFor({ state: 'visible' })
    const posterText = await poster.innerText()
    for (const text of ['王者峡谷 · 技术陪练', '王者荣耀', '微信区', '荣耀王者 62 星', '今天 20:30 开始', '5 局']) assert(posterText.includes(text), `${label} poster is missing ${text}`)
    await checkProviderDialog(`${label}订单详情`)
    if (image) await screenshot(image, false)
    await page.getByRole('dialog').getByRole('button', { name: '返回', exact: true }).click()
    await page.getByRole('dialog').waitFor({ state: 'hidden' })
  }

  await openProviderOrderDetail('PC', 'provider-order-detail-poster-pc.png')
  results.interactions.push('陪玩订单卡片使用不接此单与小尺寸低圆角按钮，详情海报动态展示玩法、区服、身份、时间和数量')

  await hashTo('/provider/reports')
  const editableReport = provider.locator('article').filter({ hasText: 'XA202611030096' })
  await editableReport.locator('summary').click()
  const evidenceFiles = Array.from({ length: 10 }, (_, index) => path.join(root, 'public', 'report-evidence', `result-${index % 3 + 1}.svg`))
  await editableReport.getByTestId('result-upload-1').setInputFiles(evidenceFiles)
  await editableReport.getByTestId('result-count-1').filter({ hasText: '9 / 9 张' }).waitFor()
  assert(await editableReport.getByRole('button', { name: /^查看战绩截图 / }).count() === 9, 'Report must retain exactly 9 result images after selecting 10')
  await textIncludes('1 张超出 9 张上限未添加')
  await editableReport.getByRole('button', { name: '提交审核', exact: true }).click()
  await editableReport.getByRole('button', { name: '撤回审核', exact: true }).waitFor()
  assert(await editableReport.getByRole('button', { name: '提交审核', exact: true }).count() === 0, 'Submitted report must replace submit with withdraw')
  await editableReport.getByRole('button', { name: '查看战绩截图 1', exact: true }).click()
  const evidencePreview = page.getByRole('dialog')
  const previewSource = await evidencePreview.locator('img').getAttribute('src')
  assert(previewSource?.startsWith('blob:'), `Submitted evidence preview must use the selected local image: ${previewSource}`)
  await checkProviderDialog('PC 报单凭证预览')
  await screenshot('provider-report-evidence-preview-pc.png', false)
  await evidencePreview.getByRole('button', { name: '关闭预览', exact: true }).click()
  await evidencePreview.waitFor({ state: 'hidden' })
  await screenshot('provider-report-review-pending-pc.png')
  await editableReport.getByRole('button', { name: '撤回审核', exact: true }).click()
  await editableReport.getByRole('button', { name: '提交审核', exact: true }).waitFor()
  assert(await editableReport.getByRole('button', { name: /^查看战绩截图 / }).count() === 9, 'Withdrawing review must preserve all 9 result images')
  await textIncludes('已撤回审核，凭证保留，可修改后重新提交')
  const reportButtonRadii = await editableReport.locator('button').evaluateAll(elements => elements.map(element => Number.parseFloat(getComputedStyle(element).borderTopLeftRadius)))
  assert(reportButtonRadii.every(radius => radius <= 8), `Report buttons must not use capsule corners: ${reportButtonRadii.join(', ')}`)
  await page.setViewportSize({ width: 375, height: 812 })
  await open('/provider/reports')
  await page.getByRole('button', { name: '移动端', exact: true }).click()
  await page.locator('iframe[title="陪玩工作台移动端预览"]').waitFor({ state: 'attached' })
  const mobileReport = provider.locator('article').filter({ hasText: 'XA202611030096' })
  const mobileEvidence = mobileReport.locator('details')
  if (!await mobileEvidence.evaluate(element => element.open)) await mobileReport.locator('summary').click()
  await mobileReport.getByTestId('result-upload-1').setInputFiles(evidenceFiles.slice(0, 9))
  await mobileReport.getByTestId('result-count-1').filter({ hasText: '9 / 9 张' }).waitFor()
  await mobileReport.getByRole('button', { name: '提交审核', exact: true }).click()
  await mobileReport.getByRole('button', { name: '撤回审核', exact: true }).click()
  await mobileReport.getByRole('button', { name: '提交审核', exact: true }).waitFor()
  assert(await mobileReport.getByRole('button', { name: /^查看战绩截图 / }).count() === 9, 'Mobile withdrawal must preserve all 9 result images')
  await checkOverflow('陪玩报单撤回后 375px')
  await checkProviderOverflow('陪玩报单撤回后 375px')
  await screenshot('provider-report-withdrawn-375.png')
  await page.setViewportSize({ width: 1440, height: 1000 })
  await open('/provider')
  await page.getByRole('button', { name: 'PC 端', exact: true }).click()
  await page.locator('iframe[title="陪玩工作台PC 端预览"]').waitFor({ state: 'attached' })
  results.interactions.push('报单一次选择 10 张仅保留 9 张，提交后可撤回并查看本机凭证大图，撤回保留全部图片且通过真实 375px 手机视口复测')

  await hashTo('/provider/messages')
  await provider.getByRole('button', { name: /收到组队邀请/ }).click()
  await checkProviderDialog('PC 消息详情')
  await page.getByRole('dialog').getByRole('button', { name: '查看相关内容' }).click()
  await provider.locator('[data-store-page="/provider/applications/105"]').waitFor({ state: 'attached' })
  await textIncludes('等待你确认')
  await provider.getByRole('button', { name: '确认组队', exact: true }).click()
  await textIncludes('组队已确认')
  await textIncludes('等待老板选定')
  await provider.getByRole('button', { name: '撤回报名', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认撤回', exact: true }).click()
  await textIncludes('报名已撤回')
  results.interactions.push('陪玩从组队消息进入报名详情，完成确认组队与撤回报名')

  await hashTo('/provider/wallet')
  await provider.getByRole('button', { name: '管理收款账户', exact: true }).click()
  await provider.locator('[data-store-page="/provider/withdraw-accounts"]').waitFor({ state: 'attached' })
  const bankAccount = provider.locator('article').filter({ hasText: '银行卡' })
  await bankAccount.getByRole('button', { name: '绑定', exact: true }).click()
  await page.getByRole('dialog').getByLabel('收款人姓名').fill('示例收款人')
  await page.getByRole('dialog').getByLabel('收款账号').fill('622202020011223344')
  await page.getByRole('dialog').getByRole('button', { name: '保存示例账户', exact: true }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  await textIncludes('收款账户已保存')
  await textIncludes('622202020011223344')
  await bankAccount.getByRole('button', { name: '解绑', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认解绑', exact: true }).click()
  await textIncludes('收款账户已解绑')
  results.interactions.push('陪玩从钱包进入收款账户，绑定并解绑银行卡且保留历史快照说明')

  await hashTo('/provider/support-chat?order_no=SHENGLANG202611040105')
  await textIncludes('SHENGLANG202611040105')
  await provider.getByLabel('输入客服消息').fill('请协助核对组队进度')
  await provider.getByRole('button', { name: '发送客服消息', exact: true }).click()
  await textIncludes('请协助核对组队进度')
  await textIncludes('已发送（示例）')
  results.interactions.push('陪玩客服会话保留订单号并核对文字消息发送结果')

  await hashTo('/provider/auditions')
  await provider.getByRole('button', { name: '查看招募并报名' }).click()
  await provider.getByRole('button', { name: '微信登录后报名', exact: true }).click()
  await provider.locator('[data-store-page="/provider-login"]').waitFor({ state: 'attached' })
  await provider.getByRole('checkbox').check()
  await provider.getByRole('button', { name: '模拟微信扫码并继续', exact: true }).click()
  await provider.getByRole('button', { name: '使用此头像与昵称并继续', exact: true }).click()
  await provider.getByRole('button', { name: '微信授权并获取验证码', exact: true }).click()
  await provider.getByLabel('短信验证码').fill('123456')
  await provider.getByRole('button', { name: '验证手机号并继续', exact: true }).click()
  await provider.getByRole('button', { name: '暂不开启，直接进入', exact: true }).click()
  await provider.getByRole('button', { name: '返回试音邀请', exact: true }).click()
  await provider.locator('[data-store-page="/provider/audition"]').waitFor({ state: 'attached' })
  await textIncludes('填写报名信息')
  assert(new URL(page.url()).hash.includes('token=SL-AUDITION-202611') && new URL(page.url()).hash.includes('role=provider'), 'Audition login must return to the original token and role')
  await provider.getByRole('button', { name: '提交示例报名', exact: true }).click()
  await textIncludes('试音报名已提交')
  await textIncludes('我的试音记录')
  await hashTo('/provider/audition?token=expired&role=provider')
  await textIncludes('邀请链接无效或已失效')
  results.interactions.push('公开试音邀请完成微信登录回跳、报名提交、历史记录与无效链接恢复')

  await open('/admin-stores')
  const admin = previewTarget('/admin-stores')
  const adminFilterPages = ['orders', 'chat', 'reports', 'services', 'gameplays', 'banners', 'announcements', 'contents', 'broadcast-rules', 'broadcast-records', 'rewards', 'lotteries', 'claims', 'bosses', 'providers', 'registrations', 'passes', 'disposes', 'auditions', 'audition-signups', 'withdraw', 'wallets', 'provider-wallets', 'transactions', 'recharges']

  async function checkAdminFilter(pageKey, advanced = false) {
    const selector = advanced ? '[data-admin-filter-advanced]' : '[data-admin-filter]'
    const filter = admin.locator(`[data-store-page="${pageKey}"] ${selector}`)
    await filter.waitFor({ state: 'visible' })
    const metrics = await filter.evaluate(container => {
      const controls = Array.from(container.querySelectorAll('input:not([type="checkbox"]):not([type="radio"]), button[role="combobox"], button:not([role="combobox"]):not([role="checkbox"])'))
        .filter(element => {
          const style = getComputedStyle(element)
          const rect = element.getBoundingClientRect()
          return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0
        })
      const containerRect = container.getBoundingClientRect()
      const boxes = controls.map(control => {
        const rect = control.getBoundingClientRect()
        return {
          label: control.getAttribute('aria-label') || control.textContent?.trim() || control.getAttribute('placeholder') || control.tagName,
          width: rect.width,
          height: rect.height,
          outside: rect.left < containerRect.left - 1 || rect.right > containerRect.right + 1,
          queryTooNarrow: control.tagName === 'BUTTON' && (control.textContent?.trim().startsWith('查询') || false) && rect.width < 72,
        }
      })
      const searchIcons = Array.from(container.querySelectorAll('svg.lucide-search')).map(icon => {
        const input = icon.parentElement?.querySelector('input')
        if (!input) return { misaligned: false }
        const iconRect = icon.getBoundingClientRect()
        const inputRect = input.getBoundingClientRect()
        return { misaligned: Math.abs(iconRect.top + iconRect.height / 2 - inputRect.top - inputRect.height / 2) > 1 }
      })
      return {
        controlCount: controls.length,
        boxes,
        heights: [...new Set(boxes.map(box => Math.round(box.height)))],
        outside: boxes.filter(box => box.outside),
        queryTooNarrow: boxes.filter(box => box.queryTooNarrow),
        misalignedSearchIcons: searchIcons.filter(icon => icon.misaligned).length,
        horizontalOverflow: container.scrollWidth > container.clientWidth + 1,
      }
    })
    assert(metrics.controlCount > 0, `Admin filter has no measurable controls: ${pageKey}${advanced ? ' advanced' : ''}`)
    assert(metrics.heights.length === 1, `Admin filter control heights differ: ${pageKey}${advanced ? ' advanced' : ''} ${JSON.stringify(metrics)}`)
    assert(metrics.outside.length === 0 && !metrics.horizontalOverflow, `Admin filter overflows its container: ${pageKey} ${JSON.stringify(metrics)}`)
    assert(metrics.queryTooNarrow.length === 0, `Admin filter query button is compressed: ${pageKey} ${JSON.stringify(metrics.queryTooNarrow)}`)
    assert(metrics.misalignedSearchIcons === 0, `Admin filter search icon is not vertically centered: ${pageKey}`)
    ;(results.filterLayouts ??= []).push({ page: pageKey, advanced, controls: metrics.controlCount, height: metrics.heights[0] })
  }

  results.routes.admin = []
  for (const key of adminPages) {
    await hashTo(`/admin-${key}`)
    await admin.locator(`[data-store-page="${key}"]`).waitFor({ state: 'attached' })
    assert(await page.locator(`[data-store-design-panel="${key}"]`).count() === 1, `Missing admin design panel: ${key}`)
    assert(await admin.locator('button[aria-label$="本页数据隔离说明"]').count() === 0, `Admin scope description is still visible: ${key}`)
    assert(await admin.locator('header span.grid.rounded-full').count() === 0, `Admin top avatar is still visible: ${key}`)
    assert(await admin.locator('aside').getByText('运营管理员', { exact: true }).count() === 0, `Admin sidebar account is still visible: ${key}`)
    assert(await admin.locator('header').getByText('运营管理员', { exact: true }).count() === (key === 'login' ? 0 : 1), `Admin header account is missing: ${key}`)
    assert(await admin.locator('[data-admin-primary-nav]').count() === (key === 'login' ? 0 : 9), `Admin primary navigation must contain 9 grouped entries: ${key}`)
    assert(await page.locator('[data-admin-design-nav]').count() === 9, `Admin prototype navigation must contain 9 grouped entries: ${key}`)
    const primaryLabels = ['平台门店管理', '运营工作台', '订单履约', '老板管理', '资金管理', '商品与玩法', '内容与播报', '营销与权益', '门店设置']
    if (key !== 'login') assert(JSON.stringify(await admin.locator('[data-admin-primary-nav]').allInnerTexts()) === JSON.stringify(primaryLabels), `Admin primary navigation must show boss management without provider management: ${key}`)
    assert(JSON.stringify(await page.locator('[data-admin-design-nav]').allInnerTexts()) === JSON.stringify(primaryLabels), `Admin prototype navigation must show boss management without provider management: ${key}`)
    const secondaryGroup = adminSecondaryGroups[key]
    if (secondaryGroup) {
      const [groupId, itemCount] = secondaryGroup
      assert(await admin.locator(`[data-admin-secondary-nav="${groupId}"]`).count() === 1, `Missing ${groupId} secondary navigation: ${key}`)
      assert(await admin.locator(`[data-admin-secondary-nav="${groupId}"] [data-admin-secondary-page]`).count() === itemCount, `Wrong ${groupId} secondary page count: ${key}`)
      const activePage = key === 'order-detail' ? 'orders' : key === 'provider-detail' ? 'providers' : key === 'registration-detail' ? 'registrations' : key
      assert(await admin.locator(`[data-admin-secondary-page="${activePage}"][aria-current="page"]`).count() === 1, `Wrong active secondary page: ${key}`)
    } else {
      assert(await admin.locator('[data-admin-secondary-nav]').count() === 0, `Unexpected secondary navigation: ${key}`)
    }
    if (adminFilterPages.includes(key)) await checkAdminFilter(key)
    await assertNoManualRefresh(admin, `Admin ${key}`)
    if (key === 'bosses') await screenshot('admin-bosses-pc.png', false)
    if (key === 'transactions') await screenshot('admin-transactions-no-manual-refresh-pc.png', false)
    if (key === 'payment-fee') await screenshot('admin-payment-fee-no-manual-refresh-pc.png', false)
    results.routes.admin.push(key)
  }

  await hashTo('/admin-bosses')
  await textIncludes('海浪收藏家')
  await admin.getByRole('button', { name: '查看海浪收藏家档案', exact: true }).click()
  const bossSheet = page.locator('[data-slot="sheet-content"]')
  await bossSheet.waitFor({ state: 'visible' })
  assert((await bossSheet.innerText()).includes('OTP 动态验证') && (await bossSheet.innerText()).includes('会员与本店资产'), 'Boss detail must show account binding, membership and store assets')
  await page.waitForTimeout(250)
  await screenshot('admin-boss-detail-pc.png', false)
  await bossSheet.getByRole('button', { name: '关闭档案', exact: true }).click()
  await bossSheet.waitFor({ state: 'hidden' })
  await hashTo('/admin-providers')
  await admin.locator('[data-store-page="providers"]').waitFor({ state: 'attached' })
  results.interactions.push('后台一级菜单以老板管理替换陪玩管理，老板档案支持筛选与只读详情，原陪玩页面路由保持可用')

  await hashTo('/admin-wallets')
  const bossWalletBody = await admin.locator('[data-wallet-kind="boss"]').innerText()
  assert(bossWalletBody.includes('W-Boss-1001'), 'Boss wallet page is missing its own account')
  assert(!bossWalletBody.includes('W-Provider') && !bossWalletBody.includes('陪玩押金'), 'Boss wallet page still exposes provider funds')
  await screenshot('admin-boss-wallets-pc.png', false)
  await admin.locator('[data-admin-secondary-page="provider-wallets"]').click()
  await admin.locator('[data-store-page="provider-wallets"]').waitFor({ state: 'attached' })
  const providerWalletBody = await admin.locator('[data-wallet-kind="provider"]').innerText()
  assert(providerWalletBody.includes('W-Provider-2001') && providerWalletBody.includes('陪玩押金'), 'Provider wallet page is missing provider funds or deposits')
  assert(!providerWalletBody.includes('W-Boss') && !providerWalletBody.includes('老板分级'), 'Provider wallet page still exposes boss accounts')
  await screenshot('admin-provider-wallets-pc.png', false)
  results.interactions.push('老板钱包与陪玩钱包使用独立页面和独立场景状态，账户、押金及平台资金互不混放')

  await hashTo('/admin-dashboard')
  const sidebar = admin.locator('[data-admin-sidebar]')
  assert(await sidebar.getAttribute('data-sidebar-expanded') === 'false', 'Admin sidebar must be collapsed by default')
  assert(await sidebar.evaluate(element => element.getBoundingClientRect().width) === 72, 'Collapsed admin sidebar must be 72px wide')
  await screenshot('admin-sidebar-collapsed-pc.png', false)
  await admin.locator('[data-admin-sidebar-toggle]').click()
  assert(await sidebar.getAttribute('data-sidebar-expanded') === 'true', 'Admin sidebar did not expand')
  assert(await sidebar.evaluate(element => element.getBoundingClientRect().width) === 232, 'Expanded admin sidebar must be 232px wide')
  await admin.getByRole('button', { name: '锁定展开侧栏', exact: true }).click()
  assert(await sidebar.getAttribute('data-sidebar-locked') === 'true', 'Admin sidebar did not lock its expanded state')
  assert(await admin.getByRole('button', { name: '收缩侧栏', exact: true }).isDisabled(), 'Locked admin sidebar must not collapse')
  await screenshot('admin-sidebar-expanded-locked-pc.png', false)
  await page.reload({ waitUntil: 'networkidle' })
  await admin.locator('[data-store-page="dashboard"]').waitFor({ state: 'attached' })
  assert(await sidebar.getAttribute('data-sidebar-expanded') === 'true' && await sidebar.getAttribute('data-sidebar-locked') === 'true', 'Locked sidebar preference did not survive refresh')
  await admin.getByRole('button', { name: '取消锁定展开', exact: true }).click()
  await admin.getByRole('button', { name: '收缩侧栏', exact: true }).click()
  assert(await sidebar.getAttribute('data-sidebar-expanded') === 'false', 'Unlocked admin sidebar did not collapse')
  await hashTo('/admin-orders')
  assert(await sidebar.getAttribute('data-sidebar-expanded') === 'false', 'Collapsed sidebar state did not survive navigation')
  results.interactions.push('后台业务侧栏默认 72px 收缩，可展开至 232px、锁定后刷新保持，解锁后可收缩并跨页面保留状态')

  await hashTo('/admin-login')
  await screenshot('admin-login-pc.png', false)
  await hashTo('/admin-dashboard')
  assert(await page.locator('iframe[title="管理后台PC 端预览"]').count() === 1, 'Admin PC iframe is missing')
  await page.getByRole('button', { name: 'iPad Pro', exact: true }).click()
  const tabletFrame = page.locator('iframe[title="管理后台iPad Pro预览"]')
  await tabletFrame.waitFor({ state: 'visible' })
  await page.waitForFunction(() => {
    const container = document.querySelector('[data-preview-viewport="tablet"]')
    return container && Math.abs(Number(container.getAttribute('data-preview-scale')) - Math.min(1, container.clientWidth / 1366)) < 0.001
  })
  const tabletSize = await tabletFrame.evaluate(frame => ({ width: frame.offsetWidth, height: frame.offsetHeight }))
  assert(tabletSize.width === 1366 && tabletSize.height === 1024, `Admin iPad Pro logical viewport mismatch: ${JSON.stringify(tabletSize)}`)
  const tabletFit = await page.locator('[data-preview-viewport="tablet"]').evaluate(container => ({ clientWidth: container.clientWidth, scrollWidth: container.scrollWidth }))
  const tabletBounds = await tabletFrame.boundingBox()
  assert(tabletBounds && tabletBounds.width <= tabletFit.clientWidth + 1 && tabletFit.scrollWidth <= tabletFit.clientWidth + 1, `Admin iPad Pro preview must fit completely: ${JSON.stringify({ tabletBounds, tabletFit })}`)
  assert(await admin.locator('aside').count() === 1, 'Admin business sidebar is missing inside the iframe')
  await screenshot('admin-dashboard-ipad-pro.png', false)
  await admin.locator('[data-admin-sidebar-toggle]').click()
  assert(await sidebar.getAttribute('data-sidebar-expanded') === 'true', 'Admin sidebar did not expand on iPad Pro')
  await page.evaluate(() => window.scrollTo(0, 0))
  await screenshot('admin-dashboard-ipad-pro-expanded.png', false)
  await admin.getByRole('button', { name: '收缩侧栏', exact: true }).click()
  await hashTo('/admin-bosses')
  await screenshot('admin-bosses-ipad-pro.png', false)
  await page.getByRole('button', { name: 'PC 端', exact: true }).click()
  const adminPcFrame = page.locator('iframe[title="管理后台PC 端预览"]')
  await page.waitForFunction(() => {
    const container = document.querySelector('[data-preview-viewport="pc"]')
    return container && Math.abs(Number(container.getAttribute('data-preview-scale')) - Math.min(1, container.clientWidth / 1440)) < 0.001
  })
  const adminPcSize = await adminPcFrame.evaluate(frame => ({ logicalWidth: frame.offsetWidth, displayedWidth: frame.getBoundingClientRect().width }))
  assert(adminPcSize.logicalWidth === 1440, `Admin PC iframe must preserve a 1440px logical viewport: ${JSON.stringify(adminPcSize)}`)
  const adminPcOverflow = await page.locator('[data-preview-viewport="pc"]').evaluate(container => ({ clientWidth: container.clientWidth, scrollWidth: container.scrollWidth }))
  assert(adminPcSize.displayedWidth <= adminPcOverflow.clientWidth + 1 && adminPcOverflow.scrollWidth <= adminPcOverflow.clientWidth + 1, `Admin PC preview must fit completely without horizontal clipping: ${JSON.stringify({ adminPcSize, adminPcOverflow })}`)
  await screenshot('admin-dashboard-pc.png', false)
  await hashTo('/admin-reservations')
  await screenshot('admin-reservations-grouped-pc.png', false)
  await admin.locator('[data-admin-secondary-page="reports"]').click()
  await admin.locator('[data-store-page="reports"]').waitFor({ state: 'attached' })
  assert(new URL(page.url()).hash === '#/admin-reports', 'Order fulfillment secondary navigation did not open report review')
  await admin.getByRole('button', { name: /搜索.*功能/ }).click()
  await page.getByPlaceholder('输入功能名称').fill('陪玩档案')
  await page.getByRole('dialog').getByRole('button').filter({ hasText: '陪玩档案' }).click()
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  await admin.locator('[data-store-page="providers"]').waitFor({ state: 'attached' })
  await admin.locator('[data-admin-secondary-page="audition-signups"]').click()
  await admin.locator('[data-store-page="audition-signups"]').waitFor({ state: 'attached' })
  assert(new URL(page.url()).hash === '#/admin-audition-signups', 'Provider secondary navigation did not open audition review')
  await admin.getByRole('button', { name: /搜索.*功能/ }).click()
  await page.getByPlaceholder('输入功能名称').fill('充值记录')
  await page.getByRole('dialog').getByRole('button').filter({ hasText: '充值记录' }).click()
  await admin.locator('[data-store-page="recharges"]').waitFor({ state: 'attached' })
  results.interactions.push('后台 9 个一级入口按业务域分组，二级页签与全局搜索均可进入原页面，详情页保留所属分组')
  await hashTo('/admin-stores')
  await screenshot('admin-stores-pc-wide.png', false)
  results.interactions.push('三端 PC 预览均为 1440px，后台按外层空间等比完整显示，并支持 1366 × 1024 iPad Pro 12.9 横屏切换')

  for (const [key, actionName] of [['providers', '更多筛选'], ['orders', '更多筛选'], ['wallets', '高级筛选'], ['services', '更多筛选'], ['withdraw', '更多筛选']]) {
    await hashTo(`/admin-${key}`)
    await admin.locator('[data-admin-filter]').getByRole('button', { name: actionName, exact: true }).click()
    await checkAdminFilter(key, true)
    await checkAdminFilter(key)
    await admin.locator('[data-admin-filter-advanced]').scrollIntoViewIfNeeded()
    await page.evaluate(() => window.scrollTo(0, 0))
    await screenshot(`admin-${key}-filters-advanced-pc.png`, false)
  }
  await hashTo('/admin-contents')
  await admin.locator('[data-admin-filter]').scrollIntoViewIfNeeded()
  await page.evaluate(() => window.scrollTo(0, 0))
  await screenshot('admin-contents-filters-pc.png', false)
  assert(new Set(results.filterLayouts.filter(item => !item.advanced).map(item => item.page)).size === adminFilterPages.length, 'Not every admin filter route was measured')
  results.interactions.push('25 个后台筛选路由逐页校验控件同高、查询按钮宽度、搜索图标居中与容器边界，5 组高级筛选展开后复测')

  // Each boss store entry must show independent wallet data.
  await page.setViewportSize({ width: 375, height: 812 })
  await open('/wallet')
  await textIncludes('326')
  await open('/home', 'xingchao')
  await textIncludes('星潮电竞 · 湖滨店')
  await textIncludes('湖滨店周末开黑节')
  results.interactions.push('老板端目标店独立入口加载本店公告')
  await hashTo('/wallet')
  await textIncludes('80')
  await textIncludes('XINGCHAO202611')
  await checkOverflow('老板端钱包 375px')
  await screenshot('boss-wallet-xingchao-375.png')

  await open('/mine', 'wudao')
  await textIncludes('云上玩家')
  await textIncludes('本店账号 ID 9056')
  await boss.getByRole('button', { name: '雾岛云城钱包余额 0 浪花，查看账单', exact: true }).waitFor()
  results.interactions.push('雾岛店老板账号、会员与零余额独立展示')

  const providerMobilePages = [
    ['/provider/applications', '陪玩报名列表', 'provider-applications-375.png', false],
    ['/provider/applications/105?application=9001&from=history', '陪玩报名详情', 'provider-application-detail-375.png', false],
    ['/provider/withdraw-accounts', '陪玩收款账户', 'provider-withdraw-accounts-375.png', false],
    ['/provider/support-chat?order_no=SHENGLANG202611040105', '陪玩客服会话', 'provider-support-chat-375.png', false],
    ['/provider/audition?token=SL-AUDITION-202611&role=provider', '陪玩公开试音邀请', 'provider-audition-invite-375.png', true],
  ]
  for (const [route, label, image, publicPage] of providerMobilePages) {
    await open(route)
    await checkOverflow(`${label} 375px`)
    await checkProviderOverflow(`${label} 375px`, publicPage)
    await screenshot(image)
  }
  await open('/provider')
  await checkOverflow('陪玩订单工作台 375px')
  await checkProviderOverflow('陪玩订单工作台 375px')
  await openProviderOrderDetail('375px', 'provider-order-detail-poster-375.png')
  await page.setViewportSize({ width: 390, height: 844 })
  for (const [route, label] of [
    ['/provider/applications/105?application=9001&from=history', '陪玩报名详情'],
    ['/provider/support-chat?order_no=SHENGLANG202611040105', '陪玩客服会话'],
  ]) {
    await open(route)
    await checkOverflow(`${label} 390px`)
    await checkProviderOverflow(`${label} 390px`)
  }
  await open('/provider')
  await checkOverflow('陪玩订单工作台 390px')
  await checkProviderOverflow('陪玩订单工作台 390px')
  await openProviderOrderDetail('390px', 'provider-order-detail-poster-390.png')
  results.interactions.push('陪玩报名、收款、客服、公开试音与订单详情通过 375px 内容溢出检查，报名详情、客服会话与订单详情复测 390px')
  await page.setViewportSize({ width: 375, height: 812 })

  await open('/provider/wallet', 'xingchao')
  await textIncludes('218')
  await textIncludes('星潮湖滨')
  const walletHeader = provider.locator('main > header').first()
  await walletHeader.getByRole('heading', { name: '我的钱包', level: 1, exact: true }).waitFor()
  const walletHeaderDirection = await walletHeader.locator(':scope > div').first().evaluate(element => getComputedStyle(element).flexDirection)
  assert(walletHeaderDirection === 'column', `Wallet mobile header must stack its title and actions: ${walletHeaderDirection}`)
  await checkOverflow('陪玩端钱包 375px')
  await screenshot('provider-wallet-xingchao-375.png')
  assert(await provider.getByRole('button', { name: /当前陪玩端门店：/ }).count() === 0, 'Provider wallet must not expose a store switcher')
  results.interactions.push('陪玩端工作台顶部不提供界面切换店铺入口')

  await open('/provider-login', 'xingchao')
  assert(await provider.getByRole('button', { name: /当前陪玩端门店：/ }).count() === 0, 'Provider account entry must not expose a store switcher')
  const loginBody = await provider.locator('body').innerText()
  assert(!loginBody.includes('账号密码登录'), 'Provider login still contains account-password authentication')
  assert(await provider.locator('section[aria-label="微信登录、手机号与可选二次验证"]').count() === 1, 'Provider WeChat login is missing')
  results.interactions.push('陪玩账号入口固定当前门店并仅保留微信快捷登录')

  const loginWechat = provider
  await loginWechat.getByRole('button', { name: '微信快捷登录', exact: true }).click()
  await textIncludes('请先阅读并同意隐私保护指引')
  await loginWechat.getByRole('checkbox').check()
  await loginWechat.getByRole('button', { name: '微信快捷登录', exact: true }).click()
  await loginWechat.getByRole('button', { name: '使用此头像与昵称并继续', exact: true }).click()
  await loginWechat.getByRole('button', { name: '微信授权并获取验证码', exact: true }).click()
  await loginWechat.getByLabel('短信验证码').fill('123456')
  await loginWechat.getByRole('button', { name: '验证手机号并继续', exact: true }).click()
  const providerOtp = loginWechat.locator('[data-otp-binding]')
  await providerOtp.getByRole('button', { name: '开启二次验证', exact: true }).click()
  await providerOtp.getByLabel('OTP 动态验证码').fill('000000')
  await providerOtp.getByRole('button', { name: '绑定 OTP 并开启二次验证', exact: true }).click()
  await textIncludes('请输入正确的 6 位 OTP 示例动态码 123456')
  await providerOtp.getByLabel('OTP 动态验证码').fill('123456')
  await checkOverflow('陪玩 OTP 绑定 375px')
  await screenshot('provider-login-otp-xingchao-375.png')
  await providerOtp.getByRole('button', { name: '绑定 OTP 并开启二次验证', exact: true }).click()
  await provider.getByText('微信登录、头像昵称同步、手机号验证与 OTP 绑定均已完成，二次验证已开启。').waitFor()
  results.interactions.push('陪玩仅通过微信完成隐私授权、手机号短信验证与首次 OTP 绑定，并拦截错误动态码')
  await provider.getByRole('button', { name: '进入接单工作台' }).click()
  await provider.locator('[data-store-page="/provider"]').waitFor({ state: 'attached' })
  results.interactions.push('陪玩微信登录完成后进入本店接单工作台')

  await open('/provider-login', 'xingchao')
  await checkOverflow('陪玩登录 375px')
  await screenshot('provider-login-xingchao-375.png')

  await open('/provider-register', 'xingchao')
  const registerBody = await provider.locator('body').innerText()
  assert(!registerBody.includes('备用登录信息') && !registerBody.includes('登录密码'), 'Provider registration still contains account-password credentials')
  await screenshot('provider-register-wechat-xingchao-375.png')

  await provider.getByRole('button', { name: '查询审核', exact: true }).click()
  const statusWechat = provider.locator('[data-wechat-purpose="status"]')
  await statusWechat.getByRole('checkbox').check()
  await statusWechat.getByRole('button', { name: '微信快捷查询', exact: true }).click()
  await statusWechat.getByRole('button', { name: '微信授权并获取验证码', exact: true }).click()
  await statusWechat.getByLabel('短信验证码').fill('123456')
  await statusWechat.getByRole('button', { name: '完成验证并查询', exact: true }).click()
  assert(await statusWechat.locator('[data-otp-binding]').count() === 0, 'Review status query must not require OTP binding')
  await provider.getByRole('button', { name: '查询本店审核进度', exact: true }).click()
  await textIncludes('已读取本店示例申请进度')
  results.interactions.push('陪玩通过绑定微信查询本店入驻审核进度且不强制 OTP')

  await open('/provider-login', 'xingchao')
  await open('/provider-register', 'xingchao')
  const registerWechat = provider.locator('[data-wechat-purpose="register"]')
  await registerWechat.getByRole('checkbox').check()
  await registerWechat.getByRole('button', { name: '微信快捷入驻', exact: true }).click()
  await registerWechat.getByRole('button', { name: '微信授权并获取验证码', exact: true }).click()
  await registerWechat.getByLabel('短信验证码').fill('123456')
  await registerWechat.getByRole('button', { name: '完成验证并入驻', exact: true }).click()
  await textIncludes('微信与手机号已绑定')
  assert(await registerWechat.locator('[data-otp-binding]').count() === 0, 'Provider registration must not require OTP binding')
  await provider.getByRole('button', { name: '使用本地示例材料' }).click()
  await checkOverflow('陪玩注册材料 375px')
  await screenshot('provider-register-form-xingchao-375.png')
  await provider.getByRole('button', { name: '提交入驻申请', exact: true }).click()
  await textIncludes('待审核')
  await textIncludes('资料已提交，请等待本店管理人员审核')
  await checkOverflow('陪玩注册 375px')
  await screenshot('provider-register-xingchao-375.png')
  results.interactions.push('陪玩微信入驻绑定、材料提交与本店待审核结果，入驻不强制 OTP')

  await open('/login', 'xingchao')
  await boss.getByRole('checkbox').check()
  await boss.getByRole('button', { name: '微信快捷登录', exact: true }).click()
  await boss.getByRole('button', { name: '使用此头像与昵称并继续', exact: true }).click()
  await boss.getByRole('button', { name: '微信授权并获取验证码', exact: true }).click()
  await boss.getByLabel('短信验证码').fill('123456')
  await boss.getByRole('button', { name: '验证手机号并继续', exact: true }).click()
  const bossOtp = boss.locator('[data-otp-binding]')
  await bossOtp.getByRole('button', { name: '开启二次验证', exact: true }).click()
  await bossOtp.getByLabel('OTP 动态验证码').fill('123456')
  await checkOverflow('老板 OTP 绑定 375px')
  await screenshot('boss-login-otp-xingchao-375.png')
  await bossOtp.getByRole('button', { name: '绑定 OTP 并开启二次验证', exact: true }).click()
  await boss.getByText('微信登录、头像昵称同步、手机号验证与 OTP 绑定均已完成，二次验证已开启。').waitFor()
  await boss.getByRole('button', { name: '进入声浪电竞', exact: true }).click()
  await boss.locator('[data-store-page="/home"]').waitFor({ state: 'attached' })
  await textIncludes('星潮电竞 · 湖滨店')
  results.interactions.push('老板端微信资料、手机号短信与首次 OTP 绑定后进入本店首页')

  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.getByRole('button', { name: 'PC 端', exact: true }).click()
  await open('/provider-login', 'xingchao')
  const loginCenter = await provider.locator('section[aria-label="微信登录、手机号与可选二次验证"]').evaluate(form => {
    const scroll = form.closest('[data-testid="provider-public-scroll"]')
    const scope = scroll?.closest('[data-store-page="/provider-login"]')
    if (!scroll || !scope) throw new Error('Provider login layout is incomplete')
    const formBox = form.getBoundingClientRect()
    const scrollBox = scroll.getBoundingClientRect()
    const headerBox = scroll.querySelector(':scope > header')?.getBoundingClientRect()
    const contentTop = headerBox?.bottom ?? scrollBox.top
    const availableMiddle = contentTop + (scrollBox.bottom - contentTop) / 2
    return { formMiddle: formBox.top + formBox.height / 2, availableMiddle }
  })
  // The footer and grid padding create a small optical offset around the geometric midpoint.
  assert(Math.abs(loginCenter.formMiddle - loginCenter.availableMiddle) <= 16, `Provider login is not vertically centered on PC: ${JSON.stringify(loginCenter)}`)
  await checkOverflow('陪玩登录 PC')
  await screenshot('provider-login-xingchao-1440.png', false)
  results.interactions.push('陪玩登录内容在 PC 剩余可视区域内居中')

  await open('/admin-dashboard')
  await textIncludes('128,460')
  await admin.getByRole('button', { name: /当前管理后台门店：声浪电竞/ }).click()
  await page.getByRole('menuitem').filter({ hasText: '星潮电竞 · 湖滨店' }).click()
  await page.getByRole('button', { name: '确认切店' }).click()
  await page.waitForURL(/store=xingchao.*#\/admin-login/)
  await textIncludes('登录星潮电竞 · 湖滨店管理后台')
  await admin.getByRole('button', { name: '安全登录' }).click()
  await admin.locator('#admin-otp').fill('123456')
  await admin.getByRole('button', { name: '验证并登录' }).click()
  await admin.locator('[data-store-page="dashboard"]').waitFor({ state: 'attached' })
  await textIncludes('46,280')
  results.interactions.push('后台切店重新鉴权后只显示目标店经营快照')
  await screenshot('admin-dashboard-xingchao-1440.png')

  await page.setViewportSize({ width: 1366, height: 1024 })
  await open('/admin-stores')
  await textIncludes('平台门店管理')
  await textIncludes('不因拥有平台权限而读取店内客户')
  await admin.getByRole('button', { name: '新增门店' }).click()
  await page.locator('#new-store-name').fill('临海市电竞 · 测试店')
  await page.locator('#new-store-slug').fill('linhai')
  await page.getByRole('button', { name: '创建初始化任务' }).click()
  await textIncludes('不复制原店客户、订单或资金')
  await page.getByRole('dialog').waitFor({ state: 'hidden' })
  const createdRow = admin.getByRole('row').filter({ hasText: 'linhai' })
  await createdRow.getByRole('button', { name: '完成示例初始化' }).click()
  await createdRow.getByRole('button', { name: '停用门店' }).click()
  await page.getByRole('button', { name: '确认停用' }).click()
  await textIncludes('临海市电竞 · 测试店已停用')
  results.interactions.push('平台空白门店创建、初始化与停用仅改变本地元数据')
  await checkOverflow('平台门店管理 1366px')
  await screenshot('admin-stores-1366.png', false)

  await open('/home', 'shenglang')
  await checkOverflow('老板端桌面 1440px')
  await screenshot('boss-home-shenglang-1440.png')

  assert(results.routes.boss.length === 42, 'Boss route count mismatch')
  assert(results.routes.provider.length === 21, 'Provider route count mismatch')
  assert(results.routes.admin.length === 36, 'Admin route count mismatch')
  assert(results.pageErrors.length === 0, `Page errors: ${results.pageErrors.join(' | ')}`)
  assert(results.consoleErrors.length === 0, `Console errors: ${results.consoleErrors.join(' | ')}`)
  results.status = 'passed'
} catch (error) {
  results.status = 'failed'
  results.failure = error instanceof Error ? error.stack : String(error)
  results.serverLog = serverLog
  console.error(error)
  process.exitCode = 1
} finally {
  if (browser) await browser.close()
  await stopServer()
  await writeFile(path.join(output, 'store-verification.json'), `${JSON.stringify(results, null, 2)}\n`)
  console.log(JSON.stringify({ status: results.status, routes: Object.fromEntries(Object.entries(results.routes).map(([key, value]) => [key, value.length])), interactions: results.interactions.length, consoleErrors: results.consoleErrors.length, pageErrors: results.pageErrors.length, screenshots: results.screenshots.length }, null, 2))
}
