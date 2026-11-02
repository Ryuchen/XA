<template>
  <div class="wd-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">先领取申请，再核对收款信息并完成人工打款；1 元 = 10 兴安币。</p>
      <div class="ap-page-head-actions">
        <Button v-if="auth.hasPerm('withdraw:audit')" variant="outline" @click="openConfig">
          {{ configLoaded ? `最低提现：${amountToXaCoin(minAmount)} 兴安币 · 税率 ${taxRate}%` : '提现规则 · 待读取' }}
        </Button>
      </div>
    </div>

    <Alert v-if="auditReview"><AlertTitle>提现审核结果待核对</AlertTitle><AlertDescription>
      <p>申请 #{{ auditReview.id }} 的{{ auditReview.action === 'approve' ? '通过' : '驳回' }}结果未确认。{{ auditReview.action === 'approve' ? '请核对申请状态和打款凭证，勿重复打款。' : '请核对申请状态及余额退还记录。' }}</p>
      <p v-if="auditReview.checked">当前状态：{{ auditReview.checked.status_display || WITHDRAW_STATUS[auditReview.checked.status]?.label || '待核对' }}<template v-if="auditReview.checked.status === 'PENDING'">。请求可能仍在处理，请先核实。</template></p>
      <p v-if="auditReviewError">{{ auditReviewError }}</p>
      <Button variant="outline" :disabled="auditReviewLoading" @click="checkAuditReview">{{ auditReviewLoading ? '读取中…' : '重新核对' }}</Button>
      <Button v-if="auditReview.checked" variant="outline" :disabled="auditReviewLoading" @click="onDetail(auditReview.checked)">查看详情</Button>
      <Button v-if="auditReview.checked" variant="outline" :disabled="auditReviewLoading" @click="finishAuditReview">{{ auditReview.checked.status === 'PENDING' ? '已核对，解除限制' : '完成核对' }}</Button>
    </AlertDescription></Alert>

    <Alert v-if="claimScopeUnsupported && query.claim_scope" variant="destructive"><AlertTitle>领取状态筛选暂不可用</AlertTitle><AlertDescription>请更新后台服务，或清除领取状态筛选后重新查询。</AlertDescription></Alert>
    <p v-if="notice && !detailVisible && !configVisible && !releaseVisible && !promptOpen" role="status">{{ notice }}</p>
    <SimplePagedList v-if="canReadWithdrawals" :key="auth.sessionRevision" ref="tableRef" :fetcher="fetchWithdrawals" :query="query"><template #toolbar="{ reload }"><div class="wd-filters"><SuggestionInput v-model="query.provider_username" :fetch-suggestions="querySuggest('provider_username')" placeholder="陪玩账号" label="按陪玩账号筛选" @select="reload" @keyup.enter="reload" @clear="reload" /><SuggestionInput v-model="query.provider_nickname" :fetch-suggestions="querySuggest('provider_nickname')" placeholder="陪玩昵称" label="按陪玩昵称筛选" @select="reload" @keyup.enter="reload" @clear="reload" /><SuggestionInput v-model="query.payee_name" :fetch-suggestions="querySuggest('payee_name')" placeholder="收款人" label="按收款人筛选" @select="reload" @keyup.enter="reload" @clear="reload" /><SuggestionInput v-model="query.payee_account" :fetch-suggestions="querySuggest('payee_account')" placeholder="收款账号" label="按收款账号筛选" @select="reload" @keyup.enter="reload" @clear="reload" /><NativeSelect v-model="query.status" aria-label="审核状态" @update:model-value="reload"><NativeSelectOption value="">全部审核状态</NativeSelectOption><NativeSelectOption v-for="(v,k) in WITHDRAW_STATUS" :key="k" :value="k">{{ v.label }}</NativeSelectOption></NativeSelect><NativeSelect v-model="query.claim_scope" aria-label="领取状态" @update:model-value="reload"><NativeSelectOption value="">全部领取状态</NativeSelectOption><NativeSelectOption value="unclaimed">未领取</NativeSelectOption><NativeSelectOption value="mine">我已领取</NativeSelectOption><NativeSelectOption value="other">其他客服已领取</NativeSelectOption></NativeSelect><Button @click="reload">查询</Button></div></template>
      <template #default="{ rows }"><Table class="wd-desktop-table"><TableHeader><TableRow><TableHead>ID</TableHead><TableHead>陪玩</TableHead><TableHead>提现金额</TableHead><TableHead>应打款（元）</TableHead><TableHead>状态</TableHead><TableHead>领取状态</TableHead><TableHead>收款方式</TableHead><TableHead>收款人</TableHead><TableHead>收款账号</TableHead><TableHead>提交时间</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell>{{ row.id }}</TableCell><TableCell>{{ row.user_name }}</TableCell><TableCell>{{ amountToXaCoin(row.amount) }} 兴安币</TableCell><TableCell>¥{{ formatRmb(row.actual_amount) }}</TableCell><TableCell><Badge variant="outline">{{ row.status_display }}</Badge></TableCell><TableCell>{{ claimLabel(row) }}</TableCell><TableCell>{{ row.payee_method_display }}</TableCell><TableCell>{{ row.payee_name }}</TableCell><TableCell>{{ row.payee_account }}</TableCell><TableCell>{{ formatDateTime(row.created_at) }}</TableCell><TableCell><div class="wd-row-actions"><Button variant="link" @click="onDetail(row)">详情</Button><template v-if="auth.hasPerm('withdraw:audit') && row.status === 'PENDING'"><Button v-if="claimState(row) === 'unclaimed'" variant="link" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onClaim(row)">领取</Button><template v-if="claimState(row) === 'mine'"><Button variant="link" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onApprove(row)">通过</Button><Button variant="link" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onReject(row)">驳回</Button></template></template><Button v-if="canRelease(row)" variant="link" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onRelease(row)">释放领取</Button></div></TableCell></TableRow></TableBody></Table><div class="wd-mobile-list" role="list" aria-label="提现申请">
          <article v-for="row in rows" :key="row.id" class="wd-mobile-card" role="listitem">
            <div class="wd-mobile-head"><strong>申请 #{{ row.id }} · {{ row.user_name || '—' }}</strong><Badge variant="outline">{{ row.status_display }}</Badge></div>
            <dl><div><dt>提现</dt><dd>{{ amountToXaCoin(row.amount) }} 兴安币</dd></div><div><dt>应打款</dt><dd>¥{{ formatRmb(row.actual_amount) }}</dd></div><div><dt>收款方式</dt><dd>{{ row.payee_method_display }}</dd></div><div><dt>收款人</dt><dd>{{ row.payee_name || '—' }}</dd></div><div><dt>领取状态</dt><dd>{{ claimLabel(row) }}</dd></div></dl>
            <p>提交 {{ formatDateTime(row.created_at) }}</p>
            <div class="wd-mobile-actions"><Button variant="outline" @click="onDetail(row)">详情</Button><template v-if="auth.hasPerm('withdraw:audit') && row.status === 'PENDING'"><Button variant="outline" v-if="claimState(row) === 'unclaimed'"  :disabled="auditBusy || releaseBusy || !!auditReview" @click="onClaim(row)">领取</Button><template v-if="claimState(row) === 'mine'"><Button variant="outline" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onApprove(row)">通过</Button><Button variant="outline" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onReject(row)">驳回</Button></template></template><Button variant="outline" v-if="canRelease(row)" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onRelease(row)">释放领取</Button></div>
          </article>
        </div></template></SimplePagedList>
  </div>

  <Dialog v-model:open="detailVisible"><DialogContent class="wd-dialog"><DialogHeader><DialogTitle>提现详情</DialogTitle><DialogDescription>查看申请、收款和领取信息。</DialogDescription></DialogHeader><p v-if="notice" role="status">{{ notice }}</p>
    <p v-if="detailLoading" role="status">正在读取提现详情…</p>
    <Alert v-else-if="detailFailed" variant="destructive"><AlertTitle>提现详情加载失败，请重试</AlertTitle><AlertDescription>
      <Button variant="link" @click="refreshCurrentWithdrawal">重新加载</Button>
    </AlertDescription></Alert>
    <template v-else-if="current">
      <dl class="wd-details">
        <div><dt>陪玩</dt><dd>{{ current.user_name }}</dd></div>
        <div><dt>提现金额</dt><dd>{{ amountToXaCoin(current.amount) }} 兴安币</dd></div>
        <div><dt>税率</dt><dd>{{ current.tax_rate }}%</dd></div>
        <div><dt>税额</dt><dd>{{ amountToXaCoin(current.tax_amount) }} 兴安币</dd></div>
        <div><dt>扣税后金额</dt><dd>{{ amountToXaCoin(current.actual_amount) }} 兴安币</dd></div>
        <div><dt>应打款人民币</dt><dd>¥{{ formatRmb(current.actual_amount) }}</dd></div>
        <div><dt>收款方式</dt><dd>{{ current.payee_method_display }}</dd></div>
        <div><dt>收款人</dt><dd>{{ current.payee_name }}</dd></div>
        <div><dt>收款账号</dt><dd>{{ current.payee_account }}</dd></div>
        <div><dt>状态</dt><dd>{{ current.status_display }}</dd></div>
        <div><dt>领取状态</dt><dd>{{ claimLabel(current) }}</dd></div>
        <div v-if="current.claimed_at"><dt>领取时间</dt><dd>{{ formatDateTime(current.claimed_at) }}</dd></div>
        <div><dt>备注</dt><dd>{{ current.remark || '-' }}</dd></div>
        <div v-if="current.status === 'REJECTED'"><dt>驳回原因</dt><dd>
          {{ current.audit_remark || '-' }}
        </dd></div>
        <div v-if="current.status === 'APPROVED'"><dt>打款流水号</dt><dd>
          {{ current.payout_reference || '-' }}
        </dd></div>
        <div v-if="current.status === 'APPROVED'"><dt>实际打款时间</dt><dd>
          {{ current.paid_at ? formatDateTime(current.paid_at) : '-' }}
        </dd></div>
        <div><dt>审核人</dt><dd>{{ current.auditor_name || '-' }}</dd></div>
        <div><dt>提交时间</dt><dd>{{ formatDateTime(current.created_at) }}</dd></div>
        <div><dt>审核时间</dt><dd>{{ current.audited_at ? formatDateTime(current.audited_at) : '-' }}</dd></div>
      </dl>
    </template>
  </DialogContent></Dialog>

  <Dialog :open="releaseVisible" @update:open="value => { if (!releaseBusy) releaseVisible = value }"><DialogContent class="wd-dialog" :show-close-button="!releaseBusy" @interact-outside="event => { if (releaseBusy) event.preventDefault() }" @escape-key-down="event => { if (releaseBusy) event.preventDefault() }"><DialogHeader><DialogTitle>释放提现领取</DialogTitle><DialogDescription>核实领取情况后再释放申请。</DialogDescription></DialogHeader><template v-if="releaseRow"><p class="wd-release-summary">申请 #{{ releaseRow.id }} · {{ releaseRow.claimed_by_name || '客服' }}已领取</p><Alert><AlertTitle>释放后其他客服可重新领取</AlertTitle><AlertDescription>请先与领取人及财务核实，确认尚未线下打款；如已打款或无法确认，请勿释放。</AlertDescription></Alert><Alert v-if="releaseError" variant="destructive"><AlertDescription>{{ releaseError }}</AlertDescription></Alert><fieldset class="wd-release-fields" :disabled="releaseBusy"><label>释放原因<textarea v-model="releaseReason" rows="3" maxlength="255" placeholder="填写核实情况或释放原因" /></label><label class="wd-check"><input v-model="releaseConfirmed" type="checkbox" />我已核实此申请尚未打款</label></fieldset></template><DialogFooter><Button variant="outline" :disabled="releaseBusy" @click="releaseVisible = false">取消</Button><Button :disabled="releaseBusy || !releaseRow || !releaseConfirmed || !releaseReason.trim() || releaseStale" @click="submitRelease">{{ releaseBusy ? '处理中…' : '确认释放' }}</Button></DialogFooter></DialogContent></Dialog>

  <Dialog :open="configVisible" @update:open="value => { if (!savingConfig) configVisible = value }"><DialogContent class="wd-dialog" :show-close-button="!savingConfig" @interact-outside="event => { if (savingConfig) event.preventDefault() }" @escape-key-down="event => { if (savingConfig) event.preventDefault() }"><DialogHeader><DialogTitle>提现配置</DialogTitle><DialogDescription>设置后续提现申请的最低金额和税率。</DialogDescription></DialogHeader><p v-if="notice" role="status">{{ notice }}</p><Alert v-if="configError" variant="destructive"><AlertDescription>{{ configError }} <Button v-if="!configLoaded" variant="link" :disabled="configLoading" @click="loadConfig">重新读取</Button></AlertDescription></Alert><p v-if="configLoading" role="status">正在读取提现配置…</p><fieldset v-else-if="configLoaded" class="wd-config-fields" :disabled="savingConfig"><label>最低提现（兴安币）<Input v-model.number="configInput" type="number" min="0" step="0.1" /><span class="tip">陪玩单次提现金额不得低于该值。</span></label><label>提现税率（%）<Input v-model.number="taxInput" type="number" min="0" max="100" step="1" /><span class="tip">按申请金额计算，具体金额以申请时的快照为准。</span></label><p class="tip">多人修改时按最后保存的配置生效。</p></fieldset><DialogFooter><Button variant="outline" :disabled="savingConfig" @click="configVisible = false">取消</Button><Button :disabled="savingConfig || !configLoaded || configLoading" @click="saveConfig">{{ savingConfig ? '保存中…' : '保存' }}</Button></DialogFooter></DialogContent></Dialog>
  <Dialog :open="promptOpen" @update:open="value => { if (!value) promptBox.close() }"><DialogContent @interact-outside="event => event.preventDefault()"><DialogHeader><DialogTitle>{{ promptTitle }}</DialogTitle><DialogDescription>{{ promptMessage }}</DialogDescription></DialogHeader><label v-if="promptInput" class="wd-prompt-input">{{ promptPlaceholder }}<Input v-model="promptText" :placeholder="promptPlaceholder" maxlength="255" /></label><p v-if="promptError" role="alert">{{ promptError }}</p><DialogFooter><Button variant="outline" @click="promptBox.close()">{{ promptCancelText }}</Button><Button @click="promptBox.submit()">{{ promptConfirmText }}</Button></DialogFooter></DialogContent></Dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useWithdrawalPrompt } from '@/composables/useWithdrawalPrompt'
import SimplePagedList from '@/components/SimplePagedList.vue'
import SuggestionInput from '@/components/SuggestionInput.vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertTitle, AlertDescription } from '@xa/shadcn-vue/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { withdrawApi, withdrawConfigApi } from '@/api/modules'
import { WITHDRAW_STATUS, PAYEE_METHOD } from '@/utils/dict'
import { amountToXaCoin, xaCoinToAmount, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { formatRmb } from '@xa/money'
import { adminWs } from '@/utils/ws'
import { useRoute } from 'vue-router'

const notice = ref(''), noticeKind = ref<'success' | 'warning' | 'error'>('success')
const feedback = { success: (value: string) => { noticeKind.value = 'success'; notice.value = value }, warning: (value: string) => { noticeKind.value = 'warning'; notice.value = value }, error: (value: string) => { noticeKind.value = 'error'; notice.value = value } }
const promptBox = useWithdrawalPrompt()
const { open: promptOpen, title: promptTitle, message: promptMessage, text: promptText, error: promptError, input: promptInput, confirmText: promptConfirmText, cancelText: promptCancelText, placeholder: promptPlaceholder } = promptBox
const auth = useAuthStore()
const route = useRoute()
const routeStatus = (value: unknown) => typeof value === 'string' && Object.prototype.hasOwnProperty.call(WITHDRAW_STATUS, value) ? value : ''
const routeClaimScope = (value: unknown) => value === 'unclaimed' || value === 'mine' || value === 'other' ? value : ''
const canReadWithdrawals = computed(() => Boolean(auth.token) && auth.hasPerm('withdraw:view'))
const tableRef = ref()
const claimScopeUnsupported = ref(false)
let withdrawalListSequence = 0
async function fetchWithdrawals(params: Record<string, any>) {
  const sequence = ++withdrawalListSequence
  const response = await withdrawApi.list(params)
  const result = response.data as typeof response.data & { claim_scope_applied?: string }
  const unsupported = !!params.claim_scope && result.claim_scope_applied !== params.claim_scope
  if (sequence === withdrawalListSequence) claimScopeUnsupported.value = unsupported
  if (unsupported) throw new Error('领取状态筛选未确认')
  return response
}
const query = reactive<Record<string, any>>({
  provider_username: '',
  provider_nickname: '',
  payee_name: '',
  payee_account: '',
  status: routeStatus(route.query.status),
  claim_scope: routeClaimScope(route.query.claim_scope),
})
watch([() => route.query.status, () => route.query.claim_scope], ([statusValue, claimValue]) => {
  const status = routeStatus(statusValue), claimScope = routeClaimScope(claimValue)
  if (query.status !== status || query.claim_scope !== claimScope) {
    query.status = status; query.claim_scope = claimScope; tableRef.value?.reload()
  }
})

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询提现单后，
 * 取返回记录对应字段值去重后作为下拉提示。
 */
function querySuggest(field: 'provider_username' | 'provider_nickname' | 'payee_name' | 'payee_account') {
  return async (keyword: string): Promise<string[]> => {
    const revision = auth.sessionRevision
    if (!keyword || !canReadWithdrawals.value || pageDisposed) {
      return []
    }
    try {
      const res = (await withdrawApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      if (revision !== auth.sessionRevision || !canReadWithdrawals.value || pageDisposed) { return [] }
      const seen = new Set<string>()
      const items: string[] = []
      for (const w of res.data?.list || []) {
        const value = w[field] || ''
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

const detailVisible = ref(false)
const current = ref<any>(null)
const detailLoading = ref(false)
const detailFailed = ref(false)
const auditBusy = ref(false)
const releaseBusy = ref(false), releaseVisible = ref(false), releaseConfirmed = ref(false), releaseStale = ref(false)
const releaseRow = ref<any>(null), releaseReason = ref(''), releaseError = ref('')
let releaseSequence = 0
type AuditAction = 'approve' | 'reject'
type AuditReview = { id: number; action: AuditAction; checked: any | null }
const auditReview = ref<AuditReview | null>(null)
const auditReviewLoading = ref(false), auditReviewError = ref('')
let auditReviewSequence = 0

function claimState(row: any): 'unclaimed' | 'mine' | 'other' | 'unknown' {
  if (!row || !Object.prototype.hasOwnProperty.call(row, 'claimed_by_account') || typeof row.claim_is_mine !== 'boolean') return 'unknown'
  if (row.claimed_by_account === null) return row.claim_is_mine ? 'unknown' : 'unclaimed'
  if (!Number.isSafeInteger(row.claimed_by_account) || row.claimed_by_account <= 0) return 'unknown'
  return row.claim_is_mine ? 'mine' : 'other'
}

function claimLabel(row: any) {
  switch (claimState(row)) {
    case 'unclaimed': return '未领取'
    case 'mine': return '我已领取'
    case 'other': return `${row.claimed_by_name || '其他客服'}已领取`
    default: return '领取状态待核对'
  }
}

function canRelease(row: any) {
  return canReadWithdrawals.value && auth.hasPerm('withdraw:release') && row?.status === 'PENDING'
    && ['mine', 'other'].includes(claimState(row)) && typeof row.claimed_at === 'string' && !!row.claimed_at
}

watch([releaseVisible, releaseBusy], ([visible, busy]) => { if (!visible && !busy) resetRelease() }, { flush: 'post' })
function resetRelease() {
  if (releaseBusy.value) return
  releaseRow.value = null; releaseReason.value = ''; releaseConfirmed.value = false
  releaseStale.value = false; releaseError.value = ''
}

async function onRelease(row: any) {
  if (!canRelease(row) || pageDisposed || auditBusy.value || releaseBusy.value || auditReview.value) return
  const sequence = ++releaseSequence, revision = auth.sessionRevision
  const active = () => !pageDisposed && canReadWithdrawals.value && auth.hasPerm('withdraw:release')
    && revision === auth.sessionRevision && sequence === releaseSequence
  releaseBusy.value = true
  try {
    const result = await withdrawApi.retrieve(row.id, true)
    if (!active()) return
    const latest = result.data
    if (latest?.id !== row.id || !canRelease(latest)) {
      feedback.warning('申请或领取状态已变化，请刷新后核对。')
      requestWithdrawalRefresh(row.id)
      return
    }
    releaseRow.value = latest; releaseReason.value = ''; releaseConfirmed.value = false
    releaseStale.value = false; releaseError.value = ''; releaseVisible.value = true
  } catch {
    if (active()) { feedback.error('领取状态读取失败，请重试。'); requestWithdrawalRefresh(row.id) }
  } finally { if (sequence === releaseSequence) releaseBusy.value = false }
}

async function submitRelease() {
  const row = releaseRow.value, reason = releaseReason.value.trim()
  if (!releaseVisible.value || !canRelease(row) || !releaseConfirmed.value || !reason || reason.length > 255
    || releaseStale.value || releaseBusy.value || auditBusy.value || auditReview.value || pageDisposed) return
  const sequence = ++releaseSequence, revision = auth.sessionRevision
  const active = () => !pageDisposed && canReadWithdrawals.value && auth.hasPerm('withdraw:release')
    && revision === auth.sessionRevision && sequence === releaseSequence
  releaseBusy.value = true; releaseError.value = ''
  try {
    const result = await withdrawApi.action(row.id, 'release-claim', {
      confirmed_unpaid: true, reason, expected_claimed_by_account: row.claimed_by_account, expected_claimed_at: row.claimed_at,
    }, true)
    if (!active()) return
    if (result.data?.id !== row.id || result.data?.status !== 'PENDING' || claimState(result.data) !== 'unclaimed') throw new Error('invalid release response')
    releaseVisible.value = false
    feedback.success('已释放，其他客服可重新领取')
  } catch (error: any) {
    if (!active()) return
    const status = error?.response?.status || error?.code
    releaseStale.value = true
    releaseError.value = status === 400 || status === 403 || status === 409
      ? (error?.response?.data?.msg || error?.msg || '释放被拒绝，请刷新后核对。')
      : '释放结果未确认，请关闭后刷新并核对领取状态。'
  } finally {
    if (sequence === releaseSequence) { releaseBusy.value = false; requestWithdrawalRefresh(row.id) }
  }
}

function auditReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_withdraw_audit_review_${id}` : ''
}
function writeAuditMarker(id: number, action: AuditAction) {
  const key = auditReviewKey()
  if (!key) return
  try { sessionStorage.setItem(key, JSON.stringify({ id, action })) } catch { /* Keep the current page locked if storage is unavailable. */ }
}
function clearAuditMarker() {
  const key = auditReviewKey()
  if (!key) return
  try { sessionStorage.removeItem(key) } catch { /* The visible state remains authoritative in this page. */ }
}
function restoreAuditMarker() {
  const key = auditReviewKey()
  if (!key || !canReadWithdrawals.value) return
  try {
    const marker = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (Number.isSafeInteger(marker?.id) && marker.id > 0 && ['approve', 'reject'].includes(marker.action)) auditReview.value = { ...marker, checked: null }
  } catch { /* Unavailable storage cannot provide recovery across reloads. */ }
}
function forgetAuditReview() {
  ++auditReviewSequence; auditReview.value = null; auditReviewLoading.value = false; auditReviewError.value = ''
  clearAuditMarker()
}
async function checkAuditReview() {
  const review = auditReview.value
  if (!review || auditReviewLoading.value || !canReadWithdrawals.value || pageDisposed) return
  const sequence = ++auditReviewSequence, revision = auth.sessionRevision
  auditReviewLoading.value = true; auditReviewError.value = ''; review.checked = null
  try {
    const res = await withdrawApi.retrieve(review.id, true)
    if (sequence !== auditReviewSequence || revision !== auth.sessionRevision || auditReview.value !== review || pageDisposed) return
    if (res.data?.id !== review.id || !['PENDING', 'APPROVED', 'REJECTED'].includes(res.data?.status)) throw new Error('invalid withdrawal')
    review.checked = res.data
    withdrawalStatuses.set(review.id, res.data.status)
    requestWithdrawalRefresh(review.id)
  } catch {
    if (sequence === auditReviewSequence && revision === auth.sessionRevision && auditReview.value === review && !pageDisposed) auditReviewError.value = '读取失败，请重试。'
  } finally { if (sequence === auditReviewSequence && revision === auth.sessionRevision && !pageDisposed) auditReviewLoading.value = false }
}
async function finishAuditReview() {
  const review = auditReview.value, checked = review?.checked
  if (!review || !checked || auditReviewLoading.value) return
  const sequence = auditReviewSequence, revision = auth.sessionRevision
  if (checked.status === 'PENDING') {
    const context = review.action === 'approve' ? '请先核对打款凭证及处理记录。' : '请先核对申请状态及余额退还记录。'
    try { await promptBox.confirm(`当前仍为待审核，${context}原请求可能仍在处理；解除限制不会修改提现状态或发起打款。`, '确认已核对', { type: 'warning', confirmButtonText: '解除限制', cancelButtonText: '继续核对' }) }
    catch { return }
  }
  if (sequence !== auditReviewSequence || revision !== auth.sessionRevision || auditReview.value !== review || review.checked !== checked || pageDisposed) return
  forgetAuditReview()
}
let detailSequence = 0
let pageDisposed = false
let pageMounted = false
let realtimeGeneration = 0
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let refreshRunning = false
let refreshPending = false
let detailRefreshPending = false
let offWithdrawalUpdate: (() => void) | null = null
let offConnected: (() => void) | null = null
let auditSequence = 0
let pendingPromptId: number | null = null
let cancelPendingPrompt: (() => void) | null = null
const withdrawalVersions = new Map<number, number>()
const withdrawalStatuses = new Map<number, string>()

function clearWithdrawalDetail() {
  ++detailSequence
  detailRefreshPending = false
  current.value = null
  detailLoading.value = detailFailed.value = false
}

watch(detailVisible, visible => { if (!visible) clearWithdrawalDetail() }, { flush: 'sync' })

function isCurrentDetail(sequence: number, revision: number, withdrawalId: number | string) {
  return !pageDisposed && canReadWithdrawals.value && auth.sessionRevision === revision
    && sequence === detailSequence && detailVisible.value && current.value?.id === withdrawalId
}

async function refreshCurrentWithdrawal() {
  const withdrawalId = current.value?.id
  if (!detailVisible.value || !withdrawalId || !canReadWithdrawals.value || pageDisposed) return
  const sequence = ++detailSequence
  const revision = auth.sessionRevision
  detailLoading.value = true
  detailFailed.value = false
  try {
    const res = await withdrawApi.retrieve(withdrawalId)
    if (isCurrentDetail(sequence, revision, withdrawalId)) current.value = res.data
  } catch {
    if (isCurrentDetail(sequence, revision, withdrawalId)) detailFailed.value = true
  } finally {
    if (isCurrentDetail(sequence, revision, withdrawalId)) detailLoading.value = false
  }
}

// Pushes invalidate HTTP data without resetting the current filters or page.
function requestWithdrawalRefresh(withdrawalId?: number | string) {
  if (pageDisposed || !canReadWithdrawals.value) return
  refreshPending = true
  if (detailVisible.value && current.value && (withdrawalId == null || current.value.id === withdrawalId)) {
    ++detailSequence
    detailRefreshPending = true
  }
  scheduleWithdrawalRefresh()
}

function scheduleWithdrawalRefresh() {
  if (pageDisposed || !canReadWithdrawals.value || !refreshPending || refreshRunning || refreshTimer !== null) return
  const generation = realtimeGeneration
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    if (generation !== realtimeGeneration || pageDisposed || !canReadWithdrawals.value) return
    refreshPending = false
    refreshRunning = true
    const refreshDetail = detailRefreshPending
    detailRefreshPending = false
    try {
      await Promise.allSettled([tableRef.value?.load(), ...(refreshDetail ? [refreshCurrentWithdrawal()] : [])])
    } finally {
      if (generation === realtimeGeneration) { refreshRunning = false; scheduleWithdrawalRefresh() }
    }
  }, 300)
}

function invalidatePendingAudit(force = false) {
  // Reconnects invalidate only confirmations that have not been submitted.
  // An in-flight audit remains locked until its request settles.
  if (pendingPromptId === null && !force) return false
  const hadPrompt = pendingPromptId !== null
  ++auditSequence
  auditBusy.value = false
  if (pendingPromptId !== null) { cancelPendingPrompt?.(); promptBox.close() }
  cancelPendingPrompt = null
  pendingPromptId = null
  return hadPrompt
}

function onWithdrawalStatusUpdate(data: any) {
  if (!Number.isSafeInteger(data?.withdraw_id) || data.withdraw_id <= 0
    || !Number.isSafeInteger(data?.account_id) || data.account_id <= 0
    || !['PENDING', 'APPROVED', 'REJECTED'].includes(data?.status)) return
  withdrawalVersions.set(data.withdraw_id, (withdrawalVersions.get(data.withdraw_id) || 0) + 1)
  withdrawalStatuses.set(data.withdraw_id, data.status)
  if (releaseVisible.value && releaseRow.value?.id === data.withdraw_id && !releaseBusy.value) {
    releaseStale.value = true
    releaseError.value = '申请状态已变化，请关闭后刷新并重新核对。'
  }
  const review = auditReview.value
  if (review && review.id === data.withdraw_id) { ++auditReviewSequence; review.checked = null; auditReviewLoading.value = false }
  if (pendingPromptId === data.withdraw_id && invalidatePendingAudit()) {
    feedback.warning('提现领取或审核状态已变化，请重新核对；如已线下打款，请先核对凭证，勿重复打款。')
  }
  requestWithdrawalRefresh(data.withdraw_id)
}

function stopWithdrawalRealtime() {
  ++realtimeGeneration
  offWithdrawalUpdate?.()
  offConnected?.()
  offWithdrawalUpdate = offConnected = null
  if (refreshTimer !== null) clearTimeout(refreshTimer)
  refreshTimer = null
  refreshPending = detailRefreshPending = refreshRunning = false
  withdrawalVersions.clear()
  withdrawalStatuses.clear()
  invalidatePendingAudit(true)
}

function startWithdrawalRealtime() {
  if (pageDisposed || !canReadWithdrawals.value) return
  offWithdrawalUpdate = adminWs.on('withdraw_status_update', onWithdrawalStatusUpdate)
  offConnected = adminWs.on('connected', () => {
    if (invalidatePendingAudit()) feedback.warning('连接已恢复，请重新核对提现状态；如已线下打款，请先核对凭证，勿重复打款。')
    if (releaseVisible.value && !releaseBusy.value) {
      releaseStale.value = true
      releaseError.value = '连接已恢复，请关闭后刷新并重新核对领取状态。'
    }
    if (auditReview.value) { ++auditReviewSequence; auditReview.value.checked = null; auditReviewLoading.value = false }
    requestWithdrawalRefresh()
  })
  adminWs.connect()
}

watch([() => auth.sessionRevision, canReadWithdrawals, () => auth.hasPerm('withdraw:audit'), () => auth.hasPerm('withdraw:release')], () => {
  stopWithdrawalRealtime()
  ++withdrawalListSequence; claimScopeUnsupported.value = false
  ++releaseSequence; releaseBusy.value = false; releaseVisible.value = false; resetRelease()
  ++auditReviewSequence; auditReview.value = null; auditReviewLoading.value = false; auditReviewError.value = ''
  ++configSequence; configLoaded.value = configLoading.value = savingConfig.value = false; configError.value = ''
  detailVisible.value = configVisible.value = false
  clearWithdrawalDetail()
  if (pageMounted) { restoreAuditMarker(); startWithdrawalRealtime() }
}, { flush: 'sync' })

onMounted(() => { pageMounted = true; restoreAuditMarker(); startWithdrawalRealtime() })
onBeforeUnmount(() => { pageDisposed = true; ++withdrawalListSequence; ++releaseSequence; stopWithdrawalRealtime(); clearWithdrawalDetail() })

const minAmount = ref<number>(0)
const taxRate = ref<number>(0)
const configVisible = ref(false)
const configInput = ref<number>(0)
const taxInput = ref<number>(0)
const savingConfig = ref(false)
const configLoaded = ref(false), configLoading = ref(false), configError = ref('')
let configSequence = 0

async function loadConfig() {
  if (!canReadWithdrawals.value || pageDisposed || savingConfig.value) return
  const id = ++configSequence
  const revision = auth.sessionRevision
  configLoading.value = true; configLoaded.value = false; configError.value = ''
  try {
    const res = await withdrawConfigApi.get(true)
    if (id !== configSequence || revision !== auth.sessionRevision || !canReadWithdrawals.value || pageDisposed) return
    if (!Number.isSafeInteger(res.data?.min_amount) || res.data.min_amount < 0 || !Number.isInteger(res.data?.tax_rate) || res.data.tax_rate < 0 || res.data.tax_rate > 100) throw new Error('提现规则数据无效')
    minAmount.value = res.data.min_amount; taxRate.value = res.data.tax_rate
    configInput.value = Number(amountToXaCoin(minAmount.value)); taxInput.value = taxRate.value
    configLoaded.value = true
  } catch {
    if (id === configSequence && revision === auth.sessionRevision && !pageDisposed) configError.value = '提现规则读取失败，请重试。'
  } finally { if (id === configSequence && revision === auth.sessionRevision && !pageDisposed) configLoading.value = false }
}

onMounted(loadConfig)

function onDetail(row: any) {
  if (!canReadWithdrawals.value || pageDisposed) return
  current.value = row
  detailVisible.value = true
  void refreshCurrentWithdrawal()
}

async function onClaim(row: any) {
  if (!canReadWithdrawals.value || !auth.hasPerm('withdraw:audit') || pageDisposed || auditBusy.value || releaseBusy.value || auditReview.value
    || row.status !== 'PENDING' || claimState(row) !== 'unclaimed') return
  const revision = auth.sessionRevision, sequence = ++auditSequence
  const active = () => !pageDisposed && canReadWithdrawals.value && auth.hasPerm('withdraw:audit')
    && revision === auth.sessionRevision && sequence === auditSequence
  auditBusy.value = true
  try {
    const result = await withdrawApi.action(row.id, 'claim', {}, true)
    if (!active()) return
    if (result.data?.id !== row.id || result.data?.status !== 'PENDING' || claimState(result.data) !== 'mine') throw new Error('invalid claim response')
    feedback.success('已领取，请核对收款信息后处理')
  } catch (error: any) {
    if (!active()) return
    const status = error?.response?.status || error?.code
    if (status === 400 || status === 403 || status === 409) feedback.warning(error?.response?.data?.msg || error?.msg || '申请已变化，请刷新后核对领取状态。')
    else feedback.warning('领取结果未确认，请刷新后核对领取状态。')
  } finally {
    if (active()) { auditBusy.value = false; requestWithdrawalRefresh(row.id) }
  }
}

async function submitAudit(row: any, action: AuditAction, payload: Record<string, string>, revision: number, sequence: number) {
  const active = () => !pageDisposed && canReadWithdrawals.value && auth.hasPerm('withdraw:audit')
    && revision === auth.sessionRevision && sequence === auditSequence
  writeAuditMarker(row.id, action)
  try {
    const result = await withdrawApi.action(row.id, action, payload, true)
    if (!active()) return
    const expectedStatus = action === 'approve' ? 'APPROVED' : 'REJECTED'
    if (result.data?.id !== row.id || result.data?.status !== expectedStatus) throw new Error('invalid audit response')
    clearAuditMarker()
    feedback.success(action === 'approve' ? '审核已通过' : '已驳回并退还')
  } catch (e: any) {
    if (!active()) return
    const status = e?.response?.status || e?.code
    if (status === 400 || status === 403 || status === 409) {
      clearAuditMarker()
      feedback.error(e?.response?.data?.msg || e?.msg || '审核被拒绝，请核对申请状态。')
    } else {
      auditReview.value = { id: row.id, action, checked: null }
      auditReviewError.value = ''
    }
  } finally { if (active()) requestWithdrawalRefresh(row.id) }
}

async function currentPendingWithdrawal(row: any, isCurrent: () => boolean) {
  try {
    const res = await withdrawApi.retrieve(row.id, true)
    if (!isCurrent()) return null
    const latest = res.data
    if (latest?.id !== row.id || !['PENDING', 'APPROVED', 'REJECTED'].includes(latest?.status)) throw new Error('invalid withdrawal')
    withdrawalStatuses.set(row.id, latest.status)
    if (latest.status !== 'PENDING') {
      feedback.warning('该提现已处理，请刷新后核对状态及打款记录。')
      requestWithdrawalRefresh(row.id)
      return null
    }
    if (claimState(latest) !== 'mine') {
      feedback.warning('申请未由你领取，请刷新后核对领取状态。')
      requestWithdrawalRefresh(row.id)
      return null
    }
    if (typeof latest.claimed_at !== 'string' || !Number.isFinite(Date.parse(latest.claimed_at))) {
      feedback.warning('领取时间待核对，请刷新后重试。')
      requestWithdrawalRefresh(row.id)
      return null
    }
    return latest
  } catch {
    if (isCurrent()) {
      feedback.error('提现状态读取失败，请重试后再审核。')
      requestWithdrawalRefresh(row.id)
    }
    return null
  }
}

async function onApprove(row: any) {
  if (!canReadWithdrawals.value || !auth.hasPerm('withdraw:audit') || pageDisposed || auditBusy.value || releaseBusy.value || auditReview.value || row.status !== 'PENDING'
    || claimState(row) !== 'mine'
    || (withdrawalStatuses.has(row.id) && withdrawalStatuses.get(row.id) !== 'PENDING')) return
  const revision = auth.sessionRevision
  const sequence = ++auditSequence
  const version = withdrawalVersions.get(row.id) || 0
  const currentAudit = () => !pageDisposed && canReadWithdrawals.value && auth.hasPerm('withdraw:audit')
    && revision === auth.sessionRevision && sequence === auditSequence && version === (withdrawalVersions.get(row.id) || 0)
  auditBusy.value = true
  try {
    const latest = await currentPendingWithdrawal(row, currentAudit)
    if (!latest || !currentAudit()) return
    pendingPromptId = row.id
    const interrupted = new Promise<null>(resolve => { cancelPendingPrompt = () => resolve(null) })
    const confirmation = await Promise.race([promptBox.prompt(
      `提现申请 #${latest.id}：请先向「${latest.user_name}」人工打款 ¥${formatRmb(latest.actual_amount)}（${amountToXaCoin(latest.actual_amount)} 兴安币，已扣除 ${amountToXaCoin(latest.tax_amount)} 兴安币税额），再填写渠道流水号或转账凭证编号。收款人：${latest.payee_name}；${latest.payee_method_display}：${latest.payee_account}。`,
      '提现审核',
      {
        type: 'warning', confirmButtonText: '确认已打款',
        inputPlaceholder: '打款流水号/凭证编号（必填）',
        inputValidator: (v) => (v && v.trim() && v.trim().length <= 100 ? true : '请填写 1–100 字的打款流水号/凭证编号'),
      },
    ).catch(() => null), interrupted])
    if (!confirmation || !currentAudit()) return
    pendingPromptId = null; cancelPendingPrompt = null
    await submitAudit(latest, 'approve', { payout_reference: confirmation.value.trim(), expected_claimed_at: latest.claimed_at }, revision, sequence)
  } finally {
    if (sequence === auditSequence) { auditBusy.value = false; pendingPromptId = null; cancelPendingPrompt = null }
  }
}

async function onReject(row: any) {
  if (!canReadWithdrawals.value || !auth.hasPerm('withdraw:audit') || pageDisposed || auditBusy.value || releaseBusy.value || auditReview.value || row.status !== 'PENDING'
    || claimState(row) !== 'mine'
    || (withdrawalStatuses.has(row.id) && withdrawalStatuses.get(row.id) !== 'PENDING')) return
  const revision = auth.sessionRevision
  const sequence = ++auditSequence
  const version = withdrawalVersions.get(row.id) || 0
  const currentAudit = () => !pageDisposed && canReadWithdrawals.value && auth.hasPerm('withdraw:audit')
    && revision === auth.sessionRevision && sequence === auditSequence && version === (withdrawalVersions.get(row.id) || 0)
  auditBusy.value = true
  try {
    const latest = await currentPendingWithdrawal(row, currentAudit)
    if (!latest || !currentAudit()) return
    pendingPromptId = row.id
    const interrupted = new Promise<null>(resolve => { cancelPendingPrompt = () => resolve(null) })
    const confirmation = await Promise.race([promptBox.prompt(`提现申请 #${latest.id} · ${latest.user_name}。驳回后 ${amountToXaCoin(latest.amount)} 兴安币将退回陪玩余额。`, '驳回提现', {
      confirmButtonText: '确认驳回', cancelButtonText: '取消',
      inputPlaceholder: '驳回原因（必填）',
      inputValidator: (v) => (v && v.trim() && v.trim().length <= 255 ? true : '请填写 1–255 字的驳回原因'),
    }).catch(() => null), interrupted])
    if (!confirmation || !currentAudit()) return
    pendingPromptId = null; cancelPendingPrompt = null
    await submitAudit(latest, 'reject', { audit_remark: confirmation.value.trim(), expected_claimed_at: latest.claimed_at }, revision, sequence)
  } finally {
    if (sequence === auditSequence) { auditBusy.value = false; pendingPromptId = null; cancelPendingPrompt = null }
  }
}

function openConfig() {
  if (!canReadWithdrawals.value || !auth.hasPerm('withdraw:audit') || pageDisposed) return
  configVisible.value = true
  void loadConfig()
}

async function saveConfig() {
  if (!canReadWithdrawals.value || !auth.hasPerm('withdraw:audit') || pageDisposed || savingConfig.value || !configLoaded.value || configLoading.value) return
  if (typeof configInput.value !== 'number' || !Number.isFinite(configInput.value) || typeof taxInput.value !== 'number' || !Number.isFinite(taxInput.value)) { configError.value = '请填写有效的最低提现金额和 0–100 的整数税率。'; return }
  const scaled = configInput.value * 10
  if (!Number.isSafeInteger(Math.round(scaled)) || Math.abs(scaled - Math.round(scaled)) > 1e-7) { configError.value = '最低提现金额最多保留 1 位小数。'; return }
  const min = xaCoinToAmount(configInput.value), rate = taxInput.value
  if (!Number.isSafeInteger(min) || min < 0 || !Number.isInteger(rate) || rate < 0 || rate > 100) { configError.value = '请填写有效的最低提现金额和 0–100 的整数税率。'; return }
  const revision = auth.sessionRevision, id = ++configSequence
  savingConfig.value = true; configError.value = ''
  try {
    const res = await withdrawConfigApi.update(min, rate, true)
    if (id !== configSequence || revision !== auth.sessionRevision || !canReadWithdrawals.value || !auth.hasPerm('withdraw:audit') || pageDisposed) return
    if (res.data?.min_amount !== min || res.data?.tax_rate !== rate) throw new Error('配置结果未确认')
    minAmount.value = res.data.min_amount
    taxRate.value = res.data.tax_rate
    feedback.success('提现配置已更新')
    configVisible.value = false
  } catch (e: any) {
    if (id !== configSequence || revision !== auth.sessionRevision || pageDisposed) return
    const status = e?.response?.status || e?.code
    if (status === 400 || status === 403) configError.value = e?.response?.data?.msg || e?.msg || '保存被拒绝，请核对输入和权限。'
    else { configLoaded.value = false; configError.value = '保存结果未确认，请重新读取后核对。' }
  } finally { if (id === configSequence && revision === auth.sessionRevision && !pageDisposed) savingConfig.value = false }
}
</script>

<style scoped lang="scss">
.wd-prompt-input { display:grid; gap:8px; }
.wd-config-fields { display:grid; gap:12px; border:0; padding:0; min-width:0; }
.wd-config-fields label { display:grid; gap:4px; }
.wd-dialog { max-height:90vh; overflow-y:auto; }
.wd-details > div { display:grid; grid-template-columns:112px minmax(0,1fr); gap:8px; padding:8px; border-bottom:1px solid var(--border); }
.wd-details dt { color:var(--muted-foreground); }
.wd-details dd { margin:0; overflow-wrap:anywhere; }
.wd-release-fields { display:grid; gap:12px; border:0; padding:0; }
.wd-release-fields label { display:grid; gap:4px; }
.wd-release-fields textarea { border:1px solid var(--border); border-radius:4px; padding:8px; background:var(--background); }
.wd-release-fields .wd-check { display:flex; align-items:center; gap:8px; }
.wd-filters, .wd-row-actions { display:flex; flex-wrap:wrap; gap:8px; }
.wd-mobile-list { display:none; }
@media (max-width:720px) { .wd-desktop-table { display:none; } .wd-mobile-list { display:grid; } }
.wd-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 让 CrudTable 内置的外层 padding 归零，融入 wd-page 的间距节奏 */
.wd-page :deep(.page-container) {
  padding: 0;
}

.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
.wd-mobile-list { gap: 12px; }
.wd-mobile-card { padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.wd-mobile-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.wd-mobile-head strong { min-width: 0; overflow-wrap: anywhere; }
.wd-mobile-card dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 16px 0 0; font-size: 13px; }
.wd-mobile-card dt { color: var(--muted-foreground); }
.wd-mobile-card dd { margin: 4px 0 0; overflow-wrap: anywhere; }
.wd-mobile-card p { color: var(--muted-foreground); font-size: 12px; margin: 12px 0; }
.wd-mobile-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.wd-release-summary { margin: 0 0 14px; font-weight: 600; }
</style>
