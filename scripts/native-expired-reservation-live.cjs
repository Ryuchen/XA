// Run the native reservation controllers against the isolated real HTTP API.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const root = path.resolve(__dirname, '../apps/boss-miniapp/native');
const storage = new Map(), navigations = [], toasts = [];
let transport = Promise.resolve();
global.wx = {
  getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
  getStorageInfoSync: () => ({keys: [...storage.keys()]}), removeStorageSync: key => storage.delete(key),
  getAccountInfoSync: () => ({miniProgram: {envVersion: 'develop'}}),
  showToast: value => toasts.push(value.title), showModal: value => value.success({confirm: true}),
  navigateTo: value => navigations.push(value.url), redirectTo: value => navigations.push(value.url),
  switchTab: value => navigations.push(value.url), stopPullDownRefresh() {},
  request(options) {
    transport = transport.then(async () => {
      try {
        const url = new URL(options.url);
        const response = await fetch(input.base + url.pathname + url.search, {method: options.method, headers: options.header,
          ...(options.data === undefined ? {} : {body: JSON.stringify(options.data)})});
        options.success({statusCode: response.status, data: await response.json()});
      } catch (error) { options.fail(error); }
    });
  },
};
let definition;
global.Page = value => { definition = value; };
function page(name, options) {
  const source = path.join(root, 'pages', name, 'index.js');
  delete require.cache[require.resolve(source)]; require(source);
  const result = {...definition, data: structuredClone(definition.data), setData(patch) { Object.assign(this.data, patch); }};
  for (const [key, value] of Object.entries(result)) if (typeof value === 'function') result[key] = value.bind(result);
  result.onLoad(options); result._visible = true;
  return result;
}
(async () => {
  require(path.join(root, 'core/auth')).setSession(input.session);
  const detail = page('reservation-detail', {id: input.id});
  await detail.load();
  assert.equal(detail.data.reservation.status, 'EXPIRED');
  assert.equal(detail.data.reservation.canConvert, false);
  assert.equal(detail.data.reservation.canCancel, false);
  detail.rebook();
  assert.equal(navigations.at(-1), `/pages/reservation-create/index?provider_id=${input.providerId}&service_id=${input.serviceId}`);
  const create = page('reservation-create', {provider_id: input.providerId, service_id: input.serviceId});
  await create.load(); assert.equal(create.data.error, '');
  const beijing = new Date(Date.now() + (48 + 8) * 3600000).toISOString().slice(0, 10);
  create.setData({day: beijing, time: '10:00', rounds: '1'});
  await create.submit();
  assert.match(navigations.at(-1), /^\/pages\/reservation-detail\/index\?id=\d+$/, JSON.stringify(toasts));
  assert.notEqual(Number(navigations.at(-1).split('=').at(-1)), input.id);
  const conflict = page('reservation-create', {provider_id: input.providerId, service_id: input.serviceId});
  await conflict.load(); conflict.setData({day: beijing, time: '10:00', rounds: '1'});
  const beforeConflict = navigations.length; await conflict.submit();
  assert.equal(navigations.length, beforeConflict);
  assert.match(conflict.data.submitError, /该时间段大神已有预约/);
  conflict.time({detail: {value: '12:00'}}); assert.equal(conflict.data.submitError, '');
  await conflict.submit();
  assert.match(navigations.at(-1), /^\/pages\/reservation-detail\/index\?id=\d+$/);
  const old = page('reservation-detail', {id: input.unavailableId});
  await old.load(); assert.equal(old.data.reservation.status, 'EXPIRED');
  old.rebook();
  assert.equal(navigations.at(-1), `/pages/reservation-create/index?provider_id=${input.providerId}&service_id=${input.unavailableServiceId}`);
  const unavailable = page('reservation-create', {provider_id: input.providerId, service_id: input.unavailableServiceId});
  await unavailable.load(); assert.ok(unavailable.data.error, 'unavailable service must show a real API error');
  unavailable.browse(); assert.equal(navigations.at(-1), '/pages/catalog/index');
  const changing = page('reservation-create', {provider_id: input.providerId, service_id: input.changingServiceId});
  await changing.load(); assert.equal(changing.data.error, '');
  const response = await fetch(`${input.base}/api/admin/service-items/${input.changingServiceId}/`, {
    method: 'PATCH', headers: { Authorization: `Bearer ${input.staffToken}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: false }),
  });
  const disabled = await response.json(); assert.equal(disabled.code, 0, JSON.stringify(disabled));
  changing.setData({day: beijing, time: '12:00', rounds: '1'});
  const before = navigations.length; await changing.submit();
  assert.equal(navigations.length, before);
  assert.match(changing.data.submitError, /服务不存在或不可用/);
  changing.browse(); assert.equal(navigations.at(-1), '/pages/catalog/index');
  detail.onUnload(); create.onUnload(); conflict.onUnload(); old.onUnload(); unavailable.onUnload(); changing.onUnload();
  console.log('Expired reservation → preselected rebook → new reservation passed over real HTTP');
})().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
