/** Current React controllers over real Django HTTP fixtures. No browser driver. */
import assert from 'node:assert/strict';
import { business as ui, clients, imageForm, intercept, login, lost, raw, readInput,
  rows, sessions, setBase, shared } from './fronted-test-runtime.mjs';

const image = 'iVBORw0KGgoAAAANSUhEUgAAAAMAAAADCAIAAADZSiLoAAAAE0lEQVR4nGNkYPjPAAZMEAqFBQAbXQEF2cdRlAAAAABJRU5ErkJggg==';
const key = () => 'react-test-' + crypto.randomUUID();
const reserved = value => value?.items || rows(value);
const details = (client, id) => client.get(`/orders/reservations/${id}/`);
const mutation = (client, path, data, read, method = 'POST') => ui.mutateAndReload({
  role: client === clients.admin ? 'admin' : client === clients.boss ? 'boss' : 'provider',
  mutate: () => client.request(path, { method, data }), reload: read, isCurrent: () => true,
});
const bodyOf = value => typeof value === 'string' ? value : JSON.stringify(value);
async function until(check) {
  const deadline = Date.now() + 5000;
  while (!check()) {
    assert.ok(Date.now() < deadline, 'the current React controller did not publish the expected server state');
    await new Promise(resolve => setTimeout(resolve, 10));
  }
}

async function reservations(input) {
  const client = login(input.token);
  const list = query => client.get('/orders/reservations/', query);
  if (input.listMode) {
    const first = await list({ cursor: 1, page_size: 20 });
    assert.equal(first.items.length, 20);
    const created = await raw(input.bossToken, '/orders/reservations/', 'POST', input.newReservation);
    const next = await list({ cursor: 1, page_size: 20, before_time: first.next_cursor.start_time, before_id: first.next_cursor.id });
    const loaded = [...first.items, ...next.items];
    assert.deepEqual(new Set(loaded.map(row => row.id)), new Set(input.initialIds));
    const refreshed = await list({ page_size: 40 });
    assert.equal(new Set(refreshed.items.map(row => row.id)).size, input.initialIds.length + 1);
    assert.ok(refreshed.items.some(row => row.id === created.id));
    console.log('Provider React reservation pagination preserves all loaded records'); return;
  }
  if (input.expiredMode) {
    const current = await details(client, input.firstId);
    assert.equal(current.status, 'EXPIRED');
    assert.equal(current.allowed_actions?.includes('confirm') || false, false);
    console.log('Expired reservation remains read-only'); return;
  }
  if (input.accountSwitchMode) {
    const pending = [], held = [];
    login(input.secondToken); const secondId = input.secondId || reserved(await list())[0].id;
    for (const [token, id] of [[input.token, input.firstId], [input.secondToken, secondId]]) {
      login(token); const trap = intercept(url => url.pathname === `/api/orders/reservations/${id}/confirm/`, { hold: true });
      pending.push(client.post(`/orders/reservations/${id}/confirm/`, {})); held.push(trap); await trap.received;
    }
    held[0].release(); await assert.rejects(pending[0], error => error.code === 'SESSION_CHANGED');
    held[1].release(); await pending[1];
    assert.equal((await details(client, secondId)).status, 'CONFIRMED');
    console.log('Reservation confirmations remain scoped while accounts switch'); return;
  }
  if (input.staleActionMode || input.eventMode) {
    await raw(input.bossToken, `/orders/reservations/${input.firstId}/cancel/`, 'POST', { reason: '老板改期' });
    await mutation(client, `/orders/reservations/${input.firstId}/reject/`, { reject_reason: '时间不合适', reason: '时间不合适' }, () => details(client, input.firstId));
    assert.equal((await details(client, input.firstId)).status, 'CANCELLED');
    if (input.eventMode) {
      await raw(input.bossToken, `/orders/reservations/${input.listId}/cancel/`, 'POST', { reason: '调整时间' });
      assert.equal((await details(client, input.listId)).status, 'CANCELLED');
    }
    console.log('Reservation stale actions reload the actual counterparty state'); return;
  }
  await lost(client, `/orders/reservations/${input.firstId}/confirm/`, {}, () => list());
  assert.equal((await details(client, input.firstId)).status, 'CONFIRMED');
  const secondId = input.secondId || reserved(await list()).find(row => row.id !== input.firstId).id;
  await lost(client, `/orders/reservations/${input.firstId}/cancel/`, { reason: '临时有事' }, () => list());
  await lost(client, `/orders/reservations/${secondId}/reject/`, { reason: '时间不合适' }, () => list());
  assert.equal((await details(client, secondId)).status, 'REJECTED');
  console.log('Provider confirmed and rejected real reservations through the React recovery controller');
}

async function adminAftersale(input) {
  const client = login(input.staffToken, 'admin');
  const read = () => client.get(`/admin/orders/${input.orderId}/`);
  const preview = await client.get(`/admin/orders/${input.orderId}/refund-preview/`, { refund_amount: 2500 });
  await lost(client, `/admin/aftersales/${input.ticketId}/refund/`, { refund_amount: 2500,
    resolution: '隔离场景协商部分退款', plan_token: preview.plan_token, client_request_id: key() }, read, 'POST', 'admin');
  for (const token of [input.bossToken, input.providerToken])
    assert.equal((await raw(token, `/orders/orders/${input.orderId}/`)).refund_summary.amount, 2500);
  assert.equal((await raw(input.providerToken, '/wallet/transactions/')).balance, 6000);
  await lost(client, `/admin/aftersales/${input.secondTicket}/resolve/`,
    { resolution: '已与老板核对问题', client_request_id: key() }, () => client.get(`/admin/orders/${input.secondOrder}/`), 'POST', 'admin');
  assert.equal((await raw(input.bossToken, `/orders/orders/${input.secondOrder}/`)).aftersale_status, 'RESOLVED');
  await raw(input.bossToken, `/orders/orders/${input.secondOrder}/aftersale/`, 'POST',
    { reason: '再次申请核对', client_request_id: 'admin-history-second-case' });
  console.log('real HTTP response-loss refund and resolution verified with current React recovery');
}

async function transfer(input) {
  const client = login(input.staffToken, 'admin');
  await lost(client, `/admin/orders/${input.order}/transfer/`, { transfers: [
    { old_provider_id: input.old, new_provider_id: input.incoming, split_type: 'PERCENT', split_value: 40, penalty_amount: 0 }],
    client_request_id: key() }, () => client.get(`/admin/orders/${input.order}/`), 'POST', 'admin');
  const old = await raw(input.oldToken, `/orders/orders/${input.order}/`);
  const incoming = await raw(input.newToken, `/orders/orders/${input.order}/`);
  const boss = await raw(input.bossToken, `/orders/orders/${input.order}/`);
  assert.equal(old.my_assignment_released, true); assert.equal(incoming.my_assignment_released, false);
  assert.ok(boss.participants.some(row => row.id === input.incoming));
  assert.ok(!boss.participants.some(row => row.id === input.old));
  assert.equal(ui.providerOrderActions(old).includes('COMPLETE'), false);
  console.log('real handoff response-loss recovery verified with current React recovery');
}

async function expired(input) {
  const client = login(input.session?.token || input.token, 'boss');
  const current = await details(client, input.id); assert.equal(current.status, 'EXPIRED');
  const target = ui.reservationRebookTarget(current);
  assert.ok(target.includes(String(input.providerId)) && target.includes(String(input.serviceId)));
  const start = new Date(Date.now() + 2 * 86400000); start.setUTCHours(2, 0, 0, 0);
  const payload = { provider_account_id: input.providerId, service_id: input.serviceId,
    start_time: start.toISOString(), end_time: new Date(+start + 3600000).toISOString(), game_rounds: 1 };
  const first = await client.post('/orders/reservations/', payload); assert.notEqual(first.id, input.id);
  await assert.rejects(client.post('/orders/reservations/', payload), /预约|时间/);
  payload.start_time = new Date(+start + 7200000).toISOString(); payload.end_time = new Date(+start + 10800000).toISOString();
  await client.post('/orders/reservations/', payload);
  await assert.rejects(client.get(`/orders/services/${input.unavailableServiceId}/`));
  await raw(input.staffToken, `/admin/service-items/${input.changingServiceId}/`, 'PATCH', { is_active: false });
  await assert.rejects(client.post('/orders/reservations/', { ...payload, service_id: input.changingServiceId }), /服务/);
  console.log('Expired reservation → current React preselected rebook → real new reservation');
}

async function invitation(input) {
  const client = login(input.partnerToken || input.token);
  await client.post('/users/escorts/status/', { status: 'OFFLINE' });
  const oldMessage = await client.get(`/messages/${input.oldMessage}/`);
  assert.ok(ui.providerMessageDestination(oldMessage).includes(String(input.order)));
  const all = await client.get(`/orders/orders/${input.order}/applications/mine/`);
  assert.equal(all.applications.length, 2);
  const firstPath = `/orders/orders/${input.order}/applications/${input.firstApplication}`;
  assert.equal((await client.get(firstPath + '/mine/')).can_confirm, false);
  await client.post(firstPath + '/action/', { action: 'WITHDRAW' });
  const exact = `/orders/orders/${input.order}/applications/${input.secondApplication}`;
  const exactMessage = await client.get(`/messages/${input.newMessage}/`);
  assert.ok(ui.providerMessageDestination(exactMessage).includes(String(input.secondApplication)));
  await client.post('/users/escorts/status/', { status: 'AVAILABLE' });
  const ids = [];
  for (let i = 0; i < 3; i++) {
    const created = await raw(input.bossToken, '/orders/create/', 'POST', { service_id: input.singleService,
      dispatch_mode: 'AUTO_GRAB', support_contact_id: input.support, client_request_id: 'ui-capacity-' + i });
    await client.post(`/orders/orders/${created.id}/grab/`, {}); ids.push(created.id);
  }
  assert.equal((await client.get(exact + '/mine/')).can_confirm, false);
  for (const [action, field] of [['start', 'entry_image'], ['complete', 'completion_image']])
    await client.upload(`/orders/orders/${ids[0]}/${action}/`, imageForm(input.image, field));
  assert.equal((await client.get(exact + '/mine/')).can_confirm, true);
  await client.post(exact + '/action/', { action: 'CONFIRM_TEAM' });
  await raw(input.bossToken, exact + '/select/', 'POST', {});
  console.log(JSON.stringify({ legacyChoices: 2, withdrawn: input.firstApplication, confirmed: input.secondApplication, selected: input.order }));
}

async function messageList(input) {
  const client = login(input.firstToken);
  const old = await client.get('/messages/', { page: 4, page_size: 20 });
  assert.ok(rows(old).some(row => row.id === input.oldest));
  const held = intercept(url => url.pathname === `/api/messages/${input.oldest}/`, { hold: true });
  const pending = client.get(`/messages/${input.oldest}/`); await held.received;
  login(input.secondToken); held.release(); await assert.rejects(pending, error => error.code === 'SESSION_CHANGED');
  assert.ok(rows(await client.get('/messages/')).every(row => row.title === '另一账号消息'));
  login(input.firstToken);
  let visible = true, snapshot = [], reads = 0;
  const refresh = ui.createRefreshCoordinator({ delay: 0, isVisible: () => visible,
    types: () => ['site_message_update'], reload: async () => {
      const pages = [];
      for (let page = 1; page <= 4; page++) pages.push(...rows(await client.get('/messages/', { page, page_size: 20 })));
      snapshot = pages; reads++;
    } });
  const push = title => raw(input.staffToken, '/admin/messages/push/', 'POST', { title,
    audience: 'CUSTOM', recipient_account_ids: [input.firstAccount] });
  const has = title => snapshot.some(row => row.title === title);
  await push('React实时消息'); refresh.event('site_message_update'); await until(() => has('React实时消息'));
  const liveRefresh = has('React实时消息') && snapshot.some(row => row.id === input.oldest);
  visible = false; const before = reads;
  await push('离线期间的新消息'); assert.equal(refresh.event('site_message_update'), false);
  await new Promise(resolve => setTimeout(resolve, 20)); assert.equal(reads, before);
  visible = true; refresh.resume(); await until(() => has('离线期间的新消息'));
  const resumeRefresh = has('离线期间的新消息');
  await push('断线期间的新消息'); refresh.event('connected'); await until(() => has('断线期间的新消息'));
  const reconnectRefresh = has('断线期间的新消息'); refresh.dispose();
  await client.post('/messages/read-all/', {});
  const readAll = (await client.get('/messages/')).unread === 0;
  assert.ok(liveRefresh && resumeRefresh && reconnectRefresh && readAll);
  console.log(JSON.stringify({ accountIsolation: true, detailAccountIsolation: true, readAll: true,
    liveRefresh, resumeRefresh, reconnectRefresh }));
}

async function chatRecovery(input, staff = false) {
  const token = staff ? input.tokens[0] : input.sessions[0].token;
  const client = login(token, staff ? 'admin' : 'boss');
  if (!staff) {
    verifyChatPersistence(input.sessions[1].token);
    console.log('PASS scoped React chat persistence');
  }
  const session = staff ? input.sessions[0] : (await client.get('/chat/session/')).session.id;
  const sendPath = staff ? `/admin/chat-sessions/${session}/reply/` : '/chat/send/';
  const statusPath = staff ? `/admin/chat-sessions/${session}/message-status/` : '/chat/message-status/';
  const read = () => client.get(staff ? `/admin/chat-sessions/${session}/` : '/chat/session/');
  const contents = staff ? ['隔离真实回复'] : ['首次消息', '存储不可用', '重试消息'];
  for (const content of contents) {
    const id = key(); const payload = { content, client_message_id: id };
    if (staff) await lost(client, sendPath, payload, read, 'POST', 'admin');
    else {
      intercept(url => url.pathname === '/api/chat/send/', { lose: true });
      const outcome = await ui.mutateAndReload({ role: 'boss',
        mutate: () => ui.deliverChatMessage('boss', { id, content, kind: 'text' }), reload: read });
      assert.ok(outcome.error); assert.equal(outcome.reloadError, undefined);
    }
    const status = staff ? await client.get(statusPath, { client_message_id: id }) : await ui.lookupChatMessage('boss', { id, content, kind: 'text' });
    assert.equal(status.status, 'FOUND');
    if (staff) await client.post(sendPath, payload);
    else await ui.deliverChatMessage('boss', { id, content, kind: 'text' });
    await assert.rejects(client.post(sendPath, { ...payload, content: '不同内容' }), error => error.status === 409);
    const outsider = staff ? input.tokens[1] : input.sessions[1].token;
    assert.equal((await raw(outsider, statusPath + '?client_message_id=' + id)).status, 'UNKNOWN');
  }
  for (let index = 0; index < (staff ? 1 : 2); index++) {
    const id = key(); const make = () => imageForm(input.image || image, 'image', { client_message_id: id });
    await lost(client, sendPath, make(), read, 'POST', staff ? 'admin' : 'boss');
    assert.equal((await client.get(statusPath, { client_message_id: id })).status, 'FOUND');
    await client.upload(sendPath, make());
  }
  if (!staff) await client.post(sendPath, { content: '旧客户端' });
  console.log(staff ? 'PASS staff reply recovery using current React request/session controller' : 'Current React chat text/image recovery preserves exact receipts');
}

function verifyChatPersistence(otherToken, role = 'boss') {
  const owner = sessions.getSession(role), originalSearch = location.search;
  const intent = { id: key(), kind: 'image', content: '仅限本门店的待核对正文',
    draft: '本账号原始草稿', scope: 'ORD00000000000000000001', attempted: true,
    image: new File(['private image pixels'], 'private.png'), base64: 'data:image/png;base64,PRIVATE_BYTES' };
  const saved = ui.saveChatPending(role, intent); assert.equal(saved.saved, true);
  const storageKey = ui.chatPendingKey(role), serialized = localStorage.getItem(storageKey);
  assert.ok(serialized.includes(intent.content));
  assert.ok(!serialized.includes('PRIVATE_BYTES') && !serialized.includes('private image pixels'));
  assert.equal(Object.hasOwn(JSON.parse(serialized), 'image'), false);
  assert.equal(Object.hasOwn(JSON.parse(serialized), 'base64'), false);
  assert.equal(ui.readChatPending(role).id, intent.id);
  if (otherToken) login(otherToken, role);
  else sessions.persistSession(role, { token: 'local-state-fixture', user: { ...owner.user, id: String(owner.user.id) + '-other' } });
  assert.equal(ui.readChatPending(role), null, 'another account cannot recover this draft');
  location.search = '?store=persistence-isolation-branch';
  // This is a local state unit fixture: equal account IDs in a different store.
  // No request is sent with these synthetic session credentials.
  sessions.persistSession(role, { token: 'local-state-fixture', user: { id: owner.user.id, role: role === 'boss' ? 'customer' : 'provider' } });
  assert.equal(ui.readChatPending(role), null, 'an equal ID in another store cannot recover the body');
  location.search = originalSearch;
  sessions.persistSession(role, { token: owner.token, user: owner.user });
  assert.equal(ui.readChatPending(role).content, intent.content);
  const originalSet = localStorage.setItem;
  try {
    localStorage.setItem = () => { throw new Error('isolated quota failure'); };
    const outcome = ui.saveChatPending(role, { ...intent, id: key(), content: '不能假称已保存' });
    assert.equal(outcome.saved, false); assert.ok(outcome.error instanceof Error);
  } finally { localStorage.setItem = originalSet; }
  assert.equal(ui.readChatPending(role).id, intent.id, 'a failed write cannot replace the recoverable intent');
  ui.clearChatPending(role, 'different-request-id'); assert.equal(ui.readChatPending(role).id, intent.id);
  assert.equal(ui.clearChatPending(role, intent.id).saved, true);
  assert.equal(ui.readChatPending(role), null);
  localStorage.setItem(storageKey, '{broken pending receipt');
  const blocked = ui.readChatPending(role);
  assert.equal(blocked.blocked, true, 'a corrupt receipt cannot silently enable another send');
  assert.match(blocked.content, /核对/);
  assert.equal(ui.clearChatPending(role).saved, true);
}

async function staffVisibility(input) {
  const client = login(input.firstStaffToken, 'admin');
  const state = { hidden: true, focused: false };
  await raw(input.bossToken, '/chat/send/', 'POST', { content: '隐藏页未读消息', client_message_id: key() });
  const before = await raw(input.secondStaffToken, '/admin/chat-sessions/?only_unread=1'); assert.equal(before.total, 1);
  assert.equal(ui.mayMarkMessagesRead(state), false);
  const messages = await client.get(`/admin/chat-sessions/${input.sessionId}/messages/`);
  assert.equal((await raw(input.secondStaffToken, '/admin/chat-sessions/?only_unread=1')).total, 1);
  state.hidden = false; state.focused = true; assert.equal(ui.mayMarkMessagesRead(state), true);
  await client.post(`/admin/chat-sessions/${input.sessionId}/read/`, { through_id: Math.max(...rows(messages).map(row => row.id)) });
  assert.equal((await raw(input.secondStaffToken, '/admin/chat-sessions/?only_unread=1')).total, 0);
  await client.post(`/admin/chat-sessions/${input.sessionId}/reply/`, { content: '客服回复', client_message_id: key() });
  assert.ok((await raw(input.bossToken, '/chat/session/')).messages.some(row => row.content === '客服回复'));
  console.log('PASS real HTTP staff visibility with current React read gate');
}

async function providerChat(input) {
  const client = login(input.providerToken);
  verifyChatPersistence(null, 'provider');
  let first = await client.get('/chat/session/');
  assert.ok(first.messages.some(row => row.content === '历史客服消息 50'));
  const earliest = Math.min(...first.messages.map(row => row.id));
  const previous = await client.get('/chat/session/', { before_id: earliest });
  assert.ok(previous.messages.some(row => row.content === '历史客服消息 0'));
  await client.post('/chat/send/', { content: '陪玩咨询排班', client_message_id: key() });
  await raw(input.staffToken, `/admin/chat-sessions/${input.sessionId}/reply/`, 'POST', { content: '客服已收到，稍后核对', client_message_id: key() });
  for (const content of ['网络中断后核对', '网络中断后重试']) {
    const id = key(); await lost(client, '/chat/send/', { content, client_message_id: id }, () => client.get('/chat/session/'));
    assert.equal((await client.get('/chat/message-status/', { client_message_id: id })).status, 'FOUND');
    await client.post('/chat/send/', { content, client_message_id: id });
  }
  const latest = await client.get('/chat/session/');
  await client.post('/chat/read/', { through_id: Math.max(...latest.messages.map(row => row.id)) });
  console.log('Provider page and staff exchanged real messages; current React history and receipt recovery passed');
}

async function adminReservations(input) {
  const client = login(input.staffToken, 'admin');
  const create = (start, end) => client.post('/orders/reservations/', {
    customer_account_id: input.bossId, provider_account_id: input.providerId,
    service_id: input.serviceId, start_time: start, end_time: end, game_rounds: 1,
  });
  const first = await create(input.start, input.end);
  assert.equal(Date.parse(first.start_time), Date.parse(input.start));
  const second = await create(input.secondStart, input.secondEnd);
  const viewer = await raw(input.viewerToken, `/orders/reservations/${second.id}/`);
  assert.deepEqual(viewer.allowed_actions, []);
  await assert.rejects(raw(input.viewerToken, `/orders/reservations/${second.id}/cancel/`, 'POST', {}));
  const cancellable = await raw(input.cancelToken, `/orders/reservations/${second.id}/`);
  assert.deepEqual(cancellable.allowed_actions, ['cancel']);
  await raw(input.cancelToken, `/orders/reservations/${second.id}/cancel/`, 'POST', {});
  await raw(input.providerToken, `/orders/reservations/${first.id}/confirm/`, 'POST', {});
  const quote = await client.get(`/orders/reservations/${first.id}/quote/`);
  assert.equal(quote.amount, 10000); assert.ok(quote.service);
  const converted = await shared.fundRequest('admin', `/orders/reservations/${first.id}/convert/`, {
    expected_amount: quote.amount, expected_service: quote.service,
  });
  assert.equal((await details(client, first.id)).status, 'CONVERTED');
  const third = await create(input.thirdStart, input.thirdEnd);
  const gate = intercept(url => url.pathname === `/api/orders/reservations/${third.id}/`, { hold: true });
  const read = details(client, third.id); await gate.received;
  await raw(input.bossToken, `/orders/reservations/${third.id}/cancel/`, 'POST', { reason: '临时有事' });
  gate.release(); await read;
  assert.equal((await details(client, third.id)).status, 'CANCELLED');
  await lost(client, '/orders/reservations/', { customer_account_id: input.bossId,
    provider_account_id: input.providerId, service_id: input.serviceId,
    start_time: input.fourthStart, end_time: input.fourthEnd, game_rounds: 1 },
    () => client.get('/orders/reservations/'), 'POST', 'admin');
  console.log(JSON.stringify({ id: first.id, orderId: converted.order_id || converted.id,
    currentReact: true, quoteReviewed: true, unknownCreateReconciled: true }));
}

async function bossJourney(input) {
  const client = login(input.session.token, 'boss');
  const message = await client.get(`/messages/${input.messageId}/`);
  assert.equal(message.is_read, true);
  assert.equal(ui.bossMessageDestination(message), '/service?id=' + input.serviceId);
  assert.equal((await client.get(`/announcements/${input.announcementId}/`)).title, '联动公告');
  const payload = { service_id: input.serviceId, game_region: '微信区', game_nickname: '联动测试',
    game_uid: '10001', dispatch_mode: input.autoGrab ? 'AUTO_GRAB' : 'BOSS_SELECT' };
  const quote = await client.post('/orders/quote/', payload); assert.equal(quote.amount, 1250);
  const loss = intercept((url, options) => url.pathname === '/api/orders/create/' && options.method === 'POST', { lose: true });
  await assert.rejects(shared.fundRequest('boss', '/orders/create/', payload)); await loss.received;
  const recovered = await shared.fundRequest('boss', '/orders/create/', payload);
  const firstId = recovered.id;
  assert.equal((await client.get('/wallet/info/')).balance, 8750);
  await lost(client, `/orders/orders/${firstId}/cancel/`, { client_request_id: key() },
    () => client.get(`/orders/orders/${firstId}/`), 'POST', 'boss');
  assert.equal((await client.get(`/orders/orders/${firstId}/`)).status, 'CANCELLED');
  assert.equal((await client.get('/wallet/info/')).balance, 10000);
  const order = await shared.fundRequest('boss', '/orders/create/', { ...payload, game_uid: '10002' });
  await raw(input.providerToken, `/orders/orders/${order.id}/grab/`, 'POST', {});
  if (!input.autoGrab) {
    const current = await client.get(`/orders/orders/${order.id}/`);
    assert.equal(current.applications.length, 1);
    await client.post(`/orders/orders/${order.id}/applications/${current.applications[0].id}/select/`, {});
  } else await assert.rejects(client.post(`/orders/orders/${order.id}/cancel/`, { client_request_id: key() }), /接单/);
  await raw(input.providerToken, '/orders/network-report/', 'POST', { samples: 3, failures: 0, average_ms: 120, jitter_ms: 30 });
  const metrics = await raw(input.adminToken, '/admin/order-traffic/'); assert.equal(metrics.metrics.network_reports, 1);
  for (const [action, field] of [['start', 'entry_image'], ['complete', 'completion_image']])
    await raw(input.providerToken, `/orders/orders/${order.id}/${action}/`, 'POST', imageForm(input.evidence, field));
  const report = rows(await raw(input.providerToken, '/wallet/reports/')).find(row => row.order_id === order.id);
  await raw(input.providerToken, `/wallet/reports/${report.id}/images/`, 'POST', imageForm(input.evidence, 'image', { kind: 'RESULT' }));
  await raw(input.providerToken, `/wallet/reports/${report.id}/submit/`, 'POST', {});
  await raw(input.adminToken, `/admin/reports/${report.id}/approve/`, 'POST', {});
  assert.equal((await raw(input.providerToken, '/wallet/info/')).balance, 0);
  await lost(client, '/orders/evaluate/', { order_id: order.id, content: '真实联动验收评价', score: 5 },
    () => client.get(`/orders/orders/${order.id}/`), 'POST', 'boss');
  assert.equal((await client.get(`/orders/orders/${order.id}/`)).is_evaluated, true);
  await ui.deliverChatMessage('boss', { id: key(), content: 'React页面真实 HTTP 联动测试', kind: 'text' });
  assert.ok((await client.get('/chat/session/')).messages.some(row => row.content === 'React页面真实 HTTP 联动测试'));
  const before = sessions.getSession('boss').user.id;
  const invite = await client.get('/audition/info', { token: input.auditionToken, role: 'boss' });
  assert.equal(invite.title, '原生试音活动');
  await assert.rejects(client.post('/audition/exchange', { token: input.auditionToken, role: 'boss' }), error => error.status === 403);
  assert.equal(sessions.getSession('boss').user.id, before, 'a transferable invitation must never switch the WeChat identity');
  console.log('Current React → real Django HTTP: purchase, recovery, refund, grab, fulfilment, report, evaluation, chat and WeChat-only invitation boundary passed');
}

async function reservationNotice(input) {
  const client = login(input.token);
  const message = await client.get(`/messages/${input.message}/`);
  const destination = ui.providerMessageDestination(message);
  assert.match(destination, new RegExp(`reservations.*id=${input.reservation}`));
  const reservation = await details(client, input.reservation);
  assert.equal(reservation.id, input.reservation);
  console.log('Current React reservation message opens its actual reservation');
}

function bossPresentation(scenario, input) {
  const order = input.order || input;
  const view = ui.bossOrderPresentation(order);
  if (scenario === 'native-order-refund-live') {
    assert.equal(order.status, input.statusText === '已完成' ? 'COMPLETED' : 'CANCELLED');
    assert.equal(view.refundAmount, input.refunded);
    assert.equal(view.refundRemaining, input.remaining);
    assert.equal(view.refundRecords.length, input.count);
  } else if (scenario === 'native-coupon-refund-live') {
    assert.equal(view.couponOutcomes.length, 1);
    assert.equal(view.couponOutcomes[0].name, input.name);
    assert.match(view.couponOutcomes[0].label, input.outcome === 'RETURNED' ? /已返还.*我的券/ : /已核销，不返还/);
    if (input.zeroPayment) assert.equal(view.zeroPaymentCancelled, true);
    if (input.replenishmentText) assert.equal(view.refundRecords[0].replenishment?.text || view.refundRecords[0].replenishment_text, input.replenishmentText);
  } else {
    assert.equal(order.status, 'IN_SERVICE'); assert.equal(order.participants.length, 2);
    assert.ok(view.teamProgressNeedsReview || order.team_progress, 'legacy teams have real progress or a clear staff-review path');
  }
  console.log('Current React order presentation agrees with the real API response');
}

export async function runScenario(scenario, input = null) {
  input ||= await readInput(); if (input.base) setBase(input.base);
  if (['native-order-refund-live', 'native-coupon-refund-live', 'native-legacy-team-progress'].includes(scenario)) return bossPresentation(scenario, input);
  if (scenario === 'provider-reservations-live') return reservations(input);
  if (scenario === 'admin-aftersale-lost-live') return adminAftersale(input);
  if (scenario === 'admin-transfer-lost-live') return transfer(input);
  if (scenario === 'native-expired-reservation-live') return expired(input);
  if (scenario === 'provider-invitation-live') return invitation(input);
  if (scenario === 'provider-messages-live') return messageList(input);
  if (scenario === 'native-chat-send-recovery-live') return chatRecovery(input);
  if (scenario === 'admin-chat-reply-recovery-live') return chatRecovery(input, true);
  if (scenario === 'admin-chat-visibility-live') return staffVisibility(input);
  if (scenario === 'provider-chat-live') return providerChat(input);
  if (scenario === 'admin-reservation-live') return adminReservations(input);
  if (scenario === 'native-live-driver') return bossJourney(input);
  if (scenario === 'provider-reservation-notice-live') return reservationNotice(input);
  throw new Error('Current React HTTP scenario has not been implemented: ' + scenario);
}
