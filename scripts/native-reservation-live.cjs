// Actual native controllers against the isolated reservation and order APIs.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map(), navigations = [], toasts = [];
let transport = Promise.resolve();
let dropReservationResponse = false, droppedReservation = null, reservationPosts = 0;
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  getStorageInfoSync: () => ({keys: [...storage.keys()]}), removeStorageSync: key => storage.delete(key),
  getAccountInfoSync: () => ({miniProgram: {envVersion: 'develop'}}),
  showToast: value => toasts.push(value.title), showModal: value => value.success({confirm: true, content: ''}),
  navigateTo: value => navigations.push(value.url), redirectTo: value => navigations.push(value.url),
  switchTab: value => navigations.push(value.url), stopPullDownRefresh() {},
  request(options) {
    transport = transport.then(async () => {
      try {
        const url = new URL(options.url);
        const response = await fetch(input.base + url.pathname + url.search, {method: options.method, headers: options.header,
          ...(options.data === undefined ? {} : {body: JSON.stringify(options.data)})});
        const data = await response.json();
        if (url.pathname === '/api/orders/reservations/' && options.method === 'POST') {
          reservationPosts++;
          if (dropReservationResponse && data.code === 0) {
            dropReservationResponse = false; droppedReservation = data.data;
            options.fail({errMsg: 'request:fail response lost'}); return;
          }
        }
        options.success({statusCode: response.status, data});
      } catch (error) { options.fail(error); }
    });
  },
};
let definition;
global.Page = value => { definition = value; };
function page(name, options) {
  const source = path.join(root, 'pages', name, 'index.js');
  delete require.cache[require.resolve(source)];
  require(source);
  const result = {...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); }};
  for (const [key, value] of Object.entries(result)) if (typeof value === 'function') result[key] = value.bind(result);
  result.onLoad(options); result._visible = true;
  return result;
}
async function providerConfirm(id) {
  const response = await fetch(input.base + '/api/orders/reservations/' + id + '/confirm/', {
    method: 'POST', headers: {Authorization: 'Bearer ' + input.providerToken, 'Content-Type': 'application/json'}, body: '{}',
  });
  const body = await response.json(); assert.equal(body.code, 0, JSON.stringify(body));
}
async function main() {
  require(path.join(root, 'core/auth')).setSession(input.session);
  if (input.listMode) {
    const listing = page('reservations', {}); await listing.load();
    assert.equal(listing.data.items.length, 20); assert.ok(listing.data.nextCursor);
    const response = await fetch(input.base + '/api/orders/reservations/', { method:'POST',
      headers:{Authorization:'Bearer ' + input.session.token,'Content-Type':'application/json'}, body:JSON.stringify(input.newReservation) });
    const created = await response.json(); assert.equal(created.code, 0, JSON.stringify(created));
    await listing.more(); assert.equal(listing.data.items.length, input.initialIds.length);
    assert.deepEqual(new Set(listing.data.items.map(row => row.id)), new Set(input.initialIds));
    await listing.load();
    assert.equal(listing.data.items.length, input.initialIds.length + 1);
    assert.equal(new Set(listing.data.items.map(row => row.id)).size, input.initialIds.length + 1);
    assert.ok(listing.data.items.some(row => row.id === created.data.id));
    listing.onHide(); listing.onShow(); await transport; await new Promise(resolve => setImmediate(resolve));
    assert.equal(listing.data.items.length, input.initialIds.length + 1);
    listing.onUnload(); console.log('Boss: actual reservation pagination survives new bookings and refresh'); return;
  }
  const create = page('reservation-create', {service_id: input.serviceId, provider_id: input.providerId});
  await create.load(); assert.equal(create.data.error, '');
  const beijing = new Date(Date.now() + (24 + 8) * 3600000).toISOString().slice(0, 10);
  create.setData({day: beijing, time: '10:00', rounds: '1', region: '本地区服', nickname: '预约老板'});
  await create.submit();
  const match = /reservation-detail\/index\?id=(\d+)/.exec(navigations.at(-1) || '');
  assert.ok(match, JSON.stringify(toasts)); const id = Number(match[1]);
  const detail = page('reservation-detail', {id}); await detail.load();
  assert.equal(detail.data.reservation.status, 'PENDING');
  assert.equal(detail.data.reservation.canConvert, false);
  const listing = page('reservations', {}); await listing.load();
  assert.ok(listing.data.items.some(row => row.id === id));
  const cancelForm = page('reservation-create', {service_id: input.serviceId, provider_id: input.providerId});
  await cancelForm.load(); cancelForm.setData({day: beijing, time: '12:00'}); await cancelForm.submit();
  const cancelledMatch = /reservation-detail\/index\?id=(\d+)/.exec(navigations.at(-1) || '');
  assert.ok(cancelledMatch, JSON.stringify(toasts));
  const cancelled = page('reservation-detail', {id: Number(cancelledMatch[1])}); await cancelled.load();
  assert.equal(cancelled.data.reservation.canCancel, true);
  await cancelled.cancel(); assert.equal(cancelled.data.reservation.status, 'CANCELLED');
  assert.equal(cancelled.data.reservation.canCancel, false);
  const uncertain = page('reservation-create', {service_id: input.serviceId, provider_id: input.providerId});
  await uncertain.load(); uncertain.setData({day: beijing, time: '14:00'});
  dropReservationResponse = true;
  await uncertain.submit();
  assert.equal(uncertain.data.uncertain, true, 'lost response leaves a persistent review prompt');
  assert.ok(droppedReservation && droppedReservation.id);
  assert.match(uncertain.data.uncertainTime, /14:00/);
  uncertain.onUnload();
  const reopened = page('reservation-create', {service_id: input.serviceId, provider_id: input.providerId});
  await reopened.load();
  assert.equal(reopened.data.uncertain, true, 'reopening the page retains the unresolved reservation');
  assert.match(reopened.data.uncertainTime, /14:00/);
  const postsBefore = reservationPosts;
  await reopened.submit();
  assert.equal(reservationPosts, postsBefore, 'uncertain form must not repeat the write');
  reopened.list(); assert.equal(navigations.at(-1), '/pages/reservations/index');
  await listing.load();
  assert.equal(listing.data.items.find(row => row.id === droppedReservation.id).status, 'PENDING');
  await providerConfirm(droppedReservation.id);
  await listing.load();
  assert.equal(listing.data.items.find(row => row.id === droppedReservation.id).status, 'CONFIRMED');
  reopened.reviewed(); assert.equal(reopened.data.uncertain, false);
  reopened.onUnload();
  const checked = page('reservation-create', {service_id: input.serviceId, provider_id: input.providerId});
  await checked.load(); assert.equal(checked.data.uncertain, false, 'explicit review clears the saved warning');
  await providerConfirm(id); await detail.load(); assert.equal(detail.data.reservation.canConvert, true);
  await detail.quote(); assert.equal(detail.data.quote.amount, 10000); assert.ok(detail.data.quote.service);
  if (input.adminToken) {
    const changed = await fetch(input.base + '/api/admin/service-items/' + input.serviceId + '/', {
      method: 'PATCH', headers: {Authorization: 'Bearer ' + input.adminToken, 'Content-Type': 'application/json'},
      body: JSON.stringify({price: 12000}),
    });
    const body = await changed.json(); assert.equal(body.code, 0, JSON.stringify(body));
    const before = navigations.length;
    await detail.convert();
    assert.equal(navigations.length, before, 'outdated price must not create an order');
    assert.equal(detail.data.reservation.status, 'CONFIRMED');
    assert.equal(detail.data.quote, null, 'outdated quote must not remain confirmable');
    assert.ok(detail.data.quoteError, 'explain why a fresh quote is needed');
    await detail.quote(); assert.equal(detail.data.quote.amount, 12000);
  }
  await detail.convert(); assert.match(navigations.at(-1), /^\/pages\/order-detail\/index\?id=\d+$/);
  const orderId = Number(/id=(\d+)$/.exec(navigations.at(-1))[1]);
  const order = page('order-detail', {id: orderId}); await order.load();
  assert.equal(order.data.order.reservationOrigin.id, id);
  assert.match(order.data.order.reservationOrigin.startText, /10:00$/);
  order.reservation(); assert.equal(navigations.at(-1), '/pages/reservation-detail/index?id=' + id);
  assert.equal(detail.data.reservation.status, 'CONVERTED');
  create.onUnload(); cancelForm.onUnload(); checked.onUnload(); cancelled.onUnload(); detail.onUnload(); listing.onUnload(); order.onUnload();
  console.log('Real reservation -> confirmation -> quote -> balance order through native controllers');
}
main().catch(error => { console.error(error); process.exitCode = 1; });
