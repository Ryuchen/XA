<template>
  <div class="registration-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">核对注册资料与押金，审核通过后开通陪玩账号。</p>
    </div>
    <el-alert v-if="reviewMarker" type="warning" :closable="false" show-icon title="审核结果未确认">
      <p>请核对申请 #{{ reviewMarker.id }}；原请求可能仍在处理中。</p>
      <el-button link type="primary" :disabled="reviewLocked" @click="openDetail(reviewMarker.id)">查看申请</el-button>
    </el-alert>
    <crud-table ref="tableRef" :fetcher="providerRegistrationApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input v-model="query.search" placeholder="账号 / 手机号 / 姓名 / 昵称" clearable class="search" @keyup.enter="reload" @clear="reload" />
        <el-select v-model="query.status" placeholder="审核状态" clearable class="status-filter" @change="reload">
          <el-option label="待审核" value="PENDING" />
          <el-option label="已通过" value="APPROVED" />
          <el-option label="需补充资料" value="REJECTED" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
      </template>
      <el-table-column prop="username" label="登录账号" min-width="150" show-overflow-tooltip />
      <el-table-column prop="display_name" label="结单昵称" min-width="140" show-overflow-tooltip />
      <el-table-column prop="real_name" label="真实姓名" min-width="110" />
      <el-table-column prop="phone" label="手机号" width="140" />
      <el-table-column prop="id_number_masked" label="身份证号" width="190" />
      <el-table-column label="状态" width="120">
        <template #default="{ row }"><el-tag :type="statusType(row.status)">{{ row.status_display }}</el-tag></template>
      </el-table-column>
      <el-table-column label="提交时间" width="170">
        <template #default="{ row }">{{ formatDateTime(row.submitted_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }"><el-button link type="primary" @click="openDetail(row.id)">{{ row.status === 'PENDING' && auth.hasPerm('registration:audit') ? '查看并审核' : '查看详情' }}</el-button></template>
      </el-table-column>
      <template #mobile="{ rows }">
        <ul class="registration-list" aria-label="注册申请">
          <li v-for="row in rows" :key="row.id" class="registration-item">
            <div class="registration-item-head"><strong>{{ row.display_name || '未填写昵称' }}</strong><el-tag :type="statusType(row.status)" size="small">{{ row.status_display }}</el-tag></div>
            <p>账号 {{ row.username }} · 申请 #{{ row.id }}</p><p>提交 {{ formatDateTime(row.submitted_at) }}</p>
            <el-button size="small" type="primary" plain @click="openDetail(row.id)">{{ row.status === 'PENDING' && auth.hasPerm('registration:audit') ? '查看并审核' : '查看详情' }}</el-button>
          </li>
        </ul>
      </template>
    </crud-table>

    <el-drawer v-model="visible" title="陪玩注册资料" size="min(760px, 100vw)" class="ap-panel" :show-close="!reviewLocked" :close-on-click-modal="!reviewLocked" :close-on-press-escape="!reviewLocked" @closed="clearDetail">
      <div v-if="loading" v-loading="loading" class="detail-loading">正在读取申请资料</div>
      <el-alert v-else-if="loadError" :title="loadError" type="error" :closable="false">
        <el-button link type="primary" @click="openDetail(currentId)">重试加载</el-button>
      </el-alert>
      <template v-else-if="current">
        <el-alert v-if="reviewMarker && reviewMarker.id === current.id" type="warning" :closable="false" show-icon title="请核对本次审核结果">
          <p>当前资料是读取时的状态，原请求可能仍在处理中。</p>
          <el-button v-if="!markerChecked" link type="primary" :disabled="reviewLocked" @click="openDetail(current.id)">重新读取申请</el-button>
          <el-button link type="primary" :disabled="reviewLocked || !markerChecked" @click="finishReview">已核对，解除限制</el-button>
        </el-alert>
        <el-alert v-if="reviewError" type="warning" :closable="false" show-icon :title="reviewError">
          <el-button v-if="reviewNeedsReload" link type="primary" :disabled="reviewLocked" @click="openDetail(current.id)">重新读取</el-button>
        </el-alert>
        <div class="detail-status">
          <el-tag :type="statusType(current.status)">{{ current.status_display }}</el-tag>
          <span>{{ formatDateTime(current.submitted_at) }} 提交</span>
        </div>
        <el-descriptions :column="1" border class="identity">
          <el-descriptions-item label="登录账号">{{ current.username }}</el-descriptions-item>
          <el-descriptions-item label="结单昵称">{{ current.display_name }}</el-descriptions-item>
          <el-descriptions-item label="真实姓名">{{ current.real_name }}</el-descriptions-item>
          <el-descriptions-item label="身份证号">{{ current.id_number }}</el-descriptions-item>
          <el-descriptions-item label="手机号">{{ current.phone }}</el-descriptions-item>
          <el-descriptions-item label="押金方式">{{ current.deposit_mode === 'EARNINGS' ? '单抵双倍' : current.deposit_mode === 'CASH' ? '现金缴纳' : '待核对' }}</el-descriptions-item>
          <el-descriptions-item label="应缴押金">{{ formatRmb(current.deposit_required) }} 元</el-descriptions-item>
          <el-descriptions-item v-if="current.status === 'APPROVED'" label="开通等级">{{ current.level_name || '未记录' }}</el-descriptions-item>
          <el-descriptions-item v-if="current.status === 'APPROVED' && current.deposit_mode === 'CASH'" label="登记现金押金">{{ formatRmb(current.deposit_paid) }} 元</el-descriptions-item>
          <el-descriptions-item v-if="current.reviewed_at" label="审核人">{{ current.reviewer_name }}</el-descriptions-item>
          <el-descriptions-item v-if="current.reviewed_at" label="审核时间">{{ formatDateTime(current.reviewed_at) }}</el-descriptions-item>
          <el-descriptions-item v-if="current.review_note" label="审核意见">{{ current.review_note }}</el-descriptions-item>
        </el-descriptions>
        <div class="documents">
          <div v-for="document in documents" :key="document.key" class="document" :class="{ receipt: document.key === 'deposit_proof' }">
            <h3>{{ document.label }}</h3>
            <PrivateImage v-if="document.url" :src="document.url" :preview-src-list="documents.map(item => item.url).filter(Boolean)" fit="contain" class="document-photo" />
            <div v-else class="missing-photo">照片不可用，请重新读取资料</div>
            <p>点击照片放大核对</p>
          </div>
        </div>
        <div v-if="current.status === 'PENDING' && auth.hasPerm('registration:audit')" class="review-form">
          <h3>审核处理</h3>
          <el-form label-position="top">
            <el-form-item label="开通后的陪玩等级">
              <el-select v-model="review.level_id" placeholder="选择陪玩等级" class="full-width" :disabled="reviewLocked">
                <el-option v-for="level in levels" :key="level.id" :label="level.name" :value="level.id" />
              </el-select>
            </el-form-item>
            <el-form-item v-if="current.deposit_mode === 'CASH'" label="已确认到账的现金押金（元）">
              <el-input-number v-model="review.deposit_yuan" :min="Number(formatRmb(current.deposit_required))" :max="100000" :precision="2" :step="50" controls-position="right" class="full-width" :disabled="reviewLocked" />
            </el-form-item>
            <div class="confirmations">
              <el-checkbox v-model="review.identity_confirmed" :disabled="reviewLocked">已核对身份证正反面、姓名与号码</el-checkbox>
              <el-checkbox v-if="current.deposit_mode === 'CASH'" v-model="review.deposit_confirmed" :disabled="reviewLocked">已核实押金实际到账与交付凭证一致</el-checkbox>
            </div>
            <el-form-item label="审核意见">
              <el-input v-model="review.review_note" type="textarea" :rows="3" :maxlength="500" show-word-limit placeholder="退回补充资料时，必须填写需要修改的内容" :disabled="reviewLocked" />
            </el-form-item>
          </el-form>
          <el-alert :title="current.deposit_mode === 'CASH' ? '请核实现金押金到账；通过后登记押金并开通账号。' : current.deposit_mode === 'EARNINGS' ? '单抵从后续订单净收益抵扣，本次不登记现金押金。' : '押金方式无法识别，请重新读取资料。'" type="info" :closable="false" />
        </div>
      </template>
      <template #footer>
        <div class="drawer-actions">
          <el-button :disabled="reviewLocked" @click="visible = false">关闭</el-button>
          <template v-if="current?.status === 'PENDING' && !loading && auth.hasPerm('registration:audit')">
            <el-button type="danger" plain :disabled="reviewLocked || !!reviewMarker || reviewNeedsReload" @click="reviewApplication(false)">退回补充资料</el-button>
            <el-button type="primary" :loading="saving" :disabled="!canApprove || reviewLocked || !!reviewMarker || reviewNeedsReload" @click="reviewApplication(true)">通过并开通账号</el-button>
          </template>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import PrivateImage from '@/components/PrivateImage.vue'
import { providerRegistrationApi, escortLevelApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'
import { formatRmb, rmbToRawAmount } from '@xa/money'

interface Registration {
  id: number; username: string; phone: string; real_name: string; display_name: string;
  id_number: string; id_front_url: string; id_back_url: string; deposit_proof_url: string;
  status: string; status_display: string; revision: number; deposit_required: number; deposit_paid: number; deposit_mode: 'CASH' | 'EARNINGS'; level_name: string;
  submitted_at: string; reviewed_at: string; review_note: string; reviewer_name: string;
}
const auth = useAuthStore()
const tableRef = ref()
const query = reactive({ search: '', status: 'PENDING' })
const visible = ref(false)
const current = ref<Registration | null>(null)
const currentId = ref(0)
const loading = ref(false)
const saving = ref(false)
const confirming = ref(false)
const reviewLocked = computed(() => saving.value || confirming.value)
const loadError = ref('')
const reviewError = ref('')
const reviewNeedsReload = ref(false)
const reviewMarker = ref<{ id: number } | null>(null)
const markerChecked = ref(false)
const levels = ref<{ id: number; name: string }[]>([])
const review = reactive({ level_id: undefined as number | undefined, deposit_yuan: 200, identity_confirmed: false, deposit_confirmed: false, review_note: '' })
let sequence = 0
let confirmationSequence = 0
let disposed = false
let markerOwner = ''
function markerKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_registration_review_${id}` : ''
}
function restoreMarker() {
  const key = markerKey()
  if (key === markerOwner) return
  markerOwner = key
  reviewMarker.value = null
  markerChecked.value = false
  if (!key) return
  try {
    const item = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (Number.isSafeInteger(item?.id) && item.id > 0) reviewMarker.value = { id: item.id }
  } catch { /* In-page guard remains available. */ }
}
function beginReview(id: number) {
  reviewMarker.value = { id }
  markerChecked.value = false
  const key = markerKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify({ id })) } catch { /* In-page guard remains available. */ }
}
function clearReview() {
  const key = markerKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  reviewMarker.value = null
  markerChecked.value = false
}
async function finishReview() {
  const marker = reviewMarker.value, revision = auth.sessionRevision, version = sequence
  if (!marker || !markerChecked.value || reviewLocked.value || current.value?.id !== marker.id) return
  confirming.value = true
  const confirmation = ++confirmationSequence
  let accepted = false
  try {
    await ElMessageBox.confirm('请核对申请当前状态与押金记录；原请求可能仍在处理中。', '解除审核限制', {
      type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对', closeOnClickModal: false,
    })
    accepted = true
  } catch { /* Keep the marker. */ }
  finally { if (confirmation === confirmationSequence) confirming.value = false }
  if (accepted && !disposed && confirmation === confirmationSequence && revision === auth.sessionRevision && version === sequence
    && auth.hasPerm('registration:view') && auth.hasPerm('registration:audit') && visible.value && reviewMarker.value === marker && markerChecked.value) clearReview()
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreMarker, { immediate: true, flush: 'sync' })
watch([() => auth.sessionRevision, () => auth.hasPerm('registration:view'), () => auth.hasPerm('registration:audit')], () => {
  if (confirming.value) ElMessageBox.close()
  ++sequence; ++confirmationSequence
  visible.value = false; current.value = null; loading.value = false; saving.value = false; confirming.value = false
  markerChecked.value = false; reviewError.value = ''; reviewNeedsReload.value = false
}, { flush: 'sync' })
onBeforeUnmount(() => {
  disposed = true; ++sequence; ++confirmationSequence
  if (confirming.value) ElMessageBox.close()
})
const canApprove = computed(() => !!current.value && !!review.level_id && review.identity_confirmed && (current.value.deposit_mode === 'EARNINGS' || (current.value.deposit_mode === 'CASH' && review.deposit_confirmed
  && Number.isFinite(review.deposit_yuan) && rmbToRawAmount(review.deposit_yuan) >= current.value.deposit_required)))
const documents = computed(() => current.value ? [
  { key: 'id_front', label: '身份证正面（人像面）', url: current.value.id_front_url },
  { key: 'id_back', label: '身份证反面（国徽面）', url: current.value.id_back_url },
  { key: 'deposit_proof', label: '押金交付凭证', url: current.value.deposit_proof_url },
] .filter(item => item.key !== 'deposit_proof' || current.value?.deposit_mode === 'CASH') : [])
function statusType(status: string) { return status === 'APPROVED' ? 'success' : status === 'REJECTED' ? 'danger' : 'warning' }
function clearDetail() { if (!visible.value) { ++sequence; current.value = null; loadError.value = ''; markerChecked.value = false } }
async function openDetail(id: number) {
  if (reviewLocked.value || !auth.hasPerm('registration:view')) return
  currentId.value = id; visible.value = true; current.value = null; loading.value = true; loadError.value = ''
  markerChecked.value = false; reviewError.value = ''; reviewNeedsReload.value = false
  Object.assign(review, { level_id: undefined, deposit_yuan: 200, identity_confirmed: false, deposit_confirmed: false, review_note: '' })
  const version = ++sequence, revision = auth.sessionRevision
  try {
    const [detail, options] = await Promise.all([
      providerRegistrationApi.retrieve(id, true), escortLevelApi.allOptions({ is_active: true }),
    ])
    if (disposed || version !== sequence || revision !== auth.sessionRevision || !visible.value || !auth.hasPerm('registration:view')) return
    current.value = detail.data as Registration
    levels.value = options.data.list
    review.deposit_yuan = Number(formatRmb(current.value.deposit_required))
    markerChecked.value = reviewMarker.value?.id === id
  } catch (error) {
    if (version === sequence && revision === auth.sessionRevision) loadError.value = '资料加载失败，请检查网络或权限后重试'
  } finally { if (version === sequence) loading.value = false }
}
async function reviewApplication(approve: boolean) {
  if (disposed || !current.value || !visible.value || reviewLocked.value || reviewMarker.value || reviewNeedsReload.value
    || current.value.status !== 'PENDING' || !auth.hasPerm('registration:view') || !auth.hasPerm('registration:audit')) return
  if (approve && !canApprove.value) return
  if (!approve && !review.review_note.trim()) { ElMessage.warning('请填写需要补充的资料和原因'); return }
  const application = current.value
  const revision = auth.sessionRevision, version = sequence, note = review.review_note.trim()
  const level = levels.value.find(item => item.id === review.level_id)
  if (approve && !level) { ElMessage.warning('请选择有效的陪玩等级'); return }
  const payload = {
    revision: application.revision, review_note: note,
    ...(approve ? { level_id: review.level_id, identity_confirmed: review.identity_confirmed,
      deposit_confirmed: application.deposit_mode === 'CASH' && review.deposit_confirmed,
      deposit_paid: application.deposit_mode === 'CASH' ? rmbToRawAmount(review.deposit_yuan) : 0 } : {}),
  }
  const summary = [h('p', `申请 #${application.id} · 账号 ${application.username}`),
    h('p', `结单昵称：${application.display_name}`)]
  if (approve) {
    summary.push(h('p', `开通等级：${level!.name}`),
      h('p', application.deposit_mode === 'CASH'
        ? `现金押金：登记 ${formatRmb(payload.deposit_paid)} 元（应缴 ${formatRmb(application.deposit_required)} 元）`
        : `单抵押金：应缴 ${formatRmb(application.deposit_required)} 元，本次不登记现金`),
      h('p', '通过后开通账号，首次登录需绑定验证器。'))
    if (note) summary.push(h('p', `审核意见：${note}`))
  } else summary.push(h('p', `退回原因：${note}`))
  const confirmation = ++confirmationSequence
  confirming.value = true
  let accepted = false
  try {
    await ElMessageBox.confirm(h('div', { class: 'registration-confirm' }, summary),
      approve ? '核对并开通账号' : '核对退回申请', { type: 'warning', confirmButtonText: approve ? '确认开通' : '确认退回',
        cancelButtonText: '返回修改', closeOnClickModal: false, customClass: 'registration-confirm-box' })
    accepted = true
  } catch { /* Keep the form. */ }
  finally { if (confirmation === confirmationSequence) confirming.value = false }
  if (!accepted || disposed || confirmation !== confirmationSequence || version !== sequence || revision !== auth.sessionRevision
    || !visible.value || current.value?.id !== application.id || !auth.hasPerm('registration:view') || !auth.hasPerm('registration:audit') || reviewMarker.value) return
  beginReview(application.id)
  saving.value = true
  try {
    await providerRegistrationApi.action(application.id, approve ? 'approve' : 'reject', payload, true)
    if (disposed || version !== sequence || revision !== auth.sessionRevision || !visible.value || current.value?.id !== application.id
      || !auth.hasPerm('registration:view') || !auth.hasPerm('registration:audit')) return
    clearReview()
    ElMessage.success(approve ? '账号已开通，首次登录需绑定验证器' : '已退回补充资料')
    visible.value = false
    await tableRef.value?.load()
  } catch (error: any) {
    if (disposed || version !== sequence || revision !== auth.sessionRevision || !auth.hasPerm('registration:view') || !auth.hasPerm('registration:audit') || !visible.value || current.value?.id !== application.id) return
    const body = error?.response?.data || error, status = error?.response?.status
    const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
    if (rejected) {
      clearReview()
      reviewNeedsReload.value = status === 409 || body?.code === 409
      reviewError.value = `审核未完成：${typeof body?.msg === 'string' && body.msg ? body.msg : '请求被拒绝'}${reviewNeedsReload.value ? '，请重新读取资料' : ''}`
      if (reviewNeedsReload.value) void tableRef.value?.load()
    } else {
      reviewError.value = '审核结果未确认，请读取最新申请并核对；原请求可能仍在处理中'
    }
  } finally { if (version === sequence && revision === auth.sessionRevision) saving.value = false }
}
</script>

<style scoped lang="scss">
.registration-page { display:flex; flex-direction:column; gap:20px; }
.registration-page :deep(.page-container) { padding:0; }
.search { width:280px; }.status-filter { width:150px; }
.registration-list { list-style:none; margin:0; padding:0 12px; }
.registration-item { padding:14px 0; border-bottom:1px solid var(--el-border-color); }
.registration-item-head { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.registration-item-head strong { min-width:0; overflow-wrap:anywhere; }
.registration-item p { margin:8px 0; color:var(--el-text-color-secondary); font-size:12px; overflow-wrap:anywhere; }
.registration-confirm p { margin:8px 0; overflow-wrap:anywhere; }
:global(.registration-confirm-box) { width:min(480px, calc(100vw - 24px)); max-height:calc(100vh - 24px); overflow:auto; }
.detail-loading { min-height:180px; display:flex; align-items:center; justify-content:center; color:var(--el-text-color-secondary); }
.detail-status { display:flex; flex-wrap:wrap; align-items:center; gap:12px; margin-bottom:20px; font-size:13px; color:var(--el-text-color-secondary); }
.documents { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:20px; margin:24px 0; }
.document { min-width:0; }.document.receipt { grid-column:1/-1; }.document h3,.review-form h3 { margin:0 0 12px; font-size:15px; font-weight:600; }
.document-photo { width:100%; height:200px; border:1px solid var(--el-border-color); border-radius:8px; background:var(--el-fill-color-light); cursor:zoom-in; }
.document.receipt .document-photo { height:280px; }.document p { margin:8px 0 0; font-size:12px; color:var(--el-text-color-secondary); }
.missing-photo { padding:48px 16px; color:var(--el-color-danger); background:var(--el-fill-color-light); border-radius:8px; text-align:center; }
.review-form { padding-top:24px; border-top:1px solid var(--el-border-color); }.full-width { width:100%; }
.confirmations { display:flex; flex-direction:column; align-items:flex-start; gap:8px; margin:0 0 20px; }.confirmations :deep(.el-checkbox) { height:auto; min-height:32px; white-space:normal; }.confirmations :deep(.el-checkbox__label) { line-height:1.7; white-space:normal; }
.drawer-actions { display:flex; align-items:center; justify-content:flex-end; flex-wrap:wrap; gap:10px; }.drawer-actions :deep(.el-button) { margin:0; }
@media(max-width:600px) { .documents { grid-template-columns:minmax(0,1fr); }.document.receipt { grid-column:auto; }.search { width:100%; }.status-filter { width:160px; }.drawer-actions :deep(.el-button) { flex:1; } }
</style>
