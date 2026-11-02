<template>
  <div class="wl-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理老板与打手钱包</p>
    </div>

    <Alert v-if="fundReview && !walletDialogVisible() && !saving"><AlertTitle>资金操作结果待核对</AlertTitle><AlertDescription>
      <p>钱包 #{{ fundReview.walletId }} 的{{ fundReview.kind === 'adjust' ? '调账' : '充值' }}请求可能仍在处理。请核对相关记录和请求状态，勿重复操作。</p>
      <Button v-if="auth.hasPerm('transaction:view')" variant="outline" @click="router.push({ name: 'transactions', query: { wallet_id: String(fundReview.walletId) } })">查看该钱包流水</Button>
      <Button v-if="fundReview.kind === 'recharge' && auth.hasPerm('recharge:view')" variant="outline" @click="router.push({ name: 'recharge-records', query: { wallet_id: String(fundReview.walletId) } })">查看关联充值记录</Button>
      <Button variant="outline" @click="finishFundReview">已核对，解除限制</Button>
    </AlertDescription></Alert>
    <p v-if="notice && !walletDialogVisible()" role="status">{{ notice }}</p>

    <div class="wl-heading"><strong>账户列表</strong><Popover><PopoverTrigger as-child><button type="button" class="wl-help" aria-label="钱包说明">?</button></PopoverTrigger><PopoverContent>余额与冻结分别展示；打手奖罚和退押金请在陪玩管理办理。</PopoverContent></Popover></div>

    <SimplePagedList v-if="canReadWallets" :key="auth.sessionRevision" ref="tableRef" :fetcher="walletApi.list" :query="query"><template #toolbar="{ reload }"><div class="wl-filter-primary"><Input v-model="query.keyword" class="wl-filter-keyword" placeholder="编号、账号、昵称或手机" aria-label="搜索编号、账号、昵称或手机号" @keyup.enter="reload" @update:model-value="value => { if (value === '') reload() }" /><NativeSelect v-model="query.account_type" class="wl-filter-type" aria-label="钱包对象" @update:model-value="reload"><NativeSelectOption value="BOSS">老板钱包</NativeSelectOption><NativeSelectOption value="PROVIDER">打手钱包</NativeSelectOption></NativeSelect></div><div class="wl-filter-actions"><Button @click="reload">查询</Button><Button variant="outline" :aria-expanded="advancedFiltersOpen" aria-controls="wl-advanced-filters" @click="advancedFiltersOpen = !advancedFiltersOpen">{{ advancedFiltersOpen ? '收起筛选' : '更多筛选' }}<template v-if="advancedFilterCount">（{{ advancedFilterCount }}）</template></Button><Button v-if="hasWalletFilters" variant="ghost" @click="clearWalletFilters(reload)">清除条件</Button></div><div v-show="advancedFiltersOpen" id="wl-advanced-filters" class="wl-advanced-filters"><SuggestionInput v-model="query.boss_no" :fetch-suggestions="querySuggest('boss_no')" placeholder="编号" label="按编号筛选" @select="reload" @keyup.enter="reload" @clear="reload" /><SuggestionInput v-model="query.nickname" :fetch-suggestions="querySuggest('nickname')" placeholder="昵称" label="按昵称筛选" @select="reload" @keyup.enter="reload" @clear="reload" /><SuggestionInput v-model="query.phone" :fetch-suggestions="querySuggest('phone')" placeholder="手机号" label="按手机号筛选" @select="reload" @keyup.enter="reload" @clear="reload" /></div></template>
      <template #default="{ rows }"><Table class="wl-desktop-table"><TableHeader><TableRow><TableHead>账户</TableHead><TableHead>兴安币余额</TableHead><TableHead>冻结</TableHead><TableHead>状态</TableHead><TableHead>手机号</TableHead><TableHead v-if="query.account_type === 'BOSS'">老板分级</TableHead><TableHead>累计实充</TableHead><TableHead>累计赠送</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell><div class="wl-table-identity"><strong>{{ row.nickname || row.username || '名称待核对' }}</strong><span>{{ row.boss_no || `钱包 #${row.id}` }} · {{ row.username || '账号待核对' }}</span></div></TableCell><TableCell>{{ amountToXaCoin(row.balance) }} 币</TableCell><TableCell>{{ amountToXaCoin(row.frozen_amount) }} 币</TableCell><TableCell><Badge variant="outline">{{ row.is_active ? '正常' : '停用' }}</Badge></TableCell><TableCell>{{ row.phone || '—' }}</TableCell><TableCell v-if="query.account_type === 'BOSS'">{{ row.boss_type_name || '—' }}</TableCell><TableCell>{{ amountToXaCoin(row.total_recharge) }} 币</TableCell><TableCell>{{ amountToXaCoin(row.total_gift) }} 币</TableCell><TableCell><div class="wl-row-actions"><Button v-if="auth.hasPerm('transaction:view')" variant="link" @click="viewWalletTransactions(row)">流水</Button><Button v-if="auth.hasPerm('recharge:view')" variant="link" @click="viewWalletRecharges(row)">充值记录</Button><Button v-if="auth.hasPerm('wallet:recharge')" variant="link" :disabled="!!fundReview || !row.is_active" @click="onRecharge(row)">客服充值</Button><Button v-if="auth.hasPerm('wallet:adjust') && row.user_role === 'CUSTOMER'" variant="link" :disabled="!!fundReview || !row.is_active" @click="onAdjust(row)">调账</Button></div></TableCell></TableRow></TableBody></Table><div class="wl-mobile-list" role="list" aria-label="钱包账户列表">
          <article v-for="row in rows" :key="row.id" class="wl-card" role="listitem">
            <div class="wl-card-head">
              <img v-if="row.avatar_url" :src="row.avatar_url" alt="" class="wl-avatar" /><span v-else class="wl-avatar">{{ (row.nickname || row.username || '账').slice(0, 1) }}</span>
              <div class="wl-identity"><strong>{{ row.nickname || row.username || '名称待核对' }}</strong><span>{{ row.boss_no || `钱包 #${row.id}` }} · {{ row.username || '账号待核对' }}</span></div>
              <Badge variant="outline">{{ row.is_active ? '钱包正常' : '钱包停用' }}</Badge>
            </div>
            <dl class="wl-facts">
              <div><dt>兴安币余额</dt><dd class="wl-primary-amount">{{ amountToXaCoin(row.balance) }} 币</dd></div>
              <div><dt>冻结</dt><dd>{{ amountToXaCoin(row.frozen_amount) }} 币</dd></div>
              <div><dt>累计实充</dt><dd>{{ amountToXaCoin(row.total_recharge) }} 币</dd></div>
              <div><dt>累计赠送</dt><dd>{{ amountToXaCoin(row.total_gift) }} 币</dd></div>
              <div v-if="row.user_role === 'CUSTOMER'"><dt>老板分级</dt><dd>{{ row.boss_type_name || '未分级' }}</dd></div>
              <div><dt>手机号</dt><dd>{{ row.phone || '未填写' }}</dd></div>
            </dl>
            <p v-if="!row.is_active" class="wl-note">钱包已停用，不能充值或调账</p>
            <div class="wl-actions">
              <Button variant="outline" v-if="auth.hasPerm('transaction:view')" @click="viewWalletTransactions(row)">查看流水</Button>
              <Button variant="outline" v-if="auth.hasPerm('recharge:view')" @click="viewWalletRecharges(row)">充值记录</Button>
              <Button variant="outline" v-if="auth.hasPerm('wallet:recharge')" :disabled="!!fundReview || !row.is_active" @click="onRecharge(row)">客服充值</Button>
              <Button variant="outline" v-if="auth.hasPerm('wallet:adjust') && row.user_role === 'CUSTOMER'" :disabled="!!fundReview || !row.is_active" @click="onAdjust(row)">调账</Button>
            </div>
          </article>
        </div></template></SimplePagedList>
  </div>

  <Dialog :open="adjustVisible" @update:open="value => { if (!saving) adjustVisible = value }"><DialogContent class="wl-modal" :show-close-button="!saving" @interact-outside="event => { if (saving) event.preventDefault() }" @escape-key-down="event => { if (saving) event.preventDefault() }"><DialogHeader><DialogTitle>钱包调账</DialogTitle><DialogDescription>核对账户、金额与调账原因。</DialogDescription></DialogHeader><p v-if="notice" role="status">{{ notice }}</p><fieldset class="wl-fields" :disabled="saving || fundUnknown"><p>调账对象：{{ walletIdentity(current) }}</p><p>最近读取余额：{{ amountToXaCoin(current?.balance) }} 兴安币</p><label>调整兴安币<Input v-model.number="adjustCoin" type="number" step="0.1" /><span class="tip">正数增加，负数扣减</span></label><label>调账原因<Input v-model="adjustRemark" placeholder="必填，例如误充纠正" maxlength="255" /></label></fieldset><Alert v-if="fundUnknown"><AlertDescription>调账结果未确认，请关闭并核对资金流水，勿重复操作。</AlertDescription></Alert><DialogFooter><Button variant="outline" :disabled="saving" @click="adjustVisible = false">{{ fundUnknown ? '关闭' : '取消' }}</Button><Button :disabled="saving || fundUnknown" @click="onAdjustSave">{{ saving ? '处理中…' : '核对调账' }}</Button></DialogFooter></DialogContent></Dialog>
  <Dialog :open="rechargeVisible" @update:open="value => { if (!saving) rechargeVisible = value }"><DialogContent class="wl-modal" :show-close-button="!saving" @interact-outside="event => { if (saving) event.preventDefault() }" @escape-key-down="event => { if (saving) event.preventDefault() }"><DialogHeader><DialogTitle>客服充值确认</DialogTitle><DialogDescription>核对账户、实收款项和充值凭证。</DialogDescription></DialogHeader><p v-if="notice" role="status">{{ notice }}</p><fieldset class="wl-fields" :disabled="saving || fundUnknown"><Alert><AlertDescription>请先核实客服收款，录入实收人民币；系统按 1 元 = 10 兴安币兑换。</AlertDescription></Alert><div class="wl-form-section"><p class="wl-section-title">充值对象</p><p>{{ current?.user_role === 'PROVIDER' ? '打手' : '老板' }}：{{ walletIdentity(current) }}</p><p>最近读取余额：{{ amountToXaCoin(current?.balance) }} 币</p></div><div class="wl-form-section"><p class="wl-section-title">充值金额</p><label v-if="rechargePackages.length">预存档位<NativeSelect :model-value="selectedPackage" @update:model-value="value => { selectedPackage = String(value); if (value) choosePackage(Number(value)) }"><NativeSelectOption value="">选择预存档位</NativeSelectOption><NativeSelectOption v-for="item in rechargePackages" :key="item.id" :value="String(item.id)">实付 {{ item.amount / 100 }} 元，赠送 {{ item.gift_amount / 100 }} 元，消费积分 {{ item.points_rate / 100 }} 倍</NativeSelectOption></NativeSelect></label><div class="wl-field-grid"><label>实收人民币<Input v-model.number="rechargeYuan" type="number" min="0" step="0.01" /></label><label>赠送兴安币<Input v-model.number="giftCoin" type="number" min="0" step="0.1" /></label></div><p>预计到账：<strong class="wl-credit-preview">{{ rechargeTotalLabel }} 兴安币</strong> <span class="tip">（{{ rechargeYuan }} 元兑换 {{ rechargeCoin }} 币 + 客服赠送）</span></p></div><div class="wl-form-section"><p class="wl-section-title">溯源信息</p><label>交易编号<Input v-model="tradeNo" placeholder="已核实的收款流水号（必填）" maxlength="64" /></label><label>充值凭证<input type="file" accept="image/*" @change="onProofChange" /></label><button v-if="proofPreview" type="button" class="wl-proof-button" @click="proofVisible = true"><img :src="proofPreview" alt="查看充值凭证" class="wl-proof-preview" /></button><p class="tip">请上传已核实的收款截图，同一收款流水只能充值一次。</p></div><label>备注<Input v-model="rechargeRemark" placeholder="充值备注" /></label></fieldset><Alert v-if="fundUnknown"><AlertDescription>充值结果未确认，请关闭并核对充值记录和资金流水，勿重复操作。</AlertDescription></Alert><DialogFooter><Button variant="outline" :disabled="saving" @click="rechargeVisible = false">{{ fundUnknown ? '关闭' : '取消' }}</Button><Button :disabled="saving || fundUnknown" @click="onRechargeSave">{{ saving ? '处理中…' : '核对并入账' }}</Button></DialogFooter></DialogContent></Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  <Dialog v-model:open="proofVisible"><DialogContent><DialogHeader><DialogTitle>充值凭证</DialogTitle><DialogDescription>核对上传的收款截图。</DialogDescription></DialogHeader><img v-if="proofPreview" :src="proofPreview" alt="充值凭证" class="wl-proof-full" /></DialogContent></Dialog>
</template>

<script setup lang="ts">
import { request } from '@/api/request'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { formatRmb, rmbToRawAmount, toCoin } from '@xa/money'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { Popover, PopoverTrigger, PopoverContent } from '@xa/shadcn-vue/popover'
import { Alert, AlertTitle, AlertDescription } from '@xa/shadcn-vue/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import SuggestionInput from '@/components/SuggestionInput.vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { walletApi } from '@/api/modules'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'
import { onBeforeRouteLeave, useRouter } from 'vue-router'

const notice = ref('')
const feedback = { success: (text: string) => { notice.value = text }, warning: (text: string) => { notice.value = text }, error: (text: string) => { notice.value = text } }
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const auth = useAuthStore()
const router = useRouter()
const canReadWallets = computed(() => Boolean(auth.token) && auth.hasPerm('wallet:view'))
const tableRef = ref()
const query = ref<Record<string, any>>({ account_type: 'BOSS', keyword: '', boss_no: '', nickname: '', phone: '' })
const advancedFiltersOpen = ref(false)
const advancedFilterCount = computed(() => ['boss_no', 'nickname', 'phone'].filter(key => Boolean(query.value[key]?.trim())).length)
const hasWalletFilters = computed(() => Boolean(query.value.keyword?.trim()) || advancedFilterCount.value > 0)
function clearWalletFilters(reload: () => void) {
  Object.assign(query.value, { keyword: '', boss_no: '', nickname: '', phone: '' })
  reload()
}
let pageDisposed = false
let pageMounted = false
let realtimeGeneration = 0
let detailSequence = 0
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let refreshRunning = false
let refreshPending = false
let detailRefreshPending = false
let offWalletUpdate: (() => void) | null = null
let offConnected: (() => void) | null = null

function walletDialogVisible() { return adjustVisible.value || rechargeVisible.value }

async function refreshCurrentWallet() {
  const walletId = current.value?.id
  if (pageDisposed || !canReadWallets.value || !walletDialogVisible() || !walletId) return
  const sequence = ++detailSequence
  const revision = auth.sessionRevision
  const accountType = current.value.user_role === 'PROVIDER' ? 'PROVIDER' : 'BOSS'
  try {
    // The detail endpoint defaults to boss wallets; provider drafts need their own account type.
    const res = await request({ url: `/wallets/${walletId}/`, params: { account_type: accountType } })
    if (!pageDisposed && canReadWallets.value && revision === auth.sessionRevision && sequence === detailSequence
      && walletDialogVisible() && current.value?.id === walletId) current.value = res.data
  } catch { /* Keep the user's draft; the HTTP client reports a failed reconciliation. */ }
}

function requestWalletRefresh(accountId?: number) {
  if (pageDisposed || !canReadWallets.value) return
  refreshPending = true
  if (walletDialogVisible() && current.value && (accountId == null || current.value.account_id === accountId)) {
    ++detailSequence
    detailRefreshPending = true
  }
  scheduleWalletRefresh()
}

function scheduleWalletRefresh() {
  if (pageDisposed || !canReadWallets.value || !refreshPending || refreshRunning || refreshTimer !== null) return
  const generation = realtimeGeneration
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    if (generation !== realtimeGeneration || pageDisposed || !canReadWallets.value) return
    refreshPending = false
    refreshRunning = true
    const refreshDetail = detailRefreshPending
    detailRefreshPending = false
    try {
      await Promise.allSettled([tableRef.value?.load(), ...(refreshDetail ? [refreshCurrentWallet()] : [])])
    } finally {
      if (generation === realtimeGeneration) { refreshRunning = false; scheduleWalletRefresh() }
    }
  }, 300)
}

function stopWalletRealtime() {
  ++realtimeGeneration
  ++detailSequence
  offWalletUpdate?.()
  offConnected?.()
  offWalletUpdate = offConnected = null
  if (refreshTimer !== null) clearTimeout(refreshTimer)
  refreshTimer = null
  refreshPending = detailRefreshPending = refreshRunning = false
}

function startWalletRealtime() {
  if (pageDisposed || !canReadWallets.value) return
  offWalletUpdate = adminWs.on('wallet_update', data => {
    if (Number.isSafeInteger(data?.account_id) && data.account_id > 0) requestWalletRefresh(data.account_id)
  })
  offConnected = adminWs.on('connected', () => requestWalletRefresh())
  adminWs.connect()
}

watch([() => auth.sessionRevision, canReadWallets, () => auth.hasPerm('wallet:adjust'), () => auth.hasPerm('wallet:recharge')], () => {
  stopWalletRealtime()
  if (confirmOpen.value) confirmNo()
  adjustVisible.value = rechargeVisible.value = false
  current.value = null
  saving.value = false
  fundUnknown.value = false
  rechargePackages.value = []
  if (pageMounted) startWalletRealtime()
}, { flush: 'sync' })

onMounted(() => { pageMounted = true; startWalletRealtime() })
onBeforeUnmount(() => { pageDisposed = true; stopWalletRealtime(); clearProof() })
onBeforeRouteLeave(() => { if (saving.value) { feedback.warning('正在核对或提交，请稍候'); return false } return true })

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询后端，
 * 取返回记录的该字段值去重后作为下拉提示。
 */
function querySuggest(field: 'boss_no' | 'nickname' | 'phone') {
  return async (keyword: string): Promise<string[]> => {
    const revision = auth.sessionRevision
    if (!keyword || !canReadWallets.value || pageDisposed) {
      return []
    }
    try {
      const res = (await walletApi.list({ account_type: query.value.account_type, [field]: keyword, page: 1, page_size: 10 })) as any
      if (revision !== auth.sessionRevision || !canReadWallets.value || pageDisposed) { return [] }
      const seen = new Set<string>()
      const items: string[] = []
      for (const w of res.data.list || []) {
        const value = w[field]
        if (value && !seen.has(value)) {
          seen.add(value)
          items.push(value)
        }
      }
      return items
    } catch {
      return []
    }
  }
}

const saving = ref(false)
const current = ref<any>(null)
const fundUnknown = ref(false)
const fundReview = ref<{ walletId: number; kind: 'adjust' | 'recharge' } | null>(null)
let fundReviewOwner = ''
function viewWalletRecharges(row: any) {
  if (!row?.id || !auth.hasPerm('recharge:view')) return
  void router.push({ name: 'recharge-records', query: { wallet_id: String(row.id) } })
}

function viewWalletTransactions(row: any) {
  if (!canReadWallets.value || !auth.hasPerm('transaction:view') || !Number.isSafeInteger(row?.id) || row.id <= 0) return
  void router.push({ name: 'transactions', query: { wallet_id: String(row.id) } })
}
function walletIdentity(row: any) { return [row?.nickname || row?.username || '名称待核对', row?.boss_no || `钱包 #${row?.id}`, row?.username].filter(Boolean).join(' · ') }
function fundReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_wallet_fund_review_${id}` : ''
}
function restoreFundReview() {
  const key = fundReviewKey()
  if (key === fundReviewOwner) return
  fundReviewOwner = key; fundReview.value = null
  if (!key) return
  try { const marker = JSON.parse(sessionStorage.getItem(key) || 'null'); if (Number.isSafeInteger(marker?.walletId) && marker.walletId > 0 && ['adjust', 'recharge'].includes(marker.kind)) fundReview.value = marker } catch { /* Current page keeps its in-memory guard. */ }
}
function beginFundReview(walletId: number, kind: 'adjust' | 'recharge') {
  fundReview.value = { walletId, kind }
  const key = fundReviewKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify({ walletId, kind })) } catch { /* Current page keeps its in-memory guard. */ }
}
function clearFundReview() {
  const key = fundReviewKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  fundReview.value = null
}
async function finishFundReview() {
  const marker = fundReview.value, revision = auth.sessionRevision
  if (!marker || saving.value) return
  if (!await askConfirm({ title: '解除资金操作限制', description: '请确认已核对相关记录和请求处理情况；请求可能仍在处理中。', confirmText: '已核对，解除限制', cancelText: '继续核对' })) return
  if (!pageDisposed && revision === auth.sessionRevision && marker === fundReview.value) { clearFundReview(); fundUnknown.value = false; requestWalletRefresh() }
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreFundReview, { immediate: true, flush: 'sync' })
function handleFundError(error: any) {
  const body = error?.response?.data || error, status = error?.response?.status
  const rejected = (!status || status < 500) && ((status >= 400 && status < 500) || (typeof body?.code === 'number' && body.code >= 400 && body.code < 500))
  if (rejected) { clearFundReview(); feedback.error(typeof body?.msg === 'string' && body.msg ? body.msg : '请求被拒绝，请核对信息') }
  else fundUnknown.value = true
}

const adjustVisible = ref(false)
const adjustCoin = ref<number>(0)
const adjustRemark = ref('')
const adjustRequestId = ref('')

function onAdjust(row: any) {
  if (!canReadWallets.value || !auth.hasPerm('wallet:adjust') || pageDisposed || saving.value || fundReview.value || !row.is_active) return
  ++detailSequence
  current.value = row
  notice.value = ''
  adjustCoin.value = 0
  adjustRemark.value = ''
  adjustRequestId.value = crypto.randomUUID()
  fundUnknown.value = false
  adjustVisible.value = true
}

async function onAdjustSave() {
  if (saving.value || fundReview.value || pageDisposed || !canReadWallets.value || !auth.hasPerm('wallet:adjust') || !adjustVisible.value || !current.value) return
  const revision = auth.sessionRevision
  const generation = realtimeGeneration
  const isCurrent = () => !pageDisposed && revision === auth.sessionRevision && generation === realtimeGeneration
    && canReadWallets.value && auth.hasPerm('wallet:adjust')
  if (!adjustRemark.value.trim()) { feedback.warning('请填写调账原因'); return }
  if (!validDecimal(adjustCoin.value, 1)) { feedback.warning('调账金额最多保留 1 位小数'); return }
  const amount = xaCoinToAmount(adjustCoin.value)
  if (amount === 0) {
    feedback.warning('调账金额不能为 0')
    return
  }
  const walletId = current.value.id, balance = current.value.balance, identity = walletIdentity(current.value), remark = adjustRemark.value.trim()
  saving.value = true
  try {
    if (!await askConfirm({ title: '确认调账', description: `账户：${identity}\n最近读取余额：${amountToXaCoin(balance)} 币\n${amount > 0 ? '增加' : '扣减'}：${amountToXaCoin(Math.abs(amount))} 币\n原因：${remark}`, confirmText: '确认调账', cancelText: '返回修改' })) return
    if (!isCurrent() || !adjustVisible.value || current.value?.id !== walletId) return
    if (current.value.balance !== balance || !current.value.is_active) { feedback.warning('钱包状态或余额已变化，请重新核对'); return }
    beginFundReview(walletId, 'adjust')
    await walletApi.action(walletId, 'adjust', { amount, remark, client_request_id: adjustRequestId.value }, true)
    if (!isCurrent()) return
    clearFundReview()
    feedback.success('调账成功')
    adjustVisible.value = false
    requestWalletRefresh()
  } catch (error: any) {
    if (isCurrent()) handleFundError(error)
  } finally {
    if (isCurrent()) saving.value = false
  }
}

const rechargePackages = ref<any[]>([])
function validDecimal(value: unknown, places: number) {
  if (value === '' || value === null || value === undefined) return false
  const n = Number(value), scale = 10 ** places, scaled = n * scale
  return Number.isFinite(n) && Number.isSafeInteger(Math.round(scaled)) && Math.abs(scaled - Math.round(scaled)) < 1e-7
}
const selectedPackage = ref(''), proofVisible = ref(false)
function choosePackage(id:number){const item=rechargePackages.value.find(row=>row.id===id);if(item){rechargeYuan.value=Number(formatRmb(item.amount));giftCoin.value=toCoin(item.gift_amount)}}
const rechargeVisible = ref(false)
const rechargeYuan = ref<number>(0)
const rechargeCoin = computed(() => toCoin(rmbToRawAmount(rechargeYuan.value)))
const giftCoin = ref<number>(0)
const rechargeTotalLabel = computed(() => amountToXaCoin(rmbToRawAmount(rechargeYuan.value) + xaCoinToAmount(giftCoin.value)))
const rechargeRemark = ref('')
const tradeNo = ref('')
const rechargeRequestId = ref('')
const proofFile = ref<File | null>(null)
const proofPreview = ref('')

function onProofChange(event: Event) {
  const raw = (event.target as HTMLInputElement).files?.[0]
  if (!raw) return
  clearProof()
  proofFile.value = raw
  proofPreview.value = URL.createObjectURL(raw)
}
function clearProof() { if (proofPreview.value) URL.revokeObjectURL(proofPreview.value); proofPreview.value = ''; proofFile.value = null }
watch(rechargeVisible, visible => { if (!visible) { proofVisible.value = false; clearProof() } })

async function onRecharge(row: any) {
  if (!canReadWallets.value || !auth.hasPerm('wallet:recharge') || pageDisposed || saving.value || fundReview.value || !row.is_active) return
  const revision = auth.sessionRevision
  const generation = realtimeGeneration
  ++detailSequence
  current.value = row
  notice.value = ''
  rechargeYuan.value = 0
  giftCoin.value = 0
  rechargeRemark.value = ''
  tradeNo.value = ''
  rechargeRequestId.value = `recharge-${Date.now()}-${Math.random().toString(36).slice(2)}`
  proofFile.value = null
  proofPreview.value = ''
  fundUnknown.value = false
  rechargeVisible.value = true
  selectedPackage.value = ''
  rechargePackages.value=[]
  if(row.user_role !== 'PROVIDER'){try{const res=await request({url:'/wallets/recharge-packages/'});if(!pageDisposed && revision===auth.sessionRevision && generation===realtimeGeneration && canReadWallets.value && auth.hasPerm('wallet:recharge') && rechargeVisible.value && current.value?.id===row.id)rechargePackages.value=res.data}catch(_){/* 手动核对仍可充值 */}}
}

async function onRechargeSave() {
  if (saving.value || fundReview.value || pageDisposed || !canReadWallets.value || !auth.hasPerm('wallet:recharge') || !rechargeVisible.value || !current.value) return
  const revision = auth.sessionRevision
  const generation = realtimeGeneration
  const isCurrent = () => !pageDisposed && revision === auth.sessionRevision && generation === realtimeGeneration
    && canReadWallets.value && auth.hasPerm('wallet:recharge')
  if (!tradeNo.value.trim() || !proofFile.value) {
    feedback.warning('请填写收款流水号并上传收款凭证')
    return
  }
  if (!validDecimal(rechargeYuan.value, 2) || Number(rechargeYuan.value) <= 0 || !validDecimal(giftCoin.value, 1) || Number(giftCoin.value) < 0) {
    feedback.warning('实收金额须大于 0 且最多两位小数，赠送币须不小于 0 且最多一位小数')
    return
  }
  const amount = rmbToRawAmount(rechargeYuan.value)
  const giftAmount = xaCoinToAmount(giftCoin.value)
  if (amount <= 0) {
    feedback.warning('实充金额必须大于 0')
    return
  }
  const walletId = current.value.id, accountId = current.value.account_id, userId = current.value.user, identity = walletIdentity(current.value), balance = current.value.balance
  saving.value = true
  try {
    if (!await askConfirm({ title: '确认收款并入账', description: `账户：${identity}\n实收：${rechargeYuan.value} 元\n赠送：${giftCoin.value} 币\n预计到账：${amountToXaCoin(amount + giftAmount)} 币\n收款流水：${tradeNo.value.trim()}`, confirmText: '确认入账', cancelText: '返回修改' })) return
    if (!isCurrent() || !rechargeVisible.value || current.value?.id !== walletId) return
    if (current.value.balance !== balance || !current.value.is_active || current.value.account_id !== accountId || current.value.user !== userId) { feedback.warning('钱包对象、状态或余额已变化，请重新核对'); return }
    const fd = new FormData()
    fd.append(accountId ? 'account_id' : 'user_id', String(accountId || userId))
    fd.append('amount', String(amount))
    fd.append('gift_amount', String(giftAmount))
    fd.append('remark', rechargeRemark.value)
    fd.append('trade_no', tradeNo.value)
    fd.append('client_request_id', rechargeRequestId.value)
    if (proofFile.value) fd.append('proof_image', proofFile.value)
    beginFundReview(walletId, 'recharge')
    await request({ url: '/wallets/recharge/', method: 'post', data: fd, silent: true })
    if (!isCurrent()) return
    clearFundReview()
    feedback.success(`已入账 ${amountToXaCoin(amount + giftAmount)} 兴安币`)
    rechargeVisible.value = false
    requestWalletRefresh()
  } catch (error: any) {
    if (isCurrent()) handleFundError(error)
  } finally {
    if (isCurrent()) saving.value = false
  }
}
</script>

<style scoped lang="scss">
.wl-modal { max-height:90vh; overflow-y:auto; }
.wl-fields { display:grid; gap:12px; border:0; padding:0; min-width:0; }
.wl-fields label { display:grid; gap:4px; min-width:0; }
.wl-field-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }
.wl-proof-button { display:block; width:fit-content; }
.wl-proof-full { max-height:70vh; max-width:100%; object-fit:contain; margin:auto; }
@media (max-width:600px) { .wl-field-grid { grid-template-columns:1fr; } }
.wl-row-actions { display:flex; flex-wrap:wrap; gap:4px; }
.wl-avatar { display:grid; place-items:center; width:32px; height:32px; flex:none; border-radius:50%; background:var(--muted); }
.wl-mobile-list { display:none; }
@media (max-width:720px) { .wl-desktop-table { display:none; } .wl-mobile-list { display:grid; } }
.wl-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.wl-page :deep(.page-container) {
  padding: 0;
}
.wl-heading { display: flex; align-items: center; gap: 4px; }
.wl-filter-primary, .wl-filter-actions, .wl-advanced-filters { display: flex; align-items: center; gap: 8px; }
.wl-filter-primary { flex: 1 1 340px; min-width: 0; }
.wl-filter-keyword { flex: 1 1 180px; min-width: 0; }
.wl-filter-type { flex: 0 0 130px; }
.wl-filter-actions { flex-wrap: wrap; }
.wl-advanced-filters { flex: 1 0 100%; flex-wrap: wrap; }
@media (max-width: 720px) { .wl-filter-type { flex-basis: 116px; } .wl-filter-actions { width: 100%; } }
.wl-help { width: var(--control-height-sm); height: var(--control-height-sm); border: 0; border-radius: 50%; color: var(--primary); background: transparent; cursor: pointer; }
.wl-help:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.wl-mobile-list { gap: 12px; }
.wl-card { padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.wl-card-head { display: flex; align-items: center; gap: 10px; }
.wl-identity { display: grid; flex: 1; min-width: 0; gap: 3px; }
.wl-identity strong, .wl-identity span { overflow-wrap: anywhere; }
.wl-identity span, .wl-note { color: var(--muted-foreground); font-size: 12px; }
.wl-table-identity { display: grid; gap: 3px; line-height: 1.4; white-space: normal; overflow-wrap: anywhere; }
.wl-table-identity span { color: var(--muted-foreground); font-size: 12px; }
.wl-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px; margin: 12px 0; padding-top: 12px; border-top: 1px solid var(--border); }
.wl-facts div { min-width: 0; }
.wl-facts dt { color: var(--muted-foreground); font-size: 12px; }
.wl-facts dd { margin: 2px 0 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.wl-primary-amount { color: var(--primary); font-weight: 650; }
.wl-note { margin: 0 0 8px; }
.wl-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
.wl-recharge-alert {
  margin-bottom: 18px;
}
.wl-credit-preview {
  margin-right: 8px;
  color: var(--primary);
  font-size: 18px;
}
.wl-proof-preview {
  display: block;
  width: 120px;
  height: 120px;
  margin-top: 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
</style>
