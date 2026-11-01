<template>
  <div class="su-page">
    <el-alert v-if="marker" title="审核结果待核对" type="warning" :closable="false" show-icon>
      <p>报名 #{{ marker.id }} · {{ marker.name }}：原请求可能仍在处理。请读取当前状态并核对。</p>
      <el-button link type="primary" :loading="checking" @click="checkResult">读取当前状态</el-button>
      <span v-if="checkText">{{ checkText }}</span>
      <el-button link type="primary" :disabled="!checked || checking" @click="clearAfterReview">已核对，解除限制</el-button>
    </el-alert>
    <div class="ap-page-head"><p class="ap-page-intro">审核试音报名；通过不代表开通陪玩账号。</p></div>
    <crud-table ref="tableRef" :fetcher="auditionSignupApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input v-model="query.keyword" placeholder="编号/账号/游戏/联系方式" aria-label="搜索试音报名" clearable style="width: 220px" @keyup.enter="reload" @clear="reload" />
        <el-select v-model="query.status" placeholder="审核状态" aria-label="审核状态" clearable style="width: 130px" @change="reload"><el-option v-for="(v, k) in AUDITION_SIGNUP_STATUS" :key="k" :label="v.label" :value="k" /></el-select>
        <el-button type="primary" @click="reload">查询</el-button>
      </template>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="link_title" label="试音活动" min-width="140" show-overflow-tooltip />
      <el-table-column label="报名人" min-width="160"><template #default="{ row }">{{ row.applicant_name || '—' }}<small class="su-identity">{{ identity(row) }}</small></template></el-table-column>
      <el-table-column prop="contact" label="联系方式" min-width="130" show-overflow-tooltip />
      <el-table-column prop="game" label="擅长游戏" min-width="120" show-overflow-tooltip />
      <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="AUDITION_SIGNUP_STATUS[row.status]?.type" size="small">{{ row.status_display }}</el-tag></template></el-table-column>
      <el-table-column label="提交时间" width="160"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
      <el-table-column label="操作" width="200" fixed="right"><template #default="{ row }">
        <el-button link type="primary" @click="onDetail(row)">详情</el-button>
        <template v-if="auth.hasPerm('audition:signup_audit') && row.status === 'PENDING'"><el-button link type="success" :disabled="busy || !!marker" @click="openReview(row, 'approve')">通过</el-button><el-button link type="danger" :disabled="busy || !!marker" @click="openReview(row, 'reject')">驳回</el-button></template>
      </template></el-table-column>
      <template #mobile="{ rows }"><ul class="su-list" aria-label="试音报名列表"><li v-for="row in rows" :key="row.id" class="su-card">
        <div class="su-card-head"><strong>{{ row.applicant_name || '—' }}</strong><el-tag :type="AUDITION_SIGNUP_STATUS[row.status]?.type" size="small">{{ row.status_display }}</el-tag></div>
        <p>{{ identity(row) }}</p><p>活动 {{ row.link_title || '—' }}</p><p>游戏 {{ row.game || '—' }} · {{ formatDateTime(row.created_at) }}</p>
        <div class="su-actions"><el-button size="small" @click="onDetail(row)">详情</el-button><template v-if="auth.hasPerm('audition:signup_audit') && row.status === 'PENDING'"><el-button size="small" type="success" plain :disabled="busy || !!marker" @click="openReview(row, 'approve')">通过</el-button><el-button size="small" type="danger" plain :disabled="busy || !!marker" @click="openReview(row, 'reject')">驳回</el-button></template></div>
      </li></ul></template>
    </crud-table>
  </div>
  <el-drawer v-model="detailVisible" title="报名详情" size="min(480px, 100vw)" class="ap-panel">
    <template v-if="current"><p class="su-hint">显示列表读取时的信息。</p><el-descriptions :column="1" border>
      <el-descriptions-item label="试音活动">{{ current.link_title }}</el-descriptions-item><el-descriptions-item label="报名人">{{ current.applicant_name }} · {{ identity(current) }}</el-descriptions-item><el-descriptions-item label="联系方式">{{ current.contact || '—' }}</el-descriptions-item><el-descriptions-item label="擅长游戏">{{ current.game || '—' }}</el-descriptions-item><el-descriptions-item label="备注">{{ current.remark || '—' }}</el-descriptions-item><el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item><el-descriptions-item v-if="current.status === 'REJECTED'" label="驳回原因">{{ current.audit_remark || '—' }}</el-descriptions-item><el-descriptions-item label="审核人">{{ current.auditor_name || '—' }}</el-descriptions-item><el-descriptions-item label="提交时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item><el-descriptions-item label="审核时间">{{ current.audited_at ? formatDateTime(current.audited_at) : '—' }}</el-descriptions-item>
    </el-descriptions></template>
  </el-drawer>
  <el-dialog v-model="reviewVisible" :title="action === 'approve' ? '通过试音报名' : '驳回试音报名'" width="min(520px, calc(100vw - 24px))" class="ap-modal" :show-close="!saving" :close-on-click-modal="false" :close-on-press-escape="!saving" :before-close="beforeClose">
    <template v-if="candidate"><p><strong>活动：</strong>{{ candidate.link_title }}</p><p><strong>报名人：</strong>{{ candidate.applicant_name }} · {{ identity(candidate) }}</p><p><strong>游戏：</strong>{{ candidate.game || '—' }}</p><p><strong>联系方式：</strong>{{ candidate.contact || '—' }}</p>
      <el-form v-if="action === 'reject'" label-position="top"><el-form-item label="驳回原因"><el-input v-model="reason" type="textarea" :rows="3" maxlength="255" show-word-limit :disabled="saving" placeholder="填写反馈给报名人的原因" /></el-form-item></el-form>
      <p class="su-hint">仅更新报名状态，并尝试创建站内通知；不代表账号已开通或通知已送达。</p>
    </template>
    <template #footer><el-button :disabled="saving" @click="reviewVisible = false">返回</el-button><el-button :type="action === 'approve' ? 'primary' : 'danger'" :loading="saving" @click="submitReview">{{ action === 'approve' ? '确认通过' : '确认驳回' }}</el-button></template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref, watch, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { auditionSignupApi } from '@/api/modules'
import { AUDITION_SIGNUP_STATUS } from '@/utils/dict'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore(), tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '', status: '' })
const detailVisible = ref(false), current = ref<any>(null), reviewVisible = ref(false), candidate = ref<any>(null)
const action = ref<'approve' | 'reject'>('approve'), reason = ref(''), busy = ref(false), saving = ref(false)
const marker = ref<{ id: number; name: string } | null>(null), checked = ref(false), checking = ref(false), checkText = ref('')
let ownerKey = '', sequence = 0, disposed = false
function identity(row: any) { return row.applicant_no ? `${row.applicant_no} · ${row.applicant_username || '—'}` : row.applicant_username ? `旧账号 · ${row.applicant_username}` : '身份待核对' }
function markerKey() { const id = auth.profile?.id; return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_audition_audit_review_${id}` : '' }
function restoreMarker() { const key = markerKey(); if (key === ownerKey) return; ownerKey = key; marker.value = null; checked.value = false; checkText.value = ''; if (key) try { const item = JSON.parse(sessionStorage.getItem(key) || 'null'); if (Number.isSafeInteger(item?.id) && item.id > 0 && typeof item.name === 'string') marker.value = item } catch { /* In-page guard remains. */ } }
function beginMarker(item: { id: number; name: string }) { marker.value = item; checked.value = false; checkText.value = ''; const key = markerKey(); if (key) try { sessionStorage.setItem(key, JSON.stringify(item)) } catch { /* In-page guard remains. */ } }
function clearMarker() { const key = markerKey(); if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }; marker.value = null; checked.value = false; checkText.value = '' }
function beforeClose(done: () => void) { if (!saving.value) done() }
function onDetail(row: any) { current.value = row; detailVisible.value = true }
async function openReview(row: any, kind: 'approve' | 'reject') {
  if (busy.value || marker.value || !auth.hasPerm('audition:signup_audit')) return
  const seq = ++sequence, revision = auth.sessionRevision
  busy.value = true
  try {
    const res = await auditionSignupApi.retrieve(row.id, true)
    if (disposed || seq !== sequence || revision !== auth.sessionRevision || !auth.hasPerm('audition:signup_audit')) return
    if (res.data.status !== 'PENDING') { ElMessage.warning('该报名已审核，请查看最新状态'); tableRef.value?.load(); return }
    candidate.value = res.data; action.value = kind; reason.value = ''; reviewVisible.value = true
  } catch { if (!disposed && seq === sequence && revision === auth.sessionRevision) ElMessage.error('报名读取失败，请刷新后重试') }
  finally { if (!disposed && seq === sequence && revision === auth.sessionRevision) busy.value = false }
}
async function submitReview() {
  const item = candidate.value, revision = auth.sessionRevision
  if (!item || saving.value || marker.value || !auth.hasPerm('audition:signup_audit')) return
  const trimmed = reason.value.trim()
  if (action.value === 'reject' && !trimmed) { ElMessage.warning('请填写驳回原因'); return }
  saving.value = true; beginMarker({ id: item.id, name: item.applicant_name || '报名人' })
  try {
    await auditionSignupApi.action(item.id, action.value, action.value === 'reject' ? { audit_remark: trimmed } : {}, true)
    if (disposed || revision !== auth.sessionRevision) return
    clearMarker(); reviewVisible.value = false; ElMessage.success(action.value === 'approve' ? '报名已通过' : '报名已驳回'); tableRef.value?.load()
  } catch (error: any) {
    if (disposed || revision !== auth.sessionRevision) return
    reviewVisible.value = false
    if (error?.response?.status >= 400 && error.response.status < 500 && error.response.status !== 409) { clearMarker(); ElMessage.error(error.response.data?.msg || '审核失败，请检查后重试') }
    else { ElMessage.warning(error?.response?.status === 409 ? '报名已被审核，请读取最新状态' : '审核结果未确认，请读取当前状态'); tableRef.value?.load() }
  } finally { if (!disposed && revision === auth.sessionRevision) saving.value = false }
}
async function checkResult() {
  const item = marker.value
  if (!item || checking.value || saving.value) return
  const revision = auth.sessionRevision, seq = ++sequence
  checking.value = true; checked.value = false; checkText.value = ''
  try { const res = await auditionSignupApi.retrieve(item.id, true); if (!disposed && seq === sequence && revision === auth.sessionRevision && marker.value === item) { checkText.value = `当前：${res.data.status_display}${res.data.audit_remark ? ' · ' + res.data.audit_remark : ''}；这是读取时快照。`; checked.value = true; tableRef.value?.load() } }
  catch (error: any) { if (!disposed && seq === sequence && revision === auth.sessionRevision && marker.value === item) { if (error?.response?.status === 404) { checkText.value = '当前记录不存在；原请求仍可能处理。'; checked.value = true } else checkText.value = '读取失败，请重试。' } }
  finally { if (!disposed && seq === sequence && revision === auth.sessionRevision) checking.value = false }
}
async function clearAfterReview() {
  const item = marker.value, revision = auth.sessionRevision
  if (!item || !checked.value || checking.value) return
  try { await ElMessageBox.confirm('当前状态是读取时快照，原请求仍可能处理。确认已核对并解除限制？', '解除待核对限制', { type: 'warning', confirmButtonText: '已核对，解除', cancelButtonText: '继续核对' }) } catch { return }
  if (!disposed && revision === auth.sessionRevision && marker.value === item && checked.value) clearMarker()
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreMarker, { immediate: true, flush: 'sync' })
watch(() => auth.sessionRevision, () => { ++sequence; busy.value = false; saving.value = false; checking.value = false; reviewVisible.value = false; detailVisible.value = false })
onBeforeUnmount(() => { disposed = true; ++sequence })
</script>

<style scoped lang="scss">
.su-page { display: flex; flex-direction: column; gap: 20px; }
.su-page :deep(.page-container) { padding: 0; }
.su-identity { display: block; color: var(--muted-foreground); overflow-wrap: anywhere; }
.su-list { display: grid; gap: 12px; margin: 0; padding: 0; list-style: none; }
.su-card { padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.su-card-head { display: flex; justify-content: space-between; gap: 12px; }
.su-card strong, .su-card p { overflow-wrap: anywhere; }
.su-card p { margin: 9px 0; color: var(--muted-foreground); font-size: 12px; }
.su-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.su-actions :deep(.el-button + .el-button) { margin-left: 0; }
.su-hint { color: var(--muted-foreground); font-size: 12px; }
</style>
