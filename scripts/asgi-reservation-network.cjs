// Real HTTP and native WebSocket against a disposable localhost Daphne process.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const base = `http://127.0.0.1:${input.httpPort || input.port}`
const sockets = [], events = input.tokens.map(() => [])
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function connect(token, index) {
  const socket = new WebSocket(`ws://127.0.0.1:${input.socketPort || input.port}/ws/orders/?token=${encodeURIComponent(token)}`)
  sockets.push(socket)
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
async function waitUpdate(id) {
  for (let attempt = 0; attempt < 100; attempt++) {
    if ([0, 1, 2].every(index => events[index].some(event => event.type === 'reservation_status_update' && event.data.reservation_id === id))) {
      for (const index of [0, 1, 2]) events[index] = []
      return
    }
    await delay(25)
  }
  throw new Error('reservation update did not reach all three roles')
}
;(async () => {
  await Promise.all(input.tokens.map(connect))
  const reservation = await http(0, '/api/orders/reservations/', 'POST', {
    provider_account_id: input.provider, service_id: input.service,
    start_time: input.start, end_time: input.end,
  })
  await waitUpdate(reservation.id)
  for (const index of [0, 1, 2]) assert.equal((await http(index, `/api/orders/reservations/${reservation.id}/`)).status, 'PENDING')
  await http(1, `/api/orders/reservations/${reservation.id}/confirm/`, 'POST', {})
  await waitUpdate(reservation.id)
  for (const index of [0, 1, 2]) assert.equal((await http(index, `/api/orders/reservations/${reservation.id}/`)).status, 'CONFIRMED')
  await http(0, `/api/orders/reservations/${reservation.id}/cancel/`, 'POST', { reason: '隔离测试档期变化' })
  await waitUpdate(reservation.id)
  for (const index of [0, 1, 2]) assert.equal((await http(index, `/api/orders/reservations/${reservation.id}/`)).status, 'CANCELLED')
  await delay(200)
  assert.equal(events[3].filter(event => event.type === 'reservation_status_update').length, 0, 'unrelated account received an update')
  console.log('real ASGI network: three roles received create, confirm and cancel updates')
})().catch(error => { console.error(error.message); process.exitCode = 1 }).finally(() => sockets.forEach(socket => socket.close()))
