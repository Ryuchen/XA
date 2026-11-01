<template>
  <div class="au-page">
    <el-alert v-if="marker" :title="`${roleLabel(marker.role)}入口绑定结果待核对`" type="warning" :closable="false" show-icon><p>「{{ marker.title }}」可能已更换绑定；原请求可能仍在处理。</p><el-button link type="primary" :loading="checking" @click="checkBinding">读取当前绑定</el-button><span v-if="checkText">{{ checkText }}</span><el-button link type="primary" :disabled="!checked || checking" @click="clearAfterReview">已核对，解除限制</el-button></el-alert>
    <div class="ap-page-head"><p class="ap-page-intro">管理试音入口；绑定账号的登录链接属于凭证，请仅交给本人。</p><div class="ap-page-head-actions"><el-button v-if="auth.hasPerm('audition:edit')" type="primary" :disabled="saving || !!marker" @click="onCreate">新增试音链接</el-button></div></div>
    <crud-table ref="tableRef" :fetcher="auditionApi.list" :query="query">
      <template #toolbar="{ reload }"><el-input v-model="query.keyword" placeholder="标题/备注" aria-label="搜索试音链接" clearable style="width: 200px" @keyup.enter="reload" @clear="reload" /><el-select v-model="query.is_active" placeholder="启用状态" aria-label="启用状态" clearable style="width: 120px" @change="reload"><el-option label="启用" :value="1" /><el-option label="停用" :value="0" /></el-select><el-button type="primary" @click="reload">查询</el-button></template>
      <el-table-column prop="id" label="ID" width="70" /><el-table-column prop="title" label="标题" min-width="130" /><el-table-column label="入口绑定" min-width="200"><template #default="{ row }"><div>老板 · {{ bindingLabel(row.boss_binding) }} {{ bossDetail(row.boss_binding) }}</div><div>陪玩 · {{ providerLabel(row.provider_binding) }} {{ bossDetail(row.provider_binding) }}<small v-if="row.provider_binding?.otp_required" class="au-warning">需验证器登录</small></div></template></el-table-column>
      <el-table-column label="链接" width="155"><template #default="{ row }"><el-button v-if="auth.hasPerm('audition:bind_boss')" link type="primary" @click="copyLink(row, 'boss')">老板链接</el-button><el-button v-if="canSeeProvider(row)" link type="primary" @click="copyLink(row, 'provider')">陪玩链接</el-button></template></el-table-column>
      <el-table-column label="截止时间" min-width="145"><template #default="{ row }">{{ row.expire_at ? formatDateTime(row.expire_at) : '长期有效' }}</template></el-table-column><el-table-column label="入口状态" width="100"><template #default="{ row }"><el-tag :type="entryType(row.entry_status)" size="small">{{ entryLabel(row.entry_status) }}</el-tag></template></el-table-column>
      <el-table-column label="操作" width="230" fixed="right"><template #default="{ row }"><el-button v-if="auth.hasPerm('audition:bind_boss')" link type="primary" :disabled="busy || !!marker" @click="openBinding(row, 'boss')">老板绑定</el-button><el-button v-if="auth.hasPerm('audition:bind_provider')" link type="primary" :disabled="busy || !!marker" @click="openBinding(row, 'provider')">陪玩绑定</el-button><el-button v-if="auth.hasPerm('audition:edit')" link type="primary" :disabled="saving || !!marker" @click="onEdit(row)">编辑</el-button><el-button v-if="auth.hasPerm('audition:delete')" link type="danger" :disabled="saving || !!marker" @click="onDelete(row)">删除</el-button></template></el-table-column>
      <template #mobile="{ rows }"><ul class="au-list" aria-label="试音链接列表"><li v-for="row in rows" :key="row.id" class="au-card"><div class="au-card-head"><strong>{{ row.title }}</strong><el-tag :type="entryType(row.entry_status)" size="small">{{ entryLabel(row.entry_status) }}</el-tag></div><p>老板入口 · {{ bindingLabel(row.boss_binding) }} {{ bossDetail(row.boss_binding) }}</p><p>陪玩入口 · {{ providerLabel(row.provider_binding) }} {{ bossDetail(row.provider_binding) }}</p><p v-if="row.provider_binding?.otp_required" class="au-warning">陪玩需使用账号和验证器登录，链接不能直接登录。</p><p>截止 {{ row.expire_at ? formatDateTime(row.expire_at) : '长期有效' }}</p><p v-if="row.boss_url_placeholder || row.provider_url_placeholder" class="au-warning">入口域名仍是占位配置，需先配置真实地址。</p><div class="au-actions"><el-button v-if="auth.hasPerm('audition:bind_boss')" size="small" @click="copyLink(row, 'boss')">老板链接</el-button><el-button v-if="canSeeProvider(row)" size="small" @click="copyLink(row, 'provider')">陪玩链接</el-button><el-button v-if="auth.hasPerm('audition:bind_boss')" size="small" :disabled="busy || !!marker" @click="openBinding(row, 'boss')">老板绑定</el-button><el-button v-if="auth.hasPerm('audition:bind_provider')" size="small" :disabled="busy || !!marker" @click="openBinding(row, 'provider')">陪玩绑定</el-button><el-button v-if="auth.hasPerm('audition:edit')" size="small" :disabled="saving || !!marker" @click="onEdit(row)">编辑</el-button><el-button v-if="auth.hasPerm('audition:delete')" size="small" type="danger" plain :disabled="saving || !!marker" @click="onDelete(row)">删除</el-button></div></li></ul></template>
    </crud-table>
  </div>
  <el-dialog v-model="dialogVisible" :title="form.id ? '编辑试音链接' : '新增试音链接'" width="min(680px, calc(100vw - 24px))" class="ap-modal" :before-close="beforeEditClose" :close-on-click-modal="false" :close-on-press-escape="!saving">
    <el-alert v-if="saveUncertain" title="保存结果未确认" type="warning" :closable="false">请关闭后刷新列表核对，原请求可能仍在处理，避免重复新建。</el-alert>
    <el-form :model="form" :disabled="saving" class="ap-modal-form ap-form-grid" label-position="top"><el-form-item label="标题"><el-input v-model="form.title" maxlength="100" placeholder="如：试音招募" /></el-form-item><el-form-item label="备注" class="ap-field--full"><el-input v-model="form.remark" maxlength="255" type="textarea" :rows="2" /></el-form-item><el-form-item label="截止时间"><el-date-picker v-model="form.expire_at" type="datetime" placeholder="留空为长期有效" value-format="YYYY-MM-DDTHH:mm:ss" style="width: 100%" /></el-form-item><el-form-item label="启用"><el-switch v-model="form.is_active" /></el-form-item></el-form>
    <p class="au-hint">新建后可管理老板入口；未绑定时老板链接不能用于登录。陪玩可使用陪玩账号登录后报名。</p><template #footer><el-button :disabled="saving" @click="dialogVisible = false">取消</el-button><el-button type="primary" :loading="saving" :disabled="saveUncertain" @click="onSave">保存</el-button></template>
  </el-dialog>
  <el-dialog v-model="bindingVisible" :title="`管理${roleLabel(bindingRole)}入口`" width="min(520px, calc(100vw - 24px))" class="ap-modal" :before-close="beforeBindingClose" :close-on-click-modal="false" :close-on-press-escape="!bindingSaving">
    <template v-if="bindingRow"><p><strong>试音：</strong>{{ bindingRow.title }}</p><p><strong>当前绑定：</strong>{{ bindingRole === 'provider' ? providerLabel(roleBinding(bindingRow, bindingRole)) : bindingLabel(roleBinding(bindingRow, bindingRole)) }} {{ bossDetail(roleBinding(bindingRow, bindingRole)) }}</p><p><strong>截止：</strong>{{ bindingRow.expire_at ? formatDateTime(bindingRow.expire_at) : '长期有效' }}</p><el-alert type="warning" :closable="false" show-icon :title="`持有${roleLabel(bindingRole)}专属链接的人可能登录绑定账号`">只交给账号本人；变更会使旧{{ roleLabel(bindingRole) }}链接失效，已签发的登录态不会自动撤销，另一端链接不变。</el-alert>
      <p v-if="bindingRole === 'provider'" class="au-hint">绑定不会限制其他已登录陪玩报名；原有报名记录保留。若启用陪玩验证器，需先登录后使用。</p>
      <el-form label-position="top" :disabled="bindingSaving"><el-form-item :label="`选择${roleLabel(bindingRole)}账号`"><el-select v-model="selectedBoss" filterable remote clearable :remote-method="searchBosses" :loading="searchLoading" placeholder="搜索编号、账号或昵称" style="width: 100%"><el-option v-for="item in bossOptions" :key="item.id" :value="item.id" :label="`${item.nickname || item.username} · ${item.account_no} · ${item.username}`" /></el-select><span v-if="searchError" class="au-warning">账号读取失败 <el-button link type="primary" @click="searchBosses(lastKeyword)">重试</el-button></span></el-form-item></el-form>
      <p class="au-hint">账号状态、链接有效期和部署地址会影响实际使用；状态以再次读取时为准。</p>
    </template><template #footer><el-button :disabled="bindingSaving" @click="bindingVisible = false">取消</el-button><el-button v-if="bindingRow && roleBinding(bindingRow, bindingRole)?.kind !== 'unbound'" :disabled="bindingSaving" @click="confirmBinding(null)">解除绑定</el-button><el-button type="primary" :loading="bindingSaving" :disabled="!selectedBoss" @click="confirmBinding(selectedBoss)">绑定账号</el-button></template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref, watch, onBeforeUnmount } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { auditionApi } from '@/api/modules'
import { request } from '@/api/request'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore(), tableRef = ref(), query = reactive<Record<string, any>>({ keyword: '', is_active: '' })
const dialogVisible = ref(false), saving = ref(false), saveUncertain = ref(false), form = reactive<any>({})
const bindingVisible = ref(false), bindingRow = ref<any>(null), selectedBoss = ref<number | null>(null), bossOptions = ref<any[]>([])
type EntryRole = 'boss' | 'provider'
const bindingRole = ref<EntryRole>('boss')
const busy = ref(false), bindingSaving = ref(false), searchLoading = ref(false), searchError = ref(false), lastKeyword = ref('')
const marker = ref<{ id: number; title: string; role: EntryRole } | null>(null), checked = ref(false), checking = ref(false), checkText = ref('')
let ownerKey = '', sequence = 0, searchSequence = 0, disposed = false
function roleLabel(role: EntryRole) { return role === 'boss' ? '老板' : '陪玩' }
function roleBinding(row: any, role: EntryRole) { return role === 'boss' ? row?.boss_binding : row?.provider_binding }
function rolePermission(role: EntryRole) { return role === 'boss' ? 'audition:bind_boss' : 'audition:bind_provider' }
function bindingLabel(b: any) { return !b ? '状态待更新' : b.kind === 'unbound' ? '未绑定' : b.kind === 'legacy' ? '旧账号绑定' : b.available ? '已绑定' : '账号不可用' }
function providerLabel(b: any) { return !b ? '状态待更新' : b.kind === 'unbound' ? '普通报名' : b.kind === 'legacy' ? '旧账号专属' : b.available ? '账号专属' : '绑定账号不可用' }
function canSeeProvider(row: any) { return !!row.provider_binding && (row.provider_binding.kind === 'unbound' || auth.hasPerm('audition:bind_provider')) }
function bossDetail(b: any) { return b?.kind === 'account' ? `${b.name} · ${b.account_no} · ${b.username}` : b?.kind === 'legacy' ? `${b.name} · ${b.username}（使用时校验）` : '' }
function entryLabel(status: string) { return ({ ACTIVE: '启用', DISABLED: '停用', EXPIRED: '已过期' } as Record<string, string>)[status] || '状态待更新' }
function entryType(status: string) { return status === 'ACTIVE' ? 'success' : status === 'EXPIRED' ? 'warning' : 'info' }
function markerKey() { const id = auth.profile?.id; return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_audition_binding_review_${id}` : '' }
function restoreMarker() { const key = markerKey(); if (key === ownerKey) return; ownerKey = key; marker.value = null; checked.value = false; checkText.value = ''; if (key) try { const item = JSON.parse(sessionStorage.getItem(key) || 'null'); if (Number.isSafeInteger(item?.id) && item.id > 0 && typeof item.title === 'string') marker.value = { ...item, role: item.role === 'provider' ? 'provider' : 'boss' } } catch { /* In-page guard remains. */ } }
function beginMarker(item: { id: number; title: string; role: EntryRole }) { marker.value = item; checked.value = false; checkText.value = ''; const key = markerKey(); if (key) try { sessionStorage.setItem(key, JSON.stringify(item)) } catch { /* In-page guard remains. */ } }
function clearMarker() { const key = markerKey(); if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }; marker.value = null; checked.value = false; checkText.value = '' }
function beforeEditClose(done: () => void) { if (!saving.value) done() }
function beforeBindingClose(done: () => void) { if (!bindingSaving.value) done() }
function onCreate() { Object.assign(form, { id: 0, title: '', remark: '', expire_at: null, is_active: true }); saveUncertain.value = false; dialogVisible.value = true }
function onEdit(row: any) { Object.assign(form, { id: row.id, title: row.title, remark: row.remark, expire_at: row.expire_at, is_active: row.is_active }); saveUncertain.value = false; dialogVisible.value = true }
async function copyLink(row: any, role: 'boss' | 'provider') {
  if (marker.value) { ElMessage.warning('请先核对上次绑定结果'); return }
  const revision = auth.sessionRevision
  let current: any
  try { current = (await auditionApi.retrieve(row.id, true)).data }
  catch { if (revision === auth.sessionRevision) ElMessage.error('入口读取失败，请重试'); return }
  if (disposed || revision !== auth.sessionRevision || marker.value || !auth.hasPerm('audition:view') || (role === 'boss' && !auth.hasPerm('audition:bind_boss'))) return
  if (current.entry_status !== 'ACTIVE') { ElMessage.warning('入口未启用、已过期或状态待更新，请先核对'); return }
  if (role === 'boss' && (!current.boss_binding || current.boss_binding.kind === 'unbound' || current.boss_binding.available === false)) { ElMessage.warning('老板入口未绑定或账号不可用，请先核对'); return }
  if (role === 'provider' && !current.provider_binding) { ElMessage.warning('服务端版本不支持核对陪玩入口，请更新后重试'); return }
  if (role === 'provider' && current.provider_binding.kind !== 'unbound' && !auth.hasPerm('audition:bind_provider')) { ElMessage.warning('无权读取陪玩专属链接'); return }
  if ((role === 'boss' ? current.boss_url_placeholder : current.provider_url_placeholder) !== false) { ElMessage.warning('入口地址未配置或服务端版本不支持，请先核对部署配置'); return }
  if (typeof (role === 'boss' ? current.boss_url : current.provider_url) !== 'string' || !(role === 'boss' ? current.boss_url : current.provider_url)) { ElMessage.warning('链接不可读取，请核对权限'); return }
  if (role === 'provider' && current.provider_binding.kind !== 'unbound') {
    try { await ElMessageBox.confirm(`该链接可能登录陪玩账号「${current.provider_binding.name}」；其他已登录陪玩也可报名。${current.provider_binding.otp_required ? '当前需使用账号和验证器登录。' : '请仅交给账号本人。'}`, '复制专属链接', { type: 'warning', confirmButtonText: '确认复制', cancelButtonText: '取消' }) } catch { return }
    if (disposed || revision !== auth.sessionRevision || marker.value || !auth.hasPerm('audition:bind_provider')) return
  }
  try { await navigator.clipboard.writeText(role === 'boss' ? current.boss_url : current.provider_url); ElMessage.success('已复制，请仅交给预定使用人') }
  catch { ElMessage.error('复制失败，请检查浏览器剪贴板权限') }
}
async function onSave() {
  if (saving.value || saveUncertain.value) return
  if (!form.title?.trim()) { ElMessage.warning('请填写标题'); return }
  const revision = auth.sessionRevision; saving.value = true
  try { const payload = { title: form.title.trim(), remark: form.remark, expire_at: form.expire_at || null, is_active: form.is_active }; if (form.id) await auditionApi.update(form.id, payload, true); else await auditionApi.create(payload, true); if (revision !== auth.sessionRevision || disposed) return; ElMessage.success('保存成功'); dialogVisible.value = false; tableRef.value?.reload() }
  catch (error: any) { if (revision === auth.sessionRevision && !disposed) { if (!error?.response || error.response.status >= 500) saveUncertain.value = true; else ElMessage.error(error.response.data?.msg || '保存失败，请检查后重试') } }
  finally { if (revision === auth.sessionRevision && !disposed) saving.value = false }
}
async function onDelete(row: any) { try { await ElMessageBox.confirm(`删除「${row.title}」将使老板及陪玩链接失效；已有报名时不能删除，请停用以保留记录。已签发的登录态不会自动撤销。`, '删除试音链接', { type: 'warning', confirmButtonText: '确认删除', cancelButtonText: '取消' }) } catch { return }; try { await auditionApi.remove(row.id, true); ElMessage.success('已删除'); tableRef.value?.reload() } catch (error: any) { if (!error?.response || error.response.status >= 500) ElMessage.warning('删除结果未确认，请刷新列表核对'); else ElMessage.error(error.response.data?.msg || '删除失败，请核对是否已有报名') } }
async function openBinding(row: any, role: EntryRole) {
  if (busy.value || marker.value || !auth.hasPerm(rolePermission(role))) return
  const seq = ++sequence, revision = auth.sessionRevision; busy.value = true
  try { const res = await auditionApi.retrieve(row.id, true); if (disposed || seq !== sequence || revision !== auth.sessionRevision || !auth.hasPerm(rolePermission(role))) return; if (!roleBinding(res.data, role) || !res.data.updated_at) { ElMessage.error('服务端版本不支持入口绑定，请更新后重试'); return }; bindingRole.value = role; bindingRow.value = res.data; selectedBoss.value = null; bossOptions.value = []; searchError.value = false; bindingVisible.value = true }
  catch { if (!disposed && seq === sequence && revision === auth.sessionRevision) ElMessage.error('链接读取失败，请刷新后重试') }
  finally { if (!disposed && seq === sequence && revision === auth.sessionRevision) busy.value = false }
}
async function searchBosses(keyword: string) {
  const seq = ++searchSequence, revision = auth.sessionRevision, role = bindingRole.value; lastKeyword.value = keyword; searchError.value = false
  if (!keyword.trim()) { bossOptions.value = bossOptions.value.filter(item => item.id === selectedBoss.value); searchLoading.value = false; return }
  searchLoading.value = true
  try { const res = await request({ url: role === 'boss' ? '/options/audition-bosses/' : '/options/audition-providers/', params: { keyword, page_size: 30 }, silent: true }); if (!disposed && seq === searchSequence && revision === auth.sessionRevision && bindingVisible.value && bindingRole.value === role) { const selected = bossOptions.value.filter(item => item.id === selectedBoss.value); bossOptions.value = [...new Map([...selected, ...res.data.list].map(item => [item.id, item])).values()] } }
  catch { if (!disposed && seq === searchSequence && revision === auth.sessionRevision && bindingVisible.value) searchError.value = true }
  finally { if (!disposed && seq === searchSequence && revision === auth.sessionRevision) searchLoading.value = false }
}
async function confirmBinding(id: number | null) {
  const row = bindingRow.value, revision = auth.sessionRevision, role = bindingRole.value, chosen = id === null ? null : bossOptions.value.find(item => item.id === id)
  if (!row || bindingSaving.value || marker.value || !auth.hasPerm(rolePermission(role)) || (id !== null && !chosen)) return
  if (id !== null && roleBinding(row, role)?.kind === 'account' && roleBinding(row, role).id === id) { ElMessage.info('该账号已绑定，无需重复操作'); return }
  const target = chosen ? `${chosen.nickname || chosen.username} · ${chosen.account_no} · ${chosen.username}` : '解除绑定'
  const impact = role === 'boss' ? '陪玩链接不变' : '老板链接与已有报名记录不变；其他已登录陪玩仍可报名'
  try { await ElMessageBox.confirm(`试音「${row.title}」：${target}。变更后旧${roleLabel(role)}链接失效，已签发的登录态不会自动撤销；${impact}。`, id === null ? '确认解除绑定' : `确认绑定${roleLabel(role)}`, { type: 'warning', confirmButtonText: '确认', cancelButtonText: '返回' }) } catch { return }
  if (disposed || revision !== auth.sessionRevision || !bindingVisible.value || bindingRow.value !== row || bindingRole.value !== role || !auth.hasPerm(rolePermission(role))) return
  bindingSaving.value = true; beginMarker({ id: row.id, title: row.title, role })
  try { await auditionApi.action(row.id, role === 'boss' ? 'bind-boss' : 'bind-provider', { account_id: id, expected_updated_at: row.updated_at }, true); if (disposed || revision !== auth.sessionRevision) return; clearMarker(); bindingVisible.value = false; ElMessage.success(`${roleLabel(role)}入口已更新，请重新复制链接`); tableRef.value?.load() }
  catch (error: any) { if (disposed || revision !== auth.sessionRevision) return; bindingVisible.value = false; if (error?.response?.status >= 400 && error.response.status < 500 && error.response.status !== 409) { clearMarker(); ElMessage.error(error.response.data?.msg || '绑定失败，请检查账号') } else { ElMessage.warning(error?.response?.status === 409 ? '链接已变化，请读取当前绑定' : '绑定结果未确认，请读取当前绑定'); tableRef.value?.load() } }
  finally { if (!disposed && revision === auth.sessionRevision) bindingSaving.value = false }
}
async function checkBinding() {
  const item = marker.value; if (!item || checking.value || bindingSaving.value) return
  const seq = ++sequence, revision = auth.sessionRevision; checking.value = true; checked.value = false; checkText.value = ''
  try { const res = await auditionApi.retrieve(item.id, true); if (!disposed && seq === sequence && revision === auth.sessionRevision && marker.value === item) { const state = roleBinding(res.data, item.role); checkText.value = `当前：${item.role === 'provider' ? providerLabel(state) : bindingLabel(state)} ${bossDetail(state)}；这是读取时快照。`; checked.value = true; tableRef.value?.load() } }
  catch (error: any) { if (!disposed && seq === sequence && revision === auth.sessionRevision && marker.value === item) { if (error?.response?.status === 404) { checkText.value = '当前链接不存在；原请求可能仍在处理。'; checked.value = true } else checkText.value = '读取失败，请重试。' } }
  finally { if (!disposed && seq === sequence && revision === auth.sessionRevision) checking.value = false }
}
async function clearAfterReview() { const item = marker.value, revision = auth.sessionRevision; if (!item || !checked.value || checking.value) return; try { await ElMessageBox.confirm('当前绑定是读取时快照，原请求仍可能处理。确认已核对并解除限制？', '解除待核对限制', { type: 'warning', confirmButtonText: '已核对，解除', cancelButtonText: '继续核对' }) } catch { return }; if (!disposed && revision === auth.sessionRevision && marker.value === item && checked.value) clearMarker() }
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreMarker, { immediate: true, flush: 'sync' })
watch(() => auth.sessionRevision, () => { ++sequence; ++searchSequence; busy.value = false; saving.value = false; bindingSaving.value = false; checking.value = false; bindingVisible.value = false; dialogVisible.value = false; bossOptions.value = [] })
watch(bindingVisible, visible => { if (!visible) { ++searchSequence; searchLoading.value = false } })
onBeforeUnmount(() => { disposed = true; ++sequence; ++searchSequence })
</script>

<style scoped lang="scss">
.au-page { display: flex; flex-direction: column; gap: 20px; }
.au-page :deep(.page-container) { padding: 0; }
.au-sub { display: block; color: var(--muted-foreground); overflow-wrap: anywhere; }
.au-list { display: grid; gap: 12px; margin: 0; padding: 0; list-style: none; }
.au-card { padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.au-card-head { display: flex; justify-content: space-between; gap: 12px; }
.au-card p { margin: 9px 0; color: var(--muted-foreground); font-size: 12px; overflow-wrap: anywhere; }
.au-actions { display: flex; flex-wrap: wrap; gap: 8px; }
.au-actions :deep(.el-button + .el-button) { margin-left: 0; }
.au-warning { color: var(--el-color-warning) !important; font-size: 12px; }
.au-hint { color: var(--muted-foreground); font-size: 12px; }
</style>
