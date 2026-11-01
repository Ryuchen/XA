// Run the actual page controller against a disposable Django HTTP database.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const { createRequire } = require('node:module')
const adminRequire = createRequire(require('node:path').resolve('apps/admin-web/package.json'))
const vue = adminRequire('vue'), ts = adminRequire('typescript')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
let activeToken = input.staffToken
let detailGate = null
let optionGate = null
let dropCreateResponse = false
// Django's in-memory LiveServer shares one SQLite connection across threads.
// Finish each real HTTP response before issuing the next request on that connection.
let transport = Promise.resolve()
const calls = []
async function http(config) {
  const url = new URL(config.url.replace(/^\//, ''), input.base + (config.baseURL || '/api/admin') + '/')
  for (const [key, value] of Object.entries(config.params || {})) if (value !== '') url.searchParams.set(key, String(value))
  const token = config.token || activeToken
  const request = transport.then(async () => {
    const response = await fetch(url, { method: (config.method || 'GET').toUpperCase(),
      headers: { Authorization: 'Bearer ' + token, 'Content-Type': 'application/json' },
      body: config.data === undefined ? undefined : JSON.stringify(config.data) })
    return response.json()
  })
  transport = request.then(() => {}, () => {})
  const result = await request
  calls.push({ path: url.pathname, url: url.href, method: config.method || 'get', body: config.data, code: result.code, actor: token === input.staffToken ? 'staff' : token === input.viewerToken ? 'viewer' : token === input.cancelToken ? 'canceller' : 'other' })
  if (dropCreateResponse && url.pathname === '/api/orders/reservations/' && config.method === 'post' && result.code === 0) {
    dropCreateResponse = false
    throw Object.assign(new Error('response lost'), { code: 'ERR_NETWORK' })
  }
  if (result.code !== 0) throw result
  if (optionGate && url.pathname === optionGate.path) {
    const gate = optionGate; optionGate = null; gate.arrived(); await gate.release
  }
  if (detailGate && url.pathname === detailGate.path && (config.method || 'get') === 'get') {
    const gate = detailGate; detailGate = null; gate.arrived(); await gate.release
  }
  return result
}
const options = resource => ({ options: params => http({ url: `options/${resource}/`, params }) })
const mounted = [], unmounted = [], auth = vue.reactive({ profile: { id: input.staffId }, sessionRevision: 1,
  permissions: ['order:view', 'order:dispatch'], hasPerm(code) { return this.permissions.includes(code) } })
const route = vue.reactive({query: {status: 'pending'}})
const messages = [], storage = new Map(), events = new Map(), socketEvents = new Map()
let confirmationGate = null, intervalCallback = null
const document = { hidden: false, addEventListener: (name, fn) => events.set(name, fn), removeEventListener: name => events.delete(name) }
const settle = async predicate => { for (let i = 0; i < 200 && !predicate(); i++) await new Promise(resolve => setTimeout(resolve, 10)); assert.ok(predicate(), 'timed out waiting for page refresh') }
const modules = { vue: { ...vue, onMounted: fn => mounted.push(fn), onUnmounted: fn => unmounted.push(fn) },
  'vue-router': { useRoute: () => route, useRouter: () => ({ push() {}, replace: ({query}) => { route.query = query } }) },
  'element-plus': { ElMessage: { warning: x => messages.push(x), success: x => messages.push(x), error: x => messages.push(x) }, ElMessageBox: { confirm: async () => confirmationGate ? confirmationGate.promise : 'confirm', prompt: async () => ({ value: '' }) } },
  '@/api/request': { request: http }, '@/api/modules': { userApi: options('users'), escortApi: options('escorts'), serviceItemApi: options('service-items') },
  '@/stores/auth': { useAuthStore: () => auth }, '@/utils/format': { amountToXaCoin: x => String(x / 10), formatDateTime: String },
  '@/utils/ws': { adminWs: { on: (name, fn) => { socketEvents.set(name, fn); return () => socketEvents.delete(name) }, connect() {} } } }
let source = fs.readFileSync('apps/admin-web/src/views/reservation/index.vue', 'utf8').match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
source = source.replace("(import.meta.env.VITE_API_BASE || '/api/admin')", "'/api/admin'")
const code = ts.transpileModule(source + '\nglobalThis.page = { form, create, view, convert, action, rows, detail, openCreate, createOpen, createUncertain, reviewCreated, reservationDate, status, changeStatus, searchBoss, searchService, bosses, services, optionLoading };', { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
const context = { exports: {}, require: name => { assert.ok(modules[name], name); return modules[name] },
  window: { innerWidth: 375, addEventListener() {}, removeEventListener() {} }, document,
  setInterval: fn => { intervalCallback = fn; return 1 }, clearInterval: () => { intervalCallback = null },
  sessionStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
  crypto: require('node:crypto').webcrypto }
const scope = vue.effectScope(); scope.run(() => vm.runInNewContext(code, context))
;(async () => {
  const page = context.page
  mounted.forEach(fn => fn())
  await settle(() => calls.some(row => row.path === '/api/orders/reservations/' && row.method === 'get'))
  assert.equal(page.status.value, 'pending', 'dashboard link opens pending filter')
  assert.equal(new URL(calls.find(row => row.path === '/api/orders/reservations/' && row.method === 'get').url).searchParams.get('status'), 'pending')
  route.query = {status: 'confirmed'}
  await settle(() => page.status.value === 'confirmed' && calls.some(row => new URL(row.url).searchParams.get('status') === 'confirmed'))
  page.status.value = ''
  page.changeStatus()
  await settle(() => !route.query.status && calls.filter(row => row.path === '/api/orders/reservations/' && row.method === 'get').length >= 3)
  assert.equal(page.status.value, '')
  await page.openCreate()
  // Hold a real boss-options response while a different field finishes searching.
  // The two independent controls must both retain their own results.
  let releaseOptions, optionsArrived
  const optionsReady = new Promise(resolve => { optionsArrived = resolve })
  optionGate = { path: '/api/admin/options/users/', arrived: optionsArrived, release: new Promise(resolve => { releaseOptions = resolve }) }
  page.bosses.value = []; page.services.value = []
  const bossSearch = page.searchBoss('')
  await optionsReady
  assert.equal(page.optionLoading.boss, true)
  await page.searchService('')
  assert.ok(page.services.value.some(row => row.id === input.serviceId), 'service options come from the real API')
  assert.equal(page.optionLoading.boss, true, 'another field must not clear the pending boss loading state')
  assert.equal(page.optionLoading.service, false)
  releaseOptions()
  await bossSearch
  assert.ok(page.bosses.value.some(row => row.id === input.bossId), 'a service search must not discard the boss search')
  assert.equal(page.optionLoading.boss, false)
  const olderReady = new Promise(resolve => { optionsArrived = resolve })
  optionGate = { path: '/api/admin/options/users/', arrived: optionsArrived, release: new Promise(resolve => { releaseOptions = resolve }) }
  const olderSearch = page.searchBoss('')
  await olderReady
  await page.searchBoss('no-such-boss-qa-7391')
  assert.equal(page.bosses.value.length, 0)
  releaseOptions()
  await olderSearch
  assert.equal(page.bosses.value.length, 0, 'an older response must not overwrite the latest query in the same field')
  await page.searchBoss('')
  Object.assign(page.form, { customer_account_id: input.bossId, provider_account_id: input.providerId,
    service_id: input.serviceId, start_time: page.reservationDate(input.start), end_time: page.reservationDate(input.end), game_rounds: 1 })
  await page.create()
  const created = calls.find(row => row.path === '/api/orders/reservations/' && row.method === 'post')
  assert.equal(created?.code, 0, JSON.stringify(messages))
  assert.equal(Date.parse(created.body.start_time), Date.parse(input.start), 'selected Beijing time must reach API as the same instant')
  assert.equal(Date.parse(created.body.end_time), Date.parse(input.end), 'end time must reach API as the same instant')
  await new Promise(resolve => setTimeout(resolve, 30))
  const id = page.rows.value[0].id
  await page.openCreate()
  Object.assign(page.form, { customer_account_id: input.bossId, provider_account_id: input.providerId,
    service_id: input.serviceId, start_time: page.reservationDate(input.secondStart), end_time: page.reservationDate(input.secondEnd), game_rounds: 1 })
  await page.create()
  const secondId = page.rows.value.find(row => row.id !== id).id
  activeToken = input.viewerToken; auth.permissions = ['order:view']; auth.sessionRevision++
  await vue.nextTick()
  await page.view(secondId)
  assert.equal(page.detail.value.allowed_actions.length, 0)
  const countBefore = calls.length
  await page.action('cancel')
  assert.equal(calls.length, countBefore, 'view-only must not send a cancel action')
  activeToken = input.cancelToken; auth.permissions = ['order:view', 'order:cancel']; auth.sessionRevision++
  await vue.nextTick()
  await page.view(secondId)
  assert.deepEqual([...page.detail.value.allowed_actions], ['cancel'])
  await page.action('cancel')
  assert.ok(page.detail.value, JSON.stringify({ messages, calls: calls.slice(-8) }))
  assert.equal(page.detail.value.status, 'CANCELLED', JSON.stringify(messages))
  activeToken = input.staffToken; auth.permissions = ['order:view', 'order:dispatch']; auth.sessionRevision++
  await vue.nextTick()
  await page.view(id)
  assert.equal(page.detail.value.status, 'PENDING')
  await page.openCreate()
  page.form.remark = '尚未提交的内容'
  await http({ baseURL: '/api', url: `orders/reservations/${id}/confirm/`, method: 'post', data: {}, token: input.providerToken })
  socketEvents.get('reservation_status_update')({reservation_id: id})
  document.hidden = true; events.get('visibilitychange')()
  const countWhileEditing = calls.length
  document.hidden = false; events.get('visibilitychange')()
  await new Promise(resolve => setTimeout(resolve, 50))
  assert.equal(calls.length, countWhileEditing, 'visibility refresh must wait for the create dialog')
  assert.equal(page.form.remark, '尚未提交的内容')
  page.createOpen.value = false
  await settle(() => page.detail.value?.status === 'CONFIRMED')
  assert.equal(page.rows.value.find(row => row.id === id).status, 'CONFIRMED')
  let release
  confirmationGate = { promise: new Promise(resolve => { release = resolve }) }
  const conversion = page.convert()
  await settle(() => calls.some(row => row.path.endsWith('/quote/')))
  const countAtQuote = calls.length
  intervalCallback()
  await new Promise(resolve => setTimeout(resolve, 50))
  assert.equal(calls.length, countAtQuote, 'periodic refresh must wait for quote confirmation')
  assert.equal(page.detail.value.status, 'CONFIRMED')
  release('confirm')
  await conversion
  confirmationGate = null
  assert.equal(page.detail.value.status, 'CONVERTED', JSON.stringify(messages))
  route.query = { find_reservation: String(id) }
  await settle(() => page.detail.value?.id === id && !route.query.find_reservation)
  assert.equal(page.detail.value.status, 'CONVERTED')
  const sent = calls.find(row => row.path.endsWith('/convert/') && row.method === 'post')
  assert.ok(sent.body.client_request_id && sent.body.expected_service)
  assert.equal(sent.body.expected_amount, 10000)
  const orderId = page.detail.value.order
  await page.openCreate()
  Object.assign(page.form, { customer_account_id: input.bossId, provider_account_id: input.providerId,
    service_id: input.serviceId, start_time: page.reservationDate(input.thirdStart), end_time: page.reservationDate(input.thirdEnd), game_rounds: 1 })
  await page.create()
  const thirdId = page.rows.value.find(row => row.id !== id && row.id !== secondId).id
  let releaseDetail, detailArrived
  const detailReady = new Promise(resolve => { detailArrived = resolve })
  detailGate = { path: `/api/orders/reservations/${thirdId}/`, arrived: detailArrived, release: new Promise(resolve => { releaseDetail = resolve }) }
  const openingDetail = page.view(thirdId)
  await detailReady
  await http({ baseURL: '/api', url: `orders/reservations/${thirdId}/cancel/`, method: 'post', data: { reason: '临时有事' }, token: input.bossToken })
  socketEvents.get('reservation_status_update')({reservation_id: thirdId})
  releaseDetail()
  await openingDetail
  await settle(() => page.detail.value?.status === 'CANCELLED')
  assert.equal(page.rows.value.find(row => row.id === thirdId).status, 'CANCELLED')
  await page.openCreate()
  Object.assign(page.form, { customer_account_id: input.bossId, provider_account_id: input.providerId,
    service_id: input.serviceId, start_time: page.reservationDate(input.fourthStart), end_time: page.reservationDate(input.fourthEnd), game_rounds: 1 })
  dropCreateResponse = true
  await page.create()
  assert.equal(page.createUncertain.value, true, 'committed request with lost response must be marked uncertain')
  const createsBefore = calls.filter(row => row.path === '/api/orders/reservations/' && row.method === 'post').length
  await page.create()
  assert.equal(calls.filter(row => row.path === '/api/orders/reservations/' && row.method === 'post').length, createsBefore, 'do not repeat from the uncertain form')
  page.reviewCreated()
  await settle(() => page.rows.value.some(row => Date.parse(row.start_time) === Date.parse(input.fourthStart)))
  assert.equal(page.createOpen.value, false)
  assert.equal(page.status.value, '', 'review must include reservations in any status')
  console.log(JSON.stringify({ id, orderId, calls: calls.length }))
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 }).finally(() => { unmounted.forEach(fn => fn()); scope.stop() })
