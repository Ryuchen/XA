<template>
  <div class="rp-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">核验三类服务凭证；收益状态以订单结算记录为准，冻结及可用余额请查看钱包</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('report:audit')" type="primary" plain @click="openCommission">
          订单默认抽成：{{ commissionLoading ? '读取中' : commissionConfig ? commissionLabel(commissionConfig) : '待读取' }}
        </el-button>
      </div>
    </div>

    <el-alert v-if="auditReview && !auditBusy && canReadReports" title="报单审核结果待核对" type="warning" :closable="false" show-icon>
      <p>报单 #{{ auditReview.id }} 的{{ auditReview.action === 'approve' ? '通过' : '驳回' }}结果未确认。原请求可能仍在处理，读取只反映当前状态。</p>
      <p v-if="auditReview.checked">当前状态：{{ auditReview.checked.status_display || REPORT_STATUS[auditReview.checked.status]?.label || '待核对' }}</p>
      <p v-if="auditReviewError">{{ auditReviewError }}</p>
      <el-button :loading="auditReviewLoading" @click="checkAuditReview">重新读取</el-button>
      <el-button v-if="auditReview.checked" :disabled="auditReviewLoading" @click="onDetail(auditReview.checked)">查看报单</el-button>
      <el-button v-if="auditReview.checked" :disabled="auditReviewLoading" @click="finishAuditReview">已核对，解除限制</el-button>
    </el-alert>

    <crud-table v-if="canReadReports" :key="auth.sessionRevision" ref="tableRef" :fetcher="reportApi.list" :query="query" table-scroll>
      <template #toolbar="{ reload }">
        <div class="rp-filter-primary">
          <el-input v-model="query.keyword" class="rp-filter-keyword" placeholder="陪玩、编号或项目" aria-label="搜索陪玩、编号或项目" clearable @keyup.enter="reload" @clear="reload" />
          <el-select v-model="query.status" class="rp-filter-status" placeholder="审核状态" aria-label="审核状态" clearable @change="onStatusFilterChange(reload)">
            <el-option v-for="(v, k) in REPORT_STATUS" :key="k" :label="v.label" :value="k" />
          </el-select>
          <el-checkbox v-model="query.order_blocked" true-value="1" false-value="" @change="onOrderBlockedFilterChange(reload)">订单状态待核对</el-checkbox>
        </div>
        <div class="rp-filter-actions">
          <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
          <el-button :aria-expanded="advancedFiltersOpen" aria-controls="rp-advanced-filters" @click="advancedFiltersOpen = !advancedFiltersOpen">{{ advancedFiltersOpen ? '收起筛选' : '更多筛选' }}<template v-if="advancedFilterCount">（{{ advancedFilterCount }}）</template></el-button>
          <el-button v-if="hasReportFilters" text @click="clearReportFilters(reload)">清除条件</el-button>
        </div>
        <div v-show="advancedFiltersOpen" id="rp-advanced-filters" class="rp-advanced-filters">
        <el-autocomplete
          v-model="query.game_name"
          :fetch-suggestions="querySuggest('game_name')"
          placeholder="游戏/项目"
          aria-label="按游戏或项目搜索"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.provider_username"
          :fetch-suggestions="querySuggest('provider_username')"
          placeholder="陪玩账号"
          aria-label="按陪玩账号搜索"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.provider_nickname"
          :fetch-suggestions="querySuggest('provider_nickname')"
          placeholder="昵称 / 陪玩编号"
          aria-label="按结单昵称、账号昵称或陪玩编号搜索"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        </div>
      </template>

      <template #mobile="{ rows }">
        <div class="rp-mobile-list" role="list" aria-label="报单列表">
          <article v-for="row in rows" :key="row.id" class="rp-mobile-card" role="listitem">
            <div class="rp-mobile-card-head">
              <div><strong>{{ row.provider_name || '陪玩待核对' }}</strong><span>报单 #{{ row.id }}</span></div>
              <el-tag :type="REPORT_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
            </div>
            <p class="rp-mobile-sub">{{ row.provider_no || '编号待核对' }} · {{ row.provider_username || '账号待核对' }}</p>
            <p class="rp-mobile-service">{{ row.game_name || '服务待核对' }}</p>
            <p v-if="row.fulfillment" class="rp-mobile-sub">{{ row.fulfillment }}</p>
            <p v-if="row.order_no" class="rp-mobile-sub">订单 <router-link v-if="auth.hasPerm('order:view')" :to="{ path: '/orders', query: { find_order: row.order_no } }">{{ row.order_no }}</router-link><span v-else>{{ row.order_no }}</span></p>
            <p v-if="row.order_approval_block_reason" class="rp-mobile-sub">{{ row.order_approval_block_reason }}</p>
            <p v-if="row.aftersale_status === 'OPEN'" class="rp-mobile-sub">售后处理中 · {{ row.settled_at ? '已结算收益以钱包流水为准' : '本单暂不结算' }}</p>
            <p v-if="row.settlement_review_reason" class="rp-mobile-sub">{{ row.settlement_review_reason }}</p>
            <dl class="rp-mobile-values">
              <div><dt>报单金额</dt><dd>{{ amountToXaCoin(row.amount) }} 币</dd></div>
              <div v-if="row.status === 'APPROVED' || row.status === 'PENDING'"><dt>{{ row.status === 'APPROVED' ? payoutLabel(row) : '预计收益' }}</dt><dd>{{ row.status === 'APPROVED' ? `${payoutAmountDisplay(row.payout_amount)} · ${reportSettlementLabel(row)}` : estimatedPayoutDisplay(row) }}</dd></div>
            </dl>
            <p class="rp-mobile-sub">提交 {{ row.submitted_at ? formatDateTime(row.submitted_at) : '—' }}</p>
            <div class="rp-mobile-actions">
              <el-button @click="onDetail(row)">查看详情</el-button>
              <template v-if="auth.hasPerm('report:audit') && row.status === 'PENDING'">
                <el-button v-if="!row.order_approval_block_reason" type="primary" :disabled="auditBusy || !!auditReview" @click="onApprove(row)">通过</el-button>
                <el-button :disabled="auditBusy || !!auditReview" @click="onReject(row)">驳回</el-button>
              </template>
            </div>
          </article>
        </div>
      </template>

      <el-table-column prop="id" label="ID" width="70" fixed="left" />
    <el-table-column label="状态" width="90">
      <template #default="{ row }">
        <el-tag :type="REPORT_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
        <div v-if="row.order_approval_block_reason" class="rp-mobile-sub">{{ row.order_approval_block_reason }}</div>
        <div v-if="row.aftersale_status === 'OPEN'" class="rp-mobile-sub">售后处理中</div>
      </template>
    </el-table-column>
    <el-table-column label="陪玩" min-width="190"><template #default="{ row }"><strong>{{ row.provider_name || '陪玩待核对' }}</strong><div class="rp-mobile-sub">{{ row.provider_no || '编号待核对' }} · {{ row.provider_username || '账号待核对' }}</div></template></el-table-column>
    <el-table-column prop="game_name" label="游戏/项目" min-width="140" />
    <el-table-column label="报单金额" width="110">
      <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 币</template>
    </el-table-column>
    <el-table-column label="收益" width="180">
      <template #default="{ row }">
        <span v-if="row.status === 'APPROVED'">{{ payoutLabel(row) }} {{ payoutAmountDisplay(row.payout_amount) }} · {{ reportSettlementLabel(row) }}</span>
        <span v-else-if="row.status === 'PENDING'">{{ estimatedPayoutDisplay(row) }}</span>
        <span v-else>—</span>
        <div v-if="row.settlement_review_reason" class="rp-mobile-sub">{{ row.settlement_review_reason }}</div>
      </template>
    </el-table-column>
    <el-table-column prop="fulfillment" label="玩法 / 转盘目标" min-width="220" />
    <el-table-column label="抽成来源" width="170">
      <template #default="{ row }">
        <span>{{ reportCommissionDesc(row) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="提交时间" width="160">
      <template #default="{ row }">{{ row.submitted_at ? formatDateTime(row.submitted_at) : '—' }}</template>
    </el-table-column>
    <el-table-column label="操作" width="160" fixed="right">
      <template #default="{ row }">
        <el-button link type="primary" @click="onDetail(row)">详情</el-button>
        <template v-if="auth.hasPerm('report:audit') && row.status === 'PENDING'">
          <el-button v-if="!row.order_approval_block_reason" link type="success" :disabled="auditBusy || !!auditReview" @click="onApprove(row)">通过</el-button>
          <el-button link type="danger" :disabled="auditBusy || !!auditReview" @click="onReject(row)">驳回</el-button>
        </template>
      </template>
    </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="报单详情" size="480px" class="ap-panel">
    <p v-if="detailLoading" role="status">正在同步报单详情…</p>
    <el-alert v-if="detailFailed" title="报单详情更新失败，请重试" type="error" :closable="false">
      <el-button text type="primary" @click="refreshCurrentReport">重新加载</el-button>
    </el-alert>
    <template v-if="current && !detailFailed">
      <el-alert v-if="current.order_approval_block_reason" :title="current.order_approval_block_reason" type="warning" :closable="false" show-icon>
        <router-link v-if="current.order_no && auth.hasPerm('order:view')" :to="{ path: '/orders', query: { find_order: current.order_no } }">查看关联订单</router-link>
      </el-alert>
      <el-alert v-if="current.aftersale_status === 'OPEN'" :title="current.settled_at ? '售后处理中，已结算收益以钱包流水为准' : '售后处理中，凭证可继续审核，本单暂不结算'" type="warning" :closable="false" show-icon>
        <router-link v-if="current.order_no && auth.hasPerm('order:view')" :to="{ path: '/orders', query: { find_order: current.order_no } }">查看关联订单</router-link>
      </el-alert>
      <el-descriptions :column="1" border>
        <el-descriptions-item label="报单编号">#{{ current.id }}</el-descriptions-item>
        <el-descriptions-item label="陪玩">{{ current.provider_name }}</el-descriptions-item>
        <el-descriptions-item label="陪玩编号">{{ current.provider_no || '待核对' }}</el-descriptions-item>
        <el-descriptions-item label="陪玩账号">{{ current.provider_username || '待核对' }}</el-descriptions-item>
        <el-descriptions-item label="游戏/项目">{{ current.game_name }}</el-descriptions-item>
        <el-descriptions-item v-if="current.order_no" label="订单号">{{ current.order_no }}</el-descriptions-item>
        <el-descriptions-item label="报单金额">{{ amountToXaCoin(current.amount) }} 币</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item label="抽成来源">{{ reportCommissionDesc(current) }}</el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'PENDING'" label="预计入账">
          {{ estimatedPayoutDisplay(current) }}
        </el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'APPROVED'" :label="payoutLabel(current)">
          {{ payoutAmountDisplay(current.payout_amount) }} · {{ reportSettlementLabel(current) }}
          <router-link v-if="isRefundedOrder(current) && auth.hasPerm('transaction:view') && current.order_no" :to="{ name: 'transactions', query: { order_no: current.order_no } }">查看订单流水</router-link>
        </el-descriptions-item>
        <el-descriptions-item v-if="current.settlement_review_reason" label="结算核对">{{ current.settlement_review_reason }} <router-link v-if="auth.hasPerm('transaction:view') && current.order_no" :to="{ name: 'transactions', query: { order_no: current.order_no } }">查看订单流水</router-link></el-descriptions-item>
        <el-descriptions-item label="服务内容">{{ current.description || '-' }}</el-descriptions-item>
        <el-descriptions-item label="备注">{{ current.remark || '-' }}</el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'REJECTED'" label="驳回原因">
          {{ current.audit_remark || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="审核人">{{ current.auditor_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="创建时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ current.submitted_at ? formatDateTime(current.submitted_at) : '—' }}</el-descriptions-item>
        <el-descriptions-item label="审核时间">{{ current.audited_at ? formatDateTime(current.audited_at) : '-' }}</el-descriptions-item>
      </el-descriptions>

      <el-divider>订单凭证</el-divider>
      <p class="evidence-help">点击图片查看大图</p>
      <template v-if="current.order">
        <div class="evidence-section">
          <div class="evidence-title">入队截图</div>
          <PrivateImage v-if="current.entry_image_url" :src="current.entry_image_url" :preview-src-list="allEvidenceUrls(current)" label="查看入队截图" fit="contain" class="evidence-image" />
          <el-empty v-else description="未上传" :image-size="50" />
        </div>
        <div class="evidence-section">
          <div class="evidence-title">结单截图</div>
          <PrivateImage v-if="current.completion_image_url" :src="current.completion_image_url" :preview-src-list="allEvidenceUrls(current)" label="查看结单截图" fit="contain" class="evidence-image" />
          <el-empty v-else description="未上传" :image-size="50" />
        </div>
        <div class="evidence-section">
          <div class="evidence-title">战绩截图（{{ current.result_image_urls?.length || 0 }} 张）</div>
          <div v-if="current.result_image_urls?.length" class="evidence-grid">
            <PrivateImage v-for="(url, index) in current.result_image_urls" :key="url" :src="url" :preview-src-list="allEvidenceUrls(current)" :label="`查看战绩截图 ${index + 1}`" fit="contain" class="evidence-image" />
          </div>
          <el-empty v-else description="未上传" :image-size="50" />
        </div>
      </template>
      <template v-else>
        <PrivateImage v-if="current.proof_image_url" :src="current.proof_image_url" :preview-src-list="[current.proof_image_url]" label="查看报单凭证" fit="contain" class="evidence-image" />
        <el-empty v-else description="无凭证" :image-size="80" />
      </template>
      <div v-if="auth.hasPerm('report:audit') && current.status === 'PENDING'" class="rp-audit-actions">
        <el-button v-if="!current.order_approval_block_reason" type="primary" :disabled="auditBusy || !!auditReview || detailLoading" @click="onApprove(current)">审核通过</el-button>
        <el-button :disabled="auditBusy || !!auditReview || detailLoading" @click="onReject(current)">驳回报单</el-button>
      </div>
    </template>
  </el-drawer>

  <el-dialog v-model="commissionVisible" title="订单默认抽成配置" width="600px" class="ap-modal" :close-on-click-modal="false" :close-on-press-escape="!savingRate" :show-close="!savingRate">
    <p v-if="commissionLoading" role="status">正在读取当前配置…</p>
    <el-alert v-if="commissionErrorText" :title="commissionErrorText" type="warning" :closable="false" show-icon>
      <el-button v-if="!commissionReady" text type="primary" :disabled="commissionLoading || savingRate" @click="loadCommission(true)">重新读取</el-button>
    </el-alert>
    <el-form v-if="commissionReady" class="ap-modal-form ap-form-grid" label-position="top">
      <CommissionEditor v-model:type="commissionType" v-model:rate="commissionInput" v-model:fixed="commissionFixed" :disabled="savingRate" />
      <p class="tip ap-field--full">仅设置后续普通订单的默认抽成。商品自定义抽成及订单档位、体验、陪玩自动规则优先；不影响礼物。已有订单按分账快照结算。</p>
      <p class="tip ap-field--full">多人同时修改时，以最后保存的配置为准。</p>
      <p v-if="commissionType === 'FIXED'" class="tip ap-field--full">旧手工报单仍沿用默认比例 {{ commissionRate }}%，不改为固定金额。</p>
    </el-form>
    <template #footer>
      <el-button :disabled="savingRate" @click="commissionVisible = false">取消</el-button>
      <el-button type="primary" :loading="savingRate" :disabled="!commissionReady || commissionLoading" @click="saveCommission">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import PrivateImage from "@/components/PrivateImage.vue"
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import CommissionEditor from '@/components/CommissionEditor.vue'
import { commissionError, commissionLabel, commissionPayload, readCommission, type CommissionConfig, type CommissionType } from '@/utils/commission'
import { reportApi } from '@/api/modules'
import { request } from '@/api/request'
import { REPORT_STATUS } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const canReadReports = computed(() => Boolean(auth.token) && auth.hasPerm('report:view'))
const tableRef = ref()
const query = reactive<Record<string, any>>({
  keyword: '',
  game_name: '',
  provider_username: '',
  provider_nickname: '',
  status: '',
  order_blocked: '',
})
const advancedFiltersOpen = ref(false)
const advancedFilterCount = computed(() => ['game_name', 'provider_username', 'provider_nickname'].filter(key => Boolean(query[key])).length)
const hasReportFilters = computed(() => Object.values(query).some(value => Boolean(value)))
function onStatusFilterChange(reload: () => void) {
  if (query.status !== 'PENDING') query.order_blocked = ''
  reload()
}
function onOrderBlockedFilterChange(reload: () => void) {
  if (query.order_blocked) query.status = 'PENDING'
  reload()
}
function clearReportFilters(reload: () => void) {
  Object.assign(query, { keyword: '', game_name: '', provider_username: '', provider_nickname: '', status: '', order_blocked: '' })
  reload()
}
watch(() => [route.query.status, route.query.order_blocked], ([status, blocked]) => {
  if (status !== 'PENDING') return
  Object.assign(query, { keyword: '', game_name: '', provider_username: '', provider_nickname: '', status: 'PENDING', order_blocked: blocked === '1' ? '1' : '' })
  tableRef.value?.reload()
  const remaining = { ...route.query }
  delete remaining.status
  delete remaining.order_blocked
  void router.replace({ query: remaining })
}, { immediate: true })

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询报单后，
 * 取返回记录对应字段值去重后作为下拉提示。
 */
function querySuggest(field: 'game_name' | 'provider_username' | 'provider_nickname') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
    }
    try {
      const res = (await reportApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const r of res.data?.list || []) {
        const values = field === 'provider_nickname' ? [r.provider_name, r.provider_nickname, r.provider_no] : [r[field]]
        for (const value of values) {
          if (value && value.toLocaleLowerCase().includes(keyword.toLocaleLowerCase()) && !seen.has(value)) {
            seen.add(value)
            items.push({ value })
          }
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
let detailSequence = 0
let pageDisposed = false
let pageMounted = false
let realtimeGeneration = 0
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let refreshRunning = false
let refreshPending = false
let detailRefreshPending = false
let offReportUpdate: (() => void) | null = null
let offOrderUpdate: (() => void) | null = null
let offConnected: (() => void) | null = null
let reportInterval: ReturnType<typeof setInterval> | null = null
const auditBusy = ref(false)
type AuditAction = 'approve' | 'reject'
type AuditReview = { id: number; action: AuditAction; checked: any | null }
const auditReview = ref<AuditReview | null>(null)
const auditReviewLoading = ref(false), auditReviewError = ref('')
let auditReviewSequence = 0
let auditReviewOwner: string | null = null

function auditReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_report_audit_review_${id}` : ''
}
function restoreAuditReview() {
  const key = auditReviewKey()
  if (key === auditReviewOwner) return
  auditReviewOwner = key
  ++auditReviewSequence
  auditReview.value = null
  auditReviewLoading.value = false
  auditReviewError.value = ''
  if (!key) return
  try {
    const marker = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (Number.isSafeInteger(marker?.id) && marker.id > 0 && ['approve', 'reject'].includes(marker.action)) auditReview.value = { ...marker, checked: null }
  } catch { /* Keep the current page guard if storage is unavailable. */ }
}
function beginAuditReview(id: number, action: AuditAction) {
  auditReview.value = { id, action, checked: null }
  const key = auditReviewKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify({ id, action })) } catch { /* In-page guard remains active. */ }
}
function clearAuditReview() {
  ++auditReviewSequence
  const key = auditReviewKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  auditReview.value = null
  auditReviewLoading.value = false
  auditReviewError.value = ''
}
function invalidateAuditReviewRead(message = '') {
  if (!auditReview.value) return
  ++auditReviewSequence
  auditReview.value.checked = null
  auditReviewLoading.value = false
  auditReviewError.value = message
}
async function checkAuditReview() {
  const review = auditReview.value
  if (!review || auditReviewLoading.value || !canReadReports.value || pageDisposed) return
  const sequence = ++auditReviewSequence, revision = auth.sessionRevision
  auditReviewLoading.value = true
  auditReviewError.value = ''
  review.checked = null
  try {
    const res = await reportApi.retrieve(review.id, true)
    if (sequence !== auditReviewSequence || revision !== auth.sessionRevision || auditReview.value !== review || pageDisposed) return
    if (res.data?.id !== review.id || !['PENDING', 'APPROVED', 'REJECTED'].includes(res.data?.status)) throw new Error('invalid report')
    review.checked = res.data
    requestReportRefresh(review.id)
  } catch {
    if (sequence === auditReviewSequence && revision === auth.sessionRevision && auditReview.value === review && !pageDisposed) auditReviewError.value = '读取失败，请重试。'
  } finally {
    if (sequence === auditReviewSequence && revision === auth.sessionRevision && !pageDisposed) auditReviewLoading.value = false
  }
}
async function finishAuditReview() {
  const review = auditReview.value, checked = review?.checked
  if (!review || !checked || auditReviewLoading.value || auditBusy.value) return
  const sequence = auditReviewSequence, revision = auth.sessionRevision
  try {
    await ElMessageBox.confirm('请确认已核对报单状态和审核记录。读取只是快照，原请求可能仍在处理；解除限制不会修改报单状态。', '确认已核对', { type: 'warning', confirmButtonText: '解除限制', cancelButtonText: '继续核对' })
  } catch { return }
  if (sequence === auditReviewSequence && revision === auth.sessionRevision && auditReview.value === review && review.checked === checked && !pageDisposed) clearAuditReview()
}
watch(() => auth.profile?.id, restoreAuditReview, { immediate: true, flush: 'sync' })
let auditSequence = 0
let auditWaitingId: number | string | null = null
let auditWaitingOrderId: number | string | null = null

function invalidateWaitingAudit() {
  if (auditWaitingId === null) return
  ++auditSequence
  auditWaitingId = null
  auditWaitingOrderId = null
  auditBusy.value = false
  ElMessageBox.close()
}

function beginAudit(row: any) {
  if (auditBusy.value || auditReview.value || row.status !== 'PENDING' || pageDisposed || !canReadReports.value || !auth.hasPerm('report:audit')) return null
  auditBusy.value = true
  auditWaitingId = row.id
  auditWaitingOrderId = row.order || null
  const sequence = ++auditSequence, revision = auth.sessionRevision
  return {
    current: () => sequence === auditSequence && revision === auth.sessionRevision && !pageDisposed && canReadReports.value && auth.hasPerm('report:audit'),
    finish: () => { if (sequence === auditSequence) { auditWaitingId = null; auditWaitingOrderId = null; auditBusy.value = false } },
  }
}

function clearReportDetail() {
  ++detailSequence
  detailRefreshPending = false
  current.value = null
  detailLoading.value = false
  detailFailed.value = false
}

watch(detailVisible, visible => {
  if (!visible) clearReportDetail()
}, { flush: 'sync' })

function isCurrentDetail(sequence: number, revision: number, reportId: number | string) {
  return !pageDisposed && canReadReports.value && auth.sessionRevision === revision
    && sequence === detailSequence && detailVisible.value && current.value?.id === reportId
}

async function refreshCurrentReport() {
  const reportId = current.value?.id
  if (!detailVisible.value || !reportId || !canReadReports.value || pageDisposed) return
  const sequence = ++detailSequence
  const revision = auth.sessionRevision
  detailLoading.value = true
  detailFailed.value = false
  try {
    const res = await reportApi.retrieve(reportId)
    if (isCurrentDetail(sequence, revision, reportId)) current.value = res.data
  } catch {
    if (isCurrentDetail(sequence, revision, reportId)) detailFailed.value = true
  } finally {
    if (isCurrentDetail(sequence, revision, reportId)) detailLoading.value = false
  }
}

// Keep CrudTable's current filters and pagination; pushes only invalidate HTTP data.
function requestReportRefresh(reportId?: number | string) {
  if (pageDisposed || !canReadReports.value) return
  refreshPending = true
  if (detailVisible.value && current.value && (reportId == null || current.value.id === reportId)) {
    ++detailSequence
    detailRefreshPending = true
  }
  scheduleReportRefresh()
}

function scheduleReportRefresh() {
  if (pageDisposed || !canReadReports.value || !refreshPending || refreshRunning || refreshTimer !== null) return
  const generation = realtimeGeneration
  refreshTimer = setTimeout(async () => {
    refreshTimer = null
    if (generation !== realtimeGeneration || pageDisposed || !canReadReports.value) return
    refreshPending = false
    refreshRunning = true
    const refreshDetail = detailRefreshPending
    detailRefreshPending = false
    try {
      await Promise.allSettled([
        tableRef.value?.load(),
        ...(refreshDetail ? [refreshCurrentReport()] : []),
      ])
    } finally {
      if (generation === realtimeGeneration) {
        refreshRunning = false
        scheduleReportRefresh()
      }
    }
  }, 300)
}

function onReportStatusUpdate(data: any) {
  if (!Number.isSafeInteger(data?.report_id) || data.report_id <= 0) return
  if (auditReview.value?.id === data.report_id) invalidateAuditReviewRead('报单状态有更新，请重新读取。')
  if (auditWaitingId === data.report_id) {
    invalidateWaitingAudit()
    ElMessage.warning('报单已变化，请核对最新状态')
  }
  requestReportRefresh(data.report_id)
}

function onOrderStatusUpdate(data: any) {
  if (!Number.isSafeInteger(data?.id) || data.id <= 0) return
  if (auditReview.value?.checked?.order === data.id) invalidateAuditReviewRead('关联订单已变化，请重新读取报单。')
  if (auditWaitingOrderId === data.id) {
    invalidateWaitingAudit()
    ElMessage.warning('关联订单已变化，请核对最新状态')
  }
  // A refund can make a pending report impossible to approve without
  // changing its report status. Re-read the list and any open detail.
  requestReportRefresh()
}

function stopReportRealtime() {
  ++realtimeGeneration
  offReportUpdate?.()
  offOrderUpdate?.()
  offConnected?.()
  offReportUpdate = offOrderUpdate = offConnected = null
  if (refreshTimer !== null) clearTimeout(refreshTimer)
  refreshTimer = null
  refreshPending = detailRefreshPending = refreshRunning = false
}

function startReportRealtime() {
  if (pageDisposed || !canReadReports.value) return
  offReportUpdate = adminWs.on('report_status_update', onReportStatusUpdate)
  offOrderUpdate = adminWs.on('order_status_update', onOrderStatusUpdate)
  offConnected = adminWs.on('connected', () => { invalidateWaitingAudit(); invalidateAuditReviewRead('连接已恢复，请重新读取报单。'); requestReportRefresh() })
  adminWs.connect()
}

watch([() => auth.sessionRevision, canReadReports, () => auth.hasPerm('report:audit')], () => {
  invalidateAuditReviewRead()
  invalidateWaitingAudit()
  ++auditSequence
  auditBusy.value = false
  stopReportRealtime()
  detailVisible.value = false
  clearReportDetail()
  resetCommission()
  if (pageMounted && canReadReports.value) void loadCommission(false)
  if (pageMounted) startReportRealtime()
}, { flush: 'sync' })

function onReportVisibility() {
  if (!document.hidden) requestReportRefresh()
}

onMounted(() => {
  pageMounted = true
  startReportRealtime()
  // Staff with report:view alone do not receive order events. Reconcile
  // while visible so refunded-order restrictions are not indefinitely stale.
  if (typeof document !== 'undefined') {
    document.addEventListener('visibilitychange', onReportVisibility)
    reportInterval = setInterval(() => { if (!document.hidden) requestReportRefresh() }, 60000)
  }
})

onBeforeUnmount(() => {
  pageDisposed = true
  ++auditReviewSequence
  invalidateWaitingAudit()
  ++auditSequence
  auditBusy.value = false
  resetCommission()
  stopReportRealtime()
  if (reportInterval !== null) clearInterval(reportInterval)
  if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onReportVisibility)
  clearReportDetail()
})

const commissionRate = ref<number>(0)
const commissionConfig = ref<CommissionConfig | null>(null)
const commissionVisible = ref(false)
const commissionLoading = ref(false)
const commissionReady = ref(false)
const commissionErrorText = ref('')
const commissionInput = ref<number | undefined>(0)
const commissionType = ref<CommissionType>('PERCENT')
const commissionFixed = ref<number | undefined>(0)
const savingRate = ref(false)
let commissionSequence = 0

function resetCommission() {
  ++commissionSequence
  commissionVisible.value = commissionLoading.value = commissionReady.value = savingRate.value = false
  commissionConfig.value = null
  commissionErrorText.value = ''
}

async function loadCommission(forEdit: boolean) {
  if (pageDisposed || !canReadReports.value || savingRate.value) return
  const sequence = ++commissionSequence, revision = auth.sessionRevision
  commissionLoading.value = true
  commissionErrorText.value = ''
  if (forEdit) commissionReady.value = false
  try {
    const res = await request<CommissionConfig>({ url: '/config/commission', method: 'get', silent: true })
    if (sequence !== commissionSequence || revision !== auth.sessionRevision || pageDisposed || !canReadReports.value || (forEdit && !commissionVisible.value)) return
    const data = res.data
    if (!data || !['PERCENT', 'FIXED'].includes(String(data.commission_type)) || !Number.isInteger(data.rate) || Number(data.rate) < 0 || Number(data.rate) > 100 || !Number.isSafeInteger(data.commission_fixed) || Number(data.commission_fixed) < 0) throw new Error('invalid configuration')
    commissionConfig.value = data
    commissionRate.value = Number(data.rate)
    if (forEdit) {
      const commission = readCommission(data)
      commissionInput.value = commission.rate
      commissionType.value = commission.type
      commissionFixed.value = commission.fixed
      commissionReady.value = true
    }
  } catch {
    if (sequence !== commissionSequence || revision !== auth.sessionRevision || pageDisposed) return
    commissionConfig.value = null
    if (forEdit) commissionErrorText.value = '当前配置读取失败，请重新读取后再修改'
  } finally {
    if (sequence === commissionSequence) commissionLoading.value = false
  }
}

onMounted(() => { void loadCommission(false) })
watch(commissionVisible, visible => {
  if (!visible) { ++commissionSequence; commissionLoading.value = false; commissionReady.value = false; commissionErrorText.value = '' }
}, { flush: 'sync' })

function onDetail(row: any) {
  if (!canReadReports.value || pageDisposed) return
  current.value = row
  detailVisible.value = true
  void refreshCurrentReport()
}

function reportRate(row: any): number | null {
  const raw = row.status === 'APPROVED' ? row.commission_rate : row.suggested_commission_rate
  if ((typeof raw !== 'number' && typeof raw !== 'string') || String(raw).trim() === '') return null
  const rate = Number(raw)
  return Number.isFinite(rate) && rate >= 0 && rate <= 100 ? rate : null
}

function reportCommissionDesc(row: any): string {
  const label = row.commission_source_label
  if (!label || label === '收益待核对') return '收益待核对'
  if (row.commission_type === 'FIXED') {
    const fixed = validPayoutAmount(row.commission_fixed)
    return fixed === null ? '收益待核对' : `固定抽取 ${amountToXaCoin(fixed)} 兴安币 · ${label}`
  }
  if (row.commission_type !== 'PERCENT') return '收益待核对'
  const rate = reportRate(row)
  return rate === null ? '收益待核对' : `${rate}% · ${label}`
}

function reportEstimatedPayout(row: any): number | null {
  return validPayoutAmount(row.expected_income)
}

function estimatedPayoutDisplay(row: any): string {
  const value = reportEstimatedPayout(row)
  return value === null ? '收益待核对' : `预计 ${amountToXaCoin(value)} 币`
}

function validPayoutAmount(amount: unknown): number | null {
  if ((typeof amount !== 'number' && typeof amount !== 'string') || String(amount).trim() === '') return null
  const value = Number(amount)
  return Number.isSafeInteger(value) && value >= 0 ? value : null
}

function payoutAmountDisplay(amount: unknown): string {
  const value = validPayoutAmount(amount)
  return value === null ? '金额待核对' : `${amountToXaCoin(value)} 币`
}

function settlementStatusLabel(status: unknown, amount: unknown): string {
  if (status === 'SETTLED') {
    const value = validPayoutAmount(amount)
    if (value === null) return '收益待核对'
    return value === 0 ? '已结算（收益为 0）' : '已入账（可能冻结）'
  }
  if (status === 'WAITING') return '待整单结算'
  return '收益待核对'
}

function isRefundedOrder(row: any): boolean {
  return row.order_payment_status === 'PARTIALLY_REFUNDED' || row.order_payment_status === 'REFUNDED'
}
function payoutLabel(row: any): string {
  return isRefundedOrder(row) ? '原核定收益' : '核定收益'
}
function reportSettlementLabel(row: any): string {
  if (row.order_payment_status === 'PARTIALLY_REFUNDED') return '已部分退款，净收益以钱包流水为准'
  if (row.order_payment_status === 'REFUNDED') return '已退款，收益变动以钱包流水为准'
  return settlementStatusLabel(row.settlement_status, row.payout_amount)
}

function allEvidenceUrls(row: any): string[] {
  return [row.entry_image_url, row.completion_image_url, ...(row.result_image_urls || [])].filter(Boolean)
}
function auditIdentity(row: any): string {
  return `${row.provider_name || '陪玩待核对'} · ${row.provider_no || '编号待核对'} · ${row.provider_username || '账号待核对'}`
}

async function onApprove(row: any) {
  if (row.order_approval_block_reason) return
  const operation = beginAudit(row)
  if (!operation) return
  const isOrderReport = Boolean(row.order)
  try {
    try { await ElMessageBox.confirm(
      `报单 #${row.id}${row.order_no ? ` · 订单 ${row.order_no}` : ''}。` + (isOrderReport
        ? `确认「${auditIdentity(row)}」的入队、结单和战绩截图完整有效？${row.settlement_status === 'SETTLED' ? '历史订单已结算，本次仅核验凭证；收益及冻结情况请查看钱包。' : row.settlement_status === 'WAITING' ? '全体报单通过、售后窗口结束且无未结客诉后，系统按订单分账快照结算。' : '收益待核对，审核通过不代表收益已入账。'}`
        : `确认通过「${auditIdentity(row)}」的报单？抽成：${reportCommissionDesc(row)}；${estimatedPayoutDisplay(row)}，实际入账以结算记录为准。`),
      '报单审核',
      { type: 'warning', confirmButtonText: '确认通过', cancelButtonText: '返回' },
    ) } catch { return }
    if (!operation.current()) return
    auditWaitingId = null
    await submitAudit(row.id, 'approve', undefined, operation)
  } finally { operation.finish() }
}

async function onReject(row: any) {
  const operation = beginAudit(row)
  if (!operation) return
  try {
    let auditRemark: string
    try {
      const { value } = await ElMessageBox.prompt(`报单 #${row.id}${row.order_no ? ` · 订单 ${row.order_no}` : ''} · ${auditIdentity(row)}。填写驳回原因，陪玩可查看。`, '驳回报单', {
        inputType: 'textarea', inputPlaceholder: '填写驳回原因（1–255 字）',
        inputValidator: (v: string) => Boolean(v?.trim() && v.trim().length <= 255),
        inputErrorMessage: '驳回原因须为 1–255 字', confirmButtonText: '确认驳回', cancelButtonText: '返回',
      })
      auditRemark = value.trim()
    } catch { return }
    if (!operation.current()) return
    auditWaitingId = null
    await submitAudit(row.id, 'reject', { audit_remark: auditRemark }, operation)
  } finally { operation.finish() }
}

async function submitAudit(id: number | string, action: 'approve' | 'reject', data: object | undefined, operation: NonNullable<ReturnType<typeof beginAudit>>) {
  const reportId = Number(id)
  if (!Number.isSafeInteger(reportId) || reportId <= 0) return
  beginAuditReview(reportId, action)
  try {
    await request({ url: `/reports/${id}/${action}/`, method: 'post', data, silent: true })
    if (!operation.current()) return
    clearAuditReview()
    ElMessage.success(action === 'approve' ? '报单审核已通过' : '报单已驳回')
  } catch (error: any) {
    if (!operation.current()) return
    const body = error?.response?.data || error, status = error?.response?.status
    const code = status || body?.code
    const rejected = Number.isInteger(code) && code >= 400 && code < 500 && code !== 408 && code !== 409
    const reason = typeof body?.msg === 'string' ? body.msg : typeof body?.detail === 'string' ? body.detail : ''
    if (rejected) clearAuditReview()
    ElMessage.warning(rejected ? `审核未完成${reason ? `：${reason}` : '，请核对报单状态'}` : '审核结果未确认，请刷新并核对报单状态后再操作')
  }
  if (operation.current()) requestReportRefresh(id)
}

function openCommission() {
  if (!canReadReports.value || !auth.hasPerm('report:audit') || savingRate.value) return
  commissionVisible.value = true
  void loadCommission(true)
}

async function saveCommission() {
  if (savingRate.value || !commissionReady.value || !commissionVisible.value || !canReadReports.value || !auth.hasPerm('report:audit')) return
  const error = commissionError(commissionType.value, commissionInput.value, commissionFixed.value)
  if (error) { ElMessage.warning(error); return }
  const sequence = commissionSequence, revision = auth.sessionRevision
  savingRate.value = true
  commissionErrorText.value = ''
  try {
    const payload = commissionPayload(commissionType.value, commissionInput.value, commissionFixed.value)
    const rate = commissionType.value === 'FIXED' ? commissionRate.value : commissionInput.value
    const res = await request<CommissionConfig>({ url: '/config/commission', method: 'put', data: { commission_type: payload.commission_type, commission_fixed: payload.commission_fixed, rate }, silent: true })
    if (sequence !== commissionSequence || revision !== auth.sessionRevision || pageDisposed || !commissionVisible.value || !auth.hasPerm('report:audit')) return
    commissionRate.value = Number(res.data.rate)
    commissionConfig.value = res.data
    ElMessage.success('订单默认抽成已更新')
    savingRate.value = false
    commissionVisible.value = false
  } catch (error: any) {
    if (sequence !== commissionSequence || revision !== auth.sessionRevision || pageDisposed || !commissionVisible.value) return
    const body = error?.response?.data || error, status = error?.response?.status
    const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
    if (rejected) commissionErrorText.value = body?.msg || body?.detail || '保存未完成，请检查配置'
    else { commissionReady.value = false; commissionConfig.value = null; commissionErrorText.value = '保存结果未确认，请重新读取当前配置后核对' }
  } finally {
    if (sequence === commissionSequence) savingRate.value = false
  }
}
</script>

<style scoped lang="scss">
.rp-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.rp-filter-primary, .rp-filter-actions, .rp-advanced-filters { display: flex; align-items: center; gap: 8px; }
.rp-filter-primary { flex: 1 1 340px; min-width: 0; }
.rp-filter-keyword { flex: 1 1 180px; min-width: 0; }
.rp-filter-status { flex: 0 0 130px; }
.rp-filter-actions { flex-wrap: wrap; }
.rp-filter-actions :deep(.el-button + .el-button) { margin-left: 0; }
.rp-advanced-filters { flex: 1 0 100%; flex-wrap: wrap; }
@media (max-width: 720px) { .rp-filter-status { flex-basis: 116px; } .rp-filter-actions { width: 100%; } .rp-filter-actions :deep(.el-button) { flex: 1 1 auto; } }

/* 让 CrudTable 内置的外层 padding 归零，融入 rp-page 的间距节奏 */
.rp-page :deep(.page-container) {
  padding: 0;
}

.evidence-section { margin-bottom: 20px; }
.evidence-help { margin: 0 0 16px; color: var(--muted-foreground); font-size: 12px; }
.evidence-title { margin-bottom: 10px; color: var(--el-text-color-primary); font-size: 14px; font-weight: 600; }
.evidence-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.evidence-image { width: 100%; height: 120px; border: 1px solid var(--border); background: var(--el-fill-color-light); border-radius: 10px; }
.rp-audit-actions { display: flex; flex-wrap: wrap; gap: 12px; margin-top: 24px; padding-top: 16px; border-top: 1px solid var(--border); }
.rp-audit-actions :deep(.el-button + .el-button) { margin-left: 0; }
.rp-mobile-list { display: grid; gap: 12px; }
.rp-mobile-card { padding: 16px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.rp-mobile-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.rp-mobile-card-head > div { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 8px; min-width: 0; }
.rp-mobile-card-head strong { color: var(--foreground); overflow-wrap: anywhere; }
.rp-mobile-card-head > div > span { color: var(--muted-foreground); font-size: 12px; }
.rp-mobile-service { margin: 12px 0 4px; color: var(--foreground); font-weight: 600; overflow-wrap: anywhere; }
.rp-mobile-sub { margin: 4px 0 0; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; overflow-wrap: anywhere; }
.rp-mobile-values { display: grid; gap: 8px; margin: 14px 0; padding: 12px 0; border-top: 1px solid var(--border); border-bottom: 1px solid var(--border); }
.rp-mobile-values > div { display: flex; justify-content: space-between; gap: 12px; }
.rp-mobile-values dt { color: var(--muted-foreground); }
.rp-mobile-values dd { margin: 0; text-align: right; overflow-wrap: anywhere; }
.rp-mobile-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.rp-mobile-actions :deep(.el-button + .el-button) { margin-left: 0; }

.tip {
  font-size: 12px;
  color: var(--muted-foreground);
}
</style>
