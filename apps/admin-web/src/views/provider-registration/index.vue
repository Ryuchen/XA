<template>
  <div class="registration-page">
    <Card class="registration-header"><h2>陪玩开户申请</h2><p>核对注册资料和押金方式后处理申请。审核通过后开通陪玩账号。</p></Card>
    <Alert v-if="reviewMarker && !visible"><AlertTitle>审核结果未确认</AlertTitle><AlertDescription>
      <p>请核对申请 #{{ reviewMarker.id }}；原请求可能仍在处理中。</p>
      <Button size="xs" variant="link" :disabled="reviewLocked" @click="openDetail(reviewMarker.id)">查看申请</Button>
    </AlertDescription></Alert>
    <p v-if="notice && !visible" role="status">{{ notice }}</p>
    <SimplePagedList ref="tableRef" :fetcher="providerRegistrationApi.list" :query="query">
      <template #toolbar="{ reload }">
        <form class="registration-filters" @submit.prevent="reload">
          <label class="search-field">搜索申请<Input v-model="query.search" placeholder="账号、手机号、姓名或昵称" aria-label="搜索注册申请" @update:model-value="value => { if (value === '') reload() }" /></label>
          <label>审核状态<NativeSelect v-model="query.status" aria-label="审核状态" @update:model-value="reload"><NativeSelectOption value="">全部状态</NativeSelectOption><NativeSelectOption value="PENDING">待审核</NativeSelectOption><NativeSelectOption value="APPROVED">已通过</NativeSelectOption><NativeSelectOption value="REJECTED">需补充资料</NativeSelectOption></NativeSelect></label>
          <Button size="xs" type="submit">查询</Button>
        </form>
      </template>
      <template #default="{ rows }">
        <div class="registration-records"><Table>
          <TableHeader><TableRow><TableHead>账号 / 昵称</TableHead><TableHead>申报身份</TableHead><TableHead>押金方式</TableHead><TableHead>审核 / 提交时间</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
          <TableBody><TableRow v-for="row in rows" :key="row.id" class="registration-item">
            <TableCell><strong>{{ row.display_name || '未填写昵称' }}</strong><small>{{ row.username || '账号待核对' }}</small><small>申请 #{{ row.id }}</small></TableCell>
            <TableCell class="personal"><span>{{ row.real_name || '未填写' }}</span><small>{{ row.phone || '未填写手机号' }}</small><small>{{ row.id_number_masked || '号码待核对' }}</small></TableCell>
            <TableCell class="mode"><span>{{ depositModeLabel(row.deposit_mode) }}</span></TableCell>
            <TableCell class="status"><Badge :variant="row.status === 'PENDING' ? 'secondary' : 'outline'">{{ row.status_display }}</Badge><small>{{ formatDateTime(row.submitted_at) }}</small></TableCell>
            <TableCell class="row-actions"><Button size="xs" variant="outline" @click="openDetail(row.id)">{{ row.status === 'PENDING' && auth.hasPerm('registration:audit') ? '查看并审核' : '查看详情' }}</Button></TableCell>
          </TableRow></TableBody>
        </Table></div>
      </template>
    </SimplePagedList>

    <Dialog :open="visible" @update:open="value => { if (!reviewLocked) visible = value }"><DialogContent class="registration-dialog" :show-close-button="!reviewLocked" @interact-outside="event => { if (reviewLocked) event.preventDefault() }" @escape-key-down="event => { if (reviewLocked) event.preventDefault() }"><DialogHeader><DialogTitle>陪玩注册资料</DialogTitle><DialogDescription>申请 #{{ currentId }} · 核对资料和押金后处理</DialogDescription></DialogHeader>
      <div class="detail-body">
      <div v-if="loading" role="status" class="detail-loading">正在读取申请资料</div>
      <Alert v-else-if="loadError" variant="destructive"><AlertDescription>{{ loadError }} <Button size="xs" variant="link" @click="openDetail(currentId)">重试加载</Button></AlertDescription></Alert>
      <template v-else-if="current">
        <Alert v-if="reviewMarker && reviewMarker.id === current.id"><AlertTitle>请核对本次审核结果</AlertTitle><AlertDescription>
          <p>当前资料是读取时的状态，原请求可能仍在处理中。</p>
          <Button size="xs" v-if="!markerChecked" variant="link" :disabled="reviewLocked" @click="openDetail(current.id)">重新读取申请</Button>
          <Button size="xs" variant="link" :disabled="reviewLocked || !markerChecked" @click="finishReview">已核对，解除限制</Button>
        </AlertDescription></Alert>
        <Alert v-if="reviewError" variant="destructive"><AlertDescription>{{ reviewError }} <Button size="xs" v-if="reviewNeedsReload" variant="link" :disabled="reviewLocked" @click="openDetail(current.id)">重新读取</Button></AlertDescription></Alert>
        <div class="detail-status">
          <Badge variant="outline">{{ current.status_display }}</Badge>
          <span>{{ formatDateTime(current.submitted_at) }} 提交</span>
        </div>
        <h3>申请信息</h3>
        <dl class="identity"><div v-for="item in identityItems" :key="item.label"><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div></dl>
        <h3>核对材料</h3>
        <div class="documents">
          <div v-for="document in documents" :key="document.key" class="document" :class="{ receipt: document.key === 'deposit_proof' }">
            <h3>{{ document.label }}</h3>
            <PrivateImage v-if="document.url" :src="document.url" :preview-src-list="documents.map(item => item.url).filter(Boolean)" fit="contain" :label="`查看${document.label}`" class="document-photo" />
            <div v-else class="missing-photo">照片不可用，请重新读取资料</div>
            <p v-if="document.url">点击放大核对</p>
          </div>
        </div>
        <div v-if="current.status === 'PENDING' && auth.hasPerm('registration:audit')" class="review-form">
          <h3>审核处理</h3>
          <div class="registration-form">
            <label>开通后的陪玩等级<NativeSelect :model-value="String(review.level_id || '')" :disabled="reviewLocked" @update:model-value="value => { review.level_id = value ? Number(value) : undefined }"><NativeSelectOption value="">选择陪玩等级</NativeSelectOption><NativeSelectOption v-for="level in levels" :key="level.id" :value="String(level.id)">{{ level.name }}</NativeSelectOption></NativeSelect></label>
            <label v-if="current.deposit_mode === 'CASH'">已确认到账的现金押金（元）<Input v-model.number="review.deposit_yuan" type="number" :min="Number(formatRmb(current.deposit_required))" max="100000" step="0.01" :disabled="reviewLocked" /></label>
            <div class="confirmations">
              <label for="registration-identity-check"><Checkbox id="registration-identity-check" v-model="review.identity_confirmed" :disabled="reviewLocked" />已核对身份证正反面、姓名与号码</label>
              <label v-if="current.deposit_mode === 'CASH'" for="registration-deposit-check"><Checkbox id="registration-deposit-check" v-model="review.deposit_confirmed" :disabled="reviewLocked" />已核实押金实际到账与交付凭证一致</label>
            </div>
            <label>审核意见<Textarea v-model="review.review_note" rows="3" maxlength="500" placeholder="退回补充资料时，必须填写需要修改的内容" :disabled="reviewLocked" /></label>
          </div>
          <Alert><AlertDescription>{{ current.deposit_mode === 'CASH' ? '请核实现金押金到账；通过后登记押金并开通账号。' : current.deposit_mode === 'EARNINGS' ? '单抵从后续订单净收益抵扣，本次不登记现金押金。' : '押金方式无法识别，请重新读取资料。' }}</AlertDescription></Alert>
        </div>
      </template>
      </div>
        <DialogFooter class="drawer-actions">
          <Button size="xs" variant="outline" :disabled="reviewLocked" @click="visible = false">关闭</Button>
          <template v-if="current?.status === 'PENDING' && !loading && auth.hasPerm('registration:audit')">
            <Button size="xs" variant="outline" :disabled="reviewLocked || !!reviewMarker || reviewNeedsReload" @click="reviewApplication(false)">退回补充资料</Button>
            <Button size="xs" :disabled="!canApprove || reviewLocked || !!reviewMarker || reviewNeedsReload" @click="reviewApplication(true)">{{ saving ? '提交中…' : '通过并开通账号' }}</Button>
          </template>
        </DialogFooter></DialogContent></Dialog>
    <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import SimplePagedList from '@/components/SimplePagedList.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
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
const notice = ref('')
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
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
    accepted = await askConfirm({ title: '解除审核限制', description: '请核对申请当前状态与押金记录；原请求可能仍在处理中。', confirmText: '已核对，解除限制', cancelText: '继续核对' })
  }
  finally { if (confirmation === confirmationSequence) confirming.value = false }
  if (accepted && !disposed && confirmation === confirmationSequence && revision === auth.sessionRevision && version === sequence
    && auth.hasPerm('registration:view') && auth.hasPerm('registration:audit') && visible.value && reviewMarker.value === marker && markerChecked.value) clearReview()
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreMarker, { immediate: true, flush: 'sync' })
watch([() => auth.sessionRevision, () => auth.hasPerm('registration:view'), () => auth.hasPerm('registration:audit')], () => {
  if (confirming.value) confirmNo()
  ++sequence; ++confirmationSequence
  visible.value = false; current.value = null; loading.value = false; saving.value = false; confirming.value = false
  markerChecked.value = false; reviewError.value = ''; reviewNeedsReload.value = false
}, { flush: 'sync' })
onBeforeUnmount(() => {
  disposed = true; ++sequence; ++confirmationSequence
  if (confirming.value) confirmNo()
})
const canApprove = computed(() => !!current.value && !!review.level_id && review.identity_confirmed && (current.value.deposit_mode === 'EARNINGS' || (current.value.deposit_mode === 'CASH' && review.deposit_confirmed
  && Number.isFinite(review.deposit_yuan) && review.deposit_yuan <= 100000 && Math.abs(review.deposit_yuan * 100 - Math.round(review.deposit_yuan * 100)) < 1e-7 && rmbToRawAmount(review.deposit_yuan) >= current.value.deposit_required)))
const documents = computed(() => current.value ? [
  { key: 'id_front', label: '身份证正面（人像面）', url: current.value.id_front_url },
  { key: 'id_back', label: '身份证反面（国徽面）', url: current.value.id_back_url },
  { key: 'deposit_proof', label: '押金交付凭证', url: current.value.deposit_proof_url },
] .filter(item => item.key !== 'deposit_proof' || current.value?.deposit_mode === 'CASH') : [])
function depositModeLabel(mode: string) { return mode === 'EARNINGS' ? '单抵双倍' : mode === 'CASH' ? '现金缴纳' : '方式待核对' }
const identityItems = computed(() => { const row = current.value; if (!row) return []; return [
  { label:'登录账号', value:row.username }, { label:'结单昵称', value:row.display_name }, { label:'真实姓名', value:row.real_name }, { label:'身份证号', value:row.id_number }, { label:'手机号', value:row.phone },
  { label:'押金方式', value:depositModeLabel(row.deposit_mode) }, { label:'应缴押金', value:`${formatRmb(row.deposit_required)} 元` },
  ...(row.status === 'APPROVED' ? [{ label:'开通等级', value:row.level_name || '未记录' }] : []),
  ...(row.status === 'APPROVED' && row.deposit_mode === 'CASH' ? [{ label:'登记现金押金', value:`${formatRmb(row.deposit_paid)} 元` }] : []),
  ...(row.reviewed_at ? [{ label:'审核人', value:row.reviewer_name }, { label:'审核时间', value:formatDateTime(row.reviewed_at) }] : []),
  ...(row.review_note ? [{ label:'审核意见', value:row.review_note }] : []),
] })
function clearDetail() { if (!visible.value) { ++sequence; current.value = null; loadError.value = ''; markerChecked.value = false } }
watch(visible, value => { if (!value) clearDetail() })
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
  if (!approve && !review.review_note.trim()) { reviewError.value = '请填写需要补充的资料和原因'; return }
  const application = current.value
  const revision = auth.sessionRevision, version = sequence, note = review.review_note.trim()
  const level = levels.value.find(item => item.id === review.level_id)
  if (approve && !level) { reviewError.value = '请选择有效的陪玩等级'; return }
  const payload = {
    revision: application.revision, review_note: note,
    ...(approve ? { level_id: review.level_id, identity_confirmed: review.identity_confirmed,
      deposit_confirmed: application.deposit_mode === 'CASH' && review.deposit_confirmed,
      deposit_paid: application.deposit_mode === 'CASH' ? rmbToRawAmount(review.deposit_yuan) : 0 } : {}),
  }
  const summary = [`申请 #${application.id} · 账号 ${application.username}`, `结单昵称：${application.display_name}`]
  if (approve) {
    summary.push(`开通等级：${level!.name}`,
      application.deposit_mode === 'CASH'
        ? `现金押金：登记 ${formatRmb(payload.deposit_paid)} 元（应缴 ${formatRmb(application.deposit_required)} 元）`
        : `单抵押金：应缴 ${formatRmb(application.deposit_required)} 元，本次不登记现金`,
      '通过后开通账号，首次登录需绑定验证器。')
    if (note) summary.push(`审核意见：${note}`)
  } else summary.push(`退回原因：${note}`)
  const confirmation = ++confirmationSequence
  confirming.value = true
  let accepted = false
  try {
    accepted = await askConfirm({ title: approve ? '核对并开通账号' : '核对退回申请', description: summary.join('；'), confirmText: approve ? '确认开通' : '确认退回', cancelText: '返回修改' })
  }
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
    notice.value = approve ? '账号已开通，首次登录需绑定验证器' : '已退回补充资料'
    // Closing the detail invalidates its request sequence before the list refresh finishes.
    saving.value = false
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

<style scoped>
.registration-page { display:grid; gap:12px; min-width:0; container:registrations / inline-size; }
.registration-header { padding:12px 16px; gap:4px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.registration-header h2 { font-size:16px; font-weight:600; }
.registration-header p,.detail-status,.document p { color:var(--muted-foreground); font-size:12px; line-height:1.5; }
.registration-filters { display:flex; flex-wrap:wrap; align-items:end; gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
.registration-filters label,.registration-form>label { display:grid; gap:4px; min-width:0; font-size:12px; }
.registration-filters .search-field { flex:1 1 208px; max-width:400px; }
.registration-filters :deep(input),.registration-filters :deep(select),.registration-form :deep(input),.registration-form :deep(select) { width:100%; min-width:0; height:28px; min-height:28px; padding-block:4px; font-size:12px; }
.registration-filters :deep(select),.registration-form :deep(select) { padding-right:32px; }
.registration-filters :deep([data-slot='native-select-wrapper']),.registration-form :deep([data-slot='native-select-wrapper']) { width:100%; }
.registration-records { background:var(--card); min-width:0; }
.registration-records :deep(th),.registration-records :deep(td) { padding:8px; white-space:normal; overflow-wrap:anywhere; vertical-align:top; }
.registration-records :deep(th) { background:var(--muted); font-size:12px; } .registration-records :deep(td) { font-size:13px; }
.registration-records small { display:block; margin-top:4px; color:var(--muted-foreground); font-size:12px; }
:global(.registration-dialog) { display:grid; grid-template-rows:auto minmax(0,1fr) auto; width:min(700px,calc(100vw - 24px)); max-width:calc(100vw - 24px); max-height:calc(100dvh - 24px); padding:16px; gap:12px; }
:global(.registration-dialog [data-slot='dialog-title']) { padding-right:32px; }
.detail-body { display:grid; gap:12px; align-content:start; min-height:0; overflow:auto; overflow-wrap:anywhere; overscroll-behavior:contain; }
.detail-body h3 { font-size:14px; font-weight:600; }
.detail-loading { padding:12px 0; font-size:13px; }
.detail-status,.drawer-actions { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.identity { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 12px; }
.identity>div { min-width:0; padding-bottom:4px; border-bottom:1px solid var(--border); }
.identity dt { color:var(--muted-foreground); font-size:12px; } .identity dd { margin:0; font-size:13px; white-space:pre-wrap; }
.documents { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,132px),1fr)); gap:8px; }
.document { display:grid; gap:4px; min-width:0; align-content:start; }
.document h3 { font-size:12px; font-weight:500; }
.document :deep(.document-photo),.missing-photo { width:100%; height:96px; border:1px solid var(--border); border-radius:8px; background:var(--secondary); }
.missing-photo { display:grid; place-items:center; padding:8px; text-align:center; color:var(--muted-foreground); font-size:12px; }
.review-form,.registration-form { display:grid; gap:8px; }
.review-form { padding-top:12px; border-top:1px solid var(--border); }
.confirmations { display:grid; gap:4px; }
.confirmations label { display:flex; align-items:center; gap:8px; padding:4px 8px; border:1px solid var(--border); border-radius:8px; cursor:pointer; font-size:13px; }
.drawer-actions { justify-content:flex-end; padding-top:8px; border-top:1px solid var(--border); }
@container registrations (max-width:760px) {
  .registration-page :deep(.table-scroll),.registration-records :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .registration-records { background:transparent; } .registration-records :deep(table),.registration-records :deep(tbody) { display:grid; }
  .registration-records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .registration-records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .registration-records .registration-item { display:grid; grid-template-columns:minmax(0,1fr) auto; align-content:start; gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
  .registration-records :deep(td) { padding:0; }
  .registration-records .personal { display:none; }
  .registration-records .status { grid-row:1; grid-column:2; text-align:right; max-width:88px; }
  .registration-records .mode { align-self:center; }
  .mode::before { content:'押金方式 '; font-size:12px; color:var(--muted-foreground); }
  .row-actions { text-align:right; }
  .registration-page :deep(.pages) { justify-content:flex-start; }
}
@container registrations (max-width:480px) { .registration-records :deep(tbody) { grid-template-columns:minmax(0,1fr); } .registration-filters .search-field { flex-basis:100%; max-width:none; } }
@media(max-width:480px) {
  :global(.registration-dialog) { padding:12px; gap:8px; }
  .drawer-actions { flex-flow:row wrap; }
}
@media(pointer:coarse) {
  .registration-page :deep([data-slot='button']),:global(.registration-dialog [data-slot='button']) { min-width:44px; min-height:44px; }
  .registration-form :deep(input),.registration-form :deep(select),.registration-filters :deep(input),.registration-filters :deep(select),.confirmations label { min-height:44px; }
}
</style>
