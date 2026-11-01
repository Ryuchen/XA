// Use real HTTP and WebSockets across two isolated Daphne processes and Redis.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const base = `http://127.0.0.1:${input.httpPort}`
const events = input.tokens.map(() => [])
const sockets = []
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))

async function connect(index) {
  const socket = new WebSocket(`ws://127.0.0.1:${input.socketPort}/ws/orders/?token=${encodeURIComponent(input.tokens[index])}`)
  sockets[index] = socket
  socket.addEventListener('message', event => events[index].push(JSON.parse(event.data)))
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', () => reject(new Error('WebSocket connection failed')), { once: true })
  })
}
async function http(index, path, method = 'GET', body) {
  const response = await fetch(base + path, { method, headers: { Authorization: 'Bearer ' + input.tokens[index],
    'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) })
  const result = await response.json()
  assert.equal(result.code, 0, JSON.stringify(result))
  return result.data
}
async function eventFor(index, type, predicate = () => true) {
  for (let attempt = 0; attempt < 120; attempt++) {
    const pos = events[index].findIndex(event => event.type === type && predicate(event.data))
    if (pos >= 0) return events[index].splice(pos, 1)[0]
    await delay(25)
  }
  throw new Error(`event ${type} did not reach actor ${index}; received ${JSON.stringify(events[index])}`)
}
async function deliverTo(indices, id) {
  await Promise.all(indices.map(index => eventFor(index, 'chat_message', data => data.message.id === id)))
}
;(async () => {
  await Promise.all(input.tokens.map((_, index) => connect(index)))
  const session = (await http(0, '/api/chat/session/')).session
  const first = await http(0, '/api/chat/send/', 'POST', { content: '隔离测试：咨询订单' })
  await deliverTo([0, 1, 2], first.id)
  let firstList = await http(1, '/api/admin/chat-sessions/?only_unread=1')
  assert.equal(firstList.list.find(row => row.id === session.id).unread_support, 1)
  await http(1, `/api/admin/chat-sessions/${session.id}/read/`, 'POST', { through_id: first.id })
  // Socket is an invalidation signal; the staff-visible unread value comes from HTTP.
  await eventFor(2, 'chat_session_update', data => data.session_id === session.id)
  firstList = await http(2, '/api/admin/chat-sessions/?only_unread=1')
  assert.ok(!firstList.list.some(row => row.id === session.id))

  sockets[0].close()
  await new Promise(resolve => sockets[0].addEventListener('close', resolve, { once: true }))
  const reply = await http(1, `/api/admin/chat-sessions/${session.id}/reply/`, 'POST', { content: '隔离测试：客服回复', read_through_id: first.id })
  await deliverTo([1, 2], reply.id)
  await connect(0)
  const recovered = await http(0, '/api/chat/session/')
  assert.deepEqual(recovered.messages.map(item => item.id), [first.id, reply.id])
  assert.equal(recovered.session.unread_user, 1)

  const reply2 = await http(2, `/api/admin/chat-sessions/${session.id}/reply/`, 'POST', { content: '隔离测试：另一客服补充', read_through_id: first.id })
  await deliverTo([0, 1, 2], reply2.id)
  await http(0, '/api/chat/read/', 'POST', { through_id: reply.id })
  const remaining = await http(0, '/api/chat/session/')
  assert.equal(remaining.session.unread_user, 1)
  assert.equal(remaining.messages.find(row => row.id === reply2.id).is_read, false)
  // The shared broadcast contains no reader capability. Each socket consumer
  // must issue an image URL bound to its own authenticated account.
  const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEklEQVR4nGMUbulhYGBgYgADAA1VASfFeWKjAAAAAElFTkSuQmCC', 'base64')
  const form = new FormData()
  form.append('image', new Blob([image], {type:'image/png'}), 'isolated.png')
  const imageResponse = await fetch(base + '/api/chat/send/', {method:'POST', headers:{Authorization:'Bearer ' + input.tokens[0]}, body:form})
  const savedImage = await imageResponse.json()
  assert.equal(savedImage.code, 0, JSON.stringify(savedImage))
  const imageId = savedImage.data.id
  const incomingImage = await eventFor(1, 'chat_message', data => data.message.id === imageId)
  assert.equal(incomingImage.data.message.content_type, 'IMAGE')
  const eventUrl = incomingImage.data.message.image_url
  assert.ok(eventUrl.includes('/api/media/private/'), 'staff receives a private image URL over the socket')
  assert.equal((await fetch(new URL(eventUrl, base))).status, 200)
  const bossEvent = await eventFor(0, 'chat_message', data => data.message.id === imageId)
  const secondStaffEvent = await eventFor(2, 'chat_message', data => data.message.id === imageId)
  assert.notEqual(bossEvent.data.message.image_url, eventUrl, 'socket capabilities must be issued per account')
  assert.notEqual(secondStaffEvent.data.message.image_url, eventUrl, 'each staff member gets their own capability')
  assert.equal((await fetch(new URL(bossEvent.data.message.image_url, base))).status, 200)
  assert.equal((await fetch(new URL(secondStaffEvent.data.message.image_url, base))).status, 200)
  const staffMessages = await http(1, `/api/admin/chat-sessions/${session.id}/messages/?cursor=1`)
  const staffImage = staffMessages.list.find(message => message.id === imageId)
  assert.ok(staffImage.image_url.includes('/api/media/private/'), 'staff receives a fresh private image URL via HTTP')
  assert.equal((await fetch(staffImage.image_url)).status, 200)
  const bossImage = (await http(0, '/api/chat/session/')).messages.find(message => message.id === imageId)
  assert.ok(bossImage.image_url.includes('/api/media/private/'))
  assert.equal((await fetch(bossImage.image_url)).status, 200)
  await delay(250)
  assert.equal(events[3].filter(event => event.type === 'chat_message' || event.type === 'chat_session_update').length, 0, 'unrelated boss received chat')
  assert.equal(events[4].filter(event => event.type === 'chat_message' || event.type === 'chat_session_update').length, 0, 'staff without chat:view received chat')
  console.log('real Redis chat: boss, two staff, unread, disconnect and HTTP recovery verified')
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 })
  .finally(() => sockets.forEach(socket => socket.close()))
