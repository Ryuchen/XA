// Real isolated HTTP refund and authenticated WebSocket; no external messaging.
const fs = require('node:fs'), assert = require('node:assert/strict')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))

;(async () => {
  const socket = new WebSocket(input.wsBase.replace(/^http/, 'ws') + '/ws/orders/?token=' + encodeURIComponent(input.bossToken))
  let connected, notified
  const ready = new Promise((resolve, reject) => { connected = resolve; socket.addEventListener('error', () => reject(new Error('isolated WebSocket connection failed')), {once:true}) })
  const message = new Promise(resolve => { notified = resolve })
  socket.addEventListener('message', event => {
    const payload = JSON.parse(String(event.data))
    if (payload.type === 'pong') connected()
    // The account-scoped consumer intentionally omits the account ID from the frame.
    if (payload.type === 'site_message_update') notified()
  })
  const timeout = async (promise, label) => {
    let timer
    try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(new Error(label)), 15000) })]) }
    finally { clearTimeout(timer) }
  }
  try {
    socket.addEventListener('open', () => socket.send(JSON.stringify({type:'ping'})), {once:true})
    await timeout(ready, 'isolated WebSocket did not connect')
    const response = await fetch(input.base + `/api/admin/orders/${input.orderId}/refund/`, {
      method:'POST', headers:{Authorization:'Bearer ' + input.staffToken, 'Content-Type':'application/json'},
      body:JSON.stringify({reason:'隔离退款验收',plan_token:input.planToken,client_request_id:'account-gift-notice-refund'})
    })
    const result = await response.json()
    assert.equal(response.status, 200); assert.equal(result.code, 0, JSON.stringify(result))
    await timeout(message, 'boss realtime notice was not received')
    console.log('Real refund committed and authenticated boss received a message update')
  } finally { socket.close() }
})().catch(error => { console.error(error.message); process.exitCode = 1 })
