<template>
  <div class="level-page">
    <p class="ap-page-intro">分范围档位与陪玩等级分别管理；部分商品和手工报单仍会使用陪玩等级。</p>
    <el-card shadow="never" class="scope-card" :class="{ 'scope-card--collapsed': !scopeExpanded && !scopeError }">
      <template #header><div class="scope-header"><strong>接单档位与自动抽成</strong><el-button text size="small" :aria-expanded="scopeExpanded" @click="scopeExpanded = !scopeExpanded">{{ scopeExpanded ? '收起规则' : '查看规则' }}</el-button></div></template>
      <el-alert v-if="scopeError" type="error" :closable="false" show-icon title="档位规则读取失败">
        <el-button link type="primary" :disabled="scopeLoading" @click="loadScopes">重新读取</el-button>
      </el-alert>
      <div v-if="scopeExpanded" v-loading="scopeLoading">
        <el-table v-if="!scopeError" :data="scopedRows" size="small" class="scope-table">
          <el-table-column prop="scope" label="接单范围" min-width="160" />
          <el-table-column prop="grades" label="档位从低到高" min-width="220" />
          <el-table-column prop="rates" label="自动抽成" min-width="250" />
        </el-table>
        <ul v-if="!scopeError" class="scope-mobile" aria-label="接单档位规则">
          <li v-for="row in scopedRows" :key="row.key"><strong>{{ row.scope }}</strong><p>{{ row.grades }}</p><small>{{ row.rates }}</small></li>
        </ul>
      </div>
      <p v-if="scopeExpanded" class="tip">同范围高档可接低档。自动抽成按订单档位；具体订单可能采用其他已配置的抽成规则。</p>
    </el-card>

    <el-alert v-if="marker" type="warning" :closable="false" show-icon title="删除结果未确认">
      <p>请核对等级「{{ marker.name }}」；原请求可能仍在处理中。</p>
      <el-button link type="primary" :disabled="operationBusy || checking" @click="checkDeletion">读取当前状态</el-button>
      <span v-if="checkResult">{{ checkResult }}</span>
      <el-button link type="primary" :disabled="operationBusy || checking || !checked" @click="finishReview">已核对，解除限制</el-button>
    </el-alert>

    <div class="ap-page-head"><div><h2>陪玩等级</h2><p class="ap-page-intro">等级与抽成会被部分业务参考，最终以订单或报单记录为准。</p></div>
      <div class="ap-page-head-actions"><el-button v-if="auth.hasPerm('escort_level:edit')" type="primary" :disabled="operationBusy" @click="onCreate">新增等级</el-button></div>
    </div>
    <crud-table ref="tableRef" :fetcher="escortLevelApi.list" :query="query">
      <template #toolbar="{ reload }"><el-select v-model="query.is_active" placeholder="状态" clearable class="status-filter" @change="reload"><el-option label="启用" value="true" /><el-option label="停用" value="false" /></el-select></template>
      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="name" label="等级名称" min-width="140" />
      <el-table-column label="等级抽成" width="120"><template #default="{ row }">{{ row.commission_rate }}%</template></el-table-column>
      <el-table-column prop="remark" label="备注" min-width="180" show-overflow-tooltip />
      <el-table-column prop="sort_order" label="排序" width="80" />
      <el-table-column label="状态" width="90"><template #default="{ row }"><el-tag :type="row.is_active ? 'success' : 'info'" size="small" effect="light">{{ row.is_active ? '启用' : '停用' }}</el-tag></template></el-table-column>
      <el-table-column label="操作" width="140" fixed="right"><template #default="{ row }">
        <el-button v-if="auth.hasPerm('escort_level:edit')" link type="primary" :disabled="operationBusy" @click="onEdit(row)">编辑</el-button>
        <el-button v-if="auth.hasPerm('escort_level:delete')" link type="danger" :disabled="operationBusy || !!marker" @click="openDelete(row)">删除</el-button>
      </template></el-table-column>
      <template #mobile="{ rows }"><ul class="level-list" aria-label="陪玩等级">
        <li v-for="row in rows" :key="row.id" class="level-item">
          <div class="level-item-head"><strong>{{ row.name }}</strong><el-tag :type="row.is_active ? 'success' : 'info'" size="small">{{ row.is_active ? '启用' : '停用' }}</el-tag></div>
          <p>等级抽成 {{ row.commission_rate }}% · 排序 {{ row.sort_order }} · #{{ row.id }}</p><p v-if="row.remark">{{ row.remark }}</p>
          <div class="level-actions"><el-button v-if="auth.hasPerm('escort_level:edit')" size="small" :disabled="operationBusy" @click="onEdit(row)">编辑</el-button><el-button v-if="auth.hasPerm('escort_level:delete')" size="small" type="danger" plain :disabled="operationBusy || !!marker" @click="openDelete(row)">删除</el-button></div>
        </li>
      </ul></template>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑陪玩等级' : '新增陪玩等级'" width="560px" class="ap-modal" :show-close="!saving" :close-on-click-modal="!saving" :close-on-press-escape="!saving" @closed="onEditClosed">
    <el-alert v-if="saveError" type="warning" :closable="false" :title="saveError" />
    <el-form :model="form" class="ap-modal-form ap-form-grid" label-position="top" :disabled="saving">
      <el-form-item label="等级名称"><el-input v-model="form.name" maxlength="50" placeholder="如：金牌陪玩" /></el-form-item>
      <el-form-item label="等级抽成率（%）"><el-input-number v-model="form.commission_rate" :min="0" :max="100" :precision="0" /><div class="tip">部分业务使用此比例；订单具体抽成以实际规则为准。</div></el-form-item>
      <el-form-item label="备注" class="ap-field--full"><el-input v-model="form.remark" type="textarea" :rows="2" maxlength="255" /></el-form-item>
      <el-form-item label="排序"><el-input-number v-model="form.sort_order" :min="0" :precision="0" /></el-form-item>
      <el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item>
    </el-form>
    <template #footer><el-button :disabled="saving" @click="dialogVisible = false">关闭</el-button><el-button type="primary" :loading="saving" :disabled="saveUnknown" @click="onSave">保存</el-button></template>
  </el-dialog>

  <el-dialog v-model="deleteVisible" title="删除陪玩等级" width="min(480px, 100vw)" class="ap-modal" :show-close="!deleting" :close-on-click-modal="!deleting" :close-on-press-escape="!deleting" @closed="clearDelete">
    <div v-if="impactLoading" v-loading="impactLoading" class="impact-loading">正在读取关联情况</div>
    <el-alert v-else-if="impactError" type="error" :closable="false" show-icon :title="impactError"><el-button link type="primary" @click="readImpact">重新读取</el-button></el-alert>
    <template v-else-if="impact"><p>删除「{{ impact.name }}」？</p><p>当前关联：{{ impact.providers }} 个陪玩档案、{{ impact.services }} 个商品、{{ impact.registrations }} 条注册申请。</p>
      <el-alert type="warning" :closable="false" show-icon title="删除会清空这些关联的等级字段">
        <p>商品最低等级被清空后，未设置分范围资格的商品将不再按该最低等级限制接单。关联数量为读取时快照。</p>
      </el-alert>
    </template>
    <template #footer><el-button :disabled="deleting" @click="deleteVisible = false">取消</el-button><el-button type="danger" :loading="deleting" :disabled="impactLoading || !impact || !!impactError || !!marker" @click="confirmDelete">确认删除</el-button></template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { qualificationOptions } from '@/utils/qualifications'
import { escortLevelApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'

type Impact = { id: number; name: string; providers: number; services: number; registrations: number }
const auth = useAuthStore(), tableRef = ref()
const query = reactive<Record<string, any>>({ is_active: '' })
const scopedMeta = ref<any>({ scopes: [], kinds: [], platforms: [] })
const scopeLoading = ref(false), scopeError = ref(false), scopeExpanded = ref(false)
const scopedRows = computed(() => scopedMeta.value.scopes.map((s: any) => ({
  key: `${s.provider_kind}-${s.platform}-${s.security_level}`,
  scope: [scopedMeta.value.platforms.find((p: any) => p.value === s.platform)?.label, ({ CONFIDENTIAL: '机密', TOP_SECRET: '绝密', ANY: '' } as Record<string, string>)[s.security_level], scopedMeta.value.kinds.find((k: any) => k.value === s.provider_kind)?.label].filter(Boolean).join(' · '),
  grades: s.grades.map((g: any) => g.label).join(' → '), rates: s.grades.map((g: any) => g.label + ' ' + (g.commission_rate == null ? '按项目' : g.commission_rate + '%')).join(' / '),
})))
const dialogVisible = ref(false), saving = ref(false), saveUnknown = ref(false), saveError = ref('')
const form = reactive<any>({})
const deleteVisible = ref(false), deleting = ref(false), impactLoading = ref(false), impactError = ref(''), impact = ref<Impact | null>(null), deleteId = ref(0)
const marker = ref<{ id: number; name: string } | null>(null), checked = ref(false), checking = ref(false), checkResult = ref(''), confirming = ref(false)
const operationBusy = computed(() => saving.value || deleting.value || confirming.value)
let scopeSequence = 0, deleteSequence = 0, saveSequence = 0, confirmSequence = 0, checkSequence = 0, ownerKey = ''
let disposed = false

function markerKey() { const id = auth.profile?.id; return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_level_delete_review_${id}` : '' }
function restoreMarker() {
  const key = markerKey(); if (key === ownerKey) return
  ownerKey = key; marker.value = null; checked.value = false; checkResult.value = ''
  if (key) try { const value = JSON.parse(sessionStorage.getItem(key) || 'null'); if (Number.isSafeInteger(value?.id) && value.id > 0 && typeof value.name === 'string') marker.value = value } catch { /* In-page guard remains. */ }
}
function beginReview(item: { id: number; name: string }) { marker.value = item; checked.value = false; checkResult.value = ''; const key = markerKey(); if (key) try { sessionStorage.setItem(key, JSON.stringify(item)) } catch { /* In-page guard remains. */ } }
function clearReview() { const key = markerKey(); if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }; marker.value = null; checked.value = false; checkResult.value = '' }
async function checkDeletion() {
  const item = marker.value, revision = auth.sessionRevision, seq = ++checkSequence
  if (!item || checking.value || operationBusy.value || !auth.hasPerm('escort_level:view')) return
  checking.value = true; checked.value = false; checkResult.value = ''
  try {
    const result = await escortLevelApi.retrieve(item.id, true)
    if (disposed || seq !== checkSequence || revision !== auth.sessionRevision || marker.value !== item || !auth.hasPerm('escort_level:view')) return
    checked.value = true; checkResult.value = `当前仍存在：${result.data.name}。`
  } catch (error: any) {
    if (disposed || seq !== checkSequence || revision !== auth.sessionRevision || marker.value !== item || !auth.hasPerm('escort_level:view')) return
    if (error?.response?.status === 404 || error?.code === 404) { checked.value = true; checkResult.value = '当前未找到该等级。' }
    else checkResult.value = '读取失败，请重试。'
  } finally { if (seq === checkSequence) checking.value = false }
}
async function finishReview() {
  const item = marker.value, revision = auth.sessionRevision, seq = ++confirmSequence
  if (!item || !checked.value || operationBusy.value || checking.value || !auth.hasPerm('escort_level:delete')) return
  confirming.value = true; let accepted = false
  try { await ElMessageBox.confirm('读取只是当前状态，原请求可能仍在处理中。请确认已核对等级及关联。', '解除删除限制', { type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对', closeOnClickModal: false }); accepted = true }
  catch { /* Keep the marker. */ }
  finally { if (seq === confirmSequence) confirming.value = false }
  if (accepted && !disposed && seq === confirmSequence && revision === auth.sessionRevision && marker.value === item && checked.value && auth.hasPerm('escort_level:delete')) clearReview()
}
async function loadScopes() {
  const seq = ++scopeSequence, revision = auth.sessionRevision
  scopeLoading.value = true; scopeError.value = false
  try { const result = await qualificationOptions(); if (!disposed && seq === scopeSequence && revision === auth.sessionRevision && auth.hasPerm('escort_level:view')) scopedMeta.value = result }
  catch { if (!disposed && seq === scopeSequence && revision === auth.sessionRevision) scopeError.value = true }
  finally { if (seq === scopeSequence) scopeLoading.value = false }
}
function blank() { return { id: 0, name: '', commission_rate: 20, remark: '', sort_order: 0, is_active: true } }
function onCreate() { if (operationBusy.value || !auth.hasPerm('escort_level:edit')) return; Object.assign(form, blank()); saveError.value = ''; saveUnknown.value = false; dialogVisible.value = true }
function onEdit(row: any) { if (operationBusy.value || !auth.hasPerm('escort_level:edit')) return; Object.assign(form, blank(), row); saveError.value = ''; saveUnknown.value = false; dialogVisible.value = true }
function onEditClosed() { if (!dialogVisible.value && saveUnknown.value) { saveUnknown.value = false; void tableRef.value?.load() } }
async function onSave() {
  if (saving.value || saveUnknown.value || !dialogVisible.value || !auth.hasPerm('escort_level:edit')) return
  if (!form.name?.trim()) { ElMessage.warning('请填写等级名称'); return }
  if (!Number.isInteger(form.commission_rate) || form.commission_rate < 0 || form.commission_rate > 100 || !Number.isInteger(form.sort_order) || form.sort_order < 0) { ElMessage.warning('请检查抽成率与排序'); return }
  const seq = ++saveSequence, revision = auth.sessionRevision, id = form.id
  const payload = { name: form.name.trim(), commission_rate: form.commission_rate, remark: form.remark, sort_order: form.sort_order, is_active: form.is_active }
  saving.value = true; saveError.value = ''
  try {
    if (id) await escortLevelApi.update(id, payload, true); else await escortLevelApi.create(payload, true)
    if (disposed || seq !== saveSequence || revision !== auth.sessionRevision || !auth.hasPerm('escort_level:edit')) return
    ElMessage.success('等级已保存'); dialogVisible.value = false; await tableRef.value?.reload()
  } catch (error: any) {
    if (disposed || seq !== saveSequence || revision !== auth.sessionRevision || !auth.hasPerm('escort_level:edit')) return
    const body = error?.response?.data || error, status = error?.response?.status
    const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
    saveUnknown.value = !rejected
    saveError.value = rejected ? `保存未完成：${typeof body?.msg === 'string' && body.msg ? body.msg : '请检查填写内容'}` : '保存结果未确认，请关闭后在列表核对，避免重复提交。'
  } finally { if (seq === saveSequence) saving.value = false }
}
function openDelete(row: any) {
  if (operationBusy.value || marker.value || !auth.hasPerm('escort_level:view') || !auth.hasPerm('escort_level:delete')) return
  deleteId.value = row.id; deleteVisible.value = true; void readImpact()
}
async function readImpact() {
  if (!deleteVisible.value || deleting.value || !auth.hasPerm('escort_level:delete') || !auth.hasPerm('escort_level:view')) return
  const seq = ++deleteSequence, revision = auth.sessionRevision, id = deleteId.value
  impact.value = null; impactError.value = ''; impactLoading.value = true
  try {
    const result = await escortLevelApi.deletionImpact(id)
    if (disposed || seq !== deleteSequence || revision !== auth.sessionRevision || !deleteVisible.value || deleteId.value !== id || !auth.hasPerm('escort_level:delete')) return
    const data = result.data
    if (data.id !== id || typeof data.name !== 'string' || ![data.providers, data.services, data.registrations].every(n => Number.isSafeInteger(n) && n >= 0)) throw new Error('invalid impact')
    impact.value = data
  } catch { if (!disposed && seq === deleteSequence && revision === auth.sessionRevision && deleteVisible.value) impactError.value = '关联情况读取失败，请重新读取后再删除' }
  finally { if (seq === deleteSequence) impactLoading.value = false }
}
function clearDelete() { if (!deleteVisible.value) { ++deleteSequence; impact.value = null; impactError.value = ''; impactLoading.value = false } }
async function confirmDelete() {
  const item = impact.value, seq = deleteSequence, revision = auth.sessionRevision
  if (!item || !deleteVisible.value || deleting.value || impactLoading.value || marker.value || !auth.hasPerm('escort_level:delete') || !auth.hasPerm('escort_level:view')) return
  beginReview({ id: item.id, name: item.name }); deleting.value = true
  try {
    await escortLevelApi.remove(item.id, true)
    if (disposed || seq !== deleteSequence || revision !== auth.sessionRevision || !auth.hasPerm('escort_level:delete')) return
    clearReview(); ElMessage.success('等级已删除'); deleting.value = false; deleteVisible.value = false; void tableRef.value?.load()
  } catch (error: any) {
    if (disposed || seq !== deleteSequence || revision !== auth.sessionRevision || !auth.hasPerm('escort_level:delete')) return
    const body = error?.response?.data || error, status = error?.response?.status
    const rejected = (!status || status < 500) && ((typeof body?.code === 'number' && body.code >= 400 && body.code < 500) || (status >= 400 && status < 500))
    if (rejected) { clearReview(); impact.value = null; impactError.value = `删除未完成：${typeof body?.msg === 'string' && body.msg ? body.msg : '请刷新后核对'}` }
    else { deleting.value = false; deleteVisible.value = false; void tableRef.value?.load() }
  } finally { if (seq === deleteSequence) deleting.value = false }
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreMarker, { immediate: true, flush: 'sync' })
watch([() => auth.sessionRevision, () => auth.hasPerm('escort_level:view'), () => auth.hasPerm('escort_level:edit'), () => auth.hasPerm('escort_level:delete')], () => {
  if (confirming.value) ElMessageBox.close()
  ++scopeSequence; ++deleteSequence; ++saveSequence; ++confirmSequence; ++checkSequence
  dialogVisible.value = deleteVisible.value = false; saving.value = deleting.value = confirming.value = checking.value = false
  scopeLoading.value = impactLoading.value = false; scopeError.value = false; impact.value = null; checked.value = false; checkResult.value = ''
  scopedMeta.value = { scopes: [], kinds: [], platforms: [] }
  if (!disposed && auth.hasPerm('escort_level:view')) void loadScopes()
}, { flush: 'sync' })
onMounted(() => { void loadScopes() })
onBeforeUnmount(() => { disposed = true; ++scopeSequence; ++deleteSequence; ++saveSequence; ++confirmSequence; ++checkSequence; if (confirming.value) ElMessageBox.close() })
</script>

<style scoped lang="scss">
.level-page { display:flex; flex-direction:column; gap:20px; }.level-page :deep(.page-container) { padding:0; }.level-page h2 { margin:0 0 6px; font-size:16px; }
.status-filter { width:150px; }.tip { margin:12px 0 0; color:var(--el-text-color-secondary); font-size:12px; line-height:1.6; }.scope-header { display:flex; align-items:center; justify-content:space-between; gap:12px; }
.scope-card--collapsed :deep(.el-card__body) { display:none; }
.scope-mobile { display:none; list-style:none; padding:0; margin:0; }.scope-mobile li { padding:12px 0; border-bottom:1px solid var(--el-border-color-lighter); }.scope-mobile p { margin:6px 0; overflow-wrap:anywhere; }.scope-mobile small { color:var(--el-text-color-secondary); overflow-wrap:anywhere; }
.level-list { list-style:none; margin:0; padding:0 12px; }.level-item { padding:14px 0; border-bottom:1px solid var(--el-border-color); }.level-item-head { display:flex; align-items:center; justify-content:space-between; gap:12px; }.level-item-head strong { overflow-wrap:anywhere; }.level-item p { margin:8px 0; color:var(--el-text-color-secondary); font-size:12px; overflow-wrap:anywhere; }.level-actions { display:flex; gap:8px; }.level-actions :deep(.el-button) { margin:0; }
.impact-loading { min-height:100px; display:flex; align-items:center; justify-content:center; color:var(--el-text-color-secondary); }
@media(max-width:720px) { .scope-card :deep(.scope-table) { display:none; }.scope-mobile { display:block; } }
</style>
