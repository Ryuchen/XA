/** Migrated historical scenarios: production React controllers and real HTTP.
 * Layout, touch targets and DOM interactions are accepted separately through CUA.
 */
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { business as ui, clients, imageForm, intercept, login, lost, raw, readInput,
  rows, sessions, setBase, shared, contractRealtime, adminControllers } from './fronted-test-runtime.mjs';

const key = () => 'react-migration-' + crypto.randomUUID();
const order = (client, id) => client.get(`/orders/orders/${id}/`);
const reports = (client, id) => client.get('/wallet/reports/', { order_id: id });
async function renderReport(client, orderId) {
  const data = await ui.loadProviderReportPage(1, { orderId: String(orderId), reportId: '' });
  const row = data.list.find(row => row.order_id === Number(orderId));
  assert.ok(row, `the current React report loader must locate order ${orderId}`);
  return { row, view: ui.providerReportPresentation(row) };
}
async function images(record) {
  const urls = [record.entry_image_url, record.completion_image_url, ...(record.result_image_urls || [])].filter(Boolean);
  assert.equal(urls.length, 3);
  for (const url of urls) {
    const response = await fetch(url);
    assert.equal(response.status, 200);
    assert.match(response.headers.get('content-type'), /^image\//);
    assert.ok((await response.arrayBuffer()).byteLength > 0);
  }
  const media = await import('../fronted/shared/src/media.ts');
  const recovery = media.createPrivateMediaRecovery('provider', urls[0]);
  const failedRenew = intercept(url => url.pathname === '/api/media/renew/', { lose: true });
  const failed = await recovery.recover();
  assert.equal((await failedRenew.received).body.code, 0);
  assert.equal(failed.failed, true);
  assert.equal((await recovery.recover()).failed, true, 'an image error remains visible until explicit retry');
  recovery.retry();
  const retried = await recovery.recover();
  assert.equal(retried.failed, false);
  assert.equal((await fetch(retried.url)).status, 200);
  const stale = media.createPrivateMediaRecovery('provider', urls[0]);
  const held = intercept(url => url.pathname === '/api/media/renew/', { hold: true });
  const pending = stale.recover();
  assert.equal((await held.received).body.code, 0);
  stale.dispose(); held.release();
  assert.equal((await pending).stale, true);
  recovery.dispose();
}

async function aftersale(input) {
  const client = login(input.token);
  let record = await order(client, input.orderId);
  let view = ui.providerOrderPresentation(record);
  if (input.ambiguous) {
    assert.equal(record.refund_summary.income_breakdown_unavailable, true);
    assert.equal(view.netIncome, null);
    assert.equal(record.refund_summary.recovered_amount, null);
    return;
  }
  if (input.inProgress) {
    assert.equal(record.status, 'IN_SERVICE');
    assert.equal(record.settled_at, null);
    assert.ok(view.actions.includes('COMPLETE'));
    return;
  }
  assert.equal(view.aftersaleOpen, true);
  const admin = login(input.staffToken, 'admin');
  const preview = await admin.get(`/admin/orders/${input.orderId}/refund-preview/`, input.partial ? { refund_amount: 2500 } : {});
  const result = await ui.mutateAndReload({ role: 'admin',
    mutate: () => admin.post(`/admin/aftersales/${input.caseId}/refund/`, {
      resolution: '隔离售后退款', plan_token: preview.plan_token, client_request_id: key(),
      ...(input.partial ? { refund_amount: 2500 } : {}),
    }), reload: () => order(client, input.orderId) });
  assert.equal(result.error, undefined);
  record = await order(client, input.orderId);
  view = ui.providerOrderPresentation(record);
  assert.equal(record.payment_status, input.partial ? 'PARTIALLY_REFUNDED' : 'REFUNDED');
  assert.equal(view.refundedAmount, input.partial ? 2500 : 10000);
  assert.equal(view.netIncome, input.partial ? 6000 : 0);
  assert.equal(view.displayIncome, input.partial ? 6000 : 0);
  assert.equal(view.incomeLabel, '服务收益流水净额');
  assert.equal(record.refund_summary.recovered_amount, input.partial ? 2000 : 0);
  const boss = await raw(input.bossToken, `/orders/orders/${input.orderId}/`);
  assert.equal(ui.bossOrderPresentation(boss).refundAmount, view.refundedAmount);
}

async function reportLabels(input) {
  const client = login(input.token);
  const { row, view } = await renderReport(client, input.orderId);
  assert.equal(row.status_display, input.statusText);
  assert.equal(view.expectedIncome, input.snapshot * 10);
  assert.equal(view.incomeLabel, '服务收益流水净额');
  const partial = input.netText.includes('600');
  assert.equal(view.netIncome, partial ? 6000 : 0);
  assert.equal(view.displayIncome, partial ? 6000 : 0);
  assert.equal(view.refundedAmount, partial ? 2500 : 10000);
  assert.equal(view.canEdit, false);
  await images(row);
}

async function settlementNotice(input) {
  const client = login(input.token);
  const message = await client.get(`/messages/${input.messageId}/`);
  assert.equal(message.preview, '凭证已通过，结算结果请查看钱包流水。');
  const destination = ui.providerMessageDestination(message);
  const focus = ui.providerReportFocus(new URLSearchParams(destination.split('?')[1]));
  assert.ok(focus.orderId);
  const { row, view } = await renderReport(client, focus.orderId);
  assert.equal(row.order_no, input.orderNo);
  assert.match(view.settlementNotice, /已结算.*钱包/);
  const wallet = await client.get('/wallet/withdraw/');
  assert.equal(wallet.frozen_amount, 3200);
}

async function refundReason(input) {
  const client = login(input.token);
  const record = await order(client, input.orderId);
  const view = ui.orderBusinessDetails(record, 'provider', input.id);
  if (input.expected) assert.equal(view.cancelReason, input.expected.replace(/^(退款|取消)原因：/, ''));
  else assert.equal(view.cancelReason, '');
  assert.equal(view.cancelReasonLabel, '取消/退款原因');
  assert.equal(record.payment_status, 'REFUNDED');
}

async function grabRetry(input) {
  const client = login(input.token);
  const payload = { team_size: 1, client_request_id: key() };
  await lost(client, `/orders/orders/${input.orderId}/grab/`, payload, () => order(client, input.orderId));
  if (input.changed) {
    await client.post(`/orders/orders/${input.orderId}/reject/`, { reason: '隔离回执延迟' });
    await raw(input.otherToken, `/orders/orders/${input.orderId}/grab/`, 'POST', { team_size: 1, client_request_id: key() });
    const historical = await order(client, input.orderId);
    assert.equal(historical.my_assignment_released, true);
    assert.equal(historical.my_assignment_departure, 'REJECTED');
    assert.deepEqual(ui.providerOrderActions(historical), []);
    const active = rows(await client.get('/orders/orders/', { status: 'grabbed' }));
    assert.ok(!active.some(row => row.id === input.orderId));
    assert.ok(rows(await client.get('/orders/orders/', { status: 'all' })).some(row => row.id === input.orderId));
  } else assert.ok(ui.providerOrderActions(await order(client, input.orderId)).includes('START'));
  for (const [token, path] of [[input.token, '/orders/orders/'], [input.bossToken, '/orders/orders/'], [input.staffToken, '/admin/orders/']])
    assert.equal((await raw(token, `${path}${input.orderId}/`)).status, 'GRABBED');
}

async function fulfillmentRetry(input) {
  const client = login(input.token);
  const before = await order(client, input.orderId);
  assert.ok(ui.providerOrderActions(before).includes(input.operation.toUpperCase()));
  const picture = (await readFile(input.image)).toString('base64');
  const data = imageForm(picture, input.operation === 'start' ? 'entry_image' : 'completion_image', { client_request_id: key() });
  await lost(client, `/orders/orders/${input.orderId}/${input.operation}/`, data,
    () => order(client, input.orderId), 'POST', 'provider', { truncate: input.partial });
  for (const [token, path] of [[input.token, '/orders/orders/'], [input.bossToken, '/orders/orders/'], [input.staffToken, '/admin/orders/']])
    assert.equal((await raw(token, `${path}${input.orderId}/`)).status, input.operation === 'start' ? 'IN_SERVICE' : 'COMPLETED');
}

async function batchImages(input) {
  const client = login(input.token);
  const files = await Promise.all(input.pictures.map(async path => new File([await readFile(path)], path.split(/[\\/]/).at(-1), { type: 'image/png' })));
  const read = () => renderReport(client, input.orderId);
  const upload = (selected, count = 0) => ui.uploadProviderReportImages({ reportId: input.reportId, kind: 'RESULT', files: selected,
    existingCount: count, reload: read });
  let result;
  if (input.scenario === 'limit') {
    result = await upload(files);
    assert.equal(result.uploaded, 9); assert.equal(result.skipped, 1); assert.equal(result.error, undefined);
  } else if (input.scenario === 'invalid') {
    const invalid = new File([await readFile(input.invalid)], 'invalid.png', { type: 'image/png' });
    result = await upload([files[0], invalid, files[1]]);
    assert.equal(result.uploaded, 1); assert.ok(result.error); assert.equal(result.error.code, 400);
    assert.equal((await read()).row.result_image_urls.length, 1);
    const corrected = await upload([files[1]], 1);
    assert.equal(corrected.error, undefined); assert.equal(corrected.uploaded, 1);
  } else {
    let writes = 0;
    const trap = intercept((url, options) => url.pathname === `/api/wallet/reports/${input.reportId}/images/` && options.method === 'POST' && ++writes === 2,
      { lose: input.scenario === 'lost', truncate: input.scenario === 'partial' });
    result = await upload(files.slice(0, 3));
    const committed = await trap.received;
    assert.equal(committed.body.code, 0); assert.equal(result.uploaded, 1); assert.ok(result.error);
    assert.equal(writes, 2, 'unknown upload stops later images and never resends automatically');
    assert.equal((await read()).row.result_image_urls.length, 2, 'the committed unknown upload is recovered by the real read');
    const remainder = await upload([files[2]], 2);
    assert.equal(remainder.error, undefined); assert.equal(remainder.uploaded, 1);
  }
  const count = input.scenario === 'limit' ? 9 : input.scenario === 'invalid' ? 2 : 3;
  const loaded = await read();
  assert.equal(loaded.row.result_image_urls.length, count);
  assert.equal(ui.providerReportPresentation(loaded.row).canSubmit, true);
  const submitted = await ui.mutateAndReload({ role: 'provider',
    mutate: () => client.post(`/wallet/reports/${input.reportId}/submit/`, {}), reload: read });
  assert.equal(submitted.error, undefined);
  const staff = await raw(input.staffToken, `/admin/reports/${input.reportId}/`);
  assert.equal(staff.result_image_urls.length, count);
  assert.equal((await raw(input.bossToken, `/orders/orders/${input.orderId}/`)).status, 'COMPLETED');
}

async function evaluationConflict(input) {
  login(input.providerToken);
  const read = () => ui.loadProviderEvaluations({ page: 1, evaluationId: input.reviewId });
  const original = (await read()).list[0]; assert.ok(original);
  const draft = '陪玩原有草稿';
  if (input.deleted) {
    await raw(input.staffToken, `/admin/evaluations/${input.reviewId}/`, 'DELETE', { reply_version: original.reply_version });
  } else if (!input.lost) {
    await raw(input.staffToken, `/admin/evaluations/${input.reviewId}/reply/`, 'POST', { reply_content: '客服先行回复', reply_version: original.reply_version });
  }
  const trap = input.lost ? intercept(url => url.pathname === `/api/orders/evaluations/${input.reviewId}/reply/`, { lose: true }) : null;
  let failure;
  try { await ui.submitProviderEvaluationReply(original, draft); }
  catch (error) { failure = error; }
  assert.ok(failure); assert.equal(ui.evaluationReplyFailure(failure).blocked, true);
  if (trap) assert.equal((await trap.received).body.code, 0);
  if (input.deleted) {
    assert.equal((await read()).list.length, 0);
    assert.equal(ui.evaluationLatestReply(null, draft).blocked, true);
    return;
  }
  if (!input.lost) {
    const failedRead = intercept(url => url.pathname === '/api/orders/evaluations/mine/', { lose: true });
    await assert.rejects(read()); await failedRead.received;
    assert.equal(draft, '陪玩原有草稿');
  }
  const latest = (await read()).list[0];
  const recovery = ui.evaluationLatestReply(latest, draft);
  assert.equal(recovery.blocked, false);
  if (input.lost) {
    assert.equal(latest.reply_content, draft); assert.equal(latest.reply_version, 1);
    assert.match(recovery.notice, /无需重复发布/);
  } else {
    assert.equal(latest.reply_content, '客服先行回复');
    assert.match(recovery.notice, /核对后决定/);
    await ui.submitProviderEvaluationReply(latest, draft);
    const chosen = (await read()).list[0];
    assert.equal(chosen.reply_content, draft); assert.equal(chosen.reply_version, 2);
  }
}

async function evaluationMessage(input) {
  const client = login(input.token);
  const message = await client.get(`/messages/${input.messageId}/`);
  const destination = ui.providerMessageDestination(message);
  assert.equal(destination, `/evaluations?order=${input.orderId}`);
  const focused = ui.evaluationFocus(new URLSearchParams(destination.split('?')[1]));
  assert.equal(focused, String(input.orderId));
  const read = orderId => ui.loadProviderEvaluations({ page: 1, orderId });
  const current = (await read(focused)).list;
  if (input.missing) {
    assert.equal(current.length, 0);
    assert.ok((await read('')).list.some(row => row.content === '另一笔订单评价'));
    return;
  }
  assert.equal(current.length, 1); assert.equal(current[0].content, '真实订单评价验证');
  assert.equal((await read(String(input.foreignOrderId))).list.length, 0);
  await ui.submitProviderEvaluationReply(current[0], '感谢老板，已收到评价');
  assert.equal((await read(focused)).list[0].reply_content, '感谢老板，已收到评价');
  assert.equal((await read('')).list.length, 2);
  assert.equal((await read(focused)).list.length, 1);
}

async function reportNavigation(input) {
  const client = login(input.token);
  const message = await client.get(`/messages/${input.messageId}/`);
  const firstDestination = ui.providerMessageDestination(message);
  const firstFocus = ui.providerReportFocus(new URLSearchParams(firstDestination.split('?')[1]));
  assert.equal(firstFocus.orderId, String(input.first.id));
  assert.equal((await ui.loadProviderReportPage(1, firstFocus)).list[0].order_id, input.first.id);
  const guard = ui.createViewScopeGuard('provider', `reports:${firstFocus.orderId}`);
  const scope = guard.capture();
  const trap = input.leaveEarly ? intercept((url, options) => url.pathname === '/api/wallet/reports/' && options.method === 'POST', { hold: true }) : null;
  const operation = ui.mutateAndReload({ role: 'provider',
    mutate: () => client.post('/wallet/reports/', { order_id: input.second.id }),
    reload: () => reports(client, input.second.id), isCurrent: scope });
  if (trap) {
    assert.equal((await trap.received).body.code, 0);
    if (input.leaveEarly === 'same') { guard.setScope('reports:'); guard.setScope(`reports:${firstFocus.orderId}`); }
    else guard.dispose();
    assert.equal(scope(), false);
    trap.release();
  }
  const outcome = await operation;
  assert.equal(outcome.stale, !!input.leaveEarly);
  if (input.leaveEarly) assert.equal(outcome.value, undefined);
  else {
    const nextFocus = ui.providerReportFocus(new URLSearchParams('order=' + input.second.id));
    assert.equal((await ui.loadProviderReportPage(1, nextFocus)).list[0].order_id, input.second.id);
    assert.equal((await ui.loadProviderReportPage(1, firstFocus)).list[0].status, 'REJECTED');
  }
  const all = await ui.loadProviderReportPage(1, { orderId: '', reportId: '' });
  assert.ok(all.list.some(row => row.order_id === input.first.id));
  assert.ok(all.list.some(row => row.order_id === input.second.id));
}

async function gameContext(input) {
  const client = login(input.token);
  const assigned = ui.orderBusinessDetails(await order(client, input.orderId), 'provider', input.id);
  assert.equal(assigned.showGameAccount, true);
  assert.deepEqual([assigned.gameRegion, assigned.gameNickname, assigned.gameUid], ['隔离大区', '隔离游戏昵称', 'ISOLATED-UID-123']);
  const pool = login(input.poolToken);
  const poolRow = rows(await pool.get('/orders/orders/', { status: 'pending' })).find(row => row.id === input.poolOrderId);
  assert.ok(poolRow);
  const pending = ui.orderBusinessDetails(poolRow, 'provider', input.poolId);
  assert.equal(pending.gameRegion, '隔离大区'); assert.equal(pending.showGameAccount, false);
  assert.equal(pending.gameNickname, ''); assert.equal(pending.gameUid, '');
  await pool.post(`/orders/orders/${input.poolOrderId}/grab/`, { client_request_id: key() });
  const grabbed = ui.orderBusinessDetails(await order(pool, input.poolOrderId), 'provider', input.poolId);
  assert.equal(grabbed.showGameAccount, true);
  assert.deepEqual([grabbed.gameNickname, grabbed.gameUid], ['池中隔离昵称', 'POOL-ISOLATED-UID']);
}

async function historyPages(input) {
  const client = login(input.token);
  const read = (page, q = '') => client.get('/orders/orders/', { status: 'all', role: 'provider', page, page_size: 20, q });
  const first = await read(1), second = await read(2);
  assert.equal(first.total, 21); assert.equal(first.list.length, 20);
  assert.equal(first.list[0].order_no, input.latest);
  assert.equal(second.list.length, 1); assert.equal(second.list[0].order_no, input.oldest);
  assert.equal(second.list[0].my_assignment_departure, 'REJECTED');
  assert.deepEqual(ui.providerOrderActions(second.list[0]), []);
  assert.equal(ui.pageBounds(2, first.total).page, 2);
  assert.equal((await read(2)).list[0].id, second.list[0].id);
  const found = await read(1, input.latest);
  assert.equal(found.total, 1); assert.equal(found.list[0].order_no, input.latest);
  assert.equal((await read(1, 'NOT-AN-ORDER')).list.length, 0);
  assert.equal((await read(1)).total, 21);
}

async function refresh(read, types = ['order_status_update']) {
  let count = 0, result;
  const coordinator = ui.createRefreshCoordinator({ reload: async () => { result = await read(); count++; },
    isVisible: () => true, types: () => types, delay: 1 });
  return { coordinator, async event(type) {
    const before = count; coordinator.event(type);
    const deadline = Date.now() + 5000;
    while (count === before) { assert.ok(Date.now() < deadline, 'React event must reload the actual HTTP state'); await new Promise(done => setTimeout(done, 5)); }
    return result;
  } };
}

async function staleGrab(input) {
  if (input.sound) return (await import('./fronted-final-four-contracts.mjs')).poolAlerts(input);
  const client = login(input.token);
  const pool = () => client.get('/orders/orders/', { status: 'pending' });
  assert.ok(rows(await pool()).some(row => row.id === input.orderId));
  const update = await refresh(pool);
  if (input.lostResponse) {
    await lost(client, `/orders/orders/${input.orderId}/grab/`, { client_request_id: key() }, pool);
    assert.equal((await order(client, input.orderId)).status, 'GRABBED');
  } else {
    let held, pending;
    if (input.holdPool) {
      held = intercept(url => url.pathname === '/api/orders/orders/' && url.searchParams.get('status') === 'pending', { hold: true });
      pending = pool(); await held.received;
    }
    await raw(input.otherToken, `/orders/orders/${input.orderId}/grab/`, 'POST', { client_request_id: key() });
    if (held) { held.release(); await pending; }
    const attempt = await ui.mutateAndReload({ role: 'provider',
      mutate: () => client.post(`/orders/orders/${input.orderId}/grab/`, { client_request_id: key() }), reload: pool });
    assert.ok(attempt.error);
    assert.ok(!rows(await update.event('order_status_update')).some(row => row.id === input.orderId));
  }
  update.coordinator.dispose();
}

async function reservationNavigation(input) {
  const client = login(input.token);
  const source = ui.orderBusinessDetails(await order(client, input.orderId), 'provider', input.id).reservation;
  assert.equal(source.destination, `/reservations?id=${input.first.id}`);
  assert.equal((await client.get(`/orders/reservations/${input.first.id}/`)).status, 'CONVERTED');
  const all = rows(await client.get('/orders/reservations/'));
  assert.ok(all.some(row => row.id === input.second.id));
  const guard = ui.createViewScopeGuard('provider', 'reservation:' + input.second.id);
  const trap = intercept(url => url.pathname === `/api/orders/reservations/${input.second.id}/confirm/`, { hold: true });
  const operation = ui.mutateAndReload({ role: 'provider', isCurrent: guard.capture(),
    mutate: () => client.post(`/orders/reservations/${input.second.id}/confirm/`, {}),
    reload: () => client.get('/orders/reservations/') });
  assert.equal((await trap.received).body.code, 0);
  guard.setScope('reservation:' + input.first.id); trap.release();
  const completed = await operation;
  assert.equal(completed.stale, true); assert.equal(completed.value, undefined);
  assert.equal((await client.get(`/orders/reservations/${input.second.id}/`)).status, 'CONFIRMED');
  const expired = await client.get(`/orders/reservations/${input.expired.id}/`);
  assert.equal(expired.status, 'EXPIRED');
  assert.ok(!['CONFIRM', 'REJECT', 'CANCEL'].some(action => expired.allowed_actions?.includes(action)));
  await assert.rejects(client.get(`/orders/reservations/${input.hidden.id}/`), error => error.code === 404);
}

async function claimNavigation(input) {
  const client = login(input.token);
  const messages = rows(await client.get('/messages/'));
  const notice = messages.find(row => row.title === '权益交付进度更新'); assert.ok(notice);
  assert.equal(ui.providerMessageDestination(notice), '/points?claim_id=' + input.oldId);
  assert.equal((await client.get(`/club/claims/${input.oldId}/`)).status, 'COMPLETED');
  const reward = rows(await client.get('/club/rewards/')).find(row => row.name === '待兑换权益乙'); assert.ok(reward);
  const request = { reward_id: reward.id, client_request_id: key(), expected_point_cost: reward.point_cost };
  let created;
  if (input.lost) {
    await lost(client, '/club/redeem/', request, () => client.get('/club/claims/'));
    created = await client.post('/club/redeem/', request);
  } else created = await client.post('/club/redeem/', request);
  assert.notEqual(created.id, input.oldId);
  assert.equal((await client.get(`/club/claims/${created.id}/`)).name_snapshot, reward.name);
  const all = rows(await client.get('/club/claims/'));
  assert.ok(all.some(row => row.id === input.oldId));
  assert.equal(all.filter(row => row.id === created.id).length, 1);
  assert.equal(ui.providerMessageDestination({ action_url: `/pages/reward-claim/index?id=${created.id}` }), '/points?claim_id=' + created.id);
}

async function claimCancellation(input) {
  const client = login(input.token);
  const pagination = ui.createPaginationController();
  const read = before => client.get('/club/claims/', { page_size: 20, ...(before ? { before } : {}) });
  const first = await read(null); pagination.merge('claims', first.items);
  const second = await read(first.next); pagination.merge('claims', second.items, true);
  assert.equal(pagination.rows('claims').length, 22);
  assert.ok(pagination.rows('claims').some(row => row.id === input.oldId));
  const original = await client.get(`/club/claims/${input.claimId}/`);
  const draft = { ...original.details, game_uid: '尚未提交的UID' };
  await raw(input.staffToken, `/admin/club-claims/${input.claimId}/process/`, 'POST', { status: 'CANCELLED', note: '隔离库存变动' });
  const update = await refresh(async () => {
    const expanded = await pagination.readExpanded('claims', read, null); pagination.merge('claims', expanded.items);
    return { expanded, points: await client.get('/club/points/') };
  }, ['site_message_update']);
  const latest = await update.event('site_message_update'); update.coordinator.dispose();
  assert.equal(latest.points.points, 4);
  assert.equal(pagination.rows('claims').length, 22);
  assert.ok(pagination.rows('claims').some(row => row.id === input.oldId));
  assert.equal(pagination.rows('claims').find(row => row.id === input.claimId).status, 'CANCELLED');
  assert.equal(rows(await raw(input.bossToken, '/club/rewards/')).find(row => row.id === input.targetId).stock, 1);
  await assert.rejects(client.request(`/club/claims/${input.claimId}/`, { method: 'PATCH', data: { details: draft, expected_details: original.details } }), error => error.code === 409);
  assert.equal(draft.game_uid, '尚未提交的UID');
}

async function couponStock(input) {
  const client = login(input.token, 'boss');
  let data = rows(await client.get('/coupons/'));
  const initial = data.find(row => row.id === input.couponId); assert.ok(initial);
  assert.equal(ui.couponPresentation(initial).claimable, true);
  for (const id of [input.legacyId, input.rateId]) {
    const coupon = data.find(row => row.id === id); assert.ok(coupon);
    assert.match(ui.couponPresentation(coupon).thresholdText, /^会员优惠后满 .* 浪花$/);
  }
  const mineBefore = await client.get('/coupons/mine/');
  let events = 0;
  const coordinator = ui.createRefreshCoordinator({ role: 'boss', delay: 1, isVisible: () => true,
    types: () => ['coupon_catalog_update'], reload: async () => { data = rows(await client.get('/coupons/')); } });
  const unsubscribe = await contractRealtime('boss', input.wsBase, message => {
    if (message.type === 'coupon_catalog_update') events++;
    coordinator.event(message.type);
  });
  try {
    await raw(input.firstToken, `/coupons/${input.couponId}/claim/`, 'POST', {});
    const deadline = Date.now() + 10000;
    while (!events || data.find(row => row.id === input.couponId)?.claim_status !== 'SOLD_OUT') {
      assert.ok(Date.now() < deadline, 'actual Redis event must reload the other owner through the React hub');
      await new Promise(done => setTimeout(done, 10));
    }
    const final = data.find(row => row.id === input.couponId), view = ui.couponPresentation(final);
    assert.equal(final.claim_status, 'SOLD_OUT'); assert.equal(view.claimable, false);
    assert.equal(final.is_claimed, false, 'other owner claim must not become the current owner personal claim');
    assert.deepEqual(await client.get('/coupons/mine/'), mineBefore);
    const claimed = rows(await raw(input.firstToken, '/coupons/mine/'));
    assert.equal(claimed.filter(row => row.name === initial.name).length, 1);
    await assert.rejects(client.post(`/coupons/${input.couponId}/claim/`, {}), error => error.code === 400);
    assert.deepEqual(await client.get('/coupons/mine/'), mineBefore);
  } finally { unsubscribe(); coordinator.dispose(); }
}

async function applicationReadiness(input) {
  const path = `/orders/orders/${input.orderId}/applications/${input.applicationId}/mine/`;
  if (input.poolState) {
    const client = login(input.provider.token);
    const entries = rows(await client.get('/orders/orders/', { status: 'pending' }));
    assert.ok(entries.some(row => row.id === input.orderId));
    const detail = await client.get(path), view = ui.providerApplicationPresentation(detail);
    assert.equal(detail.application.status, input.poolState === 'withdrawn' ? 'WITHDRAWN' : 'PENDING');
    assert.equal(view.canConfirm, input.poolState === 'unconfirmed' && input.isPartner);
    if (input.poolState !== 'withdrawn') assert.ok((await ui.loadProviderApplications(1)).list.some(row => row.id === input.applicationId));
    return;
  }
  for (const [index, provider] of input.providers.entries()) {
    const client = login(provider.token), detail = await client.get(path), view = ui.providerApplicationPresentation(detail);
    assert.equal(detail.application.ready, true); assert.equal(detail.selectable, false);
    assert.match(view.notice, index === input.offlineIndex ? /离线/ : /队友当前暂不符合/);
    if (index !== input.offlineIndex) assert.doesNotMatch(view.notice, /离线/);
  }
  login(input.providers[input.offlineIndex].token);
  await ui.updateProviderStatus('AVAILABLE');
  for (const provider of input.providers) {
    const detail = await raw(provider.token, path);
    assert.equal(detail.application.ready, true); assert.equal(detail.selectable, true);
  }
  await raw(input.bossToken, `/orders/orders/${input.orderId}/applications/${input.applicationId}/select/`, 'POST', {});
  assert.equal((await raw(input.staffToken, `/admin/orders/${input.orderId}/`)).status, 'GRABBED');
}

async function applicationReturn(input) {
  const client = login(input.token);
  const history = await ui.loadProviderApplications(1);
  const application = history.list.find(row => row.order_id === input.orderId || row.order?.id === input.orderId);
  assert.ok(application);
  const id = input.applicationId || application.id;
  const path = `/orders/orders/${input.orderId}/applications/${id}/mine/`;
  if (input.historyOnly) {
    await raw(input.bossToken, `/orders/orders/${input.orderId}/applications/${input.chosenId}/select/`, 'POST', {});
    const detail = await client.get(path);
    assert.equal(detail.application.status, 'NOT_SELECTED');
    assert.match(ui.providerApplicationPresentation(detail).notice, /其他陪玩|未被选中/);
    return;
  }
  const initial = await client.get(path);
  assert.equal(initial.application.status, 'NOT_SELECTED');
  assert.match(ui.providerApplicationPresentation(initial).notice, /其他陪玩|未被选中/);
  await raw(input.assignedToken, `/orders/orders/${input.orderId}/reject/`, 'POST', { reason: '隔离档期变化' });
  const reopened = await client.get(path);
  assert.equal(reopened.order.status, 'PENDING');
  assert.match(ui.providerApplicationPresentation(reopened).notice, /待接单|重新/);
  await raw(input.bossToken, `/orders/orders/${input.orderId}/cancel/`, 'POST', { reason: '隔离取消' });
  const cancelled = await client.get(path);
  assert.equal(cancelled.order.status, 'CANCELLED');
  assert.match(ui.providerApplicationPresentation(cancelled).notice, /取消/);
}

async function teamProgress(input) {
  const picture = (await readFile(input.image)).toString('base64');
  async function check(stage, count) {
    let members;
    for (const [role, token] of Object.entries(input.tokens)) {
      const row = await raw(token, `/${role === 'staff' ? 'admin' : 'orders'}/orders/${input.orderId}/`);
      if (stage == null) { assert.equal(row.status, 'COMPLETED'); assert.equal(row.team_progress, null); continue; }
      assert.equal(row.team_progress.stage, stage); assert.equal(row.team_progress.submitted_count, count);
      if (members) assert.deepEqual(row.team_progress.members, members); else members = row.team_progress.members;
      if (role !== 'staff') ui.bossOrderPresentation(row);
    }
  }
  await check('ENTRY', 1);
  for (const [provider, action, stage, count] of [[input.providers[1], 'start', 'COMPLETION', 0],
    [input.providers[0], 'complete', 'COMPLETION', 1], [input.providers[1], 'complete', null, 0]]) {
    const client = login(provider.token), before = await order(client, input.orderId);
    assert.ok(ui.providerOrderActions(before).includes(action.toUpperCase()));
    const result = await ui.mutateAndReload({ role: 'provider', mutate: () => client.upload(`/orders/orders/${input.orderId}/${action}/`,
      imageForm(picture, action === 'start' ? 'entry_image' : 'completion_image', { client_request_id: key() })), reload: () => order(client, input.orderId) });
    assert.equal(result.error, undefined); await check(stage, count);
  }
}

async function transferPresentation(input) {
  if (input.realtime) return (await import('./fronted-final-four-contracts.mjs')).transferRealtime(input);
  for (const [index, provider] of input.providers.entries()) {
    const client = login(provider.token), row = await order(client, input.orderId), view = ui.providerOrderPresentation(row);
    assert.equal(row.status, 'IN_SERVICE');
    assert.equal(row.my_assignment_released, index === 0);
    assert.equal(row.my_assignment_settled, index === 0 && input.settled);
    if (index === 0) assert.deepEqual(view.actions, []);
    else assert.equal(view.actions.includes('COMPLETE'), row.allowed_actions.includes('COMPLETE'));
  }
}

async function accountStatus(input) {
  const client = login(input.token), admin = login(input.staffToken, 'admin');
  const read = () => client.get('/users/escorts/me/');
  assert.equal(ui.providerAccountPresentation(await read(), null).status, 'AVAILABLE');
  await admin.post(`/admin/orders/${input.orders[0]}/assign/`, { provider_id: input.id });
  const update = await refresh(read);
  assert.equal(ui.providerAccountPresentation(await update.event('order_status_update'), null).status, 'BUSY');
  await client.post(`/orders/orders/${input.orders[0]}/reject/`, { reason: '隔离拒单' });
  assert.equal(ui.providerAccountPresentation(await update.event('order_status_update'), null).status, 'AVAILABLE');
  const trap = intercept((url, options) => url.pathname === '/api/users/escorts/status/' && options.method === 'POST', { hold: true, before: true });
  const mutation = ui.mutateAndReload({ role: 'provider', mutate: () => ui.updateProviderStatus('OFFLINE'), reload: read });
  await trap.received;
  await admin.post(`/admin/orders/${input.orders[1]}/assign/`, { provider_id: input.id });
  assert.equal((await read()).status, 'BUSY');
  trap.release(); const final = await mutation;
  assert.equal(final.error, undefined); assert.equal(ui.providerAccountPresentation(await read(), null).status, 'OFFLINE');
  update.coordinator.dispose();
}

async function accountDeposit(input) {
  const client = login(input.token), admin = login(input.staffToken, 'admin');
  const before = await client.get('/wallet/deposit/');
  assert.equal(ui.providerAccountPresentation(null, before).deposit.paid, 0);
  await admin.post(`/admin/reports/${input.reportId}/approve/`, {});
  const update = await refresh(() => client.get('/wallet/deposit/'), ['wallet_update']);
  const after = await update.event('wallet_update'); update.coordinator.dispose();
  const view = ui.providerAccountPresentation(null, after).deposit;
  assert.equal(view.paid, 4800); assert.equal(view.remaining, before.deposit_remaining - 4800); assert.equal(view.balance, 0);
  const wallet = await client.get('/wallet/withdraw/'); assert.equal(wallet.frozen_amount, 3200);
  assert.ok((await raw(input.bossToken, `/orders/orders/${input.orderId}/`)).settled_at);
  assert.equal((await admin.get(`/admin/orders/${input.orderId}/`)).settlement_status, 'SETTLED');
  const cash = login(input.cashToken);
  const pending = await cash.get('/wallet/deposit/');
  assert.equal(ui.providerAccountPresentation(null, pending).deposit.canPay, true);
  await shared.fundRequest('provider', '/wallet/deposit/', { amount: pending.deposit_remaining });
  const deposited = ui.providerAccountPresentation(null, await cash.get('/wallet/deposit/')).deposit;
  assert.deepEqual([deposited.remaining, deposited.paid, deposited.balance], [0, 20000, 1000]);
}

async function accountEvaluation(input) {
  login(input.token);
  const read = () => ui.loadProviderEvaluations({ page: 1 });
  assert.equal((await read()).total, 0);
  const reviewed = await raw(input.bossToken, '/orders/evaluate/', 'POST', { order_id: input.orderId, score: 5, content: '隔离真实评价' });
  const update = await refresh(read);
  assert.equal((await update.event('order_status_update')).total, 1);
  assert.equal((await read()).pending_total, 1);
  if (input.refundBeforeReply) {
    const preview = await raw(input.staffToken, `/admin/orders/${input.orderId}/refund-preview/`);
    await raw(input.staffToken, `/admin/orders/${input.orderId}/refund/`, 'POST', { reason: '隔离退款评价验收', client_request_id: key(), plan_token: preview.plan_token });
  }
  await raw(input.staffToken, `/admin/evaluations/${reviewed.id}/reply/`, 'POST', { reply_content: '隔离客服已回复', reply_version: 0 });
  const replied = await update.event('order_status_update'); update.coordinator.dispose();
  assert.equal(replied.total, 1); assert.equal(replied.pending_total, 0); assert.equal(replied.list[0].reply_content, '隔离客服已回复');
}

async function withdrawalReceipts(input) {
  const client = login(input.token);
  const data = await client.get('/wallet/withdraw/');
  const [approved, rejected, pending] = input.withdrawals.map(id => data.requests.find(row => row.id === id));
  assert.equal(approved.status, 'APPROVED'); assert.equal(approved.actual_amount, 1960); assert.equal(approved.payout_reference, 'ISOLATED-RECEIPT');
  assert.equal(rejected.status, 'REJECTED'); assert.equal(rejected.audit_remark, '隔离驳回原因'); assert.equal(pending.status, 'PENDING');
  assert.equal(ui.withdrawalPresentation(approved).payoutText, '已登记打款 ¥19.60');
  assert.equal(ui.withdrawalPresentation(rejected).payoutText, '已退回余额');
  assert.equal(ui.withdrawalPresentation(pending).payoutText, '预计打款 ¥19.60');
  const messages = rows(await client.get('/messages/'));
  for (const [title, id] of [['提现审核通过', approved.id], ['提现审核未通过', rejected.id]]) {
    const message = messages.find(row => row.title === title); assert.ok(message);
    assert.equal(ui.providerMessageDestination(message), `/wallet?tab=withdraw&withdraw_id=${id}`);
    const fresh = await client.get('/wallet/withdraw/');
    assert.equal(fresh.requests.find(row => row.id === id).status, id === approved.id ? 'APPROVED' : 'REJECTED');
  }
}

async function withdrawalNotice(input) {
  await withdrawalReceipts(input);
  const empty = login(input.emptyToken);
  const data = await empty.get('/wallet/withdraw/');
  assert.deepEqual(data.requests, [], 'another provider must see its own empty withdrawal history');
  const messages = rows(await empty.get('/messages/'));
  assert.ok(!messages.some(row => ['提现审核通过', '提现审核未通过'].includes(row.title)),
    'withdrawal notices must never leak to the other provider');
}

async function chatNotice(input) {
  const client = login(input.token);
  assert.equal((await client.get('/messages/unread-count/')).unread, 1);
  assert.equal((await client.get('/chat/unread/')).unread, 0);
  await raw(input.staffToken, `/admin/chat-sessions/${input.sessionId}/reply/`, 'POST', { content: '隔离客服已回复', client_message_id: key() });
  const update = await refresh(() => client.get('/chat/unread/'), ['chat_message']);
  assert.equal((await update.event('chat_message')).unread, 1);
  await raw(input.staffToken, `/admin/chat-sessions/${input.sessionId}/reply/`, 'POST', { content: '离线期间客服回复', client_message_id: key() });
  assert.equal((await update.event('reconnected')).unread, 2);
  await client.post('/messages/read-all/', {});
  assert.equal((await client.get('/messages/unread-count/')).unread, 0);
  assert.equal((await client.get('/chat/unread/')).unread, 2, 'site-message read must never consume chat unread');
  const chat = await client.get('/chat/session/');
  assert.ok(chat.messages.some(row => row.content === '隔离客服已回复'));
  assert.equal(ui.mayMarkMessagesRead({ hidden: false, focused: true, browsing: false }), true);
  await client.post('/chat/read/', { through_id: chat.messages.at(-1).id });
  assert.equal((await update.event('chat_message')).unread, 0);
  update.coordinator.dispose();
}

async function removeChatOrder(input) {
  const client = login(input.token);
  const firstScope = ui.providerChatScope(new URLSearchParams('order_no=' + input.first.order_no));
  assert.equal(firstScope, input.first.order_no);
  assert.equal(ui.saveChatDraft('provider', firstScope, '普通咨询草稿').saved, true);
  const general = ui.removeProviderChatContext(firstScope, '普通咨询草稿');
  assert.equal(general.conflict, false); assert.equal(general.saved, true);
  assert.equal(ui.readChatDraft('provider', firstScope), '');
  const emptyScope = ui.providerChatScope(new URLSearchParams()); assert.equal(emptyScope, '');
  assert.equal(ui.readChatDraft('provider', emptyScope), '普通咨询草稿');
  await ui.deliverChatMessage('provider', { id: key(), kind: 'TEXT', content: ui.readChatDraft('provider', emptyScope) });
  ui.clearChatDraft('provider', emptyScope, '普通咨询草稿');
  ui.saveChatDraft('provider', emptyScope, '另存的普通草稿');
  const secondScope = ui.providerChatScope(new URLSearchParams('order_no=' + input.second.order_no));
  ui.saveChatDraft('provider', secondScope, '另一订单咨询');
  const conflict = ui.removeProviderChatContext(secondScope, '另一订单咨询');
  assert.equal(conflict.conflict, true);
  assert.equal(ui.readChatDraft('provider', emptyScope), '另存的普通草稿');
  assert.equal(ui.readChatDraft('provider', secondScope), '另一订单咨询');
  const scoped = '订单号 ' + secondScope + '\n' + ui.readChatDraft('provider', secondScope);
  await ui.deliverChatMessage('provider', { id: key(), kind: 'TEXT', content: scoped });
  ui.clearChatDraft('provider', secondScope, '另一订单咨询');
  const messages = (await client.get('/chat/session/')).messages;
  assert.ok(messages.some(row => row.content === '普通咨询草稿'));
  assert.ok(messages.some(row => row.content === scoped));
  assert.ok(!messages.some(row => row.content.includes(input.first.order_no)));
  const stored = ui.readChatDraft('provider', emptyScope);
  assert.equal(stored, '另存的普通草稿');
}

async function giftPages(input) {
  const client = login(input.token);
  const first = await ui.loadProviderBenefits('gift', 1), second = await ui.loadProviderBenefits('gift', 2);
  assert.equal(first.pagination.gift.total, 21); assert.equal(first.gift_records.length, 20);
  assert.ok(second.gift_records.some(row => row.order_no === input.oldest));
  const dispose1 = await ui.loadProviderBenefits('dispose', 1), dispose2 = await ui.loadProviderBenefits('dispose', 2);
  assert.equal(dispose1.pagination.dispose.total, 30);
  assert.equal(new Set([...dispose1.dispose_records, ...dispose2.dispose_records].map(row => row.id)).size, 30);
  const before = await client.get('/wallet/withdraw/');
  const sent = await raw(input.bossToken, '/orders/gifts/send/', 'POST', { gift_service_id: input.giftId,
    provider_id: input.id, quantity: 1, expected_amount: 1000, client_request_id: key() });
  // Current React uses a page selector, so a refresh retains page 2 and all
  // records remain reachable through the actual page API rather than DOM append.
  const page = 2, update = await refresh(() => ui.loadProviderBenefits('gift', page), ['wallet_update']);
  const refreshed = await update.event('wallet_update'); update.coordinator.dispose();
  assert.equal(ui.pageBounds(page, refreshed.pagination.gift.total).page, 2);
  assert.ok(refreshed.gift_records.some(row => row.order_no === input.oldest));
  const newest = await ui.loadProviderBenefits('gift', 1);
  assert.equal(newest.gift_records[0].order_no, sent.order_no);
  const all = [...newest.gift_records, ...refreshed.gift_records];
  assert.equal(new Set(all.map(row => row.id)).size, 22);
  assert.equal((await client.get('/wallet/withdraw/')).balance, before.balance + 1000);
  for (const row of all) assert.equal(ui.giftBenefitPresentation(row).receivedIncome, 1000);
  const afterDispose1 = await ui.loadProviderBenefits('dispose', 1), afterDispose2 = await ui.loadProviderBenefits('dispose', 2);
  assert.equal(new Set([...afterDispose1.dispose_records, ...afterDispose2.dispose_records].map(row => row.id)).size, 30);
}

async function teamApplicationJourney(input) {
  const picture = (await readFile(input.image)).toString('base64');
  const first = login(input.first.token), boss = login(input.bossToken, 'boss'), admin = login(input.staffToken, 'admin');
  const base = `/orders/orders/${input.orderId}/applications/`;
  async function apply() {
    await first.post(`/orders/orders/${input.orderId}/grab/`, { team_size: 2, partner_id: input.partner.id });
    return (await ui.loadProviderApplications(1)).list.find(row => row.order_id === input.orderId && row.status === 'PENDING').id;
  }
  const firstId = await apply();
  const pending = await raw(input.partner.token, base + firstId + '/mine/');
  assert.equal(ui.providerApplicationPresentation(pending).canConfirm, true);
  assert.equal((await order(boss, input.orderId)).applications[0].ready, false);
  await raw(input.partner.token, base + firstId + '/action/', 'POST', { action: 'WITHDRAW' });
  assert.equal((await order(boss, input.orderId)).applications.length, 0);
  const secondId = await apply();
  await raw(input.partner.token, base + secondId + '/action/', 'POST', { action: 'CONFIRM_TEAM' });
  assert.equal((await order(boss, input.orderId)).applications[0].ready, true);
  await raw(input.partner.token, base + secondId + '/action/', 'POST', { action: 'WITHDRAW' });
  const stale = await ui.mutateAndReload({ role: 'boss', mutate: () => boss.post(base + secondId + '/select/', {}), reload: () => order(boss, input.orderId) });
  assert.ok(stale.error); assert.match(stale.error.message, /撤回/);
  assert.equal((await order(boss, input.orderId)).status, 'PENDING');
  const selected = await apply();
  await raw(input.partner.token, base + selected + '/action/', 'POST', { action: 'CONFIRM_TEAM' });
  await boss.post(base + selected + '/select/', {});
  assert.equal((await order(boss, input.orderId)).status, 'GRABBED');
  for (const [provider, action, count] of [[input.first, 'start', 1], [input.partner, 'start', 0], [input.first, 'complete', 1], [input.partner, 'complete', null]]) {
    const row = await raw(provider.token, `/orders/orders/${input.orderId}/`);
    assert.ok(ui.providerOrderActions(row).includes(action.toUpperCase()));
    await raw(provider.token, `/orders/orders/${input.orderId}/${action}/`, 'POST', imageForm(picture, action === 'start' ? 'entry_image' : 'completion_image', { client_request_id: key() }));
    const after = await order(boss, input.orderId);
    if (count == null) assert.equal(after.status, 'COMPLETED');
    else assert.equal(after.team_progress.submitted_count, count);
  }
  for (const [provider, score, content, reply] of [[input.first, 5, '双陪独立评价甲', '甲的独立回复'], [input.partner, 4, '双陪独立评价乙', '乙的独立回复']]) {
    await boss.post('/orders/evaluate/', { order_id: input.orderId, provider_account_id: provider.id, score, content });
    login(provider.token);
    const own = await ui.loadProviderEvaluations({ page: 1, orderId: String(input.orderId) });
    assert.equal(own.list.length, 1); assert.equal(own.list[0].content, content);
    await ui.submitProviderEvaluationReply(own.list[0], reply);
  }
  const caseResult = await boss.post(`/orders/orders/${input.orderId}/aftersale/`, { reason: '隔离双陪服务问题', client_request_id: key() });
  for (const provider of [input.first, input.partner])
    assert.equal(ui.providerOrderPresentation(await raw(provider.token, `/orders/orders/${input.orderId}/`)).aftersaleOpen, true);
  assert.equal((await admin.get('/admin/dashboard/stats')).open_aftersale_order_total, 1);
}

async function teamAftersaleResolution(input) {
  const admin = login(input.staffToken, 'admin');
  const record = await admin.get(`/admin/orders/${input.orderId}/`);
  const cases = rows(await admin.get('/admin/aftersales/'));
  const active = cases.find(row => (row.order_id || row.order?.id || row.order) === input.orderId && row.status === 'OPEN');
  assert.ok(active);
  for (const provider of [input.first, input.partner]) {
    const current = await raw(provider.token, `/orders/orders/${input.orderId}/`);
    assert.equal(ui.providerOrderPresentation(current).aftersaleOpen, true);
  }
  const result = await ui.mutateAndReload({ role: 'admin',
    mutate: () => admin.post(`/admin/aftersales/${active.id}/resolve/`, { resolution: '隔离双方核实处理', client_request_id: key() }),
    reload: () => admin.get('/admin/dashboard/stats') });
  assert.equal(result.error, undefined);
  for (const token of [input.bossToken, input.first.token, input.partner.token])
    assert.equal((await raw(token, `/orders/orders/${input.orderId}/`)).aftersale_status, 'RESOLVED');
  assert.equal((await admin.get('/admin/dashboard/stats')).open_aftersale_order_total, 0);
}

async function withdrawalLost(input) {
  const client = login(input.token, 'admin');
  const { reviews, api } = await adminControllers();
  const current = await client.get(`/admin/withdrawals/${input.id}/`);
  assert.equal(current.status, 'PENDING'); assert.ok(current.claimed_at);
  const path = `/admin/withdrawals/${input.id}/approve/`;
  const trap = intercept(url => url.pathname === '/api' + path, { lose: true });
  await assert.rejects(api.request({ url: `/withdrawals/${input.id}/approve/`, method: 'POST',
    data: { expected_claimed_at: current.claimed_at, payout_reference: 'ISOLATED-LOST-RESPONSE' } }));
  assert.equal((await trap.received).body.code, 0);
  assert.equal(reviews.reviewActive(path), false);
  const pending = reviews.pendingReviews(); assert.equal(pending.length, 1);
  assert.equal(pending[0].key, path);
  const reloaded = await import('../fronted/admin/src/lib/reviews.ts?contract-reload');
  assert.equal(reloaded.pendingReviews().length, 1, 'real production persistence survives module reload');
  await assert.rejects(api.request({ url: `/withdrawals/${input.id}/approve/`, method: 'POST', data: {} }), /待核对/);
  const reconciled = await api.request({ url: `/withdrawals/${input.id}/` });
  assert.equal(reconciled.data.status, 'APPROVED'); assert.equal(reconciled.data.payout_reference, 'ISOLATED-LOST-RESPONSE');
  reloaded.clearReview(path);
  assert.equal(reviews.pendingReviews().length, 0);
}

async function gameplay(input) {
  const client = login(input.token);
  const row = input.pool ? rows(await client.get('/orders/orders/', { status: 'pending' })).find(row => row.id === input.orderId) : await order(client, input.orderId);
  assert.ok(row);
  const view = ui.orderBusinessDetails(row, 'provider', input.id);
  assert.equal(view.fulfillment, input.fulfillment); assert.equal(view.gameplayRules, input.rules);
}

async function reservationOrigin(input) {
  const client = login(input.token);
  const row = await order(client, input.orderId);
  const details = ui.orderBusinessDetails(row, 'provider', input.id);
  assert.equal(details.reservation.id, input.reservationId);
  assert.equal(Date.parse(details.reservation.startTime), Date.parse(input.start));
  assert.equal(Date.parse(details.reservation.endTime), Date.parse(input.end));
  assert.equal(details.reservation.destination, input.canOpen ? `/reservations?id=${input.reservationId}` : '');
  if (input.canOpen) assert.equal((await client.get(`/orders/reservations/${input.reservationId}/`)).id, input.reservationId);
}

async function reportCreation(input) {
  const client = login(input.token);
  await client.post(`/orders/orders/${input.orderId}/reject/`, { reason: '隔离测试时间冲突' });
  const rejected = await ui.mutateAndReload({ role: 'provider', mutate: () => client.post('/wallet/reports/', { order_id: input.orderId }),
    reload: () => client.get('/wallet/report-order-choices/') });
  assert.ok(rejected.error); assert.equal(rejected.error.code, 400);
  const second = await raw(input.bossToken, '/orders/create/', 'POST', {
    service_id: input.serviceId, provider_id: input.id, client_request_id: key(),
  });
  await lost(client, '/wallet/reports/', { order_id: second.id }, () => reports(client, second.id));
  assert.ok(rows(await reports(client, second.id)).some(row => row.order_id === second.id));
  for (const [token, path] of [[input.bossToken, '/orders/orders/'], [input.staffToken, '/admin/orders/']])
    assert.equal((await raw(token, path + input.orderId + '/')).status, 'PENDING');
}

export async function runScenario(scenario, input = null) {
  input ||= await readInput();
  setBase(input.base);
  const handlers = {
    'provider-vue-aftersale-375': aftersale,
    'provider-vue-report-label-375': reportLabels,
    'provider-vue-settlement-notice-375': settlementNotice,
    'provider-vue-refund-reason-375': refundReason,
    'provider-vue-grab-retry-375': grabRetry,
    'provider-vue-fulfillment-retry-375': fulfillmentRetry,
    'provider-vue-report-batch-upload-375': batchImages,
    'provider-vue-report-creation-feedback-375': reportCreation,
    'provider-vue-evaluation-conflict-375': evaluationConflict,
    'provider-vue-evaluation-message-375': evaluationMessage,
    'provider-vue-report-navigation-375': reportNavigation,
    'provider-vue-game-context-375': gameContext,
    'provider-vue-gameplay-snapshot-375': gameplay,
    'provider-vue-reservation-origin-375': reservationOrigin,
    'provider-vue-history-pages-live': historyPages,
    'provider-vue-stale-grab-375': staleGrab,
    'provider-vue-reservation-navigation-375': reservationNavigation,
    'provider-vue-claim-navigation-375': claimNavigation,
    'provider-vue-claim-cancel-375': claimCancellation,
    'native-coupon-stock-realtime': couponStock,
    'provider-vue-application-readiness-375': applicationReadiness,
    'provider-vue-application-return-375': applicationReturn,
    'provider-vue-team-progress-375': teamProgress,
    'provider-vue-transfer-375': transferPresentation,
    'provider-vue-mine-assignment-status-375': accountStatus,
    'provider-vue-mine-deposit-375': accountDeposit,
    'provider-vue-mine-evaluation-375': accountEvaluation,
    'provider-vue-withdraw-receipt-375': withdrawalReceipts,
    'provider-vue-withdraw-notice-375': withdrawalNotice,
    'provider-vue-chat-notice-375': chatNotice,
    'provider-vue-chat-remove-order-375': removeChatOrder,
    'provider-vue-gift-page-realtime-375': giftPages,
    'three-role-application-progress-live': teamApplicationJourney,
    'team-aftersale-resolution-live': teamAftersaleResolution,
    'admin-withdraw-lost-response-live': withdrawalLost,
  };
  assert.ok(handlers[scenario], 'Current React scenario must be implemented: ' + scenario);
  await handlers[scenario](input);
  console.log('Current React production controller and real HTTP verified: ' + scenario);
}
