<template>
  <div class="us-page">
    <!-- 头部操作行 -->
    <div class="ap-page-head">
      <p class="ap-page-intro">管理老板资料、消费分级、推荐关系与账户状态</p>
    </div>

    <el-alert v-if="saveReview && !dialogVisible && !saving" title="老板资料保存结果待核对" type="warning" :closable="false" show-icon>
      <p>账户 #{{ saveReview.id }} 的请求可能仍在处理。请读取最新资料并核对实际结果。</p>
      <p v-if="reviewNotice" role="status">{{ reviewNotice }}</p>
      <el-button :loading="reviewLoading" @click="checkSaveReview">读取最新资料</el-button>
      <el-button v-if="reviewChecked" :disabled="reviewLoading" @click="finishSaveReview">已核对，解除限制</el-button>
    </el-alert>

    <div class="us-list-heading"><strong>老板资料</strong>
      <el-tooltip content="列表展示账户钱包余额；订单的支付和退款情况请以订单及资金流水为准。账户启用与允许登录分别判断。" :trigger="['hover', 'focus', 'click']" placement="top"><button type="button" class="us-help" aria-label="账户和余额说明">?</button></el-tooltip>
    </div>

    <crud-table ref="tableRef" :fetcher="userApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.boss_no"
          :fetch-suggestions="querySuggest('boss_no')"
          placeholder="编号"
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
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.is_active" placeholder="账户状态" clearable style="width: 120px" @change="reload">
          <el-option label="账户启用" value="true" />
          <el-option label="账户停用" value="false" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <template #mobile="{ rows }">
        <div class="us-mobile-list" role="list" aria-label="老板资料列表">
          <article v-for="row in rows" :key="row.id" class="us-mobile-card" role="listitem">
            <div class="us-mobile-head">
              <el-avatar :size="32" :src="row.avatar_url || undefined">{{ (row.nickname || row.username || '老板').slice(0, 1) }}</el-avatar>
              <div class="us-mobile-identity"><strong>{{ row.nickname || row.username || '名称待核对' }}</strong><span>{{ row.boss_no || `账户 #${row.id}` }} · {{ row.username || '账号待核对' }}</span></div>
              <el-tag :type="row.is_active === true ? 'success' : 'info'" effect="light" round>{{ accountStatus(row) }}</el-tag>
            </div>
            <dl class="us-mobile-facts">
              <div><dt>登录</dt><dd>{{ loginStatus(row) }}</dd></div>
              <div><dt>钱包余额</dt><dd>{{ balanceLabel(row) }}</dd></div>
              <div><dt>老板分级</dt><dd>{{ row.boss_type_name || '未分级' }}</dd></div>
              <div><dt>推荐人</dt><dd>{{ row.inviter_name || '无' }}</dd></div>
              <div><dt>手机号</dt><dd>{{ row.phone || '未填写' }}</dd></div>
              <div><dt>注册时间</dt><dd>{{ row.created_at ? formatDateTime(row.created_at) : '待核对' }}</dd></div>
            </dl>
            <div class="us-mobile-actions">
              <el-button v-if="auth.hasPerm('order:view')" @click="viewOrders(row)">查看订单</el-button>
              <el-button v-if="auth.hasPerm('user:edit')" :disabled="!!saveReview" @click="onEdit(row)">编辑</el-button>
              <el-dropdown trigger="click"><el-button>更多</el-button><template #dropdown><el-dropdown-menu>
                <el-dropdown-item v-if="auth.hasPerm('user:edit')" :disabled="retiring" @click="onRetire(row)">退店清空</el-dropdown-item>
                <el-dropdown-item @click="banTarget = row; banVisible = true">封禁记录</el-dropdown-item>
              </el-dropdown-menu></template></el-dropdown>
            </div>
          </article>
        </div>
      </template>

      <el-table-column label="编号" width="110">
      <template #default="{ row }">
        <span class="us-no">{{ row.boss_no || '-' }}</span>
      </template>
    </el-table-column>
    <el-table-column label="头像" width="70">
      <template #default="{ row }">
        <el-avatar :size="32" :src="row.avatar_url || undefined">{{ (row.nickname || row.username).charAt(0) }}</el-avatar>
      </template>
    </el-table-column>
    <el-table-column prop="username" label="账号" min-width="120" />
    <el-table-column prop="nickname" label="昵称" min-width="110" />
    <el-table-column label="状态" width="100">
      <template #default="{ row }">
        <el-tag :type="row.is_active === true ? 'success' : 'info'" effect="light" round>{{ accountStatus(row) }}</el-tag>
      </template>
    </el-table-column>
    <el-table-column label="登录" width="95"><template #default="{ row }">{{ loginStatus(row) }}</template></el-table-column>
    <el-table-column prop="phone" label="手机号" width="120" />
    <el-table-column label="老板分级" width="110">
      <template #default="{ row }">{{ row.boss_type_name || '-' }}</template>
    </el-table-column>
    <el-table-column label="账户余额" width="120" align="right">
      <template #default="{ row }">
        <span class="us-balance-cell">{{ balanceLabel(row) }}</span>
      </template>
    </el-table-column>
    <el-table-column label="推荐人" width="110">
      <template #default="{ row }">{{ row.inviter_name || '-' }}</template>
    </el-table-column>
    <el-table-column label="注册时间" width="160">
      <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
    </el-table-column>
    <el-table-column label="操作" width="150" fixed="right">
      <template #default="{ row }">
        <el-button v-if="auth.hasPerm('order:view')" link type="primary" @click="viewOrders(row)">订单</el-button>
        <el-button v-if="auth.hasPerm('user:edit')" link type="primary" :disabled="!!saveReview" @click="onEdit(row)">编辑</el-button>
        <el-dropdown trigger="click"><el-button link>更多</el-button><template #dropdown><el-dropdown-menu>
          <el-dropdown-item v-if="auth.hasPerm('user:edit')" :disabled="retiring" @click="onRetire(row)">退店清空</el-dropdown-item>
          <el-dropdown-item @click="banTarget = row; banVisible = true">封禁记录</el-dropdown-item>
        </el-dropdown-menu></template></el-dropdown>
      </template>
    </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="reviewViewing ? '核对最新老板资料' : '编辑老板资料'" width="760px" class="us-dialog ap-modal" :before-close="closeEditDialog" :close-on-click-modal="!saving" :close-on-press-escape="!saving" :show-close="!saving">
    <el-form :model="editForm" :disabled="saving || saveUnknown || reviewViewing" :inert="saving || (saveUnknown && !reviewViewing) || undefined" class="ap-modal-form ap-form-grid" label-position="top">
      <p v-if="editDirty && !saveUnknown && !reviewViewing" class="us-draft-status" role="status">有未保存的修改</p>
      <div class="us-form-section">
        <p class="us-section-title">基础信息</p>
        <el-row :gutter="16">
          <el-col :span="12">
            <el-form-item label="账号"><el-input v-model="editForm.username" disabled /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="编号"><el-input v-model="editForm.boss_no" disabled placeholder="系统自动生成" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="昵称"><el-input v-model="editForm.nickname" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="真实姓名"><el-input v-model="editForm.real_name" /></el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="手机号"><el-input v-model="editForm.phone" /></el-form-item>
          </el-col>
        </el-row>
      </div>

      <div class="us-form-section">
        <p class="us-section-title">归属与消费</p>
        <el-form-item label="老板分级">
          <el-select v-model="editForm.boss_type" placeholder="不分级" clearable style="width: 100%" :loading="bossTypesLoading" :disabled="saving || saveUnknown || reviewViewing || !bossTypesReady || currentVipManaged || missingBossType">
            <el-option
              v-for="bt in bossTypes"
              :key="bt.id"
              :label="`${bt.name}（${bt.discount_rate}%）${bt.vip_managed ? ' · VIP自动' : ''}${!bt.is_active ? ' · 已停用' : ''}`"
              :value="bt.id"
              :disabled="bt.vip_managed && bt.id !== initialBossType"
            />
          </el-select>
          <div v-if="bossTypesError || missingBossType" class="us-field-message" role="alert">{{ missingBossType ? '当前分级未找到，请核对' : '分级读取失败' }} <el-button link type="primary" @click="loadBossTypes">重新读取</el-button></div>
          <div v-else-if="currentVipManaged" class="us-field-message">VIP 分级自动管理，退店清空可重置</div>
        </el-form-item>
        <el-form-item label="推荐人">
          <el-select
            v-model="editForm.inviter"
            placeholder="搜索昵称、账号或编号"
            clearable
            filterable
            remote
            :remote-method="searchInviter"
            :loading="inviterLoading"
            @change="onInviterChange"
            style="width: 100%"
          >
            <el-option
              v-for="u in inviterOptions"
              :key="u.id"
              :label="inviterLabel(u)"
              :value="u.id"
            />
          </el-select>
          <div v-if="inviterError" class="us-field-message" role="alert">推荐人读取失败，请重新搜索</div>
        </el-form-item>
        <el-form-item :error="rateError">
          <template #label>分佣比例（%）<el-tooltip content="推荐人分得的平台抽成比例，不是订单金额比例。" :trigger="['hover', 'focus', 'click']"><button type="button" class="us-help" aria-label="分佣比例说明">?</button></el-tooltip></template>
          <el-input-number :key="`${saving}-${saveUnknown}-${reviewViewing}`" v-model="editForm.inviter_commission_rate" :min="0" :max="100" :precision="0" @change="rateError = ''" />
          <div v-if="!editForm.inviter" class="us-field-message">未选择推荐人，比例暂不生效</div>
        </el-form-item>
      </div>

      <div class="us-form-section">
        <p class="us-section-title">账户状态</p>
        <el-alert :title="`${accountStatus(editForm)} · ${loginStatus(editForm)}；状态变更请使用封禁记录`"
          type="info" :closable="false" />
      </div>
    </el-form>
    <el-alert v-if="saveUnknown" title="保存结果未确认，请关闭后读取最新资料核对，勿重复提交。" type="warning" :closable="false" />
    <template #footer>
      <el-button :disabled="saving" @click="closeEditDialog()">{{ saveUnknown || reviewViewing ? '关闭' : '取消' }}</el-button>
      <el-button v-if="!reviewViewing" type="primary" :loading="saving" :disabled="saveUnknown" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
  <account-ban-dialog v-model="banVisible" resource="users" :target-id="banTarget?.id" :target-name="banTarget?.nickname || banTarget?.username" @changed="tableRef?.load()" />
</template>

<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import AccountBanDialog from '@/components/AccountBanDialog.vue'
import { bossTypeApi, userApi } from '@/api/modules'
import { request } from '@/api/request'
import { formatDateTime, amountToXaCoin } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { onBeforeRouteLeave, useRouter } from 'vue-router'

const auth = useAuthStore()
const router = useRouter()
let userPageDisposed = false
onUnmounted(() => { userPageDisposed = true })
const tableRef = ref()
const banVisible = ref(false)
const banTarget = ref<any>(null)
const query = reactive<Record<string, any>>({ boss_no: '', nickname: '', phone: '', is_active: '' })
function accountStatus(row: any) { return row.is_active === true ? '账户启用' : row.is_active === false ? '账户停用' : '状态待核对' }
function loginStatus(row: any) { return row.is_active === false ? '不可登录' : row.is_active === true && row.can_login === true ? '可登录' : row.is_active === true && row.can_login === false ? '登录受限' : '待核对' }
function balanceLabel(row: any) { return row.wallet_balance == null ? '待核对' : `${amountToXaCoin(row.wallet_balance)} 币` }
function viewOrders(row: any) { if (auth.hasPerm('order:view') && Number.isSafeInteger(row.id) && row.id > 0) void router.push({ name: 'orders', query: { find_customer: String(row.id) } }) }

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询后端，
 * 取返回记录的该字段值去重后作为下拉提示。
 */
function querySuggest(field: 'boss_no' | 'nickname' | 'phone') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
    }
    try {
      const res = (await userApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const u of res.data.list || []) {
        const value = u[field]
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

const dialogVisible = ref(false)
const saving = ref(false)
const retiring = ref(false)
const editForm = reactive<any>({})

const bossTypes = ref<any[]>([])
const inviterOptions = ref<any[]>([])
const inviterLoading = ref(false)
const inviterError = ref(false)
const selectedInviter = ref<any>(null)
const bossTypesLoading = ref(false)
const bossTypesReady = ref(false)
const bossTypesError = ref(false)
const initialBossType = ref<number | null>(null)
const currentVipManaged = computed(() => bossTypes.value.some(bt => bt.id === initialBossType.value && bt.vip_managed))
const missingBossType = computed(() => bossTypesReady.value && initialBossType.value != null && !bossTypes.value.some(bt => bt.id === initialBossType.value))
const rateError = ref('')
const saveUnknown = ref(false)
const reviewViewing = ref(false)
const saveReview = ref<{ id: number } | null>(null)
const reviewChecked = ref(false)
const reviewLoading = ref(false)
const reviewNotice = ref('')
const editBaseline = ref<string | null>(null)
const draftFields = ['nickname', 'real_name', 'phone', 'boss_type', 'inviter', 'inviter_commission_rate']
function draftSignature() { return JSON.stringify(draftFields.map(key => editForm[key] ?? null)) }
const editDirty = computed(() => editBaseline.value !== null && draftSignature() !== editBaseline.value)
let editRevision = 0
let searchRevision = 0
let reviewOwnerKey = ''
let discardPromptOpen = false

watch(() => auth.sessionRevision, () => { editRevision++; searchRevision++; dialogVisible.value = false; editBaseline.value = null; saveUnknown.value = false; reviewViewing.value = false; saving.value = false }, { flush: 'sync' })
watch(dialogVisible, visible => { if (!visible) { editRevision++; searchRevision++ } }, { flush: 'sync' })

async function confirmDiscard() {
  if (!editDirty.value || saveUnknown.value || reviewViewing.value || !auth.hasPerm('user:edit')) return true
  if (discardPromptOpen) return false
  discardPromptOpen = true
  try {
    await ElMessageBox.confirm('关闭后未保存的老板资料将丢失。', '放弃修改？', { type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑' })
    return true
  } catch { return false } finally { discardPromptOpen = false }
}
async function closeEditDialog(done?: () => void) {
  if (saving.value || !dialogVisible.value) return
  const revision = auth.sessionRevision
  if (!await confirmDiscard() || userPageDisposed || revision !== auth.sessionRevision || !dialogVisible.value) return
  editBaseline.value = null; reviewViewing.value = false
  if (done) done(); else dialogVisible.value = false
}
onBeforeRouteLeave(async () => {
  if (!dialogVisible.value) return true
  if (saving.value) { ElMessage.warning('正在保存，请稍候'); return false }
  return confirmDiscard()
})
function onUserBeforeUnload(event: BeforeUnloadEvent) {
  if (dialogVisible.value && auth.hasPerm('user:edit') && (saving.value || (editDirty.value && !saveUnknown.value && !reviewViewing.value))) { event.preventDefault(); event.returnValue = '' }
}
onMounted(() => window.addEventListener('beforeunload', onUserBeforeUnload))
onUnmounted(() => window.removeEventListener('beforeunload', onUserBeforeUnload))

function saveReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_user_save_review_${id}` : ''
}
function restoreSaveReview() {
  const key = saveReviewKey()
  if (key === reviewOwnerKey) return
  reviewOwnerKey = key; saveReview.value = null; reviewChecked.value = false; reviewLoading.value = false; reviewNotice.value = ''
  if (!key) return
  try { const marker = JSON.parse(sessionStorage.getItem(key) || 'null'); if (Number.isSafeInteger(marker?.id) && marker.id > 0) saveReview.value = { id: marker.id } } catch { /* In-memory guard remains available. */ }
}
function beginSaveReview(id: number) {
  saveReview.value = { id }; reviewChecked.value = false; reviewNotice.value = ''
  const key = saveReviewKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify({ id })) } catch { /* Current page still retains the marker. */ }
}
function clearSaveReview() {
  const key = saveReviewKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  saveReview.value = null; reviewChecked.value = false; reviewNotice.value = ''
}
async function checkSaveReview() {
  const marker = saveReview.value, revision = auth.sessionRevision
  if (!marker || reviewLoading.value || !auth.hasPerm('user:edit')) return
  reviewLoading.value = true; reviewChecked.value = false; reviewNotice.value = ''
  try {
    const row = (await userApi.retrieve(marker.id, true)).data
    if (userPageDisposed || revision !== auth.sessionRevision || saveReview.value !== marker) return
    await onEdit(row, true)
    if (userPageDisposed || revision !== auth.sessionRevision || saveReview.value !== marker || !dialogVisible.value || !reviewViewing.value) return
    reviewChecked.value = true; reviewNotice.value = '已读取最新资料；请核对内容和请求处理情况。'
  } catch (error: any) {
    if (userPageDisposed || revision !== auth.sessionRevision || saveReview.value !== marker) return
    if (error?.response?.status === 404) { reviewChecked.value = true; reviewNotice.value = '未找到该账户。请求可能仍在处理，请确认后再解除限制。' }
    else reviewNotice.value = '读取失败，请稍后重新读取。'
  } finally { if (revision === auth.sessionRevision) reviewLoading.value = false }
}
async function finishSaveReview() {
  const marker = saveReview.value, revision = auth.sessionRevision
  if (!marker || !reviewChecked.value || reviewLoading.value) return
  try { await ElMessageBox.confirm('请确认已核对资料和请求处理情况；请求可能仍在处理中。', '解除保存限制', { type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对' }) } catch { return }
  if (!userPageDisposed && revision === auth.sessionRevision && saveReview.value === marker && reviewChecked.value) { clearSaveReview(); saveUnknown.value = false; tableRef.value?.load() }
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreSaveReview, { immediate: true, flush: 'sync' })

async function loadBossTypes() {
  const revision = editRevision
  bossTypesLoading.value = true
  bossTypesReady.value = false
  bossTypesError.value = false
  try {
    const res = await bossTypeApi.allOptions() as any
    if (revision !== editRevision || userPageDisposed) return
    bossTypes.value = res.data.list || []
    bossTypesReady.value = true
  } catch {
    if (revision === editRevision && !userPageDisposed) bossTypesError.value = true
  } finally {
    if (revision === editRevision && !userPageDisposed) bossTypesLoading.value = false
  }
}

async function searchInviter(keyword: string) {
  const revision = ++searchRevision, edit = editRevision
  inviterError.value = false
  if (!keyword.trim()) {
    inviterLoading.value = false
    inviterOptions.value = selectedInviter.value ? [selectedInviter.value] : []
    return
  }
  inviterLoading.value = true
  try {
    const res = await request<any>({ url: '/users/', params: { keyword: keyword.trim(), page: 1, page_size: 20 }, silent: true })
    if (revision !== searchRevision || edit !== editRevision || userPageDisposed) return
    const rows = (res.data.list || []).filter((u: any) => u.id !== editForm.id)
    if (selectedInviter.value && !rows.some((u: any) => u.id === selectedInviter.value.id)) rows.unshift(selectedInviter.value)
    inviterOptions.value = rows
  } catch {
    if (revision === searchRevision && edit === editRevision && !userPageDisposed) inviterError.value = true
  } finally {
    if (revision === searchRevision && edit === editRevision && !userPageDisposed) inviterLoading.value = false
  }
}
function inviterLabel(u: any) { return [u.nickname || u.username || '未命名', u.boss_no || `账户 #${u.id}`, u.username].filter(Boolean).join(' · ') }
function onInviterChange(id: number | null) { selectedInviter.value = inviterOptions.value.find(u => u.id === id) || null }

async function onEdit(row: any, review = false) {
  if (!auth.hasPerm('user:edit') || (saveReview.value && !review)) return
  editRevision++
  searchRevision++
  Object.assign(editForm, row)
  initialBossType.value = row.boss_type ?? null
  bossTypes.value = []
  bossTypesReady.value = false
  bossTypesError.value = false
  inviterError.value = false
  inviterLoading.value = false
  rateError.value = ''
  saveUnknown.value = false
  reviewViewing.value = review
  // 预置推荐人选项，保证回显当前推荐人名称（编号未知，仅回显昵称）
  selectedInviter.value = row.inviter
    ? { id: row.inviter, nickname: row.inviter_name, boss_no: '' }
    : null
  inviterOptions.value = selectedInviter.value ? [selectedInviter.value] : []
  editBaseline.value = draftSignature()
  dialogVisible.value = true
  await loadBossTypes()
}

async function onSave() {
  if (saving.value || saveUnknown.value || reviewViewing.value || saveReview.value || !auth.hasPerm('user:edit')) return
  if (!Number.isInteger(editForm.inviter_commission_rate) || editForm.inviter_commission_rate < 0 || editForm.inviter_commission_rate > 100) { rateError.value = '请输入 0 到 100 的整数'; return }
  const revision = editRevision, session = auth.sessionRevision
  saving.value = true
  try {
    const payload: any = {
      nickname: editForm.nickname,
      real_name: editForm.real_name,
      phone: editForm.phone,
      inviter: editForm.inviter || null,
      inviter_commission_rate: editForm.inviter_commission_rate,
    }
    if ((editForm.boss_type ?? null) !== initialBossType.value) payload.boss_type = editForm.boss_type || null
    beginSaveReview(editForm.id)
    await request({ url: `/users/${editForm.id}/`, method: 'patch', data: payload, silent: true })
    if (revision !== editRevision || session !== auth.sessionRevision || userPageDisposed) return
    ElMessage.success('保存成功')
    clearSaveReview(); editBaseline.value = null
    dialogVisible.value = false
    tableRef.value?.load()
  } catch (error: any) {
    if (revision !== editRevision || session !== auth.sessionRevision || userPageDisposed) return
    const body = error?.response?.data || error, status = error?.response?.status
    if ((!status || status < 500) && ((status >= 400 && status < 500) || (typeof body?.code === 'number' && body.code >= 400 && body.code < 500))) { clearSaveReview(); ElMessage.error(typeof body?.msg === 'string' && body.msg ? body.msg : '保存被拒绝，请核对资料') }
    else saveUnknown.value = true
  } finally {
    if (session === auth.sessionRevision && !userPageDisposed) saving.value = false
  }
}
async function onRetire(row: any) {
  if (retiring.value || !auth.hasPerm('user:edit')) return
  const revision = auth.sessionRevision
  retiring.value = true
  try {
    const name = row.nickname || row.username || `账户 #${row.id}`
    let value: string
    try {
      const result = await ElMessageBox.prompt(`确认清空「${name}」（${row.boss_no || `账户 #${row.id}`}）的业务权益？历史订单和财务保留；在途订单、预约、客诉及资金须先结清。请填写原因。`, '退店清空', { inputValidator: (input: string) => !!input && input.trim().length >= 2 && input.trim().length <= 500, inputErrorMessage: '请填写 2 至 500 字原因', confirmButtonText: '确认清空', cancelButtonText: '取消' })
      value = result.value
    } catch { return }
    if (userPageDisposed || revision !== auth.sessionRevision || !auth.hasPerm('user:edit')) return
    try {
      await userApi.action(row.id, 'retire', { reason: value.trim() }, true)
      if (userPageDisposed || revision !== auth.sessionRevision) return
      ElMessage.success('业务权益已重置')
      tableRef.value?.load()
    } catch (error: any) {
      if (userPageDisposed || revision !== auth.sessionRevision) return
      const body = error?.response?.data || error, status = error?.response?.status
      const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
      if (rejected) ElMessage.error(typeof body?.msg === 'string' && body.msg ? body.msg : '退店请求被拒绝，请核对资料')
      else ElMessage.warning('退店结果未确认，请先联系管理员核对，勿重复操作。')
    }
  } finally { retiring.value = false }
}
</script>

<style scoped lang="scss">
.us-draft-status { margin: 0; color: var(--muted-foreground); font-size: 12px; }
.us-field-message { width: 100%; color: var(--muted-foreground); font-size: 12px; line-height: 1.5; margin-top: 4px; }
.tip {
  font-size: 12px;
  color: var(--muted-foreground);
  margin-left: 12px;
}

/* 编辑弹窗：分区分组 + token 视觉 */

.us-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 让 CrudTable 内置的外层 padding 归零，融入 us-page 的间距节奏 */
.us-page :deep(.page-container) {
  padding: 0;
}

.us-list-heading { display: flex; align-items: center; gap: 4px; }
.us-help { width: var(--control-height-sm); height: var(--control-height-sm); border: 0; border-radius: 50%; color: var(--primary); background: transparent; cursor: pointer; }
.us-help:focus-visible { outline: 2px solid var(--primary); outline-offset: 2px; }
.us-mobile-list { display: grid; gap: 12px; }
.us-mobile-card { padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.us-mobile-head { display: flex; align-items: center; gap: 10px; }
.us-mobile-identity { display: grid; flex: 1; min-width: 0; gap: 3px; }
.us-mobile-identity strong, .us-mobile-identity span { overflow-wrap: anywhere; }
.us-mobile-identity span { color: var(--muted-foreground); font-size: 12px; }
.us-mobile-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px; margin: 12px 0; padding-top: 12px; border-top: 1px solid var(--border); }
.us-mobile-facts div { min-width: 0; }
.us-mobile-facts dt { color: var(--muted-foreground); font-size: 12px; }
.us-mobile-facts dd { margin: 2px 0 0; overflow-wrap: anywhere; }
.us-mobile-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.us-mobile-actions :deep(.el-button + .el-button) { margin-left: 0; }
.us-code {
  padding: 1px 6px;
  border-radius: 6px;
  background: var(--secondary);
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--muted-foreground);
}
.us-lock-tag {
  padding: 1px 8px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--chart-3) 16%, var(--card));
  color: var(--chart-3);
  font-size: 11px;
}
.us-balance-cell {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}
.us-no {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.02em;
}

</style>
