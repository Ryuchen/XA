<template>
  <div class="su-page">
    <Alert v-if="marker && !reviewVisible"><AlertTitle>审核结果待核对</AlertTitle><AlertDescription>
      <p>报名 #{{ marker.id }} · {{ marker.name }} 的原请求可能仍在处理。请读取当前状态并核对。</p>
      <Button size="xs" variant="link" :disabled="checking" @click="checkResult">{{ checking ? '读取中…' : '读取当前状态' }}</Button>
      <span v-if="checkText">{{ checkText }}</span>
      <Button size="xs" variant="link" :disabled="!checked || checking" @click="clearAfterReview">已核对，解除限制</Button>
    </AlertDescription></Alert>
    <p v-if="notice && !reviewVisible" role="status">{{ notice }}</p>
    <Card class="su-header"><h2>试音报名</h2><p>按报名人、游戏或联系方式查找记录。试音审核只更新报名状态，账号开通另行办理。</p></Card>
    <SimplePagedList ref="tableRef" :fetcher="auditionSignupApi.list" :query="query">
      <template #toolbar="{ reload }">
        <form class="su-filters" @submit.prevent="reload">
          <label class="su-search">搜索报名
            <Input v-model="query.keyword" placeholder="账号编号、昵称、游戏或联系方式" aria-label="搜索试音报名" @update:model-value="value => { if (value === '') reload() }" />
          </label>
          <label>审核状态
            <NativeSelect v-model="query.status" aria-label="审核状态" @update:model-value="reload"><NativeSelectOption value="">全部状态</NativeSelectOption><NativeSelectOption v-for="(v, k) in AUDITION_SIGNUP_STATUS" :key="k" :value="k">{{ v.label }}</NativeSelectOption></NativeSelect>
          </label>
          <Button size="xs" type="submit">查询</Button>
        </form>
      </template>
      <template #default="{ rows }">
        <Table class="su-table">
          <TableHeader><TableRow><TableHead>活动 / 报名编号</TableHead><TableHead>报名人 / 账号</TableHead><TableHead>游戏 / 联系方式</TableHead><TableHead>审核 / 提交时间</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
          <TableBody><TableRow v-for="row in rows" :key="row.id">
            <TableCell class="su-entry"><strong>{{ row.link_title || '—' }}</strong><small>报名 #{{ row.id }}</small></TableCell>
            <TableCell class="su-person"><strong>{{ row.applicant_name || '—' }}</strong><small>{{ identity(row) }}</small></TableCell>
            <TableCell class="su-contact"><span>{{ row.game || '—' }}</span><small>联系 {{ row.contact || '—' }}</small></TableCell>
            <TableCell class="su-state"><Badge :variant="row.status === 'PENDING' ? 'secondary' : 'outline'">{{ row.status_display || '—' }}</Badge><small>{{ formatDateTime(row.created_at) }}</small></TableCell>
            <TableCell class="su-command">
              <div class="su-actions"><Button size="xs" variant="outline" @click="onDetail(row)">详情</Button>
                <template v-if="auth.hasPerm('audition:signup_audit') && row.status === 'PENDING'">
                  <Button size="xs" :disabled="busy || !!marker" @click="openReview(row, 'approve')">通过</Button>
                  <Button size="xs" variant="outline" :disabled="busy || !!marker" @click="openReview(row, 'reject')">驳回</Button>
                </template>
              </div>
            </TableCell>
          </TableRow></TableBody>
        </Table>
      </template>
    </SimplePagedList>
  </div>
  <Dialog v-model:open="detailVisible"><DialogContent class="su-dialog">
    <DialogHeader><DialogTitle>报名详情</DialogTitle><DialogDescription>报名 #{{ current?.id }} · 列表读取时的信息</DialogDescription></DialogHeader>
    <dl v-if="current" class="su-details"><div v-for="item in detailItems" :key="item.label"><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div></dl>
  </DialogContent></Dialog>
  <Dialog :open="reviewVisible" @update:open="value => { if (!saving) reviewVisible = value }">
    <DialogContent class="su-dialog" :show-close-button="!saving" @interact-outside="event => event.preventDefault()" @escape-key-down="event => { if (saving) event.preventDefault() }">
      <DialogHeader><DialogTitle>{{ action === 'approve' ? '通过试音报名' : '驳回试音报名' }}</DialogTitle><DialogDescription>报名 #{{ candidate?.id }} · 已重新读取报名资料</DialogDescription></DialogHeader>
      <template v-if="candidate">
        <dl class="su-details">
          <div><dt>活动</dt><dd>{{ candidate.link_title || '—' }}</dd></div>
          <div><dt>报名人</dt><dd>{{ candidate.applicant_name || '—' }} · {{ identity(candidate) }}</dd></div>
          <div><dt>擅长游戏</dt><dd>{{ candidate.game || '—' }}</dd></div>
          <div><dt>联系方式</dt><dd>{{ candidate.contact || '—' }}</dd></div>
          <div><dt>报名备注</dt><dd>{{ candidate.remark || '—' }}</dd></div>
        </dl>
        <label v-if="action === 'reject'" class="su-reason">驳回原因<Textarea v-model="reason" rows="3" maxlength="255" :disabled="saving" placeholder="填写反馈给报名人的原因" /></label>
        <p v-if="notice" role="alert">{{ notice }}</p>
        <p class="su-hint">提交将更新报名状态，并尝试创建站内通知。账号开通和通知送达需另外核对。</p>
      </template>
      <DialogFooter><Button size="xs" variant="outline" :disabled="saving" @click="reviewVisible = false">返回</Button><Button size="xs" :variant="action === 'reject' ? 'destructive' : 'default'" :disabled="saving" @click="submitReview">{{ saving ? '提交中…' : action === 'approve' ? '确认通过' : '确认驳回' }}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch, onBeforeUnmount } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertTitle, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { auditionSignupApi } from '@/api/modules'
import { AUDITION_SIGNUP_STATUS } from '@/utils/dict'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore(), tableRef = ref()
const notice = ref('')
const feedback = { warning: (text: string) => { notice.value = text }, error: (text: string) => { notice.value = text }, success: (text: string) => { notice.value = text } }
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
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
const detailItems = computed(() => { const row = current.value; if (!row) return []; return [
  { label: '试音活动', value: row.link_title || '—' }, { label: '报名人', value: `${row.applicant_name || '—'} · ${identity(row)}` },
  { label: '联系方式', value: row.contact || '—' }, { label: '擅长游戏', value: row.game || '—' }, { label: '备注', value: row.remark || '—' },
  { label: '状态', value: row.status_display || '—' }, ...(row.status === 'REJECTED' ? [{ label: '驳回原因', value: row.audit_remark || '—' }] : []),
  { label: '审核人', value: row.auditor_name || '—' }, { label: '提交时间', value: formatDateTime(row.created_at) }, { label: '审核时间', value: row.audited_at ? formatDateTime(row.audited_at) : '—' },
] })
function onDetail(row: any) { current.value = row; detailVisible.value = true }
async function openReview(row: any, kind: 'approve' | 'reject') {
  if (busy.value || marker.value || !auth.hasPerm('audition:signup_audit')) return
  const seq = ++sequence, revision = auth.sessionRevision
  busy.value = true
  try {
    const res = await auditionSignupApi.retrieve(row.id, true)
    if (disposed || seq !== sequence || revision !== auth.sessionRevision || !auth.hasPerm('audition:signup_audit')) return
    if (res.data.status !== 'PENDING') { feedback.warning('该报名已审核，请查看最新状态'); tableRef.value?.load(); return }
    candidate.value = res.data; action.value = kind; reason.value = ''; notice.value = ''; reviewVisible.value = true
  } catch { if (!disposed && seq === sequence && revision === auth.sessionRevision) feedback.error('报名读取失败，请刷新后重试') }
  finally { if (!disposed && seq === sequence && revision === auth.sessionRevision) busy.value = false }
}
async function submitReview() {
  const item = candidate.value, revision = auth.sessionRevision
  if (!item || saving.value || marker.value || !auth.hasPerm('audition:signup_audit')) return
  const trimmed = reason.value.trim()
  if (action.value === 'reject' && !trimmed) { feedback.warning('请填写驳回原因'); return }
  saving.value = true; beginMarker({ id: item.id, name: item.applicant_name || '报名人' })
  try {
    await auditionSignupApi.action(item.id, action.value, action.value === 'reject' ? { audit_remark: trimmed } : {}, true)
    if (disposed || revision !== auth.sessionRevision) return
    clearMarker(); reviewVisible.value = false; feedback.success(action.value === 'approve' ? '报名已通过' : '报名已驳回'); tableRef.value?.load()
  } catch (error: any) {
    if (disposed || revision !== auth.sessionRevision) return
    reviewVisible.value = false
    if (error?.response?.status >= 400 && error.response.status < 500 && error.response.status !== 409) { clearMarker(); feedback.error(error.response.data?.msg || '审核失败，请检查后重试') }
    else { feedback.warning(error?.response?.status === 409 ? '报名已被审核，请读取最新状态' : '审核结果未确认，请读取当前状态'); tableRef.value?.load() }
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
  if (!await askConfirm({ title: '解除待核对限制', description: '当前状态是读取时快照，原请求仍可能处理。确认已核对并解除限制？', confirmText: '已核对，解除', cancelText: '继续核对' })) return
  if (!disposed && revision === auth.sessionRevision && marker.value === item && checked.value) clearMarker()
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreMarker, { immediate: true, flush: 'sync' })
watch(() => auth.sessionRevision, () => { ++sequence; busy.value = false; saving.value = false; checking.value = false; reviewVisible.value = false; detailVisible.value = false })
onBeforeUnmount(() => { disposed = true; ++sequence })
</script>

<style scoped>
.su-page { display:grid; gap:12px; min-width:0; container:signups / inline-size; }
.su-header { gap:4px; padding:12px 16px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.su-header h2 { margin:0; font-size:16px; font-weight:600; }
.su-header p,.su-hint { margin:0; color:var(--muted-foreground); font-size:12px; line-height:1.5; }
.su-filters { display:flex; align-items:end; flex-wrap:wrap; gap:8px; min-width:0; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
.su-filters label,.su-reason { display:grid; gap:4px; min-width:0; font-size:12px; }
.su-search { flex:1 1 208px; max-width:400px; }
.su-filters :deep(input),.su-filters :deep(select) { min-height:28px; height:28px; padding-block:4px; font-size:12px; }
.su-filters :deep([data-slot='native-select-wrapper']) { width:100%; }
.su-filters :deep(select) { padding-right:32px; }
.su-page :deep(.su-table) { width:100%; background:var(--card); }
.su-page :deep(.su-table td),.su-page :deep(.su-table th) { white-space:normal; padding:8px; overflow-wrap:anywhere; }
.su-page :deep(.su-table th) { font-size:12px; background:var(--muted); }
.su-page :deep(.su-table strong) { font-size:13px; font-weight:600; }
.su-page :deep(.su-table small) { display:block; margin-top:4px; font-size:12px; line-height:1.4; color:var(--muted-foreground); }
.su-actions { display:flex; flex-wrap:wrap; align-items:center; gap:4px; }
.su-dialog { gap:12px; padding:16px; max-height:calc(100dvh - 24px); overflow-y:auto; }
.su-dialog :deep([data-slot='dialog-title']) { padding-right:32px; }
.su-details { display:grid; margin:0; }
.su-details div { display:grid; grid-template-columns:64px minmax(0,1fr); gap:12px; padding:8px 0; border-bottom:1px solid var(--border); font-size:13px; }
.su-details dt { color:var(--muted-foreground); }
.su-details dd { margin:0; overflow-wrap:anywhere; white-space:pre-wrap; }
.su-reason :deep(textarea) { width:100%; min-height:64px; }
@container signups (max-width:760px) {
  .su-page :deep(.table-scroll),.su-page :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .su-page :deep(.su-table),.su-page :deep(.su-table tbody) { display:grid; }
  .su-page :deep(.su-table) { background:transparent; }
  .su-page :deep(.su-table thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .su-page :deep(.su-table tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .su-page :deep(.su-table tbody tr) { display:grid; grid-template-columns:minmax(0,1fr) auto; align-content:start; align-items:start; gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
  .su-page :deep(.su-table td) { padding:0; }
  .su-entry { grid-column:1; grid-row:1; }
  .su-state { grid-column:2; grid-row:1; text-align:right; max-width:88px; }
  .su-person,.su-contact,.su-command { grid-column:1/-1; }
  .su-page :deep(.su-table td.su-contact) { padding-top:8px; border-top:1px solid var(--border); }
  .su-page :deep(.su-table small) { margin-top:0; }
  .su-page :deep(.su-table .su-state small) { margin-top:4px; }
  .su-page :deep(.pages) { justify-content:flex-start; }
}
@container signups (max-width:480px) {
  .su-page :deep(.su-table tbody) { grid-template-columns:minmax(0,1fr); }
  .su-search { flex-basis:100%; max-width:none; }
}
@media(pointer:coarse) {
  .su-filters :deep(input),.su-filters :deep(select),.su-page :deep([data-slot='button']),.su-dialog :deep([data-slot='button']) { min-height:44px; }
  .su-page :deep([data-slot='button']),.su-dialog :deep([data-slot='button']) { min-width:44px; }
}
</style>
