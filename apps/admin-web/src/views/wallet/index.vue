<template>
  <div class="wl-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理老板与打手钱包</p>
    </div>

    <el-alert v-if="fundReview && !walletDialogVisible() && !saving" title="资金操作结果待核对" type="warning" :closable="false" show-icon>
      <p>钱包 #{{ fundReview.walletId }} 的{{ fundReview.kind === 'adjust' ? '调账' : '充值' }}请求可能仍在处理。请核对相关记录和请求状态，勿重复操作。</p>
      <el-button v-if="auth.hasPerm('transaction:view')" @click="router.push({ name: 'transactions', query: { wallet_id: String(fundReview.walletId) } })">查看该钱包流水</el-button>
      <el-button v-if="fundReview.kind === 'recharge' && auth.hasPerm('recharge:view')" @click="router.push({ name: 'recharge-records', query: { wallet_id: String(fundReview.walletId) } })">查看关联充值记录</el-button>
      <el-button @click="finishFundReview">已核对，解除限制</el-button>
    </el-alert>

    <div class="wl-heading"><strong>账户列表</strong><el-tooltip content="余额与冻结分别展示；打手奖罚和退押金请在陪玩管理办理。" :trigger="['hover', 'focus', 'click']"><button type="button" class="wl-help" aria-label="钱包说明">?</button></el-tooltip></div>

    <crud-table v-if="canReadWallets" ref="tableRef" :fetcher="walletApi.list" :query="query" table-scroll>
      <template #toolbar="{ reload }">
        <div class="wl-filter-primary">
        <el-input v-model="query.keyword" class="wl-filter-keyword" placeholder="编号、账号、昵称或手机" aria-label="搜索编号、账号、昵称或手机号" clearable @keyup.enter="reload" @clear="reload" />
        <el-select v-model="query.account_type" class="wl-filter-type" @change="reload" aria-label="钱包对象">
          <el-option label="老板钱包" value="BOSS" />
          <el-option label="打手钱包" value="PROVIDER" />
        </el-select>
        </div>
        <div class="wl-filter-actions">
          <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
          <el-button :aria-expanded="advancedFiltersOpen" aria-controls="wl-advanced-filters" @click="advancedFiltersOpen = !advancedFiltersOpen">{{ advancedFiltersOpen ? '收起筛选' : '更多筛选' }}<template v-if="advancedFilterCount">（{{ advancedFilterCount }}）</template></el-button>
          <el-button v-if="hasWalletFilters" text @click="clearWalletFilters(reload)">清除条件</el-button>
        </div>
        <div v-show="advancedFiltersOpen" id="wl-advanced-filters" class="wl-advanced-filters">
        <el-autocomplete
          v-model="query.boss_no"
          :fetch-suggestions="querySuggest('boss_no')"
          placeholder="编号"
          aria-label="按编号筛选"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.nickname"
          :fetch-suggestions="querySuggest('nickname')"
          placeholder="昵称"
          aria-label="按昵称筛选"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.phone"
          :fetch-suggestions="querySuggest('phone')"
          placeholder="手机号"
          aria-label="按手机号筛选"
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
        <div class="wl-mobile-list" role="list" aria-label="钱包账户列表">
          <article v-for="row in rows" :key="row.id" class="wl-card" role="listitem">
            <div class="wl-card-head">
              <el-avatar :size="32" :src="row.avatar_url || undefined">{{ (row.nickname || row.username || '账').slice(0, 1) }}</el-avatar>
              <div class="wl-identity"><strong>{{ row.nickname || row.username || '名称待核对' }}</strong><span>{{ row.boss_no || `钱包 #${row.id}` }} · {{ row.username || '账号待核对' }}</span></div>
              <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '钱包正常' : '钱包停用' }}</el-tag>
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
              <el-button v-if="auth.hasPerm('transaction:view')" @click="viewWalletTransactions(row)">查看流水</el-button>
              <el-button v-if="auth.hasPerm('wallet:recharge')" :disabled="!!fundReview || !row.is_active" @click="onRecharge(row)">客服充值</el-button>
              <el-button v-if="auth.hasPerm('wallet:adjust') && row.user_role === 'CUSTOMER'" :disabled="!!fundReview || !row.is_active" @click="onAdjust(row)">调账</el-button>
            </div>
          </article>
        </div>
      </template>

      <el-table-column label="账户" width="230" fixed="left">
        <template #default="{ row }">
          <div class="wl-table-identity">
            <strong>{{ row.nickname || row.username || '名称待核对' }}</strong>
            <span>{{ row.boss_no || `钱包 #${row.id}` }} · {{ row.username || '账号待核对' }}</span>
          </div>
        </template>
      </el-table-column>
      <el-table-column label="兴安币余额" width="120">
        <template #default="{ row }">{{ amountToXaCoin(row.balance) }} 币</template>
      </el-table-column>
      <el-table-column label="冻结" width="100">
        <template #default="{ row }">{{ amountToXaCoin(row.frozen_amount) }} 币</template>
      </el-table-column>
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'danger'" effect="light" round>
            {{ row.is_active ? '正常' : '停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="phone" label="手机号" width="130" />
      <el-table-column v-if="query.account_type === 'BOSS'" label="老板分级" width="110">
        <template #default="{ row }">{{ row.boss_type_name || '-' }}</template>
      </el-table-column>
      <el-table-column label="累计实充" width="110">
        <template #default="{ row }">{{ amountToXaCoin(row.total_recharge) }} 币</template>
      </el-table-column>
      <el-table-column label="累计赠送" width="110">
        <template #default="{ row }">{{ amountToXaCoin(row.total_gift) }} 币</template>
      </el-table-column>
      <el-table-column
        v-if="auth.hasPerm('transaction:view') || auth.hasPerm('wallet:adjust') || auth.hasPerm('wallet:recharge')"
        label="操作"
        width="190"
        fixed="right"
      >
        <template #default="{ row }">
          <el-button v-if="auth.hasPerm('transaction:view')" link type="primary" @click="viewWalletTransactions(row)">流水</el-button>
          <el-button v-if="auth.hasPerm('wallet:recharge')" link type="success" :disabled="!!fundReview || !row.is_active" @click="onRecharge(row)">客服充值</el-button>
          <el-button v-if="auth.hasPerm('wallet:adjust') && row.user_role === 'CUSTOMER'" link type="primary" :disabled="!!fundReview || !row.is_active" @click="onAdjust(row)">调账</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="adjustVisible" title="钱包调账" width="480px" class="ap-modal" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
    <el-form :disabled="saving || fundUnknown" :inert="saving || fundUnknown || undefined" class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="调账对象">{{ walletIdentity(current) }}</el-form-item>
      <el-form-item label="最近读取余额">{{ amountToXaCoin(current?.balance) }} 兴安币</el-form-item>
      <el-form-item label="调整兴安币">
        <el-input-number :key="`${saving}-${fundUnknown}`" v-model="adjustCoin" :precision="1" :step="10" style="width: 100%" />
        <div class="tip">正数增加，负数扣减</div>
      </el-form-item>
      <el-form-item label="调账原因" required>
        <el-input v-model="adjustRemark" placeholder="必填，例如误充纠正" maxlength="255" />
      </el-form-item>
    </el-form>
    <el-alert v-if="fundUnknown" title="调账结果未确认，请关闭并核对资金流水，勿重复操作。" type="warning" :closable="false" />
    <template #footer>
      <el-button :disabled="saving" @click="adjustVisible = false">{{ fundUnknown ? '关闭' : '取消' }}</el-button>
      <el-button type="primary" :loading="saving" :disabled="fundUnknown" @click="onAdjustSave">核对调账</el-button>
    </template>
  </el-dialog>

  <el-dialog v-model="rechargeVisible" title="客服充值确认" width="760px" class="wl-dialog ap-modal" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
    <el-form :disabled="saving || fundUnknown" :inert="saving || fundUnknown || undefined" class="ap-modal-form ap-form-grid" label-position="top">
      <el-alert
        title="请先核实客服收款，录入实收人民币；系统按 1 元 = 10 兴安币兑换"
        type="info"
        :closable="false"
        show-icon
        class="wl-recharge-alert"
      />
      <div class="wl-form-section">
        <p class="wl-section-title">充值对象</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item :label="current?.user_role === 'PROVIDER' ? '打手' : '老板'">{{ walletIdentity(current) }}</el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="最近读取余额">{{ amountToXaCoin(current?.balance) }} 币</el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="wl-form-section">
        <p class="wl-section-title">充值金额</p><el-form-item v-if="rechargePackages.length" label="预存档位"><el-select placeholder="选择预存档位，自动填入实付与赠送" @change="choosePackage"><el-option v-for="item in rechargePackages" :key="item.id" :value="item.id" :label="'实付 '+item.amount/100+' 元，赠送 '+item.gift_amount/100+' 元，消费积分 '+item.points_rate/100+' 倍'"/></el-select></el-form-item>
        <el-row :gutter="16">
          <el-col :span="12">
              <el-form-item label="实收人民币">
                <el-input-number :key="`${saving}-${fundUnknown}`" v-model="rechargeYuan" :min="0" :precision="2" :step="100" style="width: 100%" />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="赠送兴安币">
              <el-input-number :key="`${saving}-${fundUnknown}`" v-model="giftCoin" :min="0" :precision="1" :step="10" style="width: 100%" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="预计到账">
          <strong class="wl-credit-preview">{{ rechargeTotalLabel }} 兴安币</strong>
          <span class="tip">（{{ rechargeYuan }} 元兑换 {{ rechargeCoin }} 币 + 客服赠送）</span>
        </el-form-item>
      </div>

      <div class="wl-form-section">
        <p class="wl-section-title">溯源信息</p>
        <el-form-item label="交易编号">
          <el-input v-model="tradeNo" placeholder="已核实的收款流水号（必填）" maxlength="64" />
        </el-form-item>
        <el-form-item label="充值凭证" class="ap-field--full">
          <el-upload
            :auto-upload="false"
            :show-file-list="false"
            accept="image/*"
            :on-change="onProofChange"
          >
            <el-button>选择凭证图片</el-button>
          </el-upload>
          <el-image
            v-if="proofPreview"
            :src="proofPreview"
            :preview-src-list="[proofPreview]"
            fit="contain"
            class="wl-proof-preview"
          />
          <div class="tip">请上传已核实的收款截图，同一收款流水只能充值一次</div>
        </el-form-item>
      </div>

      <el-form-item label="备注">
        <el-input v-model="rechargeRemark" placeholder="充值备注" />
      </el-form-item>
    </el-form>
    <el-alert v-if="fundUnknown" title="充值结果未确认，请关闭并核对充值记录和资金流水，勿重复操作。" type="warning" :closable="false" />
    <template #footer>
      <el-button :disabled="saving" @click="rechargeVisible = false">{{ fundUnknown ? '关闭' : '取消' }}</el-button>
      <el-button type="primary" :loading="saving" :disabled="fundUnknown" @click="onRechargeSave">核对并入账</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { request } from '@/api/request'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { formatRmb, rmbToRawAmount, toCoin } from '@xa/money'
import { ElMessage, ElMessageBox, type UploadFile } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { walletApi } from '@/api/modules'
import { amountToXaCoin, xaCoinToAmount } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { adminWs } from '@/utils/ws'
import { onBeforeRouteLeave, useRouter } from 'vue-router'

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

watch(() => [auth.sessionRevision, canReadWallets.value, auth.hasPerm('wallet:adjust'), auth.hasPerm('wallet:recharge')], () => {
  stopWalletRealtime()
  adjustVisible.value = rechargeVisible.value = false
  current.value = null
  saving.value = false
  fundUnknown.value = false
  rechargePackages.value = []
  if (pageMounted) startWalletRealtime()
}, { flush: 'sync' })

onMounted(() => { pageMounted = true; startWalletRealtime() })
onBeforeUnmount(() => { pageDisposed = true; stopWalletRealtime(); clearProof() })
onBeforeRouteLeave(() => { if (saving.value) { ElMessage.warning('正在核对或提交，请稍候'); return false } return true })

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询后端，
 * 取返回记录的该字段值去重后作为下拉提示。
 */
function querySuggest(field: 'boss_no' | 'nickname' | 'phone') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    const revision = auth.sessionRevision
    if (!keyword || !canReadWallets.value || pageDisposed) {
      cb([])
      return
    }
    try {
      const res = (await walletApi.list({ account_type: query.value.account_type, [field]: keyword, page: 1, page_size: 10 })) as any
      if (revision !== auth.sessionRevision || !canReadWallets.value || pageDisposed) { cb([]); return }
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const w of res.data.list || []) {
        const value = w[field]
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

const saving = ref(false)
const current = ref<any>(null)
const fundUnknown = ref(false)
const fundReview = ref<{ walletId: number; kind: 'adjust' | 'recharge' } | null>(null)
let fundReviewOwner = ''
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
  try { await ElMessageBox.confirm('请确认已核对相关记录和请求处理情况；请求可能仍在处理中。', '解除资金操作限制', { type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对' }) } catch { return }
  if (!pageDisposed && revision === auth.sessionRevision && marker === fundReview.value) { clearFundReview(); fundUnknown.value = false; requestWalletRefresh() }
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreFundReview, { immediate: true, flush: 'sync' })
function handleFundError(error: any) {
  const body = error?.response?.data || error, status = error?.response?.status
  const rejected = (!status || status < 500) && ((status >= 400 && status < 500) || (typeof body?.code === 'number' && body.code >= 400 && body.code < 500))
  if (rejected) { clearFundReview(); ElMessage.error(typeof body?.msg === 'string' && body.msg ? body.msg : '请求被拒绝，请核对信息') }
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
  if (!adjustRemark.value.trim()) { ElMessage.warning('请填写调账原因'); return }
  const amount = xaCoinToAmount(adjustCoin.value)
  if (amount === 0) {
    ElMessage.warning('调账金额不能为 0')
    return
  }
  const walletId = current.value.id, balance = current.value.balance, identity = walletIdentity(current.value), remark = adjustRemark.value.trim()
  saving.value = true
  try {
    try { await ElMessageBox.confirm(`账户：${identity}\n最近读取余额：${amountToXaCoin(balance)} 币\n${amount > 0 ? '增加' : '扣减'}：${amountToXaCoin(Math.abs(amount))} 币\n原因：${remark}`, '确认调账', { type: 'warning', customClass: 'wl-fund-confirm', confirmButtonText: '确认调账', cancelButtonText: '返回修改' }) } catch { return }
    if (!isCurrent() || !adjustVisible.value || current.value?.id !== walletId) return
    if (current.value.balance !== balance || !current.value.is_active) { ElMessage.warning('钱包状态或余额已变化，请重新核对'); return }
    beginFundReview(walletId, 'adjust')
    await walletApi.action(walletId, 'adjust', { amount, remark, client_request_id: adjustRequestId.value }, true)
    if (!isCurrent()) return
    clearFundReview()
    ElMessage.success('调账成功')
    adjustVisible.value = false
    requestWalletRefresh()
  } catch (error: any) {
    if (isCurrent()) handleFundError(error)
  } finally {
    if (isCurrent()) saving.value = false
  }
}

const rechargePackages = ref<any[]>([])
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

function onProofChange(uploadFile: UploadFile) {
  const raw = uploadFile.raw
  if (!raw) return
  clearProof()
  proofFile.value = raw
  proofPreview.value = URL.createObjectURL(raw)
}
function clearProof() { if (proofPreview.value) URL.revokeObjectURL(proofPreview.value); proofPreview.value = ''; proofFile.value = null }
watch(rechargeVisible, visible => { if (!visible) clearProof() })

async function onRecharge(row: any) {
  if (!canReadWallets.value || !auth.hasPerm('wallet:recharge') || pageDisposed || saving.value || fundReview.value || !row.is_active) return
  const revision = auth.sessionRevision
  const generation = realtimeGeneration
  ++detailSequence
  current.value = row
  rechargeYuan.value = 0
  giftCoin.value = 0
  rechargeRemark.value = ''
  tradeNo.value = ''
  rechargeRequestId.value = `recharge-${Date.now()}-${Math.random().toString(36).slice(2)}`
  proofFile.value = null
  proofPreview.value = ''
  fundUnknown.value = false
  rechargeVisible.value = true
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
    ElMessage.warning('请填写收款流水号并上传收款凭证')
    return
  }
  const amount = rmbToRawAmount(rechargeYuan.value)
  const giftAmount = xaCoinToAmount(giftCoin.value)
  if (amount <= 0) {
    ElMessage.warning('实充金额必须大于 0')
    return
  }
  const walletId = current.value.id, accountId = current.value.account_id, userId = current.value.user, identity = walletIdentity(current.value), balance = current.value.balance
  saving.value = true
  try {
    try { await ElMessageBox.confirm(`账户：${identity}\n实收：${rechargeYuan.value} 元\n赠送：${giftCoin.value} 币\n预计到账：${amountToXaCoin(amount + giftAmount)} 币\n收款流水：${tradeNo.value.trim()}`, '确认收款并入账', { type: 'warning', customClass: 'wl-fund-confirm', confirmButtonText: '确认入账', cancelButtonText: '返回修改' }) } catch { return }
    if (!isCurrent() || !rechargeVisible.value || current.value?.id !== walletId) return
    if (current.value.balance !== balance || !current.value.is_active || current.value.account_id !== accountId || current.value.user !== userId) { ElMessage.warning('钱包对象、状态或余额已变化，请重新核对'); return }
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
    ElMessage.success(`已入账 ${amountToXaCoin(amount + giftAmount)} 兴安币`)
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
:global(.wl-fund-confirm .el-message-box__message p) { white-space: pre-line; overflow-wrap: anywhere; line-height: 1.8; }
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
.wl-filter-actions :deep(.el-button + .el-button) { margin-left: 0; }
.wl-advanced-filters { flex: 1 0 100%; flex-wrap: wrap; }
@media (max-width: 720px) { .wl-filter-type { flex-basis: 116px; } .wl-filter-actions { width: 100%; } .wl-filter-actions :deep(.el-button) { flex: 1 1 auto; } }
.wl-help { width: var(--control-height-sm); height: var(--control-height-sm); border: 0; border-radius: 50%; color: var(--primary); background: transparent; cursor: pointer; }
.wl-help:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.wl-mobile-list { display: grid; gap: 12px; }
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
.wl-actions :deep(.el-button + .el-button) { margin-left: 0; }
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
