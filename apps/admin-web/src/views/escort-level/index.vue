<template>
  <div class="level-page">
    <Card class="level-panel">
      <header class="level-header">
        <div><h2>陪玩等级</h2><p class="tip">部分商品与手工报单会使用等级抽成，具体以订单或报单记录为准。</p></div>
        <Button size="xs" v-if="auth.hasPerm('escort_level:edit')" :disabled="operationBusy" @click="onCreate">新增等级</Button>
      </header>
      <section class="scope-card">
        <div class="scope-header"><h3>接单档位与自动抽成</h3><Button size="xs" variant="outline" :aria-expanded="scopeExpanded" @click="scopeExpanded = !scopeExpanded">{{ scopeExpanded ? '收起规则' : '查看规则' }}</Button></div>
        <Alert v-if="scopeError" variant="destructive"><AlertDescription>档位规则读取失败 <Button size="xs" variant="link" :disabled="scopeLoading" @click="loadScopes">重新读取</Button></AlertDescription></Alert>
        <template v-if="scopeExpanded">
          <p v-if="scopeLoading" role="status" class="tip">正在读取档位规则…</p>
          <ul v-else-if="!scopeError" class="scope-list" aria-label="接单档位规则">
            <li v-for="row in scopedRows" :key="row.key"><strong>{{ row.scope }}</strong>
              <ul class="grade-list"><li v-for="grade in row.levels" :key="grade.label"><span>{{ grade.label }}</span><b>{{ grade.commission_rate == null ? '按项目' : grade.commission_rate + '%' }}</b></li></ul>
            </li>
          </ul>
          <p class="tip">档位从低到高排列，同范围高档可接低档。自动抽成按订单档位，具体订单可能采用其他已配置的抽成规则。</p>
        </template>
      </section>
    <Alert v-if="marker"><AlertTitle>删除结果未确认</AlertTitle><AlertDescription>
      <p>请核对等级「{{ marker.name }}」；原请求可能仍在处理中。</p>
      <Button size="xs" variant="link" :disabled="operationBusy || checking" @click="checkDeletion">读取当前状态</Button>
      <span v-if="checkResult">{{ checkResult }}</span>
      <Button size="xs" variant="link" :disabled="operationBusy || checking || !checked" @click="finishReview">已核对，解除限制</Button>
    </AlertDescription></Alert>
    <p v-if="notice" role="status">{{ notice }}</p>

    <SimplePagedList ref="tableRef" :fetcher="escortLevelApi.list" :query="query">
      <template #toolbar="{ reload }"><NativeSelect v-model="query.is_active" aria-label="等级状态" class="status-filter" @update:model-value="reload"><NativeSelectOption value="">全部状态</NativeSelectOption><NativeSelectOption value="true">启用</NativeSelectOption><NativeSelectOption value="false">停用</NativeSelectOption></NativeSelect></template>
      <template #default="{ rows }">
        <Table>
          <TableHeader><TableRow>
            <TableHead>陪玩等级</TableHead><TableHead>等级抽成</TableHead><TableHead>排序</TableHead>
            <TableHead>状态</TableHead><TableHead v-if="canManage">操作</TableHead>
          </TableRow></TableHeader>
          <TableBody><TableRow v-for="row in rows" :key="row.id">
            <TableCell class="level-name"><h3>{{ row.name }}</h3><p v-if="row.remark" class="remark">{{ row.remark }}</p><p class="tip">ID {{ row.id }}</p></TableCell>
            <TableCell data-label="等级抽成"><strong class="rate">{{ row.commission_rate }}%</strong></TableCell>
            <TableCell data-label="排序">{{ row.sort_order }}</TableCell>
            <TableCell data-label="状态"><Badge :variant="row.is_active ? 'secondary' : 'outline'">{{ row.is_active ? '启用' : '停用' }}</Badge></TableCell>
            <TableCell v-if="canManage" class="level-actions"><div class="actions">
              <Button size="xs" v-if="auth.hasPerm('escort_level:edit')" variant="outline" :disabled="operationBusy" @click="onEdit(row)">编辑</Button>
              <Button size="xs" v-if="auth.hasPerm('escort_level:delete')" variant="outline" :disabled="operationBusy || !!marker" @click="openDelete(row)">删除</Button>
            </div></TableCell>
          </TableRow></TableBody>
        </Table>
      </template>
    </SimplePagedList>
    </Card>
  </div>

  <Dialog :open="dialogVisible" @update:open="value => { if (!saving) dialogVisible = value }"><DialogContent class="level-dialog" :show-close-button="!saving" @interact-outside="event => { if (saving) event.preventDefault() }" @escape-key-down="event => { if (saving) event.preventDefault() }"><DialogHeader><DialogTitle>{{ form.id ? '编辑陪玩等级' : '新增陪玩等级' }}</DialogTitle><DialogDescription>设置等级和部分业务使用的抽成比例。</DialogDescription></DialogHeader>
    <Alert v-if="saveError" variant="destructive"><AlertDescription>{{ saveError }}</AlertDescription></Alert>
    <div class="level-form"><label>等级名称<Input v-model="form.name" maxlength="50" placeholder="如：金牌陪玩" :disabled="saving" /></label><label>等级抽成率（%）<Input v-model.number="form.commission_rate" type="number" min="0" max="100" step="1" :disabled="saving" /><span class="tip">具体订单以实际规则为准。</span></label><label class="level-full">备注<Textarea v-model="form.remark" :rows="2" maxlength="255" :disabled="saving" /></label><label>排序<Input v-model.number="form.sort_order" type="number" min="0" step="1" :disabled="saving" /></label><label class="level-switch">启用<Switch v-model="form.is_active" aria-label="启用等级" :disabled="saving" /></label></div>
    <DialogFooter><Button size="xs" variant="outline" :disabled="saving" @click="dialogVisible = false">关闭</Button><Button size="xs" :disabled="saving || saveUnknown" @click="onSave">{{ saving ? '保存中…' : '保存' }}</Button></DialogFooter></DialogContent></Dialog>

  <Dialog :open="deleteVisible" @update:open="value => { if (!deleting) deleteVisible = value }"><DialogContent class="level-dialog" :show-close-button="!deleting" @interact-outside="event => { if (deleting) event.preventDefault() }" @escape-key-down="event => { if (deleting) event.preventDefault() }"><DialogHeader><DialogTitle>删除陪玩等级</DialogTitle><DialogDescription>删除前核对当前关联情况。</DialogDescription></DialogHeader>
    <div v-if="impactLoading" role="status" class="impact-loading">正在读取关联情况</div>
    <Alert v-else-if="impactError" variant="destructive"><AlertDescription>{{ impactError }} <Button size="xs" variant="link" @click="readImpact">重新读取</Button></AlertDescription></Alert>
    <template v-else-if="impact"><p>删除「{{ impact.name }}」？</p><p>当前关联：{{ impact.providers }} 个陪玩档案、{{ impact.services }} 个商品、{{ impact.registrations }} 条注册申请。</p>
      <Alert><AlertTitle>删除会清空这些关联的等级字段</AlertTitle><AlertDescription>
        <p>商品最低等级被清空后，未设置分范围资格的商品将不再按该最低等级限制接单。关联数量为读取时快照。</p>
      </AlertDescription></Alert>
    </template>
    <DialogFooter><Button size="xs" variant="outline" :disabled="deleting" @click="deleteVisible = false">取消</Button><Button size="xs" variant="destructive" :disabled="deleting || impactLoading || !impact || !!impactError || !!marker" @click="confirmDelete">{{ deleting ? '删除中…' : '确认删除' }}</Button></DialogFooter></DialogContent></Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import SimplePagedList from '@/components/SimplePagedList.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Badge } from '@xa/shadcn-vue/badge'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Input } from '@xa/shadcn-vue/input'
import { Switch } from '@xa/shadcn-vue/switch'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { qualificationOptions } from '@/utils/qualifications'
import { escortLevelApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'

type Impact = { id: number; name: string; providers: number; services: number; registrations: number }
const auth = useAuthStore(), tableRef = ref()
const notice = ref('')
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const canManage = computed(() => auth.hasPerm('escort_level:edit') || auth.hasPerm('escort_level:delete'))
const query = reactive<Record<string, any>>({ is_active: '' })
const scopedMeta = ref<any>({ scopes: [], kinds: [], platforms: [] })
const scopeLoading = ref(false), scopeError = ref(false), scopeExpanded = ref(false)
const scopedRows = computed(() => scopedMeta.value.scopes.map((s: any) => ({
  key: `${s.provider_kind}-${s.platform}-${s.security_level}`,
  scope: [scopedMeta.value.platforms.find((p: any) => p.value === s.platform)?.label, ({ CONFIDENTIAL: '机密', TOP_SECRET: '绝密', ANY: '' } as Record<string, string>)[s.security_level], scopedMeta.value.kinds.find((k: any) => k.value === s.provider_kind)?.label].filter(Boolean).join(' · '),
  levels: s.grades as {label:string; commission_rate:number | null}[],
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
  try { accepted = await askConfirm({ title: '解除删除限制', description: '读取只是当前状态，原请求可能仍在处理中。请确认已核对等级及关联。', confirmText: '已核对，解除限制', cancelText: '继续核对' }) }
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
function onCreate() { if (operationBusy.value || !auth.hasPerm('escort_level:edit')) return; Object.assign(form, blank()); notice.value = ''; saveError.value = ''; saveUnknown.value = false; dialogVisible.value = true }
function onEdit(row: any) { if (operationBusy.value || !auth.hasPerm('escort_level:edit')) return; Object.assign(form, blank(), row); notice.value = ''; saveError.value = ''; saveUnknown.value = false; dialogVisible.value = true }
function onEditClosed() { if (!dialogVisible.value && saveUnknown.value) { saveUnknown.value = false; void tableRef.value?.load() } }
async function onSave() {
  if (saving.value || saveUnknown.value || !dialogVisible.value || !auth.hasPerm('escort_level:edit')) return
  if (!form.name?.trim()) { saveError.value = '请填写等级名称'; return }
  if (!Number.isInteger(form.commission_rate) || form.commission_rate < 0 || form.commission_rate > 100 || !Number.isInteger(form.sort_order) || form.sort_order < 0) { saveError.value = '请检查抽成率与排序'; return }
  const seq = ++saveSequence, revision = auth.sessionRevision, id = form.id
  const payload = { name: form.name.trim(), commission_rate: form.commission_rate, remark: form.remark, sort_order: form.sort_order, is_active: form.is_active }
  saving.value = true; saveError.value = ''
  try {
    if (id) await escortLevelApi.update(id, payload, true); else await escortLevelApi.create(payload, true)
    if (disposed || seq !== saveSequence || revision !== auth.sessionRevision || !auth.hasPerm('escort_level:edit')) return
    notice.value = '等级已保存'; dialogVisible.value = false; await tableRef.value?.reload()
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
    clearReview(); notice.value = '等级已删除'; deleting.value = false; deleteVisible.value = false; void tableRef.value?.load()
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
  if (confirming.value) confirmNo()
  ++scopeSequence; ++deleteSequence; ++saveSequence; ++confirmSequence; ++checkSequence
  dialogVisible.value = deleteVisible.value = false; saving.value = deleting.value = confirming.value = checking.value = false
  scopeLoading.value = impactLoading.value = false; scopeError.value = false; impact.value = null; checked.value = false; checkResult.value = ''
  scopedMeta.value = { scopes: [], kinds: [], platforms: [] }
  if (!disposed && auth.hasPerm('escort_level:view')) void loadScopes()
}, { flush: 'sync' })
onMounted(() => { void loadScopes() })
watch(dialogVisible, value => { if (!value) onEditClosed() })
watch(deleteVisible, value => { if (!value) clearDelete() })
onBeforeUnmount(() => { disposed = true; ++scopeSequence; ++deleteSequence; ++saveSequence; ++confirmSequence; ++checkSequence; if (confirming.value) confirmNo() })
</script>

<style scoped>
.level-page { min-width:0; container:levels / inline-size; }
.level-panel { display:grid; grid-template-columns:minmax(0,1fr); min-width:0; gap:12px; padding:16px; border:1px solid var(--border); border-radius:12px; box-shadow:none; }
.level-header,.scope-header,.actions,.grade-list,.grade-list li { display:flex; align-items:center; gap:8px; }
.level-header,.scope-header { justify-content:space-between; }
.actions,.grade-list { flex-wrap:wrap; }
h2,h3,p,ul { margin:0; }
h2 { font-size:16px; line-height:24px; font-weight:600; }
h3,.rate { font-size:14px; line-height:20px; font-weight:600; }
.tip,.remark,.scope-list,.level-form label { font-size:12px; line-height:16px; }
.tip,.remark { color:var(--muted-foreground); }
.level-name p,.level-header .tip,.level-form .tip { margin-top:4px; }
.remark { white-space:pre-wrap; overflow-wrap:anywhere; }
.scope-card { display:grid; gap:8px; min-width:0; padding:8px 12px; border:1px solid var(--border); border-radius:8px; }
.scope-list,.grade-list { list-style:none; padding:0; }
.scope-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 16px; }
.scope-list>li { min-width:0; padding-top:8px; border-top:1px solid var(--border); }
.grade-list { gap:4px 8px; margin-top:4px; }
.grade-list li { gap:4px; min-width:0; padding:0 4px; line-height:20px; border-radius:4px; background:var(--muted); overflow-wrap:anywhere; }
.grade-list b { font-variant-numeric:tabular-nums; }
.level-panel :deep(th),.level-panel :deep(td) { padding:8px; white-space:normal; overflow-wrap:anywhere; }
.level-panel :deep(th) { white-space:nowrap; }
.level-panel :deep(select),.level-form :deep(input) { min-height:28px; height:28px; padding-block:4px; font-size:12px; }
.level-form { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; align-items:start; }
.level-form label { min-width:0; display:grid; gap:4px; }
.level-full { grid-column:1/-1; }
.level-form .level-switch { display:flex; align-items:center; justify-content:space-between; align-self:end; padding:4px 8px; border:1px solid var(--border); border-radius:8px; }
.impact-loading { min-height:80px; display:grid; place-items:center; color:var(--muted-foreground); }
:global(.level-dialog) { max-height:calc(100dvh - 32px); overflow:auto; }
@container levels (max-width:800px) {
 .level-panel { padding:12px; } .level-panel :deep(.paged-list) { min-width:0; grid-template-columns:minmax(0,1fr); }
 .level-panel :deep(.table-scroll),.level-panel :deep([data-slot='table-container']) { border:0; overflow:visible; }
 .level-panel :deep(table),.level-panel :deep(tbody) { display:grid; }
 .level-panel :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
 .level-panel :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
 .level-panel :deep(tbody tr) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); align-content:start; min-width:0; gap:4px 8px; padding:12px; border:1px solid var(--border); border-radius:8px; }
 .level-panel :deep(td) { padding:0; }
 .level-panel :deep(td[data-label]) { display:flex; align-items:baseline; gap:4px; font-size:12px; }
 .level-panel :deep(td[data-label])::before { content:attr(data-label); color:var(--muted-foreground); }
 .level-name,.level-actions { grid-column:1/-1; } .level-name { margin-bottom:4px; } .level-actions { margin-top:4px; }
 .level-panel :deep(.pages) { justify-content:flex-start; }
}
@container levels (max-width:480px) { .level-panel :deep(tbody),.scope-list { grid-template-columns:minmax(0,1fr); } }
@media(max-width:480px) { .level-form { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) { .level-panel :deep(button),.level-panel :deep(select),.level-form :deep(input),.level-form .level-switch { min-height:44px; } .level-panel :deep(button) { min-width:44px; } }
</style>
