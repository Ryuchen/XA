<template>
  <div class="registration-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">核对实名资料，现金方式核实到账，单抵方式从后续净收益抵扣。申请通过后，陪玩首次登录需自行绑定验证器。</p>
    </div>
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
    </crud-table>

    <el-drawer v-model="visible" title="陪玩注册资料" size="min(760px, 100vw)" class="ap-panel" @closed="clearDetail">
      <div v-if="loading" v-loading="loading" class="detail-loading">正在读取申请资料</div>
      <el-alert v-else-if="loadError" :title="loadError" type="error" :closable="false">
        <el-button link type="primary" @click="openDetail(currentId)">重试加载</el-button>
      </el-alert>
      <template v-else-if="current">
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
          <el-descriptions-item label="押金方式">{{ current.deposit_mode === 'EARNINGS' ? '单抵双倍' : '现金缴纳' }}</el-descriptions-item>
          <el-descriptions-item label="应缴押金">{{ formatRmb(current.deposit_required) }} 元</el-descriptions-item>
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
              <el-select v-model="review.level_id" placeholder="选择陪玩等级" class="full-width">
                <el-option v-for="level in levels" :key="level.id" :label="level.name" :value="level.id" />
              </el-select>
            </el-form-item>
            <el-form-item v-if="current.deposit_mode === 'CASH'" label="已确认到账的现金押金（元）">
              <el-input-number v-model="review.deposit_yuan" :min="Number(formatRmb(current.deposit_required))" :max="100000" :precision="2" :step="50" controls-position="right" class="full-width" />
            </el-form-item>
            <div class="confirmations">
              <el-checkbox v-model="review.identity_confirmed">已核对身份证正反面、姓名与号码</el-checkbox>
              <el-checkbox v-if="current.deposit_mode === 'CASH'" v-model="review.deposit_confirmed">已核实押金实际到账与交付凭证一致</el-checkbox>
            </div>
            <el-form-item label="审核意见">
              <el-input v-model="review.review_note" type="textarea" :rows="3" :maxlength="500" show-word-limit placeholder="退回补充资料时，必须填写需要修改的内容" />
            </el-form-item>
          </el-form>
          <el-alert :title="current.deposit_mode === 'CASH' ? '照片上传不代表押金已到账。核实收款记录后通过审核，将登记现金押金并开通账号。' : '单抵应缴押金为400元，通过审核后从订单净收益按平台比例抵扣，达到400元后全部结款。本次不登记现金押金。'" type="info" :closable="false" />
        </div>
      </template>
      <template #footer>
        <div class="drawer-actions">
          <el-button :disabled="saving" @click="visible = false">关闭</el-button>
          <template v-if="current?.status === 'PENDING' && !loading && auth.hasPerm('registration:audit')">
            <el-button type="danger" plain :loading="saving" @click="reviewApplication(false)">退回补充资料</el-button>
            <el-button type="primary" :loading="saving" :disabled="!canApprove" @click="reviewApplication(true)">通过并开通账号</el-button>
          </template>
        </div>
      </template>
    </el-drawer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref } from 'vue'
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
  status: string; status_display: string; revision: number; deposit_required: number; deposit_mode: 'CASH' | 'EARNINGS';
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
const loadError = ref('')
const levels = ref<{ id: number; name: string }[]>([])
const review = reactive({ level_id: undefined as number | undefined, deposit_yuan: 200, identity_confirmed: false, deposit_confirmed: false, review_note: '' })
let sequence = 0
onBeforeUnmount(() => { ++sequence })
const canApprove = computed(() => !!current.value && !!review.level_id && review.identity_confirmed && (current.value.deposit_mode === 'EARNINGS' || (review.deposit_confirmed
  && Number.isFinite(review.deposit_yuan) && rmbToRawAmount(review.deposit_yuan) >= current.value.deposit_required)))
const documents = computed(() => current.value ? [
  { key: 'id_front', label: '身份证正面（人像面）', url: current.value.id_front_url },
  { key: 'id_back', label: '身份证反面（国徽面）', url: current.value.id_back_url },
  { key: 'deposit_proof', label: '押金交付凭证', url: current.value.deposit_proof_url },
] .filter(item => item.key !== 'deposit_proof' || current.value?.deposit_mode === 'CASH') : [])
function statusType(status: string) { return status === 'APPROVED' ? 'success' : status === 'REJECTED' ? 'danger' : 'warning' }
function clearDetail() { ++sequence; current.value = null; loadError.value = '' }
async function openDetail(id: number) {
  currentId.value = id; visible.value = true; current.value = null; loading.value = true; loadError.value = ''
  Object.assign(review, { level_id: undefined, deposit_yuan: 200, identity_confirmed: false, deposit_confirmed: false, review_note: '' })
  const version = ++sequence
  try {
    const [detail, options] = await Promise.all([
      providerRegistrationApi.retrieve(id), escortLevelApi.allOptions({ is_active: true }),
    ])
    if (version !== sequence) return
    current.value = detail.data as Registration
    levels.value = options.data.list
    review.deposit_yuan = Number(formatRmb(current.value.deposit_required))
    review.level_id = levels.value[0]?.id
  } catch (error) {
    if (version === sequence) loadError.value = error instanceof Error ? error.message : '资料加载失败'
  } finally { if (version === sequence) loading.value = false }
}
async function reviewApplication(approve: boolean) {
  if (!current.value || saving.value) return
  if (approve && !canApprove.value) return
  if (!approve && !review.review_note.trim()) { ElMessage.warning('请填写需要补充的资料和原因'); return }
  const application = current.value
  try {
    await ElMessageBox.confirm(
      approve ? (application.deposit_mode === 'CASH' ? '确认实名资料与现金押金到账均已核实，并开通该陪玩账号？' : '确认实名资料已核实，以单抵400元方式开通账号，押金从后续净收益抵扣？') : '确认将申请退回，由申请人补充资料后重新提交？',
      approve ? '通过注册审核' : '退回补充资料', { type: 'warning', confirmButtonText: '确认处理' },
    )
  } catch { return }
  saving.value = true
  try {
    await providerRegistrationApi.action(application.id, approve ? 'approve' : 'reject', {
      revision: application.revision, review_note: review.review_note.trim(),
      ...(approve ? { level_id: review.level_id, identity_confirmed: review.identity_confirmed,
        deposit_confirmed: application.deposit_mode === 'CASH' && review.deposit_confirmed, deposit_paid: application.deposit_mode === 'CASH' ? rmbToRawAmount(review.deposit_yuan) : 0 } : {}),
    })
    ElMessage.success(approve ? '账号已开通，首次登录需绑定验证器' : '已退回补充资料')
    visible.value = false
    await tableRef.value?.load()
  } catch (error) {
    // A revision conflict requires reading fresh documents before making a new decision.
    if (visible.value) await openDetail(application.id)
  } finally { saving.value = false }
}
</script>

<style scoped lang="scss">
.registration-page { display:flex; flex-direction:column; gap:20px; }
.registration-page :deep(.page-container) { padding:0; }
.search { width:280px; }.status-filter { width:150px; }
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
