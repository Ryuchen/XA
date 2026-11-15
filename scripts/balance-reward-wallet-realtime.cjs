// Both identities are denied shop redemption; the boss can win a balance prize.
const fs = require('node:fs'), assert = require('node:assert/strict')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
async function timeout(promise, label) { let timer; try { return await Promise.race([promise, new Promise((_, reject) => { timer = setTimeout(() => reject(Error(label)), 5000) })]) } finally { clearTimeout(timer) } }
async function redeem(account, index) {
  const socket = new WebSocket(input.wsBase.replace(/^http/, 'ws') + '/ws/orders/?token=' + encodeURIComponent(account.token))
  let connected, updated
  const ready = new Promise(resolve => { connected = resolve }), update = new Promise(resolve => { updated = resolve })
  socket.addEventListener('message', event => { const message = JSON.parse(String(event.data)); if (message.type === 'pong') connected(); if (message.type === 'wallet_update' && String(message.data.account_id) === String(account.id)) updated() })
  try {
    socket.addEventListener('open', () => socket.send('{"type":"ping"}'), {once:true})
    await timeout(ready, 'socket connection failed')
    const body = JSON.stringify({reward_id:input.rewardId, client_request_id:`reward-wallet-${index}`})
    const options = {method:'POST',headers:{Authorization:'Bearer ' + account.token,'Content-Type':'application/json'},body}
    const denied = await (await fetch(input.base + '/api/club/redeem/', options)).json()
    assert.equal(denied.errorCode, 'REWARD_UNAVAILABLE', JSON.stringify(denied))
    if (index === 0) {
      const draw = { ...options, body: JSON.stringify({campaign_id:input.campaignId, version:input.version, count:1, client_request_id:'wallet-lottery-test'}) }
      const result = await (await fetch(input.base + '/api/club/lottery/', draw)).json()
      assert.equal(result.code, 0, JSON.stringify(result))
      await timeout(update, 'wallet update missing after lottery')
      const replay = await (await fetch(input.base + '/api/club/lottery/', draw)).json()
      assert.equal(replay.code, 0, JSON.stringify(replay)); assert.equal(replay.data.id, result.data.id)
    }
    const wallet = await (await fetch(input.base + '/api/wallet/info/', {headers:{Authorization:'Bearer ' + account.token}})).json()
    assert.equal(wallet.code, 0); assert.equal(wallet.data.balance, index === 0 ? 250 : 0)
  } finally { socket.close() }
}
;(async () => { for (let i = 0; i < input.accounts.length; i++) await redeem(input.accounts[i], i); console.log('Shop balance blocked; boss lottery wallet updated') })().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
