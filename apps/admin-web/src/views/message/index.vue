<template>
  <div class="mg-page">
    <Alert v-if="pending"><AlertTitle>上次推送结果待核对</AlertTitle><AlertDescription>
      <p>「{{ pending.title }}」（{{ formatDateTime(pending.startedAt) }}）可能已创建部分记录。请按标题和时间核对；原请求可能仍在处理。</p>
      <Button size="xs" variant="link" @click="tableRef?.reload()">刷新列表</Button><Button size="xs" variant="link" @click="clearPendingAfterReview">已核对，解除限制</Button>
    </AlertDescription></Alert>
    <p v-if="pageNotice" role="status">{{ pageNotice }}</p>
    <SimplePagedList ref="tableRef" :fetcher="messageApi.list" :query="query">
      <template #toolbar="{ reload }">
        <Card class="mg-toolbar">
          <div><h2>消息记录</h2><p>按接收人核对内容与阅读状态。</p></div>
          <div class="mg-tools"><label>消息类型<NativeSelect v-model="query.type" aria-label="消息类型" @update:model-value="reload"><NativeSelectOption value="">全部消息类型</NativeSelectOption><NativeSelectOption v-for="(item, key) in MESSAGE_TYPE" :key="key" :value="key">{{ item.label }}</NativeSelectOption></NativeSelect></label>
            <Button size="xs" v-if="auth.hasPerm('message:push')" :disabled="!!pending" @click="onPush">推送消息</Button>
          </div>
        </Card>
      </template>
      <template #default="{ rows }">
        <Table class="mg-table"><TableHeader><TableRow><TableHead>记录号</TableHead><TableHead>接收人</TableHead><TableHead>类型</TableHead><TableHead>标题</TableHead><TableHead>摘要</TableHead><TableHead>阅读状态</TableHead><TableHead>时间</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell>{{ row.id }}</TableCell><TableCell>{{ row.recipient_name || '—' }}<small class="mg-identity">{{ recipientIdentity(row) }}</small></TableCell><TableCell>{{ row.type_display }}</TableCell><TableCell>{{ row.title }}</TableCell><TableCell>{{ row.preview || '—' }}</TableCell><TableCell><Badge :variant="row.is_read ? 'secondary' : 'outline'">{{ row.is_read ? '已读' : '未读' }}</Badge></TableCell><TableCell>{{ formatDateTime(row.created_at) }}</TableCell><TableCell><Button size="xs" variant="link" @click="detailMessage = row; detailVisible = true">详情</Button></TableCell></TableRow></TableBody></Table>
        <ul class="mg-list" aria-label="站内消息记录">
          <li v-for="row in rows" :key="row.id" class="mg-card">
            <div class="mg-card-head"><strong>{{ row.title }}</strong><Badge :variant="row.is_read ? 'secondary' : 'outline'">{{ row.is_read ? '已读' : '未读' }}</Badge></div>
            <p v-if="row.preview" class="mg-summary">{{ row.preview }}</p>
            <dl class="mg-meta">
              <div><dt>接收人</dt><dd>{{ row.recipient_name || '—' }}<small class="mg-identity">{{ recipientIdentity(row) }}</small></dd></div>
              <div><dt>类型</dt><dd>{{ row.type_display }}</dd></div>
              <div><dt>时间</dt><dd><time :datetime="row.created_at">{{ formatDateTime(row.created_at) }}</time></dd></div>
            </dl>
            <div class="mg-card-footer"><span>记录 #{{ row.id }}</span><Button size="xs" variant="outline" @click="detailMessage = row; detailVisible = true">详情</Button></div>
          </li>
        </ul>
      </template>
    </SimplePagedList>
  </div>

  <Dialog :open="dialogVisible" @update:open="value => { if (!saving && !reviewVisible) dialogVisible = value }"><DialogContent :show-close-button="!saving && !reviewVisible" class="mg-push-dialog" @interact-outside.prevent><DialogHeader><DialogTitle>推送站内消息</DialogTitle><DialogDescription>选择接收范围并核对消息内容。</DialogDescription></DialogHeader>
    <Alert v-if="sendUncertain"><AlertTitle>发送结果未确认</AlertTitle><AlertDescription>可能已有部分记录生成。请关闭后按标题和时间核对列表；原请求可能仍在处理，请勿立即重复发送。</AlertDescription></Alert>
    <div class="mg-form">
      <fieldset><legend id="mg-audience-label">推送范围</legend>
        <RadioGroup v-model="form.audience" class="mg-audiences" aria-labelledby="mg-audience-label" :disabled="saving || reviewVisible">
          <label v-for="item in audiences" :key="item.value" :for="'mg-audience-' + item.value"><RadioGroupItem :id="'mg-audience-' + item.value" :value="item.value" />{{ item.label }}</label>
        </RadioGroup>
      </fieldset>
      <Alert v-if="form.audience !== 'CUSTOM'"><AlertDescription>{{ form.audience === 'ALL' ? '包含启用的后台旧账号，按发送时旧用户状态计算。' : '按发送时启用的旧用户角色计算。' }}有些旧用户可能未关联业务账号；创建记录不代表已送达或阅读。</AlertDescription></Alert>
      <div v-if="form.audience === 'CUSTOM'" class="mg-field"><label>搜索接收人<Input v-model="recipientKeyword" placeholder="搜索编号、账号或昵称" :disabled="saving || reviewVisible" @keyup.enter="searchRecipients(recipientKeyword)" /></label><Button size="xs" variant="outline" :disabled="saving || reviewVisible || recipientsLoading" @click="searchRecipients(recipientKeyword)">{{ recipientsLoading ? '搜索中…' : '搜索' }}</Button><span v-if="recipientError" class="mg-error">接收人读取失败，请重试</span><p class="mg-hint">仅可选择有有效身份映射的账号；同名账号请核对编号。已选 {{ recipientIds.length }} 人。</p><div class="mg-options"><label v-for="account in recipientOptions" :key="account.id" class="mg-option"><Checkbox :model-value="recipientIds.includes(account.id)" :disabled="account.recipient_mapped !== true || saving || reviewVisible" @update:model-value="value => toggleRecipient(account.id, value === true)" /><span>{{ account.nickname || account.username }} · {{ account.account_no }}（{{ account.role_display }}）{{ account.recipient_mapped === true ? '' : ' · 身份待核对' }}</span></label></div></div>
      <label>消息类型<NativeSelect v-model="form.type" :disabled="saving || reviewVisible"><NativeSelectOption v-for="(item, key) in MESSAGE_TYPE" :key="key" :value="key">{{ item.label }}</NativeSelectOption></NativeSelect></label>
      <label>标题<Input v-model="form.title" maxlength="100" :disabled="saving || reviewVisible" /></label><label>摘要<Input v-model="form.preview" maxlength="255" :disabled="saving || reviewVisible" /></label><label>详情<Textarea v-model="form.detail" :rows="4" :disabled="saving || reviewVisible" /></label>
      <p v-if="pushNotice" role="alert">{{ pushNotice }}</p>
    </div><DialogFooter><Button size="xs" variant="outline" :disabled="saving" @click="dialogVisible = false">取消</Button><Button size="xs" :disabled="saving || sendUncertain" @click="prepareSend">核对并推送</Button></DialogFooter></DialogContent></Dialog>
  <Dialog :open="reviewVisible" @update:open="value => { if (!saving) reviewVisible = value }"><DialogContent :show-close-button="!saving" class="mg-review-dialog" @interact-outside.prevent><DialogHeader><DialogTitle>核对站内消息</DialogTitle><DialogDescription>请确认接收范围和消息内容。</DialogDescription></DialogHeader>
    <div v-if="review" class="mg-review-body"><p><strong>接收范围：</strong>{{ review.audienceLabel }}</p><ul v-if="review.recipients.length" class="mg-recipients"><li v-for="item in review.recipients" :key="item.id">{{ item.label }}</li></ul><p><strong>类型：</strong>{{ review.typeLabel }}</p><p><strong>标题：</strong>{{ review.payload.title }}</p><p><strong>摘要：</strong>{{ review.payload.preview || '—' }}</p><p class="mg-detail"><strong>详情：</strong>{{ review.payload.detail || '—' }}</p><p class="mg-hint">提交创建站内消息记录；数量以服务端结果为准，并非送达或阅读回执。</p></div>
    <DialogFooter><Button size="xs" variant="outline" :disabled="saving" @click="reviewVisible = false">返回修改</Button><Button size="xs" :disabled="saving" @click="confirmSend">{{ saving ? '推送中…' : '确认推送' }}</Button></DialogFooter></DialogContent></Dialog>
  <Dialog v-model:open="detailVisible"><DialogContent class="mg-detail-dialog"><DialogHeader><DialogTitle>消息详情</DialogTitle><DialogDescription>查看接收人、内容及阅读状态。</DialogDescription></DialogHeader>
    <dl v-if="detailMessage" class="mg-detail-list"><div><dt>记录号</dt><dd>#{{ detailMessage.id }}</dd></div><div><dt>消息类型</dt><dd>{{ detailMessage.type_display }}</dd></div><div><dt>接收人</dt><dd>{{ detailMessage.recipient_name || '—' }}</dd></div><div><dt>账号</dt><dd>{{ recipientIdentity(detailMessage) }}</dd></div><div><dt>标题</dt><dd>{{ detailMessage.title }}</dd></div><div><dt>摘要</dt><dd>{{ detailMessage.preview || '—' }}</dd></div><div><dt>内容</dt><dd class="mg-detail">{{ detailMessage.detail || '—' }}</dd></div><div><dt>阅读状态</dt><dd>{{ detailMessage.is_read ? '已读' : '未读' }}</dd></div><div><dt>发送时间</dt><dd>{{ formatDateTime(detailMessage.created_at) }}</dd></div></dl>
  </DialogContent></Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import SimplePagedList from '@/components/SimplePagedList.vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { Checkbox } from '@xa/shadcn-vue/checkbox'
import { Card } from '@xa/shadcn-vue/card'
import { RadioGroup, RadioGroupItem } from '@xa/shadcn-vue/radio-group'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Badge } from '@xa/shadcn-vue/badge'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { messageApi } from '@/api/modules'
import { request } from '@/api/request'
import { MESSAGE_TYPE } from '@/utils/dict'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const pageNotice = ref('')
const tableRef = ref()
const query = reactive<Record<string, any>>({ type: '' })

const dialogVisible = ref(false)
const saving = ref(false)
const recipientIds = ref<number[]>([])
const recipientKeyword = ref(''), pushNotice = ref('')
const audiences = [{value:'ALL',label:'全部启用旧用户'},{value:'CUSTOMER',label:'老板端'},{value:'PROVIDER',label:'陪玩端'},{value:'CUSTOM',label:'指定用户'}]
function toggleRecipient(id: number, checked: boolean) { recipientIds.value = checked ? [...new Set([...recipientIds.value, id])] : recipientIds.value.filter(item => item !== id) }
const recipientOptions = ref<any[]>([])
const recipientsLoading = ref(false)
const recipientError = ref(false)
const lastKeyword = ref('')
const detailMessage = ref<any>(null)
const detailVisible = ref(false)
const reviewVisible = ref(false)
const sendUncertain = ref(false)
const review = ref<any>(null)
const pending = ref<{ title: string; startedAt: string } | null>(null)
let ownerKey = ''
let searchSequence = 0
const form = reactive<any>({})
function markerKey() { const id = auth.profile?.id; return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_message_push_review_${id}` : '' }
function restorePending() {
  const key = markerKey(); if (key === ownerKey) return
  ownerKey = key; pending.value = null
  if (key) try { const item = JSON.parse(sessionStorage.getItem(key) || 'null'); if (typeof item?.title === 'string' && typeof item?.startedAt === 'string') pending.value = item } catch { /* In-page guard remains if storage is unavailable. */ }
}
function setPending(item: { title: string; startedAt: string }) { pending.value = item; const key = markerKey(); if (key) try { sessionStorage.setItem(key, JSON.stringify(item)) } catch { /* In-page guard remains. */ } }
function clearPending() { const key = markerKey(); if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }; pending.value = null }
async function clearPendingAfterReview() {
  const item = pending.value, revision = auth.sessionRevision
  if (!item || saving.value) return
  if (!await askConfirm({ title: '解除待核对限制', description: `已核对「${item.title}」的记录？原请求仍可能处理；解除后请自行判断是否需要再次发送。`, confirmText: '已核对，解除', cancelText: '继续核对' })) return
  if (revision === auth.sessionRevision && pending.value === item) { clearPending(); sendUncertain.value = false }
}
function recipientIdentity(row: any) { return row.recipient_no ? `${row.recipient_no} · ${row.recipient_username || '—'}` : row.recipient_username ? `旧账号 · ${row.recipient_username}` : '身份待核对' }
watch(dialogVisible, visible => { if (!visible) { ++searchSequence; recipientsLoading.value = false } })
watch(() => auth.sessionRevision, () => {
  ++searchSequence; dialogVisible.value = false; reviewVisible.value = false; detailVisible.value = false
  recipientOptions.value = []; recipientIds.value = []; review.value = null; saving.value = false
})
watch([() => auth.sessionRevision, () => auth.profile?.id], restorePending, { immediate: true, flush: 'sync' })

function blank() {
  return { audience: 'CUSTOM', type: 'SYSTEM', title: '', preview: '', detail: '' }
}

function onPush() {
  pageNotice.value = ''
  ++searchSequence
  Object.assign(form, blank())
  recipientIds.value = []
  recipientOptions.value = []
  recipientError.value = false; lastKeyword.value = ''; recipientKeyword.value = ''; pushNotice.value = ''; sendUncertain.value = false; review.value = null
  dialogVisible.value = true
}

function prepareSend() {
  if (saving.value || sendUncertain.value || pending.value || !auth.hasPerm('message:push')) return
  if (!form.title.trim()) { pushNotice.value = '请填写标题'; return }
  if (form.audience === 'CUSTOM' && !recipientIds.value.length) { pushNotice.value = '请选择接收人'; return }
  const recipients = form.audience === 'CUSTOM' ? recipientIds.value.map(id => recipientOptions.value.find(item => item.id === id)) : []
  if (recipients.some(item => !item || item.recipient_mapped !== true)) { pushNotice.value = '接收人身份待核对，请重新选择'; return }
  const audienceLabel = form.audience === 'ALL' ? '全部启用旧用户（包括后台账号）' : form.audience === 'CUSTOMER' ? '启用的旧老板账号' : form.audience === 'PROVIDER' ? '启用的旧陪玩账号' : `${recipients.length} 个所选账号`
  pushNotice.value = ''
  review.value = {
    revision: auth.sessionRevision, audienceLabel, typeLabel: MESSAGE_TYPE[form.type]?.label || form.type,
    recipients: recipients.map(item => ({ id: item.id, label: `${item.nickname || item.username} · ${item.account_no} · ${item.username}（${item.role_display}）` })),
    payload: { broadcast: form.audience === 'ALL', audience: form.audience,
      recipient_account_ids: form.audience === 'CUSTOM' ? [...recipientIds.value] : [], type: form.type,
      title: form.title.trim(), preview: form.preview.trim(), detail: form.detail },
  }
  reviewVisible.value = true
}

async function confirmSend() {
  const snapshot = review.value
  if (!snapshot || saving.value || sendUncertain.value || pending.value || snapshot.revision !== auth.sessionRevision || !auth.hasPerm('message:push')) return
  saving.value = true
  setPending({ title: snapshot.payload.title, startedAt: new Date().toISOString() })
  try {
    const res = await request({ url: '/messages/push/', method: 'post', data: snapshot.payload, silent: true }) as any
    if (snapshot.revision !== auth.sessionRevision) return
    clearPending()
    if (snapshot.recipients.length && typeof res.data?.count === 'number' && res.data.count < snapshot.recipients.length) pageNotice.value = `已创建 ${res.data.count} 条记录，少于所选 ${snapshot.recipients.length} 个账号，请核对列表`
    else pageNotice.value = typeof res.data?.count === 'number' ? `已创建 ${res.data.count} 条站内消息记录` : '请求已完成，请在列表核对记录'
    reviewVisible.value = false
    dialogVisible.value = false
    tableRef.value?.reload()
  } catch (error: any) {
    if (snapshot.revision !== auth.sessionRevision) return
    reviewVisible.value = false
    if (!error?.response || error.response.status >= 500) sendUncertain.value = true
    else { clearPending(); pushNotice.value = error.response.data?.msg || '推送失败，请检查接收人后重试' }
  } finally {
    if (snapshot.revision === auth.sessionRevision) saving.value = false
  }
}

async function searchRecipients(keyword: string) {
  const sequence = ++searchSequence
  const revision = auth.sessionRevision
  lastKeyword.value = keyword; recipientError.value = false
  if (!keyword.trim()) { recipientOptions.value = recipientOptions.value.filter(account => recipientIds.value.includes(account.id)); recipientsLoading.value = false; return }
  recipientsLoading.value = true
  try {
    const result = await request({ url: '/options/message-recipients/', params: { keyword, page_size: 30 }, silent: true })
    if (sequence === searchSequence && revision === auth.sessionRevision && dialogVisible.value) {
      const selected = recipientOptions.value.filter(account => recipientIds.value.includes(account.id))
      recipientOptions.value = [...new Map([...selected, ...result.data.list].map(account => [account.id, account])).values()]
    }
  } catch { if (sequence === searchSequence && revision === auth.sessionRevision && dialogVisible.value) recipientError.value = true }
  finally { if (sequence === searchSequence && revision === auth.sessionRevision) recipientsLoading.value = false }
}
</script>

<style scoped>
.mg-page { display:grid; gap:12px; min-width:0; container:messages / inline-size; }
.mg-toolbar { display:flex; flex-direction:row; align-items:center; justify-content:space-between; flex-wrap:wrap; gap:8px 16px; padding:12px 16px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.mg-toolbar h2 { font-size:16px; font-weight:600; }
.mg-toolbar p,.mg-tools label,.mg-identity,.mg-hint,.mg-card-footer,.mg-meta dt { font-size:12px; color:var(--muted-foreground); }
.mg-toolbar p { margin-top:4px; }
.mg-tools { display:flex; align-items:end; flex-wrap:wrap; gap:8px; }
.mg-tools label,.mg-form label { display:grid; min-width:0; gap:4px; }
.mg-tools :deep(select),.mg-form :deep(select),.mg-form :deep(input[data-slot="input"]) { height:28px; min-height:28px; padding-block:4px; font-size:12px; }
.mg-identity { display:block; margin-top:4px; overflow-wrap:anywhere; }
.mg-page :deep(.mg-table td),.mg-page :deep(.mg-table th) { padding:8px; vertical-align:top; }
.mg-page :deep(.mg-table td) { font-size:13px; white-space:normal; overflow-wrap:anywhere; }
.mg-page :deep(.mg-table th) { background:var(--muted); font-size:12px; }
.mg-list { display:none; }
.mg-card { display:grid; align-content:start; min-width:0; gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
.mg-card-head,.mg-card-footer { display:flex; justify-content:space-between; align-items:start; gap:8px; }
.mg-card-head strong,.mg-summary,.mg-detail,.mg-recipients,.mg-review-body { overflow-wrap:anywhere; }
.mg-summary,.mg-detail { white-space:pre-wrap; }
.mg-meta,.mg-detail-list { display:grid; gap:4px; }
.mg-meta { border-top:1px solid var(--border); padding-top:8px; font-size:13px; }
.mg-meta div,.mg-detail-list div { display:grid; grid-template-columns:48px minmax(0,1fr); gap:8px; }
.mg-meta dd,.mg-detail-list dd { margin:0; overflow-wrap:anywhere; }
.mg-card-footer { align-items:center; }
.mg-error,.mg-form [role="alert"]:not([data-slot]) { color:var(--destructive); font-size:12px; }
.mg-detail-list { gap:8px; margin:0; font-size:13px; }
.mg-detail-list div { grid-template-columns:72px minmax(0,1fr); }
.mg-detail-list dt { color:var(--muted-foreground); }
:global(.mg-detail-dialog) { top:0; right:0; left:auto; translate:none; transform:none; width:min(480px,100vw); max-width:100vw; height:100dvh; max-height:100dvh; overflow:auto; align-content:start; border-radius:0; }
:global(.mg-push-dialog),:global(.mg-review-dialog) { max-width:min(680px,calc(100vw - 24px)); max-height:calc(100dvh - 24px); overflow:auto; }
.mg-detail-dialog :deep([data-slot="dialog-header"]),.mg-push-dialog :deep([data-slot="dialog-header"]),.mg-review-dialog :deep([data-slot="dialog-header"]) { padding-right:32px; }
.mg-form,.mg-field,.mg-review-body { display:grid; min-width:0; gap:8px; font-size:13px; }
.mg-field { grid-template-columns:minmax(0,1fr) auto; align-items:end; }
.mg-field>p,.mg-field>span,.mg-options { grid-column:1/-1; }
.mg-form fieldset { border:0; margin:0; padding:0; min-width:0; }
.mg-audiences { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:4px; margin-top:4px; }
.mg-audiences label,.mg-form label.mg-option { display:flex; align-items:center; gap:8px; padding:4px 8px; border:1px solid var(--border); border-radius:4px; cursor:pointer; overflow-wrap:anywhere; }
.mg-audiences label:has([aria-checked="true"]),.mg-option:has([aria-checked="true"]) { border-color:var(--ring); background:var(--secondary); }
.mg-options { display:grid; gap:4px; max-height:160px; overflow:auto; }
.mg-recipients { max-height:160px; overflow-y:auto; padding-left:16px; }
@container messages (max-width:860px) {
  .mg-page :deep(.table-scroll) { border:0; border-radius:0; overflow:visible; }
  .mg-page :deep([data-slot="table-container"]) { display:none; }
  .mg-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .mg-page :deep(.pages) { justify-content:flex-start; }
}
@container messages (max-width:520px) {
  .mg-list { grid-template-columns:minmax(0,1fr); }
  .mg-tools { flex-basis:100%; }
}
@media(pointer:coarse) {
  .mg-page :deep([data-slot="button"]),.mg-tools :deep(select),.mg-form :deep(select),.mg-form :deep(input[data-slot="input"]),.mg-audiences label,.mg-form label.mg-option { min-width:44px; min-height:44px; }
}
</style>
