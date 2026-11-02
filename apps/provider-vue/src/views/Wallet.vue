<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { formatRmb, formatXaCoin, toRawAmount } from '@xa/money'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Input } from '@xa/shadcn-vue/input'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { api, currentUser } from '../lib/session'
import { fundRequest } from '../lib/fund'
import { coalesced, onRealtime } from '../lib/realtime'

type Account = { id:number; version:number; payee_method:string; payee_name:string; payee_account:string }
type Withdraw = { id:number; amount:number; actual_amount:number; tax_amount:number; payee_method_display:string; payee_name:string; payee_account:string; status:string; processing?:boolean; audit_remark:string; payout_reference:string; created_at:string; paid_at:string | null }
type Overview = { balance:number; frozen_amount:number; min_amount:number; balance_threshold?:number | null; can_withdraw?:boolean; tax_rate:number; requests:Withdraw[]; amount?:number; tax_amount?:number; actual_amount?:number }
type Quote = Overview & { amount:number; tax_amount:number; actual_amount:number }
type Transaction = { id:number; tx_no:string; tx_type:string; amount:number; status:string; remark:string; created_at:string; is_referral_income?:boolean; is_reversal?:boolean; gift_source?:string }
type Transactions = { transactions:Transaction[]; total:number; total_income:number; page:number }
type Dispose = { id:number; type_display:string; signed_amount:number; reason:string; created_at:string }
type Gift = { id:number; order_no:string; source_order_no?:string; service_name:string; customer_name:string; amount:number; provider_income:number; received_income:number; settlement_status:string; completed_at:string }
type Benefits = { summary:{ total_reward:number; total_penalty:number; total_gift_income:number }; dispose_records:Dispose[]; gift_records:Gift[]; pagination:{ page_size:number; dispose:{page:number;total:number}; gift:{page:number;total:number} } }
type Tab = 'transactions' | 'dispose' | 'gift' | 'withdraw'
const route = useRoute(), router = useRouter(), overview = ref<Overview | null>(null), transactions = ref<Transactions | null>(null), benefits = ref<Benefits | null>(null), accounts = ref<Account[]>([])
function routeTab(value:unknown):Tab { return ['transactions','dispose','gift','withdraw'].includes(String(value)) ? value as Tab : 'transactions' }
const loading = ref(true), error = ref(''), notice = ref(''), tab = ref<Tab>(routeTab(route.query.tab)), loadingMore = ref(false)
const show = ref(false), input = ref(''), accountId = ref(''), quote = ref<Quote | null>(null), quoteLoading = ref(false), quoteError = ref(''), issue = ref(''), busy = ref(false)
let sequence = 0, quoteSequence = 0, alive = true
let paginationIntent:{kind:'transactions' | 'dispose' | 'gift'; page:number} | null = null
const tabs: { id:Tab; label:string }[] = [{id:'transactions',label:'流水'}, {id:'dispose',label:'奖惩'}, {id:'gift',label:'礼物'}, {id:'withdraw',label:'提现'}]
const account = computed(() => accounts.value.find(item => String(item.id) === accountId.value))
const amount = computed(() => { const text = input.value.trim(); if (!/^\d+(\.\d)?$/.test(text)) return 0; const raw = toRawAmount(text); return Number.isSafeInteger(raw) && raw > 0 && raw <= 2147483647 ? raw : 0 })
function validQuote(value:number, data:Overview | null): data is Quote {
  if (!data || !value || data.amount !== value) return false
  return [data.amount, data.tax_amount, data.actual_amount, data.tax_rate, data.min_amount, data.balance].every(item => typeof item === 'number' && Number.isSafeInteger(item) && item >= 0) && data.tax_rate <= 100 && data.tax_amount! + data.actual_amount! === value
}
function invalidateQuote() { quoteSequence++; quote.value = null; quoteError.value = ''; quoteLoading.value = false }
async function load() {
  const intent = paginationIntent
  const oldTransactions = transactions.value?.transactions || [], oldDispose = benefits.value?.dispose_records || [], oldGifts = benefits.value?.gift_records || []
  const oldestTransaction = oldTransactions[oldTransactions.length - 1]?.id, oldestDispose = oldDispose[oldDispose.length - 1]?.id, oldestGift = oldGifts[oldGifts.length - 1]?.id
  const current = ++sequence; loading.value = true; error.value = ''; loadingMore.value = false; invalidateQuote()
  try {
    const [w, tx, b, a] = await Promise.all([api<Overview>('/wallet/withdraw/'), api<Transactions>('/wallet/transactions/?page_size=50'), api<Benefits>('/wallet/provider-benefits/'), api<Account[]>('/wallet/withdraw-accounts/')])
    if (!alive || current !== sequence) return
    const transactionIds = new Set(tx.transactions.map(row => row.id))
    while (((intent?.kind === 'transactions' && tx.page < intent.page) || (oldestTransaction && !transactionIds.has(oldestTransaction))) && tx.transactions.length < tx.total) {
      const page = await api<Transactions>(`/wallet/transactions/?page=${tx.page + 1}&page_size=50`)
      if (!alive || current !== sequence) return
      if (!page.transactions.length) break
      for (const row of page.transactions) if (!transactionIds.has(row.id)) { transactionIds.add(row.id); tx.transactions.push(row) }
      tx.page = page.page; tx.total = page.total; tx.total_income = page.total_income
    }
    const disposeIds = new Set(b.dispose_records.map(row => row.id)), giftIds = new Set(b.gift_records.map(row => row.id))
    while (((intent?.kind === 'dispose' && b.pagination.dispose.page < intent.page) || (oldestDispose && !disposeIds.has(oldestDispose))) && b.dispose_records.length < b.pagination.dispose.total) {
      const page = await api<Benefits>(`/wallet/provider-benefits/?dispose_page=${b.pagination.dispose.page + 1}&page_size=${b.pagination.page_size}`)
      if (!alive || current !== sequence) return
      if (!page.dispose_records.length) break
      for (const row of page.dispose_records) if (!disposeIds.has(row.id)) { disposeIds.add(row.id); b.dispose_records.push(row) }
      b.pagination.dispose = page.pagination.dispose; b.summary = page.summary
    }
    while (((intent?.kind === 'gift' && b.pagination.gift.page < intent.page) || (oldestGift && !giftIds.has(oldestGift))) && b.gift_records.length < b.pagination.gift.total) {
      const page = await api<Benefits>(`/wallet/provider-benefits/?gift_page=${b.pagination.gift.page + 1}&page_size=${b.pagination.page_size}`)
      if (!alive || current !== sequence) return
      if (!page.gift_records.length) break
      for (const row of page.gift_records) if (!giftIds.has(row.id)) { giftIds.add(row.id); b.gift_records.push(row) }
      b.pagination.gift = page.pagination.gift; b.summary = page.summary
    }
    overview.value = w; transactions.value = tx; benefits.value = b; accounts.value = a
    if (paginationIntent === intent) paginationIntent = null
    if (!a.some(item => String(item.id) === accountId.value)) accountId.value = a[0] ? String(a[0].id) : ''
  } catch (cause) { if (alive && current === sequence) error.value = cause instanceof Error ? cause.message : '钱包加载失败' }
  finally { if (alive && current === sequence) loading.value = false }
}
async function preview() {
  const current = ++quoteSequence, value = amount.value; quote.value = null; quoteError.value = ''
  if (!show.value || !value || loading.value || error.value) { quoteLoading.value = false; return }
  quoteLoading.value = true
  try { const data = await api<Overview>(`/wallet/withdraw/?amount=${value}`); if (alive && current === quoteSequence) { if (!validQuote(value, data)) throw new Error('提现试算不完整，请重试'); quote.value = data; overview.value = data } }
  catch (cause) { if (alive && current === quoteSequence) quoteError.value = cause instanceof Error ? cause.message : '试算失败，请重试' }
  finally { if (alive && current === quoteSequence) quoteLoading.value = false }
}
watch([amount, show, loading, error], () => { void preview() })
watch(() => route.query.tab, value => { tab.value = routeTab(value) })
function selectTab(value:Tab) { return router.push({path:'/wallet',query:value === 'transactions' ? {} : {tab:value}}) }
const validation = computed(() => {
  const q = quote.value
  if (loading.value || error.value) return '请先重新加载钱包信息'
  if (!amount.value) return '请输入有效金额，最多一位小数'
  if (quoteLoading.value || !validQuote(amount.value, q)) return '请等待完整试算后再提交'
  if (q.can_withdraw === false) return '可用余额未达到提现条件'
  if (amount.value < q.min_amount) return `最低提现 ${formatXaCoin(q.min_amount)} 兴安币`
  if (amount.value > q.balance) return '提现金额超出可用余额'
  if (!account.value) return '请先绑定收款账户'
  return ''
})
async function submit() {
  const q = quote.value, selected = account.value
  if (busy.value || issue.value || validation.value || !q || !selected) return
  busy.value = true; issue.value = ''
  try {
    await fundRequest('/wallet/withdraw/', { amount:amount.value, withdrawal_account_id:selected.id, expected_payee_version:selected.version, expected_tax_rate:q.tax_rate, expected_tax_amount:q.tax_amount, expected_actual_amount:q.actual_amount })
    if (!alive) return
    show.value = false; input.value = ''; await selectTab('withdraw'); notice.value = '提现申请已提交'; await load()
  } catch (cause) {
    if (!alive) return
    const failure = cause as { code?:number; errorCode?:string; message?:string }
    if (!failure.code || failure.code >= 500 || failure.code === 409 || failure.code === 429) issue.value = '提现结果未确认，请查看提现记录或联系客服核对，暂勿重复提交。'
    else { issue.value = failure.message || '提现失败'; if (failure.errorCode === 'PAYEE_CHANGED') await load(); else invalidateQuote() }
  } finally { if (alive) busy.value = false }
}
async function more() {
  if (loading.value || loadingMore.value) return
  const current = sequence; loadingMore.value = true
  try {
    if (tab.value === 'transactions' && transactions.value) {
      paginationIntent = { kind:'transactions', page:transactions.value.page + 1 }
      const data = await api<Transactions>(`/wallet/transactions/?page=${transactions.value.page + 1}&page_size=50`); if (!alive || current !== sequence || !transactions.value) return
      const ids = new Set(transactions.value.transactions.map(row => row.id)); transactions.value = { ...data, transactions:[...transactions.value.transactions, ...data.transactions.filter(row => !ids.has(row.id))] }
    } else if ((tab.value === 'dispose' || tab.value === 'gift') && benefits.value) {
      const kind = tab.value, old = benefits.value, dp = old.pagination.dispose.page + (kind === 'dispose' ? 1 : 0), gp = old.pagination.gift.page + (kind === 'gift' ? 1 : 0)
      paginationIntent = { kind, page:kind === 'dispose' ? dp : gp }
      const data = await api<Benefits>(`/wallet/provider-benefits/?dispose_page=${dp}&gift_page=${gp}&page_size=${old.pagination.page_size}`); if (!alive || current !== sequence || !benefits.value) return
      if (kind === 'dispose') { const ids = new Set(old.dispose_records.map(row => row.id)); old.dispose_records.push(...data.dispose_records.filter(row => !ids.has(row.id))) }
      else { const ids = new Set(old.gift_records.map(row => row.id)); old.gift_records.push(...data.gift_records.filter(row => !ids.has(row.id))) }
      old.summary = data.summary; old.pagination[kind] = data.pagination[kind]
    }
  } catch (cause) { notice.value = cause instanceof Error ? cause.message : '明细加载失败' }
  finally { if (alive && current === sequence) { loadingMore.value = false; paginationIntent = null } }
}
const hasMore = computed(() => tab.value === 'transactions' ? !!transactions.value && transactions.value.transactions.length < transactions.value.total : tab.value === 'dispose' ? !!benefits.value && benefits.value.dispose_records.length < benefits.value.pagination.dispose.total : tab.value === 'gift' ? !!benefits.value && benefits.value.gift_records.length < benefits.value.pagination.gift.total : false)
const labels:Record<string,string> = { TOPUP:'充值', PAY:'订单支付', INCOME:'收益', WITHDRAW:'提现', DEPOSIT:'押金', REWARD:'奖励', PENALTY:'罚款', PASS_PURCHASE:'通行证', GIFT:'赠送', REFUND:'退款', ADJUST:'调账', EARNINGS_FREEZE:'收益冻结', EARNINGS_RELEASE:'收益解冻' }
function transactionLabel(row:Transaction) { if (row.is_referral_income) return row.amount < 0 ? '推荐收益退回' : '推荐分佣'; if (row.tx_type === 'INCOME' && row.amount < 0) return '收益退回'; const label = row.tx_type === 'GIFT' ? row.gift_source === 'ORDER_GIFT' ? '礼物收益' : row.gift_source === 'RECHARGE_GIFT' ? '客服赠送' : '赠送' : labels[row.tx_type] || row.tx_type; return row.is_reversal ? `${label}冲回` : label }
const refresh = coalesced(load)
function accountUpdate(data:unknown) { const id = (data as {account_id?:unknown} | null)?.account_id; if (!document.hidden && id != null && String(id) === String(currentUser.value?.id)) refresh.request() }
const offWallet = onRealtime('wallet_update', accountUpdate), offWithdraw = onRealtime('withdraw_status_update', accountUpdate), offConnect = onRealtime('connected', () => { if (!document.hidden) refresh.request() })
function visible() { if (!document.hidden) refresh.request() }
onMounted(() => { void load(); document.addEventListener('visibilitychange', visible) })
onUnmounted(() => { alive = false; sequence++; quoteSequence++; refresh.dispose(); offWallet(); offWithdraw(); offConnect(); document.removeEventListener('visibilitychange', visible) })
</script>

<template>
  <main class="page stack">
    <header class="row"><div><p class="muted">兴安电竞 · 陪玩端</p><h1>资产钱包</h1></div><Button variant="outline" @click="router.push('/orders')">返回订单</Button></header>
    <Card class="panel wallet"><p class="muted">可提现兴安币</p><strong class="balance">{{ overview ? formatXaCoin(overview.balance) : '—' }} 币</strong><div class="row"><p class="muted">冻结 {{ overview ? formatXaCoin(overview.frozen_amount) : '—' }} 币 · 服务及推荐净收益 {{ transactions ? formatXaCoin(transactions.total_income) : '—' }} 币</p><Button :disabled="loading || !!error || !overview || overview.balance <= 0" @click="issue = ''; show = true">提现</Button></div></Card>
    <div class="row"><p class="muted">微信、支付宝、银行卡各可绑定一个账户</p><div class="actions"><Button variant="outline" @click="router.push('/support')">充值与钱包咨询</Button><Button variant="outline" :disabled="busy" @click="router.push('/withdraw-accounts')">管理账户</Button></div></div>
    <p v-if="notice" role="status" class="notice">{{ notice }}</p><p v-if="error" role="alert" class="error">{{ error }} <Button variant="outline" @click="load">重新加载</Button></p>
    <div v-if="benefits" class="summary"><div><strong>+{{ formatXaCoin(benefits.summary.total_reward) }}</strong><span>奖励</span></div><div><strong>-{{ formatXaCoin(benefits.summary.total_penalty) }}</strong><span>处罚</span></div><div><strong>{{ formatXaCoin(benefits.summary.total_gift_income) }}</strong><span>礼物净实得</span></div></div>
    <nav class="tabs"><Button v-for="item in tabs" :key="item.id" :variant="tab === item.id ? 'default' : 'outline'" :aria-pressed="tab === item.id" @click="selectTab(item.id)">{{ item.label }}</Button></nav>
    <p v-if="loading" class="muted">正在加载钱包…</p>
    <template v-else-if="!error">
      <Card v-if="tab === 'transactions'" class="panel list"><p v-if="!transactions?.transactions.length" class="muted">暂无资金流水</p><div v-for="row in transactions?.transactions" :key="row.id" class="record"><div><strong>{{ transactionLabel(row) }}</strong><p class="muted">{{ row.remark || row.tx_no }}</p><p v-if="row.status !== 'SUCCESS'" class="muted">{{ row.status === 'PENDING' ? '处理中' : row.tx_type === 'WITHDRAW' ? '已退回余额 · 原申请金额' : '未成功 · 原流水金额' }}</p><p class="muted">{{ new Date(row.created_at).toLocaleString() }}</p></div><strong>{{ row.amount > 0 ? '+' : row.amount < 0 ? '-' : '' }}{{ formatXaCoin(Math.abs(row.amount)) }} 币</strong></div></Card>
      <Card v-if="tab === 'dispose'" class="panel list"><p v-if="!benefits?.dispose_records.length" class="muted">暂无奖惩记录</p><div v-for="row in benefits?.dispose_records" :key="row.id" class="record"><div><strong>{{ row.type_display }}</strong><p class="muted">{{ row.reason || '暂无说明' }}</p><p class="muted">{{ new Date(row.created_at).toLocaleString() }}</p></div><strong>{{ row.signed_amount > 0 ? '+' : '' }}{{ formatXaCoin(row.signed_amount) }} 币</strong></div></Card>
      <Card v-if="tab === 'gift'" class="panel list"><p v-if="!benefits?.gift_records.length" class="muted">暂无礼物打赏</p><div v-for="row in benefits?.gift_records" :key="row.id" class="record"><div><strong>{{ row.service_name }}</strong><p class="muted">{{ row.customer_name }} · {{ row.order_no }}</p><p v-if="typeof row.source_order_no === 'string'" class="muted">{{ row.source_order_no ? `服务订单 ${row.source_order_no}` : '直接赠送' }}</p><p class="muted">{{ new Date(row.completed_at).toLocaleString() }}</p></div><div class="amount"><strong>{{ row.received_income > 0 ? '+' : '' }}{{ formatXaCoin(row.received_income) }} 币</strong><p class="muted">{{ row.settlement_status === 'PARTIALLY_REFUNDED' ? '部分退款 · 保留净实得' : row.settlement_status === 'REFUNDED' ? '已退款 · 收益已冲回' : row.settlement_status === 'SETTLED' ? '已入账' : row.settlement_status === 'PENDING' ? '待入账' : '请联系客服核对' }}</p><p v-if="!['SETTLED','REFUNDED','PARTIALLY_REFUNDED'].includes(row.settlement_status)" class="muted">应得 {{ formatXaCoin(row.provider_income) }} 币</p></div></div></Card>
      <Card v-if="tab === 'withdraw'" class="panel list"><p v-if="!overview?.requests.length" class="muted">暂无提现记录</p><div v-for="row in overview?.requests" :key="row.id" class="record"><div><strong>{{ row.payee_method_display }} · {{ row.payee_name }}</strong><p class="muted">收款账号 •••• {{ row.payee_account.slice(-4) }}</p><p class="muted">{{ row.status === 'REJECTED' ? row.audit_remark ? `驳回：${row.audit_remark}` : '已退回余额' : `${row.status === 'APPROVED' ? '已登记打款' : '预计打款'} ¥${formatRmb(row.actual_amount)}` }}</p><p v-if="row.status === 'PENDING'" class="muted">{{ row.processing ? '客服已领取，等待处理' : '等待客服领取' }}</p><p v-if="row.payout_reference" class="muted">打款凭证：{{ row.payout_reference }}</p><p class="muted">{{ new Date(row.paid_at || row.created_at).toLocaleString() }}</p></div><div class="amount"><strong>{{ row.status === 'REJECTED' ? '原申请 ' : '-' }}{{ formatXaCoin(row.amount) }} 币</strong><p class="muted">{{ row.status === 'REJECTED' ? '已退回余额' : row.status === 'APPROVED' ? '已通过' : row.processing ? '处理中' : '待审核' }}</p></div></div></Card>
      <Button v-if="hasMore" variant="outline" :disabled="loadingMore" @click="more">{{ loadingMore ? '加载中…' : '加载更多' }}</Button>
    </template>
    <Dialog :open="show" @update:open="value => { if (!busy) show = value }"><DialogContent :show-close-button="!busy"><DialogHeader><DialogTitle>申请提现</DialogTitle><DialogDescription>客服线下审核并打款，请核对收款资料。</DialogDescription></DialogHeader>
      <p v-if="issue" role="alert" class="error">{{ issue }} <Button variant="outline" :disabled="busy" @click="show = false; selectTab('withdraw'); load()">查看提现记录</Button></p>
      <label class="field">提现兴安币<Input v-model="input" inputmode="decimal" maxlength="12" placeholder="0" :disabled="busy" /></label><p class="muted">可用 {{ overview ? formatXaCoin(overview.balance) : '—' }} 币 · {{ overview?.balance_threshold != null ? `余额大于 ${formatRmb(overview.balance_threshold)} 元可申请` : `最低 ${overview ? formatXaCoin(overview.min_amount) : '—'} 币` }}</p>
      <p class="muted">{{ quote ? `税率 ${quote.tax_rate}% · 应扣税 ${formatXaCoin(quote.tax_amount)} 币 · 预计打款 ¥${formatRmb(quote.actual_amount)}` : quoteLoading ? '正在试算到账金额…' : quoteError || '填写金额后试算' }}</p><Button v-if="quoteError" variant="outline" :disabled="busy" @click="preview">重新试算</Button>
      <label class="field">收款账户<NativeSelect v-model="accountId" :disabled="busy || !accounts.length"><NativeSelectOption value="">请选择收款账户</NativeSelectOption><NativeSelectOption v-for="row in accounts" :key="row.id" :value="String(row.id)">{{ row.payee_method === 'WECHAT' ? '微信' : row.payee_method === 'ALIPAY' ? '支付宝' : '银行卡' }} · {{ row.payee_name }} · {{ row.payee_account }}</NativeSelectOption></NativeSelect></label><Button variant="outline" :disabled="busy" @click="router.push('/withdraw-accounts')">管理账户</Button><p v-if="validation && input" class="muted">{{ validation }}</p>
      <DialogFooter><Button variant="outline" :disabled="busy" @click="show = false">取消</Button><Button :disabled="busy || !!issue || !!validation" @click="submit">{{ busy ? '提交中…' : '确认提现' }}</Button></DialogFooter>
    </DialogContent></Dialog>
  </main>
</template>
<style scoped>
h1 { margin:4px 0 0; font-size:24px; } p { margin:0; overflow-wrap:anywhere; } .wallet { gap:8px; } .balance { font-size:28px; } .summary { display:grid; grid-template-columns:repeat(3,1fr); gap:8px; } .summary div { padding:12px; background:var(--card); border:1px solid var(--border); border-radius:12px; } .summary strong,.summary span { display:block; } .summary span { margin-top:4px; color:var(--muted-foreground); font-size:12px; } .tabs { display:flex; gap:8px; overflow-x:auto; } .list { gap:0; } .record { display:flex; justify-content:space-between; gap:12px; padding:12px 0; border-bottom:1px solid var(--border); } .record:last-child { border:0; } .record strong { overflow-wrap:anywhere; } .amount { text-align:right; } .notice { color:#1e3a8a; }
</style>
