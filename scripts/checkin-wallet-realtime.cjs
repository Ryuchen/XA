// Real check-in against a boss who has already completed a real gift purchase.
const fs = require('node:fs'), assert = require('node:assert/strict')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
async function timeout(promise,label) { let timer; try { return await Promise.race([promise,new Promise((_,reject) => { timer=setTimeout(() => reject(Error(label)),5000) })]) } finally { clearTimeout(timer) } }
;(async () => {
  const socket = new WebSocket(input.wsBase.replace(/^http/,'ws') + '/ws/orders/?token=' + encodeURIComponent(input.token))
  let connected, updated; const ready = new Promise(resolve => { connected=resolve }), update = new Promise(resolve => { updated=resolve })
  socket.addEventListener('message',event => { const message=JSON.parse(String(event.data)); if(message.type==='pong') connected(); if(message.type==='wallet_update' && String(message.data.account_id)===String(input.id)) updated() })
  const headers={Authorization:'Bearer '+input.token,'Content-Type':'application/json'}
  try {
    socket.addEventListener('open',() => socket.send('{"type":"ping"}'),{once:true}); await timeout(ready,'socket connection failed')
    const result=await (await fetch(input.base+'/api/users/checkin/',{method:'POST',headers,body:'{}'})).json()
    assert.equal(result.code,0,JSON.stringify(result)); assert.ok(result.data.reward_amount>0)
    await timeout(update,'wallet update missing after check-in')
    const wallet=await (await fetch(input.base+'/api/wallet/info/',{headers})).json()
    assert.equal(wallet.code,0); assert.equal(wallet.data.balance,1200+result.data.reward_amount)
    const replay=await (await fetch(input.base+'/api/users/checkin/',{method:'POST',headers,body:'{}'})).json()
    assert.equal(replay.code,400)
  } finally { socket.close() }
})().catch(error => { console.error(error.stack||error); process.exitCode=1 })
