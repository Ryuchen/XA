<template>
  <div class="wd-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">先领取申请，再核对收款信息并完成人工打款；1 元 = 10 兴安币。</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('withdraw:audit')" type="warning" plain @click="openConfig">
          {{ configLoaded ? `最低提现：${amountToXaCoin(minAmount)} 兴安币 · 税率 ${taxRate}%` : '提现规则 · 待读取' }}
        </el-button>
      </div>
    </div>

    <el-alert v-if="auditReview" title="提现审核结果待核对" type="warning" :closable="false" show-icon>
      <p>申请 #{{ auditReview.id }} 的{{ auditReview.action === 'approve' ? '通过' : '驳回' }}结果未确认。{{ auditReview.action === 'approve' ? '请核对申请状态和打款凭证，勿重复打款。' : '请核对申请状态及余额退还记录。' }}</p>
      <p v-if="auditReview.checked">当前状态：{{ auditReview.checked.status_display || WITHDRAW_STATUS[auditReview.checked.status]?.label || '待核对' }}<template v-if="auditReview.checked.status === 'PENDING'">。请求可能仍在处理，请先核实。</template></p>
      <p v-if="auditReviewError">{{ auditReviewError }}</p>
      <el-button :loading="auditReviewLoading" @click="checkAuditReview">重新核对</el-button>
      <el-button v-if="auditReview.checked" :disabled="auditReviewLoading" @click="onDetail(auditReview.checked)">查看详情</el-button>
      <el-button v-if="auditReview.checked" :disabled="auditReviewLoading" @click="finishAuditReview">{{ auditReview.checked.status === 'PENDING' ? '已核对，解除限制' : '完成核对' }}</el-button>
    </el-alert>

    <el-alert v-if="claimScopeUnsupported && query.claim_scope" title="领取状态筛选暂不可用" description="请更新后台服务，或清除领取状态筛选后重新查询。" type="error" :closable="false" show-icon />
    <crud-table v-if="canReadWithdrawals" ref="tableRef" :fetcher="fetchWithdrawals" :query="query" :external-error="claimScopeUnsupported && !!query.claim_scope">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.provider_username"
          :fetch-suggestions="querySuggest('provider_username')"
          placeholder="陪玩账号"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.provider_nickname"
          :fetch-suggestions="querySuggest('provider_nickname')"
          placeholder="陪玩昵称"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.payee_name"
          :fetch-suggestions="querySuggest('payee_name')"
          placeholder="收款人"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.payee_account"
          :fetch-suggestions="querySuggest('payee_account')"
          placeholder="收款账号"
          clearable
          value-key="value"
          style="width: 160px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.status" placeholder="审核状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in WITHDRAW_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-select v-model="query.claim_scope" placeholder="领取状态" clearable style="width: 130px" @change="reload">
          <el-option label="未领取" value="unclaimed" />
          <el-option label="我已领取" value="mine" />
          <el-option label="其他客服已领取" value="other" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <template #mobile="{ rows }">
        <div class="wd-mobile-list" role="list" aria-label="提现申请">
          <article v-for="row in rows" :key="row.id" class="wd-mobile-card" role="listitem">
            <div class="wd-mobile-head"><strong>申请 #{{ row.id }} · {{ row.user_name || '—' }}</strong><el-tag :type="WITHDRAW_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag></div>
            <dl><div><dt>提现</dt><dd>{{ amountToXaCoin(row.amount) }} 兴安币</dd></div><div><dt>应打款</dt><dd>¥{{ formatRmb(row.actual_amount) }}</dd></div><div><dt>收款方式</dt><dd>{{ row.payee_method_display }}</dd></div><div><dt>收款人</dt><dd>{{ row.payee_name || '—' }}</dd></div><div><dt>领取状态</dt><dd>{{ claimLabel(row) }}</dd></div></dl>
            <p>提交 {{ formatDateTime(row.created_at) }}</p>
            <div class="wd-mobile-actions"><el-button @click="onDetail(row)">详情</el-button><template v-if="auth.hasPerm('withdraw:audit') && row.status === 'PENDING'"><el-button v-if="claimState(row) === 'unclaimed'" type="primary" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onClaim(row)">领取</el-button><template v-if="claimState(row) === 'mine'"><el-button :disabled="auditBusy || releaseBusy || !!auditReview" @click="onApprove(row)">通过</el-button><el-button :disabled="auditBusy || releaseBusy || !!auditReview" @click="onReject(row)">驳回</el-button></template></template><el-button v-if="canRelease(row)" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onRelease(row)">释放领取</el-button></div>
          </article>
        </div>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="user_name" label="陪玩" min-width="110" />
      <el-table-column label="提现金额" width="110">
        <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 兴安币</template>
      </el-table-column>
      <el-table-column label="应打款（元）" width="125">
        <template #default="{ row }">¥{{ formatRmb(row.actual_amount) }}</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="WITHDRAW_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="领取状态" min-width="135" show-overflow-tooltip>
        <template #default="{ row }">{{ claimLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="收款方式" width="100">
        <template #default="{ row }">
          <el-tag :type="PAYEE_METHOD[row.payee_method]?.type" effect="light" round>{{ row.payee_method_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="payee_name" label="收款人" width="100" />
      <el-table-column prop="payee_account" label="收款账号" min-width="160" show-overflow-tooltip />
      <el-table-column label="提交时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onDetail(row)">详情</el-button>
          <template v-if="auth.hasPerm('withdraw:audit') && row.status === 'PENDING'">
            <el-button v-if="claimState(row) === 'unclaimed'" link type="primary" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onClaim(row)">领取</el-button>
            <template v-if="claimState(row) === 'mine'">
              <el-button link type="success" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onApprove(row)">通过</el-button>
              <el-button link type="danger" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onReject(row)">驳回</el-button>
            </template>
          </template>
          <el-button v-if="canRelease(row)" link type="warning" :disabled="auditBusy || releaseBusy || !!auditReview" @click="onRelease(row)">释放领取</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="提现详情" size="480px" class="ap-panel">
    <el-skeleton v-if="detailLoading" :rows="6" animated />
    <el-alert v-else-if="detailFailed" title="提现详情加载失败，请重试" type="error" :closable="false">
      <el-button text type="primary" @click="refreshCurrentWithdrawal">重新加载</el-button>
    </el-alert>
    <template v-else-if="current">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="陪玩">{{ current.user_name }}</el-descriptions-item>
        <el-descriptions-item label="提现金额">{{ amountToXaCoin(current.amount) }} 兴安币</el-descriptions-item>
        <el-descriptions-item label="税率">{{ current.tax_rate }}%</el-descriptions-item>
        <el-descriptions-item label="税额">{{ amountToXaCoin(current.tax_amount) }} 兴安币</el-descriptions-item>
        <el-descriptions-item label="扣税后金额">{{ amountToXaCoin(current.actual_amount) }} 兴安币</el-descriptions-item>
        <el-descriptions-item label="应打款人民币">¥{{ formatRmb(current.actual_amount) }}</el-descriptions-item>
        <el-descriptions-item label="收款方式">{{ current.payee_method_display }}</el-descriptions-item>
        <el-descriptions-item label="收款人">{{ current.payee_name }}</el-descriptions-item>
        <el-descriptions-item label="收款账号">{{ current.payee_account }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item label="领取状态">{{ claimLabel(current) }}</el-descriptions-item>
        <el-descriptions-item v-if="current.claimed_at" label="领取时间">{{ formatDateTime(current.claimed_at) }}</el-descriptions-item>
        <el-descriptions-item label="备注">{{ current.remark || '-' }}</el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'REJECTED'" label="驳回原因">
          {{ current.audit_remark || '-' }}
        </el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'APPROVED'" label="打款流水号">
          {{ current.payout_reference || '-' }}
        </el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'APPROVED'" label="实际打款时间">
          {{ current.paid_at ? formatDateTime(current.paid_at) : '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="审核人">{{ current.auditor_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="审核时间">{{ current.audited_at ? formatDateTime(current.audited_at) : '-' }}</el-descriptions-item>
      </el-descriptions>
    </template>
  </el-drawer>

  <el-dialog v-model="releaseVisible" title="释放提现领取" width="440px" class="ap-modal wd-release-dialog" :close-on-click-modal="!releaseBusy" :close-on-press-escape="!releaseBusy" :show-close="!releaseBusy" @closed="resetRelease">
    <template v-if="releaseRow">
      <p class="wd-release-summary">申请 #{{ releaseRow.id }} · {{ releaseRow.claimed_by_name || '客服' }}已领取</p>
      <el-alert title="释放后其他客服可重新领取" description="请先与领取人及财务核实，确认尚未线下打款；如已打款或无法确认，请勿释放。" type="warning" :closable="false" show-icon />
      <el-alert v-if="releaseError" :title="releaseError" type="error" :closable="false" show-icon role="alert" />
      <el-form class="ap-modal-form" label-position="top" :disabled="releaseBusy">
        <el-form-item label="释放原因" required>
          <el-input v-model="releaseReason" type="textarea" :rows="3" maxlength="255" show-word-limit placeholder="填写核实情况或释放原因" />
        </el-form-item>
        <el-checkbox v-model="releaseConfirmed">我已核实此申请尚未打款</el-checkbox>
      </el-form>
    </template>
    <template #footer>
      <el-button :disabled="releaseBusy" @click="releaseVisible = false">取消</el-button>
      <el-button type="primary" :loading="releaseBusy" :disabled="!releaseRow || !releaseConfirmed || !releaseReason.trim() || releaseStale" @click="submitRelease">确认释放</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="configVisible" title="提现配置" width="440px" class="ap-modal" :close-on-click-modal="!savingConfig" :close-on-press-escape="!savingConfig" :show-close="!savingConfig">
    <el-alert v-if="configError" :title="configError" type="error" :closable="false" show-icon><el-button v-if="!configLoaded" link type="primary" :disabled="configLoading" @click="loadConfig">重新读取</el-button></el-alert>
    <el-skeleton v-if="configLoading" :rows="3" animated />
    <el-form v-else-if="configLoaded" class="ap-modal-form ap-form-grid" label-position="top" :disabled="savingConfig">
      <el-form-item label="最低提现(兴安币)">
        <el-input-number v-model="configInput" :min="0" :step="10" :precision="1" style="width: 100%" />
        <div class="tip">陪玩单次提现金额不得低于该值</div>
      </el-form-item>
      <el-form-item label="提现税率(%)">
        <el-input-number v-model="taxInput" :min="0" :max="100" :step="1" :precision="0" style="width: 100%" />
        <div class="tip">按申请金额计算，具体金额以申请时的快照为准。</div>
      </el-form-item>
      <p class="tip">多人修改时按最后保存的配置生效。</p>
    </el-form>
    <template #footer>
      <el-button :disabled="savingConfig" @click="configVisible = false">取消</el-button>
      <el-button type="primary" :loading="savingConfig" :disabled="!configLoaded || configLoading" @click="saveConfig">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { withdrawApi, withdrawConfigApi } from '@/api/modules'
import { WITHDRAW_STATUS, PAYEE_METHOD } from '@/utils/dict'
import { amountToXaCoin, xaCoinToAmount, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { formatRmb } from '@xa/money'
import { adminWs } from '@/utils/ws'
import { useRoute } from 'vue-router'

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
  return async (keyword: string, cb: (items: any[]) => void) => {
    const revision = auth.sessionRevision
    if (!keyword || !canReadWithdrawals.value || pageDisposed) {
      cb([])
      return
    }
    try {
      const res = (await withdrawApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      if (revision !== auth.sessionRevision || !canReadWithdrawals.value || pageDisposed) { cb([]); return }
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const w of res.data?.list || []) {
        const value = w[field] || ''
        if (value && !seen.has(value)) {
          seen.add(value)
          items.push({ value })
        }
      }
      cb(items)
    } catch {
      cb([])
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
      ElMessage.warning('申请或领取状态已变化，请刷新后核对。')
      requestWithdrawalRefresh(row.id)
      return
    }
    releaseRow.value = latest; releaseReason.value = ''; releaseConfirmed.value = false
    releaseStale.value = false; releaseError.value = ''; releaseVisible.value = true
  } catch {
    if (active()) { ElMessage.error('领取状态读取失败，请重试。'); requestWithdrawalRefresh(row.id) }
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
    ElMessage.success('已释放，其他客服可重新领取')
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
    try { await ElMessageBox.confirm(`当前仍为待审核，${context}原请求可能仍在处理；解除限制不会修改提现状态或发起打款。`, '确认已核对', { type: 'warning', confirmButtonText: '解除限制', cancelButtonText: '继续核对' }) }
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
  if (pendingPromptId !== null) ElMessageBox.close()
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
    ElMessage.warning('提现领取或审核状态已变化，请重新核对；如已线下打款，请先核对凭证，勿重复打款。')
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
    if (invalidatePendingAudit()) ElMessage.warning('连接已恢复，请重新核对提现状态；如已线下打款，请先核对凭证，勿重复打款。')
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
    ElMessage.success('已领取，请核对收款信息后处理')
  } catch (error: any) {
    if (!active()) return
    const status = error?.response?.status || error?.code
    if (status === 400 || status === 403 || status === 409) ElMessage.warning(error?.response?.data?.msg || error?.msg || '申请已变化，请刷新后核对领取状态。')
    else ElMessage.warning('领取结果未确认，请刷新后核对领取状态。')
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
    ElMessage.success(action === 'approve' ? '审核已通过' : '已驳回并退还')
  } catch (e: any) {
    if (!active()) return
    const status = e?.response?.status || e?.code
    if (status === 400 || status === 403 || status === 409) {
      clearAuditMarker()
      ElMessage.error(e?.response?.data?.msg || e?.msg || '审核被拒绝，请核对申请状态。')
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
      ElMessage.warning('该提现已处理，请刷新后核对状态及打款记录。')
      requestWithdrawalRefresh(row.id)
      return null
    }
    if (claimState(latest) !== 'mine') {
      ElMessage.warning('申请未由你领取，请刷新后核对领取状态。')
      requestWithdrawalRefresh(row.id)
      return null
    }
    if (typeof latest.claimed_at !== 'string' || !Number.isFinite(Date.parse(latest.claimed_at))) {
      ElMessage.warning('领取时间待核对，请刷新后重试。')
      requestWithdrawalRefresh(row.id)
      return null
    }
    return latest
  } catch {
    if (isCurrent()) {
      ElMessage.error('提现状态读取失败，请重试后再审核。')
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
    const confirmation = await ElMessageBox.prompt(
      `提现申请 #${latest.id}：请先向「${latest.user_name}」人工打款 ¥${formatRmb(latest.actual_amount)}（${amountToXaCoin(latest.actual_amount)} 兴安币，已扣除 ${amountToXaCoin(latest.tax_amount)} 兴安币税额），再填写渠道流水号或转账凭证编号。收款人：${latest.payee_name}；${latest.payee_method_display}：${latest.payee_account}。`,
      '提现审核',
      {
        type: 'warning', confirmButtonText: '确认已打款',
        inputPlaceholder: '打款流水号/凭证编号（必填）',
        inputValidator: (v) => (v && v.trim() && v.trim().length <= 100 ? true : '请填写 1–100 字的打款流水号/凭证编号'),
      },
    ).catch(() => null)
    if (!confirmation || !currentAudit()) return
    pendingPromptId = null
    await submitAudit(latest, 'approve', { payout_reference: confirmation.value.trim(), expected_claimed_at: latest.claimed_at }, revision, sequence)
  } finally {
    if (sequence === auditSequence) { auditBusy.value = false; pendingPromptId = null }
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
    const confirmation = await ElMessageBox.prompt(`提现申请 #${latest.id} · ${latest.user_name}。驳回后 ${amountToXaCoin(latest.amount)} 兴安币将退回陪玩余额。`, '驳回提现', {
      confirmButtonText: '确认驳回', cancelButtonText: '取消',
      inputPlaceholder: '驳回原因（必填）',
      inputValidator: (v) => (v && v.trim() && v.trim().length <= 255 ? true : '请填写 1–255 字的驳回原因'),
    }).catch(() => null)
    if (!confirmation || !currentAudit()) return
    pendingPromptId = null
    await submitAudit(latest, 'reject', { audit_remark: confirmation.value.trim(), expected_claimed_at: latest.claimed_at }, revision, sequence)
  } finally {
    if (sequence === auditSequence) { auditBusy.value = false; pendingPromptId = null }
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
    ElMessage.success('提现配置已更新')
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
.wd-mobile-list { display: flex; flex-direction: column; gap: 12px; }
.wd-mobile-card { padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.wd-mobile-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.wd-mobile-head strong { min-width: 0; overflow-wrap: anywhere; }
.wd-mobile-head :deep(.el-tag) { flex: none; white-space: nowrap; }
.wd-mobile-card dl { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; margin: 16px 0 0; font-size: 13px; }
.wd-mobile-card dt { color: var(--muted-foreground); }
.wd-mobile-card dd { margin: 4px 0 0; overflow-wrap: anywhere; }
.wd-mobile-card p { color: var(--muted-foreground); font-size: 12px; margin: 12px 0; }
.wd-mobile-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.wd-mobile-actions :deep(.el-button + .el-button) { margin-left: 0; }
.wd-release-summary { margin: 0 0 14px; font-weight: 600; }
.wd-release-dialog :deep(.el-alert + .el-alert) { margin-top: 10px; }
.wd-release-dialog :deep(.el-checkbox) { height: auto; white-space: normal; align-items: flex-start; }
.wd-release-dialog :deep(.el-checkbox__label) { white-space: normal; line-height: 1.5; }
</style>
