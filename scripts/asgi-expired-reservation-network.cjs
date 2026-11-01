// An actual management command publishes through Redis to another Daphne process.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const { execFile } = require('node:child_process')
const { promisify } = require('node:util')
const exec = promisify(execFile)
const input = JSON.parse(fs.readFileSync(0, 'utf8'))
const base = `http://127.0.0.1:${input.httpPort}`
const events = input.tokens.map(() => []), sockets = []
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function connect(token, index) {
  const socket = new WebSocket(`ws://127.0.0.1:${input.socketPort}/ws/orders/?token=${encodeURIComponent(token)}`)
  sockets.push(socket)
  socket.addEventListener('message', event => events[index].push(JSON.parse(event.data)))
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', () => reject(new Error('WebSocket connection failed')), { once: true })
  })
}
async function get(index, path) {
  const response = await fetch(base + path, { headers: { Authorization: 'Bearer ' + input.tokens[index] } })
  const result = await response.json()
  assert.equal(result.code, 0, JSON.stringify(result))
  return result.data
}
;(async () => {
  await Promise.all(input.tokens.map(connect))
  const before = await get(2, '/api/admin/dashboard/stats')
  assert.deepEqual([before.pending_reservation_total, before.confirmed_reservation_total], [1, 1])
  const wait = Date.parse(input.deadline) - Date.now() + 150
  if (wait > 0) await delay(wait)
  // This is the real scheduled-scan command in a separate process, not an API read.
  await exec(input.python, ['manage.py', 'expire_reservations'], { cwd: input.backend, env: process.env, timeout: 15000 })
  for (let i = 0; i < 200; i++) {
    if ([0, 1, 2].every(index => input.ids.every(id => events[index].some(e => e.type === 'reservation_status_update' && e.data.reservation_id === id)))) break
    await delay(25)
  }
  for (const index of [0, 1, 2]) {
    for (const id of input.ids) {
      assert.ok(events[index].some(e => e.type === 'reservation_status_update' && e.data.reservation_id === id), `missing event for role ${index} reservation ${id}`)
      const detail = await get(index, `/api/orders/reservations/${id}/`)
      assert.equal(detail.status, 'EXPIRED')
      assert.ok(!detail.allowed_actions.some(action => ['confirm', 'convert'].includes(action)))
    }
  }
  const after = await get(2, '/api/admin/dashboard/stats')
  assert.deepEqual([after.pending_reservation_total, after.confirmed_reservation_total], [0, 0])
  await delay(200)
  assert.equal(events[3].filter(e => e.type === 'reservation_status_update').length, 0)
  console.log('real Redis network: both expired reservations reached all three roles and workbench counts cleared')
})().catch(error => { console.error(error.stack || error); process.exitCode = 1 }).finally(() => sockets.forEach(socket => socket.close()))
