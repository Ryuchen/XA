import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { test } from 'node:test'

const source = fs.readFileSync(new URL('../../wechat-bridge/native/core/bridge.js', import.meta.url), 'utf8')
function bridge(wx: Record<string, unknown>) {
  const module = { exports: {} as any }
  vm.runInNewContext(source, { module, wx, require: () => ({ release: { apiBase: 'https://example.invalid/api' } }) })
  return module.exports
}
test('minimal WeChat bridge refuses wallet/business endpoints without transmitting', async () => {
  let requests = 0
  const api = bridge({ request: () => requests++ })
  await assert.rejects(api.request('/wallet/withdraw/', 'POST', {}), /无效的微信授权操作/)
  assert.equal(requests, 0)
})
test('minimal WeChat bridge fails closed when its environment is unknown', async () => {
  let requests = 0
  const api = bridge({ getAccountInfoSync: () => ({ miniProgram: { envVersion: 'unknown' } }), request: () => requests++ })
  await assert.rejects(api.request('/users/web-login/authorize/', 'POST', {}), /服务地址尚未配置/)
  assert.equal(requests, 0)
})
test('bridge transports only the explicit proof payload and preserves SMS cooldown errors', async () => {
  let last: any
  const api = bridge({ getAccountInfoSync: () => ({ miniProgram: { envVersion: 'release' } }), request: (options: any) => { last = options; options.success({ statusCode: 429, data: { code: 429, msg: '请稍后重试', retry_after: 60 } }) } })
  await assert.rejects(api.request('/users/web-login/sms/', 'POST', { ticket: 'test-ticket', bridge_token: 'test-proof' }), (error: any) => error.status === 429 && error.retry_after === 60)
  assert.equal(last.url, 'https://example.invalid/api/users/web-login/sms/')
  assert.deepEqual(Object.keys(last.header), ['Content-Type'])
  assert.deepEqual(last.data, { ticket: 'test-ticket', bridge_token: 'test-proof' })
})

test('bridge preserves both original and new-store scenes and rejects malformed requests', () => {
  let page: any
  const code = fs.readFileSync(new URL('../../wechat-bridge/native/pages/web-login/index.js', import.meta.url), 'utf8')
  vm.runInNewContext(code, { Page: (value: any) => { page = value }, require: () => ({}) })
  const open = (ticket: string) => {
    const instance = { ...page, data: { ...page.data }, setData(value: any) { Object.assign(this.data, value) } }
    instance.onLoad({ scene: ticket })
    return instance
  }
  assert.equal(open('f'.repeat(32))._ticket, 'f'.repeat(32))
  assert.equal(open('2_' + 'a'.repeat(22))._ticket, '2_' + 'a'.repeat(22))
  assert.match(open('../other-store').data.error, /登录请求无效/)
})
