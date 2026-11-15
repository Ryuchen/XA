const fs = require('node:fs'), assert = require('node:assert/strict')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
async function timeout(promise, label) { let timer; try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(Error(label)), 6000) })]) } finally { clearTimeout(timer) } }
;(async () => {
  const socket = new WebSocket(input.wsBase.replace(/^http/, 'ws') + '/ws/orders/?token=' + encodeURIComponent(input.providerToken))
  let connected, updated
  const ready = new Promise(resolve => { connected = resolve }), update = new Promise(resolve => { updated = resolve })
  socket.addEventListener('message', event => {
    const message = JSON.parse(String(event.data))
    if (message.type === 'pong') connected()
    if (message.type === 'wallet_update' && String(message.data.account_id) === String(input.account)) updated()
  })
  try {
    socket.addEventListener('open', () => socket.send('{"type":"ping"}'), { once: true })
    await timeout(ready, 'socket connection failed')
    const response = await fetch(input.base + `/api/admin/escorts/${input.profile}/deposit-topups/`, {
      method: 'POST', headers: { Authorization: 'Bearer ' + input.staffToken, 'Content-Type': 'application/json' },
      body: JSON.stringify({ amount: 20000, reason: '隔离实时补差', reference: 'TEST-CASH-REALTIME', client_request_id: 'cash-realtime-test' }),
    })
    const result = await response.json()
    assert.equal(result.code, 0, JSON.stringify(result))
    await timeout(update, 'provider wallet update missing')
    const overview = await (await fetch(input.base + '/api/wallet/deposit/', { headers: { Authorization: 'Bearer ' + input.providerToken } })).json()
    assert.equal(overview.code, 0); assert.equal(overview.data.deposit_paid, 40000); assert.equal(overview.data.balance, 0)
    console.log('Offline deposit updated the correct provider without wallet credit')
  } finally { socket.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
