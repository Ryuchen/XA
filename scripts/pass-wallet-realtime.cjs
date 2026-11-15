const fs = require('node:fs'), assert = require('node:assert/strict')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
async function timeout(promise, label) { let timer; try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(Error(label)), 5000) })]) } finally { clearTimeout(timer) } }
;(async () => {
  const socket = new WebSocket(input.wsBase.replace(/^http/, 'ws') + '/ws/orders/?token=' + encodeURIComponent(input.token))
  let connected, updated
  const ready = new Promise(resolve => { connected = resolve }), update = new Promise(resolve => { updated = resolve })
  socket.addEventListener('message', event => { const message = JSON.parse(String(event.data)); if (message.type === 'pong') connected(); if (message.type === 'wallet_update' && String(message.data.account_id) === String(input.id)) updated() })
  const headers = {Authorization:'Bearer ' + input.token,'Content-Type':'application/json'}
  try {
    socket.addEventListener('open', () => socket.send('{"type":"ping"}'), {once:true}); await timeout(ready, 'socket connection failed')
    const initial = await (await fetch(input.base + '/api/users/provider-pass/', {headers})).json()
    assert.equal(initial.code, 0); const product = initial.data.products.find(row => row.tier === 'BRONZE'); assert.ok(product)
    const body = JSON.stringify({tier:product.tier,days:1,expected_daily_price:product.daily_price,client_request_id:'pass-wallet-purchase'})
    const result = await (await fetch(input.base + '/api/users/provider-pass/', {method:'POST',headers,body})).json()
    assert.equal(result.code, 0, JSON.stringify(result)); await timeout(update, 'wallet update missing after pass purchase')
    const wallet = await (await fetch(input.base + '/api/wallet/info/', {headers})).json()
    assert.equal(wallet.code, 0); assert.equal(wallet.data.balance, 100000 - product.daily_price)
    const replay = await (await fetch(input.base + '/api/users/provider-pass/', {method:'POST',headers,body})).json()
    assert.ok([0,409].includes(replay.code), JSON.stringify(replay))
  } finally { socket.close() }
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
