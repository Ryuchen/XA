// Real provider deposit and staff refund through separate ASGI workers and Redis.
const fs = require('node:fs'), assert = require('node:assert/strict')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
async function timeout(promise, label) {
  let timer
  try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(Error(label)), 5000) })]) }
  finally { clearTimeout(timer) }
}
;(async () => {
  const socket = new WebSocket(input.wsBase.replace(/^http/, 'ws') + '/ws/orders/?token=' + encodeURIComponent(input.providerToken))
  let resolveReady, resolveUpdate
  const ready = new Promise(resolve => { resolveReady = resolve })
  socket.addEventListener('message', event => {
    const message = JSON.parse(String(event.data))
    if (message.type === 'pong') resolveReady()
    if (message.type === 'wallet_update' && String(message.data.account_id) === String(input.providerId)) resolveUpdate?.()
  })
  const postAndWait = async (path, token, body) => {
    const update = new Promise(resolve => { resolveUpdate = resolve })
    const response = await fetch(input.base + path, { method:'POST', headers:{ Authorization:'Bearer ' + token, 'Content-Type':'application/json' }, body:JSON.stringify(body) })
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result))
    await timeout(update, `wallet update missing after ${path}`); resolveUpdate = null
  }
  const snapshot = async (balance, paid) => {
    const response = await fetch(input.base + '/api/wallet/deposit/', { headers:{ Authorization:'Bearer ' + input.providerToken } })
    const result = await response.json(); assert.equal(result.code, 0, JSON.stringify(result))
    assert.equal(result.data.balance, balance); assert.equal(result.data.deposit_paid, paid)
  }
  try {
    socket.addEventListener('open', () => socket.send('{"type":"ping"}'), {once:true})
    await timeout(ready, 'provider websocket did not connect')
    await postAndWait('/api/wallet/deposit/', input.providerToken, {amount:2000,client_request_id:'deposit-live-pay'})
    await snapshot(3000, 2000)
    await postAndWait(`/api/admin/escorts/${input.profileId}/deposit-refund/`, input.staffToken,
      {amount:1000,reason:'隔离验收退还',client_request_id:'deposit-live-refund'})
    await snapshot(4000, 1000)
    console.log('Provider wallet received real deposit and refund updates')
  } finally { socket.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
