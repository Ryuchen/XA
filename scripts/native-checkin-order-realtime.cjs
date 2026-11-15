// Native page controller with genuine HTTP and WebSocket; only the wx host is substituted.
const fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict')
const input = JSON.parse(fs.readFileSync(0,'utf8')), root = path.resolve('apps/boss-miniapp/native'), storage = new Map()
let connected, heldModal; const ready = new Promise(resolve => { connected=resolve })
global.wx = {
  getStorageSync:key => storage.get(key), setStorageSync:(key,value) => storage.set(key,value),
  removeStorageSync:key => storage.delete(key), getStorageInfoSync:() => ({keys:[...storage.keys()]}),
  getAccountInfoSync:() => ({miniProgram:{envVersion:'develop'}}), showToast() {}, showModal(options) { heldModal=options },
  request(options) { const url=new URL(options.url); fetch(input.base+url.pathname+url.search,{method:options.method,headers:options.header,
    ...(options.data===undefined?{}:{body:JSON.stringify(options.data)})}).then(async response => options.success({statusCode:response.status,data:await response.json()})).catch(options.fail) },
  connectSocket(options) { const target=new URL(options.url), socket=new WebSocket(input.wsBase.replace(/^http/,'ws')+target.pathname+target.search)
    return {onOpen:handler => socket.addEventListener('open',handler),onMessage:handler => socket.addEventListener('message',event => {if(String(event.data).includes('pong')) connected(); handler({data:event.data})}),
      onClose:handler => socket.addEventListener('close',handler),onError:handler => socket.addEventListener('error',handler),send:value => socket.send(value.data),close:() => socket.close()} },
}
const auth=require(path.join(root,'core/auth'))
const session=(token,id) => ({token,user:{id:String(id),role:'customer'}})
auth.setSession(session(input.token,input.bossId))
let definition; global.Page=value => {definition=value}; require(path.join(root,'pages/checkin/index.js'))
const page={...definition,data:structuredClone(definition.data),setData(patch){Object.assign(this.data,patch)}}
const realtime=require(path.join(root,'core/realtime'))
async function waitFor(predicate,label){const deadline=Date.now()+10000; while(Date.now()<deadline){if(predicate()) return; await new Promise(resolve => setTimeout(resolve,50))} throw Error(label+': '+JSON.stringify(page.data))}
;(async () => {
  try {
    page.onLoad(); page.onShow(); realtime.show(); await Promise.race([ready,new Promise((_,reject) => setTimeout(() => reject(Error('socket timeout')),10000))])
    await waitFor(() => !!page.data.calendar && !page.data.loading,'initial calendar')
    assert.equal(page.data.calendar.today_eligible,false)
    const adminHeaders={Authorization:'Bearer '+input.staffToken,'Content-Type':'application/json'}
    const updateRule=async amount => {
      const result=await (await fetch(input.base+'/api/admin/config/checkin',{method:'PUT',headers:adminHeaders,body:JSON.stringify({daily_spend_required:amount,makeup_card_spend_required:amount})})).json()
      assert.equal(result.code,0,JSON.stringify(result))
    }
    await updateRule(19000)
    await waitFor(() => page.data.calendar?.daily_spend_required === 19000 && !page.data.loading,'calendar did not update after rule change')
    const updatedGift=await (await fetch(input.base+`/api/admin/checkin-gifts/${input.giftConfigId}/`,{method:'PATCH',headers:adminHeaders,
      body:JSON.stringify({name:'实时签到礼',reward_amount:50})})).json()
    assert.equal(updatedGift.code,0,JSON.stringify(updatedGift))
    await waitFor(() => page.data.calendar?.rewards?.[0]?.name === '实时签到礼' && page.data.calendar.rewards[0].amount === 50 && !page.data.loading,'calendar did not update after gift change')
    await updateRule(18800)
    await waitFor(() => page.data.calendar?.daily_spend_required === 18800 && !page.data.loading,'calendar did not restore rule')
    const sendGift=async id => {
      const response=await fetch(input.base+'/api/orders/gifts/send/',{method:'POST',headers:{Authorization:'Bearer '+input.token,'Content-Type':'application/json'},
        body:JSON.stringify({gift_service_id:input.serviceId,quantity:1,provider_id:input.providerId,expected_amount:18800,client_request_id:id})})
      const result=await response.json(); assert.equal(result.code,0,JSON.stringify(result)); return result.data
    }
    const gift=await sendGift('native-checkin-gift')
    await waitFor(() => page.data.calendar?.today_eligible && !page.data.loading,'calendar did not update after purchase')
    if (page.data.calendar.makeup_available_days.length) {
      assert.equal(page.data.calendar.makeup_cards,1)
      const pending=page.makeup({currentTarget:{dataset:{day:page.data.calendar.makeup_available_days[0]}}})
      await waitFor(() => !!heldModal,'makeup confirmation did not open')
      await updateRule(18000)
      await waitFor(() => page._checkinPending,'configuration update not queued during confirmation')
      heldModal.success({confirm:false}); await pending
      await waitFor(() => page.data.calendar?.daily_spend_required === 18000 && !page.data.loading,'calendar did not update after cancelled confirmation')
      await updateRule(18800)
      await waitFor(() => page.data.calendar?.daily_spend_required === 18800 && !page.data.loading,'calendar did not restore final rule')
      heldModal=null
      const oldConfirmation=page.makeup({currentTarget:{dataset:{day:page.data.calendar.makeup_available_days[0]}}})
      await waitFor(() => !!heldModal,'old account confirmation did not open')
      auth.setSession(session(input.otherToken,input.otherId))
      await waitFor(() => !!page.data.calendar && page.data.calendar.today_eligible && page.data.calendar.makeup_cards === 1 && !page.data.loading,'other account calendar')
      heldModal.success({confirm:true}); await oldConfirmation
      const otherCalendar=await (await fetch(input.base+'/api/users/checkin/',{headers:{Authorization:'Bearer '+input.otherToken}})).json()
      assert.equal(otherCalendar.code,0); assert.equal(otherCalendar.data.checked_count,0,'old account confirmation must not sign in the new account')
      assert.equal(otherCalendar.data.makeup_cards,1)
      auth.setSession(session(input.token,input.bossId))
      await waitFor(() => !!page.data.calendar && page.data.calendar.today_eligible && page.data.calendar.makeup_cards === 1 && !page.data.loading,'original account calendar')
      heldModal=null
      const confirmed=page.makeup({currentTarget:{dataset:{day:page.data.calendar.makeup_available_days[0]}}})
      await waitFor(() => !!heldModal,'makeup confirmation did not reopen')
      heldModal.success({confirm:true}); await confirmed
      assert.equal(page.data.calendar.checked_count,1); assert.equal(page.data.calendar.makeup_cards,0)
    }
    const preview=await (await fetch(input.base+`/api/admin/orders/${gift.id}/refund-preview/`,{headers:adminHeaders})).json()
    assert.equal(preview.code,0,JSON.stringify(preview))
    const refund=await (await fetch(input.base+`/api/admin/orders/${gift.id}/refund/`,{method:'POST',headers:adminHeaders,
      body:JSON.stringify({reason:'隔离签到退款',plan_token:preview.data.plan_token,client_request_id:'native-checkin-refund'})})).json()
    assert.equal(refund.code,0,JSON.stringify(refund))
    await waitFor(() => page.data.calendar?.today_eligible === false && !page.data.loading,'calendar did not update after refund')
    await sendGift('native-checkin-gift-again')
    await waitFor(() => page.data.calendar?.today_eligible && !page.data.loading,'calendar did not update after repurchase')
    await page.checkin(); assert.equal(page.data.calendar.today_checked,true)
  } finally { realtime.hide(); page.onUnload() }
})().catch(error => { console.error(error.stack||error); process.exitCode=1 })
