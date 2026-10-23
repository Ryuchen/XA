const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { createRequire } = require('node:module');
const ts = createRequire(path.resolve('apps/admin-web/package.json'))('typescript');
const money = require('../packages/money/src/index.js');
const clone = value => JSON.parse(JSON.stringify(value));
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const commissionModule = { exports: {} };
vm.runInNewContext(ts.transpileModule(fs.readFileSync('apps/admin-web/src/utils/commission.ts', 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { module: commissionModule, exports: commissionModule.exports, require: () => ({ amountToXaCoin: money.formatXaCoin, xaCoinToAmount: money.toRawAmount }) });
const commission = commissionModule.exports;

test('fixed configuration converts coins at the shared boundary and keeps explicit zero distinct from inheritance', () => {
  assert.deepEqual(clone(commission.readCommission({ commission_type: 'FIXED', commission_fixed: 300 })), { type: 'FIXED', rate: 0, fixed: 30 });
  assert.equal(commission.commissionPayload('FIXED', 25, .1).commission_fixed, 1);
  assert.equal(commission.commissionConfigured({ commission_type: 'FIXED', commission_fixed: 0 }), true);
  assert.equal(commission.commissionLabel({ commission_type: 'FIXED', commission_fixed: 0 }), '整单固定 0 兴安币');
  assert.deepEqual(clone(commission.inheritedCommission()), { commission_type: 'PERCENT', commission_rate: null, commission_fixed: 0 });
  assert.equal(commission.commissionConfigured(commission.inheritedCommission()), false);
});

test('fixed validation rejects lossy precision, missing, negative and overflowing values before conversion', () => {
  for (const value of [undefined, null, NaN, Infinity, -1, .01, 1.11, 214748364.8]) assert.ok(commission.commissionError('FIXED', 0, value), String(value));
  for (const value of [0, .1, .1 + .2, 12.3, 214748364.7]) assert.equal(commission.commissionError('FIXED', undefined, value), '', String(value));
  assert.equal(commission.commissionPayload('FIXED', 50, 214748364.7).commission_fixed, 2147483647);
});

test('percentage validation retains inclusive integer bounds and payloads clear inactive values', () => {
  for (const rate of [0, 20, 100]) assert.equal(commission.commissionError('PERCENT', rate, undefined), '');
  for (const rate of [undefined, null, NaN, -1, 101, 20.5]) assert.ok(commission.commissionError('PERCENT', rate, 0));
  assert.deepEqual(clone(commission.commissionPayload('PERCENT', 20, 30)), { commission_type: 'PERCENT', commission_rate: 20, commission_fixed: 0 });
  assert.deepEqual(clone(commission.commissionPayload('FIXED', 20, 30)), { commission_type: 'FIXED', commission_rate: 0, commission_fixed: 300 });
});

function pageFunctions(file, names, context, prelude = '') {
  const script = fs.readFileSync(file, 'utf8').match(/<script setup[^>]*>([\s\S]*?)<\/script>/)[1];
  const ast = ts.createSourceFile('page.ts', script, ts.ScriptTarget.Latest, true);
  const code = names.map(name => {
    const fn = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
    assert.ok(fn, name); return fn.getText(ast);
  }).join('\n');
  const module = { exports: {} };
  vm.runInNewContext(ts.transpileModule(`${prelude}\n${code}\nmodule.exports={${names.join(',')}}`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { module, exports: module.exports, ...context });
  return module.exports;
}

function dispatchHarness() {
  const ref = value => ({ value });
  const mode = ref('AUTO'), fixed = ref(0), pendingRate = ref(), quote = ref(), warnings = [], submissions = [], requests = [], quotePending = [];
  const auth = { sessionRevision: 1, allowed: true, hasPerm() { return this.allowed; } };
  const form = { customer_id: 11, service_id: 22, game_rounds: 3, escort_mode: 'DOUBLE', provider_gender: 'ANY', providers: [{ provider_id: 31, commission_rate: 15 }, { provider_id: 32, commission_rate: 35 }], remark: '' };
  const visible = ref(true), dispatching = ref(false);
  const quoteResult = { amount: 1001, commission_type: 'FIXED', commission_fixed_total: 301, commission_locked: false,
    provider_shares: [{ settlement_base: 501, commission_fixed: 151, provider_income: 350 }, { settlement_base: 500, commission_fixed: 150, provider_income: 350 }] };
  const context = {
    auth, pageDisposed: false, dispatchForm: form, dispatchCommissionMode: mode, dispatchCommissionFixed: fixed, dispatchPendingRate: pendingRate,
    dispatchVisible: visible, dispatching, dispatchRequestId: ref('synthetic-intent'), dispatchQuote: quote, quoteLoading: ref(false),
    dispatchCommissionError: { get value() { return mode.value === 'FIXED' ? commission.commissionError('FIXED', 0, fixed.value) : ''; } },
    dispatchFixed: { get value() { return mode.value === 'FIXED' || quote.value?.commission_type === 'FIXED'; } },
    currentService: ref({ billing_provider_count: 2 }), insufficient: ref(false),
    estimateAmount: { get value() { return quote.value?.amount || 0; } },
    commissionError: commission.commissionError, amountToXaCoin: money.formatXaCoin, xaCoinToAmount: money.toRawAmount,
    ElMessage: { warning(message) { warnings.push(message); }, success() {} }, tableRef: ref({ load() {} }), onDispatchMessage() {},
    async request(options) { requests.push(clone(options)); return quotePending.length ? quotePending.shift().promise : { data: clone(quoteResult) }; },
    orderApi: { async collectionAction(action, payload) { submissions.push({ action, payload: clone(payload) }); return { data: { status: 'GRABBED' } }; } },
  };
  const page = pageFunctions('apps/admin-web/src/views/order/index.vue', ['dispatchPendingPercentage', 'dispatchQuoteKey', 'dispatchCommissionParams', 'refreshDispatchQuote', 'serviceProviderCount', 'onDispatchSave', 'providerBase', 'providerIncome', 'providerIncomeDesc', 'dispatchProviderShare', 'onProviderPicked', 'closeDispatchReview'], context, 'let quoteSequence=0,quotedInputs="",quotedSession=-1;');
  return { ...page, mode, fixed, pendingRate, quote, form, visible, auth, warnings, submissions, requests, quotePending, quoteResult, currentService: context.currentService };
}

test('double-provider fixed dispatch sends one top-level whole-order value and uses server shares without re-dividing', async () => {
  const s = dispatchHarness(); s.mode.value = 'FIXED'; s.fixed.value = 30.1;
  await s.refreshDispatchQuote();
  assert.equal(s.requests[0].params.commission_fixed, 301);
  assert.equal(s.providerIncome(s.form.providers[0]), 350);
  assert.match(s.providerIncomeDesc(s.form.providers[0]), /50\.1币 − 分摊抽成 15\.1币/);
  await s.onDispatchSave();
  assert.equal(s.submissions.length, 1);
  const data = s.submissions[0].payload;
  assert.equal(data.commission_type, 'FIXED'); assert.equal(data.commission_fixed, 301); assert.equal(data.game_rounds, 3);
  assert.deepEqual(data.providers, [{ provider_id: 31 }, { provider_id: 32 }]);
});

test('configured automatic fixed zero stays active and sends no repeated provider fixed amounts', async () => {
  const s = dispatchHarness(); s.quoteResult.commission_fixed_total = 0;
  await s.refreshDispatchQuote(); await s.onDispatchSave();
  assert.equal(s.submissions[0].payload.commission_type, 'AUTO');
  assert.equal(Object.hasOwn(s.submissions[0].payload, 'commission_fixed'), false);
  assert.deepEqual(s.submissions[0].payload.providers, [{ provider_id: 31 }, { provider_id: 32 }]);
});

test('manual percentage preserves different provider rates while locked automatic mode leaves server rates authoritative', async () => {
  const s = dispatchHarness(); s.mode.value = 'PERCENT'; s.quoteResult.commission_type = 'PERCENT'; s.quoteResult.commission_fixed_total = null;
  await s.refreshDispatchQuote(); await s.onDispatchSave();
  assert.deepEqual(s.submissions[0].payload.providers.map(p => p.commission_rate), [15, 35]);
  const locked = dispatchHarness(); locked.quoteResult.commission_type = 'PERCENT'; locked.quoteResult.commission_locked = true; locked.quoteResult.commission_fixed_total = null;
  await locked.refreshDispatchQuote(); await locked.onDispatchSave();
  assert.deepEqual(locked.submissions[0].payload.providers, [{ provider_id: 31 }, { provider_id: 32 }]);
});

test('edited fixed input invalidates the successful quote before any reactive watcher runs', async () => {
  const s = dispatchHarness(); s.mode.value = 'FIXED'; s.fixed.value = 30.1; await s.refreshDispatchQuote();
  s.fixed.value = 40; await s.onDispatchSave();
  assert.equal(s.submissions.length, 0); assert.match(s.warnings.at(-1), /试算/);
  s.fixed.value = .01; await s.onDispatchSave(); assert.equal(s.submissions.length, 0); assert.match(s.warnings.at(-1), /1 位小数/);
});

test('late quote and suggestion replies cannot cross a mode, session or selected-provider change', async () => {
  const s = dispatchHarness(), pending = deferred(); s.quotePending.push(pending);
  const job = s.refreshDispatchQuote(); s.mode.value = 'FIXED'; pending.resolve({ data: s.quoteResult }); await job;
  assert.equal(s.quote.value, undefined);
  const pendingSuggestion = deferred(); s.quotePending.push(pendingSuggestion); const suggestion = s.onProviderPicked(0);
  s.auth.sessionRevision++; pendingSuggestion.resolve({ data: { commission_rate: 99 } }); await suggestion;
  assert.equal(s.form.providers[0].commission_rate, 15);
  assert.equal(s.form.providers[0].suggestion_loading, false);
});

test('global fixed settings preserve the legacy standalone-report percentage', async () => {
  const values = { savingRate: { value: false }, commissionType: { value: 'FIXED' }, commissionInput: { value: 23 }, commissionFixed: { value: 40 }, commissionRate: { value: 23 }, commissionConfig: { value: {} }, commissionVisible: { value: true } };
  let sent;
  const page = pageFunctions('apps/admin-web/src/views/report/index.vue', ['saveCommission'], {
    ...values, ...commission, ElMessage: { success() {}, warning() {} },
    commissionApi: { async update(payload) { sent = clone(payload); return { data: { ...payload, rate: 23 } }; } },
  });
  await page.saveCommission(); assert.deepEqual(sent, { commission_type: 'FIXED', commission_fixed: 400, rate: 23 });
});

test('a completed quote cannot authorize a dispatch after switching accounts or revoking dispatch permission', async () => {
  const s = dispatchHarness(); await s.refreshDispatchQuote(); s.auth.sessionRevision++;
  await s.onDispatchSave(); assert.equal(s.submissions.length, 0);
  await s.refreshDispatchQuote(); s.auth.allowed = false;
  await s.onDispatchSave(); assert.equal(s.submissions.length, 0);
  s.closeDispatchReview(); assert.equal(s.visible.value, false); assert.equal(s.quote.value, undefined);
});

test('service opt-in alone activates custom commission, including fixed zero on scoped experience services', async () => {
  const refs = { commissionEnabled: { value: false }, commissionType: { value: 'FIXED' }, commissionRate: { value: 23 }, commissionFixed: { value: 0 }, saving: { value: false }, priceCoin: { value: 100 }, isRoulette: { value: false }, isGift: { value: false }, imagesText: { value: '' }, highlightsText: { value: '' }, dialogVisible: { value: true }, tableRef: { value: { reload() {} } } };
  const form = { id: 99, name: 'Scoped experience', provider_kind: 'GUARD', provider_grade: 'N3', commission_rate: 23, experience_kind: 'DAILY' };
  const sent = [];
  const page = pageFunctions('apps/admin-web/src/views/service/index.vue', ['onSave', 'fromLines'], {
    form, ...refs, ...commission, xaCoinToAmount: money.toRawAmount, ElMessage: { success() {}, warning() {} }, serviceItemApi: { async update(id, payload) { sent.push(clone(payload)); } },
  });
  await page.onSave(); assert.equal(sent[0].commission_override, false);
  for (const key of ['commission_rate', 'commission_type', 'commission_fixed']) assert.equal(Object.hasOwn(sent[0], key), false, 'off preserves old field ' + key);
  refs.commissionEnabled.value = true; await page.onSave();
  assert.equal(sent[1].commission_override, true); assert.equal(sent[1].commission_type, 'FIXED'); assert.equal(sent[1].commission_fixed, 0);
  assert.equal(sent[1].provider_grade, 'N3'); assert.equal(sent[1].experience_kind, 'DAILY'); assert.equal(sent[1].price, 1000);
});

test('a pending single-provider manual percentage uses the server default then quotes and freezes the edited top-level rate', async () => {
  const s = dispatchHarness(); s.mode.value = 'PERCENT'; s.form.escort_mode = 'SINGLE'; s.form.providers = [{ commission_rate: 0 }]; s.currentService.value.billing_provider_count = 1;
  s.quoteResult.commission_type = 'PERCENT'; s.quoteResult.commission_fixed_total = null; s.quoteResult.commission_rate = 23;
  await s.refreshDispatchQuote(); assert.equal(s.pendingRate.value, 23);
  s.pendingRate.value = 35; await s.onDispatchSave(); assert.equal(s.submissions.length, 0);
  s.quoteResult.commission_rate = 35; await s.refreshDispatchQuote(); await s.onDispatchSave();
  assert.equal(s.requests.at(-1).params.commission_rate, 35);
  assert.equal(s.submissions[0].payload.commission_rate, 35); assert.deepEqual(s.submissions[0].payload.providers, []);
});

test('locked automatic percentages stay authoritative when a provider suggestion used a different amount tier', async () => {
  const s = dispatchHarness(); s.quoteResult.commission_type = 'PERCENT'; s.quoteResult.commission_fixed_total = null; s.quoteResult.commission_locked = true;
  s.quoteResult.commission_rate = 10; s.quoteResult.commission_source_label = '体验单自动规则';
  await s.refreshDispatchQuote(); assert.deepEqual(s.form.providers.map(p => p.commission_rate), [10, 10]);
  const pending = deferred(); s.quotePending.push(pending); const job = s.onProviderPicked(0);
  assert.equal(s.requests.at(-1).params.amount, s.quoteResult.amount);
  pending.resolve({ data: { commission_rate: 0, commission_source_label: 'old tier' } }); await job;
  assert.equal(s.form.providers[0].commission_rate, 10); assert.equal(s.form.providers[0].source_label, '体验单自动规则');
});
