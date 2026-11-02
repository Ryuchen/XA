const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const path = require('node:path')
const { createRequire } = require('node:module')
const requireAdmin = createRequire(path.resolve('apps/admin-web/package.json'))
const ts = requireAdmin('typescript')

function setup() {
  const file = fs.readFileSync('apps/admin-web/src/layout/index.vue', 'utf8')
  const source = file.match(/<script setup lang="ts">([\s\S]*?)<\/script>/)[1]
    + '\nexport { canDispatch, staffQueueLabel, openStaffQueue }\n'
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText
  const auth = { token: 'token', permissions: [], hasPerm(value) { return this.permissions.includes(value) } }
  const queue = { status: 'idle', count: null }
  const navigations = []
  const router = { push: target => { navigations.push(target); return Promise.resolve() }, getRoutes: () => [] }
  const route = { meta: {}, fullPath: '/', path: '/' }
  const vue = { computed: fn => ({ get value() { return fn() } }), ref: value => ({ value }),
    watch: () => {}, onMounted: () => {}, onBeforeUnmount: () => {} }
  const mocks = {
    vue, 'vue-router': { useRoute: () => route, useRouter: () => router },
    'element-plus': { ElMessageBox: {} }, '@element-plus/icons-vue': { Search: {} },
    '@/stores/auth': { useAuthStore: () => auth }, '@/stores/staffDispatchQueue': { useStaffDispatchQueue: () => queue },
    '@/router/navigation': { buildNavigation: () => [], flattenNavigation: () => [], searchNavigation: () => [] },
  }
  const module = { exports: {} }
  vm.runInNewContext(js, { module, exports: module.exports, require: name => {
    if (!(name in mocks)) throw new Error('Unexpected import ' + name)
    return mocks[name]
  }, window: { innerWidth: 1280 }, localStorage: { getItem: () => null },
  document: { documentElement: { classList: { toggle() {} } } } })
  return { ...module.exports, auth, queue, navigations, file }
}

test('staff queue entry requires both permissions and opens the server-filtered queue', () => {
  const s = setup()
  assert.match(s.file, /v-if="canDispatch" class="ap-staff-queue"/)
  assert.match(s.file, /role="status" aria-atomic="true"/)
  for (const permissions of [[], ['order:view'], ['order:dispatch']]) {
    s.auth.permissions = permissions
    assert.equal(s.canDispatch.value, false)
    s.openStaffQueue()
  }
  assert.equal(s.navigations.length, 0)
  s.auth.permissions = ['order:view', 'order:dispatch']
  assert.equal(s.canDispatch.value, true)
  s.openStaffQueue()
  assert.deepEqual(JSON.parse(JSON.stringify(s.navigations[0])), { name: 'orders', query: { queue: 'staff' } })
  s.auth.token = ''
  assert.equal(s.canDispatch.value, false)
})

test('loading, zero, positive and failed queue reads are distinct', () => {
  const s = setup()
  assert.equal(s.staffQueueLabel.value, '客服待派 · 更新中')
  s.queue.status = 'ready'; s.queue.count = 0
  assert.equal(s.staffQueueLabel.value, '客服待派 0 单')
  s.queue.count = 3
  assert.equal(s.staffQueueLabel.value, '客服待派 3 单')
  s.queue.status = 'error'; s.queue.count = null
  assert.equal(s.staffQueueLabel.value, '客服待派 · 查询失败')
  s.queue.status = 'review'; s.auth.token = 'token'; s.auth.permissions = ['order:view', 'order:dispatch']
  assert.equal(s.staffQueueLabel.value, '客服待派 · 部分待核对')
  s.openStaffQueue()
  assert.deepEqual(JSON.parse(JSON.stringify(s.navigations.at(-1))), { name: 'orders', query: { queue: 'staff_review' } })
})
