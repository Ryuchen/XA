import assert from 'node:assert/strict';
import { test } from 'node:test';
import { business as ui, clients, deferred, imageForm, intercept, login, lost, raw,
  rows, sessions, setBase } from './fronted-test-runtime.mjs';

const cases = {
  order: {
    XA_REAL_PROVIDER_REFUND: 'real provider refund response renders the order income and refund',
    XA_REAL_RESOLVED_AFTERSALE: 'real resolved aftersale response is visible on the provider order',
    XA_REAL_LEGACY_TEAM_ORDER: 'real running legacy team response explains each provider report',
    XA_TRANSFERRED_ORDER_JSON: 'transferred provider sees their own handoff without an active completion action',
    XA_PROVIDER_GRAB_LOST_HTTP: 'a lost grab response prompts verification and refreshes the real pool',
    XA_PROVIDER_STALE_GRAB_HTTP: 'a rejected stale grab refreshes the real pool after another provider takes the order',
    XA_TEAM_APPLICATION_LOST_HTTP: 'lost team application and confirmation recover across both providers and boss',
    XA_PROVIDER_TEAM_LOST_HTTP: 'lost first teammate evidence shows real partial team progress',
    XA_PROVIDER_FLOW_LOST_HTTP: 'lost application and evidence responses reconcile against real API state',
    XA_TEAM_REPORT_NAV_HTTP: 'real team evidence destination focuses the selected order',
    XA_REPORT_ACTIONS_LOST_HTTP: 'real order report entry opens report after a lost response',
    XA_REPORT_FOCUS_HTTP: 'real order report entry queues the same order after creating its report',
  },
  report: {
    XA_REAL_PROVIDER_REFUND: 'real provider refund response renders the report income and refund',
    XA_REAL_RESOLVED_REPORT: 'real resolved aftersale report shows its outcome and settlement',
    XA_TEAM_REPORT_NAV_HTTP: 'real team report evidence link opens its own order and completes both stages',
    XA_REPORT_ACTIONS_LOST_HTTP: 'real report actions recover after their committed responses are lost',
    XA_REPORT_FOCUS_HTTP: 'real multi-order report list focuses the intended order and clears on account change',
    XA_REPORT_ACCOUNT_SWITCH_HTTP: 'an actual old-account response cannot replace the current report selection',
    XA_REPORT_SUBMIT_SWITCH_HTTP: 'two real report submissions stay isolated when account changes',
    XA_REPORT_STALE_HTTP: 'stale rejected report refreshes after another device really resubmits',
    XA_REPORT_UPLOAD_SUCCESS_HTTP: 'a real evidence upload confirms success and displays the saved image',
    XA_REPORT_UPLOAD_LOST_HTTP: 'a partially completed real image batch with a lost response asks to check the saved evidence',
  },
  application: {
    XA_TEAM_DETAIL_LOST_HTTP: 'lost confirmation on invitation detail reloads the committed team status',
    XA_TEAM_DETAIL_WITHDRAW_LOST_HTTP: 'lost withdrawal reloads the real withdrawn status and provides pool navigation',
    XA_TEAM_CANCELLED_HTTP: 'real cancelled team application stays readable for both participants',
    XA_TEAM_SELECTED_HTTP: 'real selected invitation opens the associated order',
    XA_REJECTED_SELECTION_HTTP: 'real rejected selection shows its historical outcome',
  },
  message: {
    XA_WITHDRAWN_TEAM_NOTICE: 'real withdrawn-team notice opens the pool',
    XA_EVALUATION_NOTICE: 'real evaluation notice opens the order returned by the message API',
    XA_REPORT_NOTICE: 'real report review notice opens the report page',
  },
  wallet: {
    XA_REAL_WALLET_JSON: 'renders actual isolated settlement and refund wallet response',
    XA_REAL_GIFT_WALLET_JSON: 'renders actual gift source wallet response',
    XA_REAL_GIFT_BENEFITS: 'real gift benefit data separates referral income through refunds',
  },
};

function sameAction(order, action) {
  const actions = ui.providerOrderActions(order);
  assert.equal(actions.includes(action), (order.allowed_actions || []).includes(action));
}
function text(value) { return typeof value === 'string' ? value : JSON.stringify(value); }
function validateReport(report) {
  const view = ui.providerReportPresentation(report);
  assert.ok(view && typeof view === 'object');
  if (report.refund_summary) {
    assert.equal(view.netIncome, report.refund_summary.net_income, 'present the server net income');
    assert.equal(view.refundedAmount, report.refund_summary.amount);
  }
  return view;
}
async function run(key, data, family) {
  if (data.base) setBase(data.base);
  const token = data.providerToken || data.token || data.partnerToken;
  const client = token ? login(token) : clients.provider;
  const detail = id => client.get(`/orders/orders/${id}/`);
  const reports = () => client.get('/wallet/reports/');
  const getReport = async id => rows(await reports()).find(row => row.id === id);
  const pool = () => client.get('/orders/orders/', { status: 'pending' });
  const upload = (id, kind = 'RESULT') => client.upload(`/wallet/reports/${id}/images/`, imageForm(data.image, 'image', { kind }));
  if (key === 'XA_REAL_PROVIDER_REFUND') {
    if (family === 'report') validateReport(data.report);
    else {
      const view = ui.providerOrderPresentation(data.order);
      assert.equal(view.netIncome, data.order.refund_summary.net_income);
      assert.equal(view.refundedAmount, data.order.refund_summary.amount);
      assert.equal(view.remainingFrozen, data.order.refund_summary.remaining_frozen_amount ?? null);
      sameAction(data.order, 'COMPLETE');
      assert.equal(Boolean(view.reportDestination), data.order.report_entry?.available === true);
    }
    return;
  }
  if (key === 'XA_REAL_RESOLVED_AFTERSALE') {
    assert.equal(data.aftersale_status, 'RESOLVED'); sameAction(data, 'COMPLETE');
    assert.ok(ui.providerReportDestination(data).includes(String(data.id))); return;
  }
  if (key === 'XA_REAL_RESOLVED_REPORT') { assert.equal(data.aftersale_status, 'RESOLVED'); validateReport(data); return; }
  if (key === 'XA_REAL_LEGACY_TEAM_ORDER' || key === 'XA_TRANSFERRED_ORDER_JSON') {
    sameAction(data, 'START'); sameAction(data, 'COMPLETE');
    if (key === 'XA_TRANSFERRED_ORDER_JSON') assert.equal(ui.providerOrderActions(data).includes('COMPLETE'), false);
    if (data.can_report) assert.ok(ui.providerReportDestination(data).includes(String(data.id)));
    return;
  }
  if (family === 'message') {
    const route = ui.providerMessageDestination(data);
    if (key === 'XA_WITHDRAWN_TEAM_NOTICE') assert.equal(route, '/orders');
    else if (key === 'XA_REPORT_NOTICE') assert.match(route, new RegExp(`reports.*(?:order_id|order)=${data.related_order_id}`));
    else assert.match(route, new RegExp(`evaluations\\?order=${data.related_order_id}`));
    return;
  }
  if (family === 'wallet') {
    if (key === 'XA_REAL_GIFT_BENEFITS') {
      const income = data.map(snapshot => snapshot.gift_records.map(ui.giftBenefitPresentation));
      assert.equal(data[0].gift_records[0].received_income, 8000);
      assert.equal(data[1].gift_records[0].received_income, 4000);
      assert.equal(data[2].gift_records[0].received_income, 0);
      assert.equal(income[0][0].receivedIncome, 8000); assert.equal(income[1][0].receivedIncome, 4000);
      assert.equal(income[2][0].receivedIncome, 0);
      assert.ok(text(income[2]).includes('退款')); assert.ok(!text(income[2]).includes('待入账'));
    } else {
      const views = data.transactions.map(ui.walletTransactionPresentation);
      if (key === 'XA_REAL_GIFT_WALLET_JSON') {
        for (const title of ['礼物收益', '客服赠送', '赠送', '礼物收益冲回']) assert.ok(text(views).includes(title), title);
      } else {
        assert.equal(data.total_income, 6745);
        assert.ok(text(views).includes('推荐')); assert.ok(text(views).includes('退回') || text(views).includes('冲回'));
      }
    }
    return;
  }
  if (key === 'XA_PROVIDER_GRAB_LOST_HTTP' || key === 'XA_PROVIDER_STALE_GRAB_HTTP') {
    assert.ok(rows(await pool()).some(row => row.id === data.orderId));
    if (key === 'XA_PROVIDER_STALE_GRAB_HTTP') {
      await raw(data.otherToken, `/orders/orders/${data.orderId}/grab/`, 'POST', {});
      await assert.rejects(client.post(`/orders/orders/${data.orderId}/grab/`, {}));
      await ui.mutateAndReload({ role: 'provider', mutate: () => Promise.reject(new Error('stale order')), reload: pool, isCurrent: () => true });
    } else await lost(client, `/orders/orders/${data.orderId}/grab/`, {}, pool);
    assert.ok(!rows(await pool()).some(row => row.id === data.orderId));
    if (key === 'XA_PROVIDER_GRAB_LOST_HTTP') assert.ok(rows(await client.get('/orders/orders/', { status: 'grabbed' })).some(row => row.id === data.orderId));
    return;
  }
  if (key === 'XA_TEAM_APPLICATION_LOST_HTTP') {
    const teammates = rows(await client.get(`/orders/orders/${data.orderId}/teammates/`));
    const partner = teammates.find(row => (row.nickname || row.display_name || '').includes(data.partnerName));
    assert.ok(partner, 'the actual teammate is eligible');
    await lost(client, `/orders/orders/${data.orderId}/grab/`, { team_size: 2, partner_id: partner.id }, pool);
    let bossOrder = await raw(data.bossToken, `/orders/orders/${data.orderId}/`);
    assert.equal(bossOrder.applications.length, 1); assert.equal(bossOrder.applications[0].ready, false);
    const application = bossOrder.applications[0];
    const partnerClient = login(data.partnerToken);
    await lost(partnerClient, `/orders/orders/${data.orderId}/applications/${application.id}/action/`, { action: 'CONFIRM_TEAM' }, pool);
    bossOrder = await raw(data.bossToken, `/orders/orders/${data.orderId}/`);
    assert.equal(bossOrder.applications.length, 1); assert.equal(bossOrder.applications[0].ready, true);
    const selected = await raw(data.bossToken, `/orders/orders/${data.orderId}/applications/${application.id}/select/`, 'POST', {});
    assert.equal(selected.status, 'GRABBED'); return;
  }
  if (key === 'XA_PROVIDER_FLOW_LOST_HTTP' || key === 'XA_PROVIDER_TEAM_LOST_HTTP') {
    if (key === 'XA_PROVIDER_FLOW_LOST_HTTP') {
      const application = rows(await pool()).find(row => row.requires_boss_selection);
      assert.ok(application);
      await lost(client, `/orders/orders/${application.id}/grab/`, { team_size: 1 }, pool);
      const running = rows(await client.get('/orders/orders/', { status: 'grabbed' })).find(row => row.order_no === data.serviceNo);
      assert.ok(running); data.orderId = running.id;
    }
    for (const [action, field, status] of [['start', 'entry_image', 'IN_SERVICE'], ['complete', 'completion_image', 'COMPLETED']]) {
      await lost(client, `/orders/orders/${data.orderId}/${action}/`, imageForm(data.image, field), () => detail(data.orderId));
      let order = await detail(data.orderId);
      sameAction(order, action === 'start' ? 'START' : 'COMPLETE');
      if (key === 'XA_PROVIDER_TEAM_LOST_HTTP') {
        assert.notEqual(order.status, status, 'one teammate cannot advance the whole team');
        await raw(data.teammateToken, `/orders/orders/${data.orderId}/${action}/`, 'POST', imageForm(data.image, field));
        order = await detail(data.orderId);
      }
      assert.equal(order.status, status);
    }
    return;
  }
  if (family === 'application') {
    const path = `/orders/orders/${data.orderId}/applications/${data.applicationId}/mine/`;
    const action = `/orders/orders/${data.orderId}/applications/${data.applicationId}/action/`;
    if (key === 'XA_TEAM_DETAIL_LOST_HTTP' || key === 'XA_TEAM_DETAIL_WITHDRAW_LOST_HTTP') {
      await lost(client, action, { action: key.includes('WITHDRAW') ? 'WITHDRAW' : 'CONFIRM_TEAM' }, () => client.get(path));
      const current = await client.get(path);
      if (key.includes('WITHDRAW')) assert.equal(current.application.status, 'WITHDRAWN');
      else assert.equal(current.application.partner_confirmed, true);
      assert.equal(current.can_confirm, false); return;
    }
    if (key === 'XA_TEAM_CANCELLED_HTTP') {
      await raw(data.bossToken, `/orders/orders/${data.orderId}/cancel/`, 'POST', {});
      for (const actor of data.tokens) {
        const current = await raw(actor, path); assert.equal(current.order.status, 'CANCELLED');
        assert.equal(current.can_confirm, false);
        assert.ok(current.closed_reason, 'the cancelled invitation must explain its terminal state');
        assert.equal(current.order.allowed_actions?.includes('GRAB') || false, false);
      }
      await assert.rejects(raw(data.outsiderToken, path)); return;
    }
    const current = await client.get(path);
    assert.equal(current.can_confirm, false);
    if (key === 'XA_TEAM_SELECTED_HTTP') { assert.equal(current.application.status, 'SELECTED'); assert.equal(current.order.id, data.orderId); }
    else { assert.equal(current.application.status, 'SELECTED'); assert.equal(current.order.status, 'PENDING'); assert.match(current.closed_reason, /回到待接单池/); }
    return;
  }
  if (key === 'XA_TEAM_REPORT_NAV_HTTP') {
    const order = await detail(data.orderId);
    assert.ok(ui.providerReportDestination(order).includes(String(data.orderId)));
    if (family === 'order') return;
    for (const [action, field] of [['start', 'entry_image'], ['complete', 'completion_image']]) {
      for (const actor of [data.providerToken, data.partnerToken]) await raw(actor,
        `/orders/orders/${data.orderId}/${action}/`, 'POST', imageForm(data.image, field));
    }
    assert.equal((await raw(data.bossToken, `/orders/orders/${data.orderId}/`)).status, 'COMPLETED'); return;
  }
  if (key === 'XA_REPORT_UPLOAD_SUCCESS_HTTP' || key === 'XA_REPORT_UPLOAD_LOST_HTTP') {
    if (key === 'XA_REPORT_UPLOAD_SUCCESS_HTTP') {
      const result = await ui.uploadProviderReportImages({ reportId: data.reportId, kind: 'RESULT',
        files: [new File([Buffer.from(data.image, 'base64')], 'result.png', { type: 'image/png' })], reload: reports });
      assert.equal(result.error, undefined); assert.equal(result.uploaded, 1);
    }
    else {
      let attempts = 0;
      const captured = intercept((url, options) => url.pathname === `/api/wallet/reports/${data.reportId}/images/`
        && options.method === 'POST' && ++attempts === 2, { lose: true });
      const files = Array.from({ length: 3 }, (_, index) => new File([Buffer.from(data.image, 'base64')], `result-${index}.png`, { type: 'image/png' }));
      const outcome = await ui.uploadProviderReportImages({ reportId: data.reportId, kind: 'RESULT', files, reload: reports });
      await captured.received;
      assert.ok(outcome.error); assert.equal(outcome.uploaded, 1); assert.equal(attempts, 2);
      assert.equal((await getReport(data.reportId)).result_images.length, 2, 'stop the batch after an unknown outcome');
      for (let i = 0; i < 7; i++) await upload(data.reportId);
      await assert.rejects(upload(data.reportId), /9/);
    }
    assert.equal((await getReport(data.reportId)).result_images.length, key.includes('LOST') ? 9 : 1); return;
  }
  if (key === 'XA_REPORT_STALE_HTTP') {
    const stale = await getReport(data.reportId); assert.equal(stale.status, 'REJECTED');
    await raw(data.token, `/wallet/reports/${data.reportId}/submit/`, 'POST', {});
    await ui.mutateAndReload({ role: 'provider', mutate: () => client.post(`/wallet/reports/${data.reportId}/submit/`, {}), reload: reports, isCurrent: () => true });
    assert.equal((await getReport(data.reportId)).status, 'PENDING'); return;
  }
  if (key === 'XA_REPORT_ACTIONS_LOST_HTTP') {
    if (family === 'order') {
      await lost(client, '/wallet/reports/', { order_id: data.firstOrderId }, reports);
      const created = rows(await reports()).find(row => row.order_id === data.firstOrderId);
      const order = await detail(data.firstOrderId);
      assert.equal(rows(await reports()).find(row => row.order_id === data.firstOrderId).id, created.id);
      assert.ok(ui.providerReportDestination(order).includes(String(data.firstOrderId))); return;
    }
    await lost(client, '/wallet/reports/', { order_id: data.firstOrderId }, reports);
    const first = rows(await reports()).find(row => row.order_id === data.firstOrderId); assert.ok(first);
    await upload(first.id);
    const image = (await getReport(first.id)).result_images[0];
    await lost(client, `/wallet/reports/${first.id}/images/${image.id}/`, undefined, reports, 'DELETE');
    assert.equal((await getReport(first.id)).result_images.length, 0);
    const second = rows(await reports()).find(row => row.order_id === data.secondOrderId);
    await lost(client, `/wallet/reports/${second.id}/submit/`, {}, reports);
    assert.equal((await getReport(second.id)).status, 'PENDING'); return;
  }
  if (key === 'XA_REPORT_FOCUS_HTTP') {
    if (family === 'order') {
      const created = await client.post('/wallet/reports/', { order_id: data.secondOrderId });
      assert.ok(created.id); return;
    }
    const all = rows(await reports());
    assert.ok(all.some(row => row.order_id === data.firstOrderId));
    assert.ok(all.some(row => row.order_id === data.secondOrderId));
    assert.equal(ui.providerReportFocus(new URLSearchParams({ order_id: String(data.secondOrderId) })).orderId, String(data.secondOrderId));
    assert.equal(all.filter(row => row.order_id === data.missingOrderId).length, 0);
    login(data.otherToken); assert.equal(rows(await reports()).length, 0); return;
  }
  if (key === 'XA_REPORT_ACCOUNT_SWITCH_HTTP') {
    const hold = intercept(url => url.pathname === '/api/orders/orders/', { hold: true });
    const stale = client.get('/orders/orders/'); await hold.received;
    login(data.otherToken); hold.release(); await assert.rejects(stale, error => error.code === 'SESSION_CHANGED');
    login(data.token);
    const creation = intercept((url, options) => url.pathname === '/api/wallet/reports/' && options.method === 'POST', { hold: true });
    const pending = client.post('/wallet/reports/', { order_id: data.orderId }); await creation.received;
    login(data.otherToken); creation.release(); await assert.rejects(pending, error => error.code === 'SESSION_CHANGED');
    assert.equal(rows(await reports()).length, 0); return;
  }
  if (key === 'XA_REPORT_SUBMIT_SWITCH_HTTP') {
    const holds = [], operations = [];
    for (const actor of data.tokens) {
      login(actor); const report = rows(await reports()).find(row => row.can_submit); assert.ok(report);
      const hold = intercept(url => url.pathname === `/api/wallet/reports/${report.id}/submit/`, { hold: true });
      operations.push(client.post(`/wallet/reports/${report.id}/submit/`, {})); holds.push(hold); await hold.received;
    }
    holds[0].release(); await assert.rejects(operations[0], error => error.code === 'SESSION_CHANGED');
    holds[1].release(); await operations[1];
    assert.equal(rows(await reports())[0].status, 'PENDING'); return;
  }
  throw new Error('Unmigrated current React contract: ' + key);
}

export function register(family) {
  for (const [key, name] of Object.entries(cases[family])) {
    test(name, { skip: !process.env[key] }, () => run(key, JSON.parse(process.env[key]), family));
  }
}
