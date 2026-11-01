<template>
  <div class="sp-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">管理小程序客服名片与企业微信入口。</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('support:edit')" type="primary" :disabled="!!pendingReview || saving" @click="onCreate">新增名片</el-button>
      </div>
    </div>

    <el-alert type="info" :closable="false" show-icon title="启用名片按排序和更新时间决定默认客服；订单优先使用负责客服。">
      <span>参数已配置不代表接待可用，请在小程序绑定企业后用手机验证。</span>
        <el-tooltip content="排序数值越小越靠前；相同排序按最近更新时间优先。原负责客服停用或删除后，订单咨询回退到当前默认客服；没有启用名片时没有客服名片。企业微信工作台处理微信客服，本站消息与客服处理平台内会话。" placement="top" :trigger="['hover', 'focus', 'click']">
        <el-button link type="primary" aria-label="查看客服选择与接待说明">查看说明</el-button>
      </el-tooltip>
    </el-alert>
    <el-alert v-if="pendingReview && !dialogVisible" type="warning" :closable="false" show-icon title="客服名片操作结果待核对">
      <p>{{ pendingReview.action }}「{{ pendingReview.name }}」的请求可能仍在处理；列表只显示当前状态，不能证明原请求最终结果。</p>
      <p v-if="reviewNotice" role="status">{{ reviewNotice }}</p>
      <el-button size="small" :loading="checking" @click="checkReview">{{ pendingReview.id ? '读取目标名片' : '按名称查找' }}</el-button>
      <el-button v-if="reviewChecked" size="small" :disabled="checking" @click="finishReview">已核对，解除限制</el-button>
    </el-alert>
    <crud-table ref="tableRef" :fetcher="supportCardApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input v-model="query.keyword" placeholder="名称 / 所属 / 微信号" aria-label="按名称、所属或微信号搜索" clearable class="sp-search" @keyup.enter="reload" @clear="reload" />
        <el-button type="primary" @click="reload">查询</el-button>
      </template>
      <template #mobile="{ rows }">
        <div class="sp-cards" role="list" aria-label="客服名片">
          <article v-for="row in rows" :key="row.id" class="sp-card" role="listitem">
            <div class="sp-card-head"><div class="sp-person"><el-avatar :size="32" :src="row.avatar_url || undefined">{{ (row.name || '客').charAt(0) }}</el-avatar><div><strong>{{ row.name }}</strong><small>#{{ row.id }}<template v-if="row.company"> · {{ row.company }}</template></small></div></div><el-tag size="small" :type="row.is_active ? 'success' : 'info'">{{ row.is_active ? '启用' : '停用' }}</el-tag></div>
            <dl class="sp-facts"><div><dt>企业微信</dt><dd>{{ row.wecom_corp_id && row.wecom_service_url ? '参数已配置' : '未配置' }}</dd></div><div><dt>排序</dt><dd>{{ row.sort_order }}</dd></div><div><dt>微信号</dt><dd>{{ row.wechat_id || '未填写' }}</dd></div><div><dt>二维码</dt><dd>{{ row.qrcode_url ? '已填写' : '未填写' }}</dd></div></dl>
            <div class="sp-actions"><el-button size="small" @click="openDetail(row.id)">查看配置</el-button><el-button v-if="auth.hasPerm('support:edit')" size="small" :disabled="!!pendingReview || saving" @click="onEdit(row)">编辑</el-button><el-button v-if="auth.hasPerm('support:delete')" size="small" type="danger" text :disabled="!!pendingReview || saving" @click="onDelete(row)">删除</el-button></div>
          </article>
        </div>
      </template>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="头像" width="70">
        <template #default="{ row }">
          <el-avatar :size="36" :src="row.avatar_url || undefined">{{ (row.name || '客').charAt(0) }}</el-avatar>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="名称" min-width="120" />
      <el-table-column prop="company" label="所属" min-width="120" />
      <el-table-column prop="wechat_id" label="微信号" min-width="130" />
      <el-table-column label="企业微信客服" min-width="130">
        <template #default="{ row }">
          <el-tag :type="row.wecom_corp_id && row.wecom_service_url ? 'success' : 'info'" effect="light" round>
            {{ row.wecom_corp_id && row.wecom_service_url ? '参数已配置' : '未配置' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="二维码" width="80">
        <template #default="{ row }">
          <el-image v-if="row.qrcode_url" :src="row.qrcode_url" fit="cover" style="width: 40px; height: 40px; border-radius: 4px" :preview-src-list="[row.qrcode_url]" />
          <span v-else>-</span>
        </template>
      </el-table-column>
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_active ? 'success' : 'info'" effect="light" round>{{ row.is_active ? '启用' : '停用' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="170" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openDetail(row.id)">查看配置</el-button>
          <el-button v-if="auth.hasPerm('support:edit')" link type="primary" :disabled="!!pendingReview || saving" @click="onEdit(row)">编辑</el-button>
          <el-button v-if="auth.hasPerm('support:delete')" link type="danger" :disabled="!!pendingReview || saving" @click="onDelete(row)">删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="客服名片配置" size="min(520px, 100vw)" class="ap-panel" @closed="closeDetail">
    <div v-if="detailLoading" v-loading="detailLoading" class="sp-detail-loading">正在读取名片…</div>
    <el-alert v-else-if="detailError" :title="detailError" type="warning" :closable="false" show-icon>
      <el-button size="small" @click="openDetail(detailId)">重新读取</el-button>
    </el-alert>
    <template v-else-if="detail">
      <el-alert v-if="pendingReview" title="这是当前读取的配置，原请求可能仍在处理。" type="warning" :closable="false" show-icon />
      <el-descriptions class="sp-detail" :column="1" border>
        <el-descriptions-item label="名称">{{ detail.name }} · #{{ detail.id }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ detail.is_active ? '启用' : '停用' }} · 排序 {{ detail.sort_order }}</el-descriptions-item>
        <el-descriptions-item label="所属">{{ detail.company || '未填写' }}</el-descriptions-item>
        <el-descriptions-item label="微信号">{{ detail.wechat_id || '未填写' }}</el-descriptions-item>
        <el-descriptions-item label="企业 ID"><span class="sp-detail-value">{{ detail.wecom_corp_id || '未填写' }}</span></el-descriptions-item>
        <el-descriptions-item label="客服链接"><span class="sp-detail-value">{{ detail.wecom_service_url || '未填写' }}</span></el-descriptions-item>
        <el-descriptions-item label="头像 URL"><span class="sp-detail-value">{{ detail.avatar_url || '未填写' }}</span></el-descriptions-item>
        <el-descriptions-item label="二维码 URL"><span class="sp-detail-value">{{ detail.qrcode_url || '未填写' }}</span></el-descriptions-item>
        <el-descriptions-item label="提示语"><span class="sp-detail-value">{{ detail.tips || '未填写' }}</span></el-descriptions-item>
        <el-descriptions-item label="更新时间">{{ formatDateTime(detail.updated_at) }}</el-descriptions-item>
      </el-descriptions>
    </template>
  </el-drawer>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑客服名片' : '新增客服名片'" width="720px" class="ap-modal" :before-close="beforeClose" :close-on-click-modal="false" :show-close="!saving">
    <el-form :model="form" :disabled="saving || !!pendingReview" class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="名称"><el-input v-model="form.name" maxlength="50" /></el-form-item>
      <el-form-item label="所属"><el-input v-model="form.company" /></el-form-item>
      <el-form-item label="微信号" class="ap-field--full"><el-input v-model="form.wechat_id" /></el-form-item>
      <el-divider content-position="left">企业微信客服</el-divider>
      <el-form-item label="企业ID" class="ap-field--full">
        <el-input v-model="form.wecom_corp_id" placeholder="例如：wwxxxxxxxxxxxxxxxx" />
      </el-form-item>
      <el-form-item label="客服链接" class="ap-field--full">
        <el-input v-model="form.wecom_service_url" placeholder="https://work.weixin.qq.com/kfid/..." />
      </el-form-item>
      <el-alert class="wecom-tip" type="info" :closable="false" show-icon title="企业 ID 与客服链接须同时填写或留空；小程序也需绑定该企业。" />
      <el-form-item label="头像URL"><el-input v-model="form.avatar_url" /></el-form-item>
      <el-form-item label="二维码URL"><el-input v-model="form.qrcode_url" /></el-form-item>
      <el-form-item label="提示语" class="ap-field--full"><el-input v-model="form.tips" type="textarea" :rows="2" /></el-form-item>
      <el-form-item label="排序"><el-input-number v-model="form.sort_order" :min="0" :precision="0" /></el-form-item>
      <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="saving" @click="requestClose">{{ pendingReview ? '关闭并核对' : '取消' }}</el-button>
      <el-button type="primary" :loading="saving" :disabled="!!pendingReview" @click="onSave">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { supportCardApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '' })

const dialogVisible = ref(false)
const saving = ref(false)
const form = reactive<any>({})
const initialForm = ref('')
const pendingReview = ref<{ action: string; name: string; id?: number } | null>(null)
const reviewChecked = ref(false)
const checking = ref(false)
const reviewNotice = ref('')
const detailVisible = ref(false)
const detailLoading = ref(false)
const detailError = ref('')
const detail = ref<any>(null)
const detailId = ref(0)
let detailSequence = 0
let reviewOwner = ''
let disposed = false

function blank() {
  return { id: 0, name: '', company: '', wechat_id: '', wecom_corp_id: '', wecom_service_url: '', avatar_url: '', qrcode_url: '', tips: '', sort_order: 0, is_active: true }
}
function payload() {
  return { name: form.name, company: form.company, wechat_id: form.wechat_id, wecom_corp_id: form.wecom_corp_id,
    wecom_service_url: form.wecom_service_url, avatar_url: form.avatar_url, qrcode_url: form.qrcode_url,
    tips: form.tips, sort_order: form.sort_order, is_active: form.is_active }
}
const dirty = computed(() => dialogVisible.value && JSON.stringify(payload()) !== initialForm.value)
function reviewKey() { const id = auth.profile?.id; return typeof id === 'number' && id > 0 ? `xa_support_review_${id}` : '' }
function restoreReview() {
  const key = reviewKey()
  if (key === reviewOwner) return
  reviewOwner = key; pendingReview.value = null; reviewChecked.value = false; reviewNotice.value = ''
  if (!key) return
  try { const value = JSON.parse(sessionStorage.getItem(key) || 'null'); if (typeof value?.action === 'string' && typeof value?.name === 'string') pendingReview.value = value } catch { /* in-page guard */ }
}
function markReview(action: string, name: string, id?: number) {
  pendingReview.value = { action, name, id }; reviewChecked.value = false; reviewNotice.value = ''
  const key = reviewKey(); if (key) try { sessionStorage.setItem(key, JSON.stringify(pendingReview.value)) } catch { /* in-page guard */ }
}
function clearReview() {
  const key = reviewKey(); if (key) try { sessionStorage.removeItem(key) } catch { /* storage unavailable */ }
  pendingReview.value = null; reviewChecked.value = false; reviewNotice.value = ''
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreReview, { immediate: true, flush: 'sync' })
watch(() => auth.sessionRevision, () => { dialogVisible.value = false; detailVisible.value = false; detail.value = null; detailSequence++; saving.value = false }, { flush: 'sync' })
onBeforeUnmount(() => { disposed = true; detailSequence++ })
function closeDetail() { detailSequence++; detail.value = null; detailError.value = '' }
async function openDetail(id: number): Promise<'found' | 'missing' | 'failed'> {
  if (!Number.isSafeInteger(id) || id <= 0) return 'failed'
  const sequence = ++detailSequence, revision = auth.sessionRevision
  detailId.value = id; detail.value = null; detailError.value = ''; detailLoading.value = true; detailVisible.value = true
  try {
    const result = await supportCardApi.retrieve(id, true)
    if (disposed || revision !== auth.sessionRevision || sequence !== detailSequence) return 'failed'
    detail.value = result.data
    if (pendingReview.value?.id === id) { reviewChecked.value = true; reviewNotice.value = '已读取目标配置，请核对详情；原请求仍可能在处理。' }
    return 'found'
  } catch (error: any) {
    if (disposed || revision !== auth.sessionRevision || sequence !== detailSequence) return 'failed'
    const missing = error?.response?.status === 404
    detailError.value = missing ? '该名片当前不存在。原请求可能仍在处理。' : '读取失败，请检查网络或权限后重试。'
    if (pendingReview.value?.id === id) {
      reviewChecked.value = missing
      reviewNotice.value = missing ? '目标名片当前不存在；不能仅凭此判断原请求结果。' : '读取失败，请重试。'
    }
    return missing ? 'missing' : 'failed'
  } finally { if (sequence === detailSequence && revision === auth.sessionRevision) detailLoading.value = false }
}
async function checkReview() {
  if (checking.value || !pendingReview.value) return
  const marker = pendingReview.value, revision = auth.sessionRevision
  checking.value = true; reviewChecked.value = false; reviewNotice.value = ''
  try {
    if (marker.id) {
      const outcome = await openDetail(marker.id)
      if (disposed || revision !== auth.sessionRevision || marker !== pendingReview.value) return
      if (outcome === 'failed') { reviewNotice.value = '读取失败，请重试。'; return }
      reviewChecked.value = true
      reviewNotice.value = outcome === 'found' ? '已读取目标配置，请核对详情；原请求仍可能在处理。' : '目标名片当前不存在；不能仅凭此判断原请求结果。'
      tableRef.value?.reload()
      return
    }
    query.keyword = marker.name.trim()
    const result = await supportCardApi.list({ ...query, page: 1, page_size: 10 })
    if (disposed || revision !== auth.sessionRevision || marker !== pendingReview.value) return
    tableRef.value?.reload(); reviewChecked.value = true
    reviewNotice.value = `按名称读取到 ${result.data.total} 条当前匹配记录；可能存在同名，请逐条查看配置核对。原请求仍可能在处理。`
  } catch { if (revision === auth.sessionRevision) reviewNotice.value = '读取失败，请检查网络或权限后重试。' }
  finally { if (revision === auth.sessionRevision) checking.value = false }
}
async function finishReview() {
  const marker = pendingReview.value, revision = auth.sessionRevision
  if (!marker || !reviewChecked.value || checking.value) return
  try { await ElMessageBox.confirm('请确认已核对名片；原请求仍可能在处理。解除后可再次操作。', '解除操作限制', { type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对' }) } catch { return }
  if (!disposed && revision === auth.sessionRevision && marker === pendingReview.value) clearReview()
}
async function canClose() {
  if (saving.value) return false
  if (!dirty.value || pendingReview.value) return true
  try { await ElMessageBox.confirm('名片修改尚未保存，确定放弃？', '放弃修改', { type: 'warning', confirmButtonText: '放弃修改', cancelButtonText: '继续编辑' }); return true } catch { return false }
}
async function beforeClose(done: () => void) { if (await canClose()) done() }
async function requestClose() { if (await canClose()) dialogVisible.value = false }
onBeforeRouteLeave(async () => !dialogVisible.value || await canClose())

function onCreate() {
  if (saving.value || pendingReview.value) return
  Object.assign(form, blank())
  initialForm.value = JSON.stringify(payload())
  dialogVisible.value = true
}

function onEdit(row: any) {
  if (saving.value || pendingReview.value) return
  Object.assign(form, blank(), row)
  initialForm.value = JSON.stringify(payload())
  dialogVisible.value = true
}

async function onSave() {
  if (saving.value || pendingReview.value) return
  if (!form.name.trim()) { ElMessage.warning('请填写名称'); return }
  if (!!form.wecom_corp_id.trim() !== !!form.wecom_service_url.trim()) { ElMessage.warning('企业 ID 和客服链接须同时填写或留空'); return }
  const revision = auth.sessionRevision, data = payload(), id = form.id
  saving.value = true
  markReview(id ? '保存' : '新增', data.name.trim(), id || undefined)
  try {
    if (id) {
      await supportCardApi.update(id, data)
    } else {
      await supportCardApi.create(data)
    }
    if (disposed || revision !== auth.sessionRevision) return
    clearReview()
    ElMessage.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } catch (error: any) {
    if (disposed || revision !== auth.sessionRevision) return
    const status = error?.response?.status ?? (typeof error?.code === 'number' ? error.code : undefined)
    if (status && status >= 400 && status < 500 && status !== 408 && status !== 429) clearReview()
    else ElMessage.warning('保存结果未确认，请关闭后核对当前列表。')
  } finally {
    if (revision === auth.sessionRevision) saving.value = false
  }
}

async function onDelete(row: any) {
  if (saving.value || pendingReview.value) return
  const revision = auth.sessionRevision
  saving.value = true
  try {
    try { await ElMessageBox.confirm(`删除「${row.name}」（#${row.id}）后，关联订单将解除该客服；订单咨询回退到当前默认客服，没有启用名片时无法提供名片。确定删除？`, '删除客服名片', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }) } catch { return }
    if (disposed || revision !== auth.sessionRevision) return
    markReview('删除', `${row.name} #${row.id}`, row.id)
    try {
      await supportCardApi.remove(row.id)
      if (disposed || revision !== auth.sessionRevision) return
      clearReview(); ElMessage.success('已删除'); tableRef.value.reload()
    } catch (error: any) {
      if (disposed || revision !== auth.sessionRevision) return
      const status = error?.response?.status ?? (typeof error?.code === 'number' ? error.code : undefined)
      if (status && status >= 400 && status < 500 && status !== 408 && status !== 429) clearReview()
      else ElMessage.warning('删除结果未确认，请核对当前列表。')
    }
  } finally { if (revision === auth.sessionRevision) saving.value = false }
}
</script>

<style scoped lang="scss">
.sp-page {
  display: flex;
  flex-direction: column;
  gap: 20px;

  :deep(.page-container) {
    padding: 0;
  }
}

.wecom-tip {
  margin: -4px 0 16px;
}
.sp-cards { display: grid; gap: 12px; }
.sp-card { min-width: 0; padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.sp-card-head, .sp-person { display: flex; align-items: center; gap: 10px; min-width: 0; }
.sp-card-head { justify-content: space-between; }
.sp-person > div { display: grid; min-width: 0; gap: 3px; }
.sp-person strong, .sp-person small { overflow-wrap: anywhere; }
.sp-person small, .sp-facts dt { color: var(--muted-foreground); }
.sp-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px; margin: 12px 0 0; padding-top: 12px; border-top: 1px solid var(--border); font-size: 12px; }
.sp-facts div { display: flex; gap: 6px; min-width: 0; }
.sp-facts dt { flex: none; }.sp-facts dd { min-width: 0; margin: 0; overflow-wrap: anywhere; }
.sp-actions { display: flex; gap: 8px; margin-top: 12px; }.sp-actions :deep(.el-button) { margin: 0; }
.sp-search { width: 220px; }
.sp-detail-loading { min-height: 160px; display: flex; align-items: center; justify-content: center; color: var(--muted-foreground); }
.sp-detail { margin-top: 16px; }
.sp-detail-value { overflow-wrap: anywhere; white-space: pre-wrap; }
@media (max-width: 600px) { .sp-search { width: min(100%, 220px); } }
</style>
