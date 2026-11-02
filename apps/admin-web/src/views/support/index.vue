<template>
  <div class="sp-page">
    <Card class="sp-header"><div><h2>客服名片</h2><p>管理小程序展示的联系方式和企业微信入口。</p></div><Button size="xs" v-if="auth.hasPerm('support:edit')" :disabled="!!pendingReview || saving" @click="onCreate">新增名片</Button></Card>
    <Alert><AlertTitle>订单优先使用负责客服</AlertTitle><AlertDescription>普通咨询使用当前默认客服。企业微信接待需在小程序绑定企业后用手机验证。
      <Popover><PopoverTrigger as-child><Button size="xs" variant="link" aria-label="查看客服选择与接待说明">选择与接待说明</Button></PopoverTrigger><PopoverContent>启用名片按排序从小到大排列，相同排序按最近更新时间优先。原负责客服停用或删除后，订单回退到默认客服。没有启用名片时不会显示名片。微信客服由企业微信工作台接待，平台内会话由本站客服处理。</PopoverContent></Popover>
    </AlertDescription></Alert>
    <Alert v-if="pendingReview && !dialogVisible"><AlertTitle>客服名片操作结果待核对</AlertTitle><AlertDescription><p>{{ pendingReview.action }}「{{ pendingReview.name }}」的请求可能仍在处理；列表只显示当前状态，不能证明原请求最终结果。</p><p v-if="reviewNotice" role="status">{{ reviewNotice }}</p><Button size="xs" variant="outline" :disabled="checking" @click="checkReview">{{ checking ? '读取中…' : pendingReview.id ? '读取目标名片' : '按名称查找' }}</Button><Button size="xs" v-if="reviewChecked" variant="outline" :disabled="checking" @click="finishReview">已核对，解除限制</Button></AlertDescription></Alert>
    <p v-if="notice && !dialogVisible" role="status">{{ notice }}</p>
    <SimplePagedList ref="tableRef" :fetcher="supportCardApi.list" :query="query">
      <template #toolbar="{ reload }"><form class="sp-filters" @submit.prevent="reload"><label>搜索名片<Input v-model="query.keyword" placeholder="名称、所属或微信号" aria-label="按名称、所属或微信号搜索" @update:model-value="value => { if (value === '') reload() }" /></label><Button size="xs" type="submit">查询</Button></form></template>
      <template #default="{ rows }"><div class="sp-records"><Table>
        <TableHeader><TableRow><TableHead>客服 / 所属</TableHead><TableHead>联系方式</TableHead><TableHead>企业微信</TableHead><TableHead>名片状态</TableHead><TableHead>操作</TableHead></TableRow></TableHeader>
        <TableBody><TableRow v-for="row in rows" :key="row.id" class="sp-card">
          <TableCell class="sp-name"><div class="sp-person"><Avatar><AvatarImage v-if="row.avatar_url" :src="row.avatar_url" alt="" /><AvatarFallback>{{ (row.name || '客').charAt(0) }}</AvatarFallback></Avatar><div><strong>{{ row.name }}</strong><small>#{{ row.id }} · {{ row.company || '未填写所属' }}</small></div></div></TableCell>
          <TableCell class="sp-contact"><span>微信号 {{ row.wechat_id || '未填写' }}</span><div v-if="row.qrcode_url" class="sp-qr-action"><Button size="xs" variant="outline" :aria-label="`查看${row.name}的二维码`" @click="qrUrl = row.qrcode_url">查看二维码</Button></div><small v-else>二维码未填写</small></TableCell>
          <TableCell class="sp-wecom"><span class="sp-mobile-label">企业微信 </span><span>{{ row.wecom_corp_id && row.wecom_service_url ? '参数已配置' : '未配置' }}</span></TableCell>
          <TableCell class="sp-status"><Badge :variant="row.is_active ? 'secondary' : 'outline'">{{ row.is_active ? '启用' : '停用' }}</Badge><small>排序 {{ row.sort_order }}</small></TableCell>
          <TableCell class="sp-command"><div class="sp-actions"><Button size="xs" variant="outline" @click="openDetail(row.id)">查看配置</Button><Button size="xs" v-if="auth.hasPerm('support:edit')" variant="outline" :disabled="!!pendingReview || saving" @click="onEdit(row)">编辑</Button><Button size="xs" v-if="auth.hasPerm('support:delete')" variant="outline" :disabled="!!pendingReview || saving" @click="onDelete(row)">删除</Button></div></TableCell>
        </TableRow></TableBody>
      </Table></div></template>
    </SimplePagedList>
  </div>
  <Dialog :open="detailVisible" @update:open="value => { detailVisible = value; if (!value) closeDetail() }"><DialogContent class="sp-dialog sp-detail-dialog"><DialogHeader><DialogTitle>客服名片配置</DialogTitle><DialogDescription>名片 #{{ detailId }} · 显示本次读取的信息。</DialogDescription></DialogHeader><div class="sp-dialog-body"><p v-if="detailLoading" role="status">正在读取名片…</p><Alert v-else-if="detailError"><AlertDescription>{{ detailError }} <Button size="xs" variant="link" @click="openDetail(detailId)">重新读取</Button></AlertDescription></Alert><template v-else-if="detail"><Alert v-if="pendingReview"><AlertDescription>这是当前读取的配置，原请求可能仍在处理。</AlertDescription></Alert><dl class="sp-detail"><div v-for="item in detailItems" :key="item.label"><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div></dl></template></div></DialogContent></Dialog>
  <Dialog :open="!!qrUrl" @update:open="value => { if (!value) qrUrl = '' }"><DialogContent class="sp-qr-dialog"><DialogHeader><DialogTitle>客服二维码</DialogTitle><DialogDescription>查看名片配置中的二维码。</DialogDescription></DialogHeader><img v-if="qrUrl" :src="qrUrl" alt="客服二维码" class="sp-qr-preview" /></DialogContent></Dialog>
  <Dialog :open="dialogVisible" @update:open="value => { if (!value) requestClose() }"><DialogContent class="sp-dialog sp-edit-dialog" :show-close-button="!saving" @interact-outside="event => event.preventDefault()" @escape-key-down="event => { if (saving) event.preventDefault() }"><DialogHeader><DialogTitle>{{ form.id ? '编辑客服名片' : '新增客服名片' }}</DialogTitle><DialogDescription>配置小程序客服名片及企业微信入口。</DialogDescription></DialogHeader>
    <div class="sp-dialog-body"><p v-if="notice" role="status">{{ notice }}</p><fieldset :disabled="saving || !!pendingReview" class="sp-form"><label>名称<Input v-model="form.name" maxlength="50" /></label><label>所属<Input v-model="form.company" /></label><label>微信号<Input v-model="form.wechat_id" /></label><p class="sp-form-full"><strong>企业微信客服</strong></p><label>企业 ID<Input v-model="form.wecom_corp_id" placeholder="例如：wwxxxxxxxxxxxxxxxx" /></label><label>客服链接<Input v-model="form.wecom_service_url" placeholder="https://work.weixin.qq.com/kfid/..." /></label><Alert class="sp-form-full"><AlertDescription>企业 ID 与客服链接须同时填写或留空；小程序也需绑定该企业。</AlertDescription></Alert><label>头像 URL<Input v-model="form.avatar_url" /></label><label>二维码 URL<Input v-model="form.qrcode_url" /></label><label class="sp-form-full">提示语<Textarea v-model="form.tips" rows="2" /></label><label>排序<Input v-model.number="form.sort_order" type="number" min="0" step="1" /></label><label class="sp-check" for="support-active">启用<Switch id="support-active" v-model="form.is_active" aria-label="启用" :disabled="saving || !!pendingReview" /></label></fieldset></div>
    <DialogFooter><Button size="xs" variant="outline" :disabled="saving" @click="requestClose">{{ pendingReview ? '关闭并核对' : '取消' }}</Button><Button size="xs" :disabled="saving || !!pendingReview" @click="onSave">{{ saving ? '保存中…' : '保存' }}</Button></DialogFooter></DialogContent></Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { Button } from '@xa/shadcn-vue/button'
import { Card } from '@xa/shadcn-vue/card'
import { Switch } from '@xa/shadcn-vue/switch'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Avatar, AvatarImage, AvatarFallback } from '@xa/shadcn-vue/avatar'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertTitle, AlertDescription } from '@xa/shadcn-vue/alert'
import { Popover, PopoverTrigger, PopoverContent } from '@xa/shadcn-vue/popover'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { supportCardApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
import { formatDateTime } from '@/utils/format'

const notice = ref(''), qrUrl = ref('')
const feedback = { warning: (text: string) => { notice.value = text }, success: (text: string) => { notice.value = text } }
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
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
const detailItems = computed(() => { const d = detail.value; if (!d) return []; return [{label:'名称',value:`${d.name} · #${d.id}`},{label:'状态',value:`${d.is_active ? '启用' : '停用'} · 排序 ${d.sort_order}`},...([['所属',d.company],['微信号',d.wechat_id],['企业 ID',d.wecom_corp_id],['客服链接',d.wecom_service_url],['头像 URL',d.avatar_url],['二维码 URL',d.qrcode_url],['提示语',d.tips]] as [string,string][]).map(([label,value]) => ({label,value:value || '未填写'})),{label:'更新时间',value:formatDateTime(d.updated_at)}] })
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
  if (!await askConfirm({title:'解除操作限制', description:'请确认已核对名片；原请求仍可能在处理。解除后可再次操作。', confirmText:'已核对，解除限制', cancelText:'继续核对'})) return
  if (!disposed && revision === auth.sessionRevision && marker === pendingReview.value) clearReview()
}
async function canClose() {
  if (saving.value) return false
  if (!dirty.value || pendingReview.value) return true
  return await askConfirm({title:'放弃修改', description:'名片修改尚未保存，确定放弃？', confirmText:'放弃修改', cancelText:'继续编辑'})
}
async function requestClose() { if (await canClose()) dialogVisible.value = false }
onBeforeRouteLeave(async () => !dialogVisible.value || await canClose())

function onCreate() {
  if (saving.value || pendingReview.value) return
  notice.value = ''
  Object.assign(form, blank())
  initialForm.value = JSON.stringify(payload())
  dialogVisible.value = true
}

function onEdit(row: any) {
  if (saving.value || pendingReview.value) return
  notice.value = ''
  Object.assign(form, blank(), row)
  initialForm.value = JSON.stringify(payload())
  dialogVisible.value = true
}

async function onSave() {
  if (saving.value || pendingReview.value) return
  if (!form.name.trim()) { feedback.warning('请填写名称'); return }
  if (!!form.wecom_corp_id.trim() !== !!form.wecom_service_url.trim()) { feedback.warning('企业 ID 和客服链接须同时填写或留空'); return }
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
    feedback.success('保存成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } catch (error: any) {
    if (disposed || revision !== auth.sessionRevision) return
    const status = error?.response?.status ?? (typeof error?.code === 'number' ? error.code : undefined)
    if (status && status >= 400 && status < 500 && status !== 408 && status !== 429) clearReview()
    else feedback.warning('保存结果未确认，请关闭后核对当前列表。')
  } finally {
    if (revision === auth.sessionRevision) saving.value = false
  }
}

async function onDelete(row: any) {
  if (saving.value || pendingReview.value) return
  const revision = auth.sessionRevision
  saving.value = true
  try {
    if (!await askConfirm({title:'删除客服名片', description:`删除「${row.name}」（#${row.id}）后，关联订单将解除该客服；订单咨询回退到当前默认客服，没有启用名片时无法提供名片。确定删除？`, confirmText:'确认删除'})) return
    if (disposed || revision !== auth.sessionRevision) return
    markReview('删除', `${row.name} #${row.id}`, row.id)
    try {
      await supportCardApi.remove(row.id)
      if (disposed || revision !== auth.sessionRevision) return
      clearReview(); feedback.success('已删除'); tableRef.value.reload()
    } catch (error: any) {
      if (disposed || revision !== auth.sessionRevision) return
      const status = error?.response?.status ?? (typeof error?.code === 'number' ? error.code : undefined)
      if (status && status >= 400 && status < 500 && status !== 408 && status !== 429) clearReview()
      else feedback.warning('删除结果未确认，请核对当前列表。')
    }
  } finally { if (revision === auth.sessionRevision) saving.value = false }
}
</script>

<style scoped>
.sp-page { display:grid; gap:12px; min-width:0; container:support-cards / inline-size; }
.sp-header { display:flex; flex-flow:row wrap; justify-content:space-between; align-items:center; gap:8px; padding:12px 16px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.sp-header h2 { margin:0; font-size:16px; font-weight:600; }
.sp-header p { margin:4px 0 0; color:var(--muted-foreground); font-size:12px; }
.sp-filters,.sp-actions,.sp-person { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.sp-filters { align-items:end; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
.sp-filters label,.sp-form label { display:grid; gap:4px; min-width:0; font-size:12px; }
.sp-filters label { flex:1 1 180px; max-width:400px; }
.sp-filters :deep(input),.sp-form :deep([data-slot='input']) { height:28px; min-height:28px; padding-block:4px; font-size:12px; }
.sp-records { min-width:0; background:var(--card); }
.sp-records :deep(td),.sp-records :deep(th) { padding:8px; white-space:normal; overflow-wrap:anywhere; vertical-align:top; }
.sp-records :deep(td) { font-size:13px; } .sp-records :deep(th) { background:var(--muted); font-size:12px; }
.sp-records small { display:block; margin-top:4px; color:var(--muted-foreground); font-size:12px; }
.sp-person { flex-wrap:nowrap; align-items:start; } .sp-person>div { min-width:0; }
.sp-person :deep([data-slot='avatar']) { width:24px; height:24px; }
.sp-mobile-label { display:none; } .sp-qr-action { margin-top:4px; }
.sp-actions { gap:4px; }
:global(.sp-dialog),:global(.sp-qr-dialog) { display:grid; gap:12px; padding:16px; width:min(600px,calc(100vw - 24px)); max-width:calc(100vw - 24px); max-height:calc(100dvh - 24px); }
:global(.sp-detail-dialog) { grid-template-rows:auto minmax(0,1fr); }
:global(.sp-edit-dialog) { grid-template-rows:auto minmax(0,1fr) auto; }
:global(.sp-qr-dialog) { width:min(400px,calc(100vw - 24px)); overflow-y:auto; }
:global(.sp-dialog [data-slot='dialog-title']),:global(.sp-qr-dialog [data-slot='dialog-title']) { padding-right:32px; }
.sp-qr-preview { width:100%; max-height:min(400px,60dvh); object-fit:contain; }
.sp-dialog-body { min-height:0; overflow:auto; overscroll-behavior:contain; }
.sp-detail { margin:0; display:grid; }
.sp-detail>div { display:grid; grid-template-columns:88px minmax(0,1fr); align-items:baseline; gap:8px; padding:8px 0; border-bottom:1px solid var(--border); }
.sp-detail dt { color:var(--muted-foreground); font-size:12px; } .sp-detail dd { margin:0; overflow-wrap:anywhere; white-space:pre-wrap; font-size:13px; }
.sp-form { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; padding:0; border:0; }
.sp-form-full { grid-column:1/-1; }
.sp-form .sp-check { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:4px 8px; border:1px solid var(--border); border-radius:8px; cursor:pointer; }
@container support-cards (max-width:760px) {
  .sp-page :deep(.table-scroll),.sp-records :deep([data-slot='table-container']) { border:0; overflow:visible; }
  .sp-records { background:transparent; } .sp-records :deep(table),.sp-records :deep(tbody) { display:grid; }
  .sp-records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .sp-records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .sp-records .sp-card { display:grid; grid-template-columns:minmax(0,1fr) auto; align-content:start; gap:8px; padding:12px; background:var(--card); border:1px solid var(--border); border-radius:8px; }
  .sp-records :deep(td) { padding:0; }
  .sp-status { grid-row:1; grid-column:2; text-align:right; }
  .sp-contact,.sp-wecom,.sp-command { grid-column:1/-1; }
  .sp-mobile-label { display:inline; color:var(--muted-foreground); font-size:12px; }
  .sp-page :deep(.pages) { justify-content:flex-start; }
}
@container support-cards (max-width:480px) { .sp-records :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@media(max-width:480px) { .sp-form { grid-template-columns:minmax(0,1fr); } :global(.sp-dialog),:global(.sp-qr-dialog) { gap:8px; padding:12px; } }
@media(pointer:coarse) {
  .sp-page :deep([data-slot='button']),:global(.sp-dialog [data-slot='button']),:global(.sp-qr-dialog [data-slot='button']) { min-width:44px; min-height:44px; }
  .sp-filters :deep(input),.sp-form :deep([data-slot='input']),.sp-form .sp-check { min-height:44px; }
}
</style>
