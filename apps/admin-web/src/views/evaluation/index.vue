<template>
  <div class="ev-page">
    <Card class="ev-header"><h2>服务评价</h2><p>按订单、老板或陪玩查找评价，核对评分与回复。</p></Card>
    <Alert v-if="query.exclude_direct_test === '1'"><AlertDescription>当前不含直接支付测试单评价 <Button size="xs" variant="outline" @click="router.replace({ path: '/evaluations', query: { only_unreplied: query.only_unreplied || undefined } })">查看全部订单评价</Button></AlertDescription></Alert>
    <Alert v-if="deleteReview && !deleteVisible"><AlertTitle>评价删除结果待核对</AlertTitle><AlertDescription>
      <p>订单 {{ deleteReview.orderNo }} 的请求可能仍在处理；读取结果仅是当前快照。核对前暂停删除。</p>
      <p v-if="deleteReviewNotice" role="status">{{ deleteReviewNotice }}</p>
      <Button size="xs" variant="outline" :disabled="deleteReviewLoading" @click="checkDeleteReview">{{ deleteReviewLoading ? '读取中…' : '读取当前状态' }}</Button>
      <Button size="xs" v-if="deleteReviewChecked" variant="outline" :disabled="deleteReviewLoading" @click="finishDeleteReview">已核对，解除限制</Button>
    </AlertDescription></Alert>
    <p v-if="notice && !replyVisible && !deleteVisible" role="status">{{ notice }}</p>

    <SimplePagedList ref="tableRef" :fetcher="evaluationApi.list" :query="query">
      <template #toolbar="{ reload }">
        <div class="ev-filters">
        <label class="ev-search">查找评价<Input v-model="query.keyword" placeholder="订单号 / 老板 / 陪玩" aria-label="订单号、老板或陪玩" @keyup.enter="reload" @update:model-value="value => { if (value === '') reload() }" /></label>
        <label>评分<NativeSelect v-model="query.score" aria-label="评分" @update:model-value="reload"><NativeSelectOption value="">全部评分</NativeSelectOption><NativeSelectOption v-for="n in 5" :key="n" :value="String(n)">{{ n }} 星</NativeSelectOption></NativeSelect></label>
        <label>回复状态<NativeSelect v-model="query.only_unreplied" aria-label="回复状态" @update:model-value="reload"><NativeSelectOption value="">全部回复状态</NativeSelectOption><NativeSelectOption value="1">未回复</NativeSelectOption></NativeSelect></label>
        <Button size="xs" @click="reload">查询</Button>
        <details class="ev-advanced">
          <summary>更多筛选<span v-if="advancedCount">（{{ advancedCount }}）</span></summary>
          <div class="ev-advanced-fields">
        <SuggestionInput v-model="query.order_no" :fetch-suggestions="querySuggest('order_no')" placeholder="订单号" label="按订单号筛选" @select="reload" @keyup.enter="reload" @clear="reload" />
        <SuggestionInput v-model="query.customer_username" :fetch-suggestions="querySuggest('customer_username')" placeholder="老板账号" label="按老板账号筛选" @select="reload" @keyup.enter="reload" @clear="reload" />
        <SuggestionInput v-model="query.customer_nickname" :fetch-suggestions="querySuggest('customer_nickname')" placeholder="老板昵称" label="按老板昵称筛选" @select="reload" @keyup.enter="reload" @clear="reload" />
        <SuggestionInput v-model="query.provider_username" :fetch-suggestions="querySuggest('provider_username')" placeholder="陪玩账号" label="按陪玩账号筛选" @select="reload" @keyup.enter="reload" @clear="reload" />
        <SuggestionInput v-model="query.provider_nickname" :fetch-suggestions="querySuggest('provider_nickname')" placeholder="陪玩昵称" label="按陪玩昵称筛选" @select="reload" @keyup.enter="reload" @clear="reload" />
          </div>
        </details>
        </div>
      </template>

      <template #default="{ rows }">
        <Table class="ev-table"><TableHeader><TableRow><TableHead>订单号</TableHead><TableHead>老板</TableHead><TableHead>陪玩</TableHead><TableHead>服务</TableHead><TableHead>评分</TableHead><TableHead>三维分</TableHead><TableHead>评价内容</TableHead><TableHead>回复</TableHead><TableHead>评价时间</TableHead><TableHead>操作</TableHead></TableRow></TableHeader><TableBody><TableRow v-for="row in rows" :key="row.id"><TableCell>{{ row.order_no }}</TableCell><TableCell>{{ row.customer_name }}</TableCell><TableCell>{{ row.provider_name || '—' }}</TableCell><TableCell>{{ row.service_name }}</TableCell><TableCell>{{ row.score }} / 5</TableCell><TableCell>技 {{ row.skill_score }} · 态 {{ row.attitude_score }} · 沟 {{ row.communication_score }}</TableCell><TableCell>{{ row.content }}</TableCell><TableCell><Badge :variant="row.reply_content ? 'secondary' : 'outline'">{{ row.reply_content ? '已回复' : '未回复' }}</Badge></TableCell><TableCell>{{ formatDateTime(row.created_at) }}</TableCell><TableCell><Button size="xs" variant="link" @click="onDetail(row)">详情</Button><Button size="xs" v-if="auth.hasPerm('evaluation:reply')" variant="link" @click="onReply(row)">{{ row.reply_content ? '修改回复' : '回复' }}</Button><Button size="xs" v-if="auth.hasPerm('evaluation:delete')" class="ev-delete" variant="link" :disabled="!!deleteReview" @click="onDelete(row)">删除</Button></TableCell></TableRow></TableBody></Table>
        <div class="ev-list" role="list" aria-label="评价列表">
          <article v-for="row in rows" :key="row.id" class="ev-card" role="listitem">
            <div class="ev-card-head"><strong>{{ row.service_name || '服务待核对' }}</strong><Badge :variant="row.reply_content ? 'secondary' : 'outline'">{{ row.reply_content ? '已回复' : '未回复' }}</Badge></div>
            <p class="ev-score">{{ row.score }} 分 <span>· 技术 {{ row.skill_score }} · 态度 {{ row.attitude_score }} · 沟通 {{ row.communication_score }}</span></p>
            <p class="ev-content">{{ row.content || '未填写评价内容' }}</p>
            <dl class="ev-facts">
              <div><dt>老板</dt><dd>{{ row.customer_name || '—' }}</dd></div>
              <div><dt>陪玩</dt><dd>{{ row.provider_name || '—' }}</dd></div>
              <div class="ev-wide"><dt>订单</dt><dd>{{ row.order_no }}</dd></div>
              <div class="ev-wide"><dt>时间</dt><dd>{{ formatDateTime(row.created_at) }}</dd></div>
            </dl>
            <div class="ev-actions">
              <Button size="xs" variant="outline" @click="onDetail(row)">详情</Button>
              <Button size="xs" v-if="auth.hasPerm('evaluation:reply')" variant="outline" @click="onReply(row)">{{ row.reply_content ? '修改回复' : '回复' }}</Button>
              <Button size="xs" v-if="auth.hasPerm('evaluation:delete')" class="ev-delete" variant="ghost" :disabled="!!deleteReview" @click="onDelete(row)">删除</Button>
            </div>
          </article>
        </div>
      </template>

    </SimplePagedList>
  </div>

  <Dialog v-model:open="detailVisible"><DialogContent class="ev-dialog"><DialogHeader><DialogTitle>评价详情</DialogTitle><DialogDescription>查看这位陪玩的评价及回复。</DialogDescription></DialogHeader><dl v-if="current" class="ev-detail"><div v-for="item in detailItems" :key="item.label"><dt>{{ item.label }}</dt><dd>{{ item.value }}</dd></div></dl></DialogContent></Dialog>

  <Dialog :open="replyVisible" @update:open="value => { if (!replying && !replyRefreshing) replyVisible = value }"><DialogContent class="ev-dialog" :show-close-button="!replying && !replyRefreshing" @interact-outside="event => { if (replying || replyRefreshing) event.preventDefault() }" @escape-key-down="event => { if (replying || replyRefreshing) event.preventDefault() }"><DialogHeader><DialogTitle>{{ replyTarget?.reply_content ? '修改回复' : '回复评价' }}</DialogTitle><DialogDescription>请核对评价和回复内容。</DialogDescription></DialogHeader>
    <div v-if="replyTarget" class="ev-reply-context">
      <strong>{{ replyTarget.service_name || '服务待核对' }} · {{ replyTarget.score }} 分</strong>
      <p>订单 {{ replyTarget.order_no }}</p>
      <p>{{ replyTarget.content || '未填写评价内容' }}</p>
      <p v-if="replyTarget.reply_content">当前{{ replyTarget.reply_author === 'PROVIDER' ? '陪玩' : replyTarget.reply_author === 'STAFF' ? '客服' : '来源未记录的' }}回复：{{ replyTarget.reply_content }}</p>
    </div>
    <Alert v-if="replyTarget?.reply_content"><AlertDescription>保存后将覆盖当前回复，请先核对内容。</AlertDescription></Alert>
    <Alert v-if="replyConflict"><AlertTitle>回复已变化，草稿已保留</AlertTitle><AlertDescription>读取最新回复并核对后，再决定是否保存。<Button size="xs" variant="link" :disabled="replyRefreshing" @click="refreshReply">读取最新回复</Button></AlertDescription></Alert>
    <Alert v-if="replyUnknown"><AlertTitle>回复结果未确认</AlertTitle><AlertDescription>请求可能仍在处理。请关闭后刷新列表，核对当前回复再操作。</AlertDescription></Alert>
    <Alert v-if="notice" variant="destructive"><AlertDescription>{{ notice }}</AlertDescription></Alert>
    <label class="ev-reply-label" for="evaluation-reply-text">回复内容</label>
    <Textarea id="evaluation-reply-text" v-model="replyText" rows="4" maxlength="1000" placeholder="填写回复内容" :disabled="replying || replyUnknown || replyRefreshing" />
    <DialogFooter><Button size="xs" variant="outline" :disabled="replying || replyRefreshing" @click="replyVisible = false">关闭</Button><Button size="xs" :disabled="replying || replyUnknown || replyConflict || replyRefreshing" @click="submitReply">{{ replying ? '保存中…' : replyTarget?.reply_content ? '保存回复' : '发布回复' }}</Button></DialogFooter></DialogContent></Dialog>
  <Dialog :open="deleteVisible" @update:open="value => { if (!value) closeDelete(); else deleteVisible = true }"><DialogContent class="ev-dialog" :show-close-button="!deleteBusy" @interact-outside="event => { if (deleteBusy) event.preventDefault() }" @escape-key-down="event => { if (deleteBusy) event.preventDefault() }"><DialogHeader><DialogTitle>删除评价</DialogTitle><DialogDescription>核对评价及回复后再删除。</DialogDescription></DialogHeader>
    <div v-if="deleteTarget" class="ev-reply-context">
      <strong>{{ deleteTarget.service_name || '服务待核对' }} · {{ deleteTarget.score }} 分</strong>
      <p>订单 {{ deleteTarget.order_no }}</p>
      <p>评价：{{ deleteTarget.content || '未填写评价内容' }}</p>
      <p>回复：{{ deleteTarget.reply_content || '尚未回复' }}</p>
    </div>
    <Alert><AlertDescription>删除评价及回复，并回退陪玩评分统计。</AlertDescription></Alert>
    <Alert v-if="deleteConflict"><AlertDescription>回复已变化，请重新核对评价。<Button size="xs" variant="link" :disabled="deleteBusy" @click="refreshDelete">读取最新评价</Button></AlertDescription></Alert>
    <Alert v-if="deleteNotice || notice" variant="destructive"><AlertDescription>{{ deleteNotice || notice }}</AlertDescription></Alert>
    <DialogFooter><Button size="xs" variant="outline" :disabled="deleteBusy" @click="closeDelete()">关闭</Button><Button size="xs" variant="destructive" :disabled="deleteBusy || deleteConflict || deleteUnavailable" @click="submitDelete">{{ deleteBusy ? '删除中…' : '确认删除' }}</Button></DialogFooter></DialogContent></Dialog>
  <ConfirmDialog v-model:open="confirmOpen" v-bind="confirmOptions" @confirm="confirmYes" @cancel="confirmNo" />
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import ConfirmDialog from '@/components/ConfirmDialog.vue'
import { useConfirmDialog } from '@/composables/useConfirmDialog'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Card } from '@xa/shadcn-vue/card'
import { Textarea } from '@xa/shadcn-vue/textarea'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import SuggestionInput from '@/components/SuggestionInput.vue'
import SimplePagedList from '@/components/SimplePagedList.vue'
import { Badge } from '@xa/shadcn-vue/badge'
import { Alert, AlertDescription, AlertTitle } from '@xa/shadcn-vue/alert'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@xa/shadcn-vue/dialog'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import { evaluationApi } from '@/api/modules'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { useRoute, useRouter } from 'vue-router'
import { adminWs } from '@/utils/ws'

const auth = useAuthStore()
const notice = ref('')
const feedback = { warning: (text: string) => { notice.value = text }, error: (text: string) => { notice.value = text }, success: (text: string) => { notice.value = text } }
const { open: confirmOpen, options: confirmOptions, ask: askConfirm, confirm: confirmYes, cancel: confirmNo } = useConfirmDialog()
const route = useRoute()
const router = useRouter()
const tableRef = ref()
const query = reactive<Record<string, any>>({
  keyword: '',
  order_no: '',
  customer_username: '',
  customer_nickname: '',
  provider_username: '',
  provider_nickname: '',
  score: '',
  only_unreplied: '',
  exclude_direct_test: '',
})
watch([() => route.query.only_unreplied, () => route.query.exclude_direct_test], ([value, excluded]) => {
  for (const key of Object.keys(query)) query[key] = ''
  query.only_unreplied = value === '1' ? '1' : ''
  query.exclude_direct_test = excluded === '1' ? '1' : ''
  tableRef.value?.reload()
}, { immediate: true })
const advancedCount = computed(() => ['order_no', 'customer_username', 'customer_nickname', 'provider_username', 'provider_nickname'].filter(key => Boolean(query[key])).length)

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询评价后，
 * 取返回记录对应字段值去重后作为下拉提示。
 */
function querySuggest(
  field: 'order_no' | 'customer_username' | 'customer_nickname' | 'provider_username' | 'provider_nickname',
) {
  return async (keyword: string): Promise<string[]> => {
    if (!keyword) {
      return []
    }
    try {
      const res = (await evaluationApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const e of res.data?.list || []) {
        const value = e[field] || ''
        if (value && !seen.has(value)) {
          seen.add(value)
          items.push({ value })
        }
      }
      return items.map(item => item.value)
    } catch {
      return []
    }
  }
}

const detailVisible = ref(false)
const current = ref<any>(null)
const detailItems = computed(() => { const row = current.value; if (!row) return []; return [
  { label:'订单号', value:row.order_no }, { label:'老板', value:row.customer_name }, { label:'陪玩', value:row.provider_name || '—' }, { label:'服务', value:row.service_name },
  { label:'评分', value:`${row.score} / 5` }, { label:'专业技能', value:`${row.skill_score} 分` }, { label:'服务态度', value:`${row.attitude_score} 分` }, { label:'沟通配合', value:`${row.communication_score} 分` }, { label:'综合均分', value:`${row.avg_score} 分` },
  { label:'评价内容', value:row.content || '—' }, { label:'匿名', value:row.is_anonymous ? '是' : '否' }, { label:'回复来源', value:row.reply_content ? (row.reply_author === 'PROVIDER' ? '陪玩' : row.reply_author === 'STAFF' ? '客服' : '未记录') : '—' }, { label:'回复', value:row.reply_content || '—' }, { label:'回复时间', value:row.replied_at ? formatDateTime(row.replied_at) : '—' }, { label:'评价时间', value:formatDateTime(row.created_at) },
] })

function onDetail(row: any) {
  current.value = row
  detailVisible.value = true
}

const replyVisible = ref(false)
const replyText = ref('')
const replying = ref(false)
const replyUnknown = ref(false)
const replyConflict = ref(false)
const replyRefreshing = ref(false)
const replyTarget = ref<any>(null)

function onReply(row: any) {
  notice.value = ''
  replyTarget.value = row
  replyText.value = row.reply_content || ''
  replyUnknown.value = false
  replyConflict.value = false
  replyVisible.value = true
}

async function refreshReply() {
  if (!replyTarget.value || replyRefreshing.value) return
  replyRefreshing.value = true
  try {
    const res = await evaluationApi.retrieve(replyTarget.value.id, true)
    replyTarget.value = res.data
    replyConflict.value = false
  } catch (error: any) {
    if (error?.code !== 'SESSION_CHANGED') feedback.error('读取失败，请检查网络或评价是否已删除')
  } finally { replyRefreshing.value = false }
}

async function submitReply() {
  if (replying.value || replyUnknown.value || replyConflict.value || replyRefreshing.value || !replyTarget.value) return
  const content = replyText.value.trim()
  if (!content) {
    feedback.warning('请输入回复内容')
    return
  }
  replying.value = true
  try {
    await evaluationApi.action(replyTarget.value.id, 'reply', { reply_content: content, reply_version: replyTarget.value.reply_version }, true)
    feedback.success('已回复')
    replyVisible.value = false
    tableRef.value.load()
  } catch (error: any) {
    const status = error?.response?.status ?? (typeof error?.code === 'number' ? error.code : undefined)
    if (error?.code === 'SESSION_CHANGED') return
    if (status === 409 && error?.response?.data?.errorCode === 'EVALUATION_REPLY_CONFLICT') replyConflict.value = true
    else if (!status || status >= 500) replyUnknown.value = true
    else feedback.error(error?.response?.data?.msg || error?.msg || '回复未保存，请检查内容和权限后重试')
  } finally {
    replying.value = false
  }
}

const deleteVisible = ref(false)
const deleteTarget = ref<any>(null)
const deleteBusy = ref(false)
const deleteConflict = ref(false)
const deleteUnavailable = ref(false)
const deleteNotice = ref('')
const deleteReview = ref<{ id: number; orderNo: string } | null>(null)
const deleteReviewChecked = ref(false)
const deleteReviewLoading = ref(false)
const deleteReviewNotice = ref('')
let deleteReviewOwner = ''
let pageDisposed = false
let deleteEpoch = 0
watch(() => auth.sessionRevision, () => {
  deleteEpoch++
  deleteVisible.value = false
  deleteTarget.value = null
  deleteBusy.value = deleteConflict.value = deleteUnavailable.value = false
  deleteNotice.value = ''
}, { flush: 'sync' })

function deleteReviewKey() {
  const id = auth.profile?.id
  return typeof id === 'number' && Number.isSafeInteger(id) && id > 0 ? `xa_evaluation_delete_review_${id}` : ''
}
function restoreDeleteReview() {
  const key = deleteReviewKey()
  if (key === deleteReviewOwner) return
  deleteReviewOwner = key
  deleteReview.value = null; deleteReviewChecked.value = false; deleteReviewLoading.value = false; deleteReviewNotice.value = ''
  if (!key) return
  try {
    const marker = JSON.parse(sessionStorage.getItem(key) || 'null')
    if (Number.isSafeInteger(marker?.id) && marker.id > 0 && typeof marker.orderNo === 'string') deleteReview.value = marker
  } catch { /* In-page guard remains when storage is unavailable. */ }
}
function beginDeleteReview(row: any) {
  const marker = { id: row.id as number, orderNo: row.order_no as string }
  deleteReview.value = marker; deleteReviewChecked.value = false; deleteReviewNotice.value = ''
  const key = deleteReviewKey()
  if (key) try { sessionStorage.setItem(key, JSON.stringify(marker)) } catch { /* In-page guard remains. */ }
}
function clearDeleteReview() {
  const key = deleteReviewKey()
  if (key) try { sessionStorage.removeItem(key) } catch { /* Storage may be unavailable. */ }
  deleteReview.value = null; deleteReviewChecked.value = false; deleteReviewNotice.value = ''
}
async function checkDeleteReview() {
  const marker = deleteReview.value, revision = auth.sessionRevision
  if (!marker || deleteReviewLoading.value || !auth.hasPerm('evaluation:view')) return
  deleteReviewLoading.value = true; deleteReviewChecked.value = false; deleteReviewNotice.value = ''
  try {
    const result = await evaluationApi.retrieve(marker.id, true)
    if (pageDisposed || revision !== auth.sessionRevision || deleteReview.value !== marker) return
    current.value = result.data; detailVisible.value = true
    deleteReviewChecked.value = true
    deleteReviewNotice.value = '评价当前仍存在；原请求可能仍在处理，请核对详情。'
  } catch (error: any) {
    if (pageDisposed || revision !== auth.sessionRevision || deleteReview.value !== marker) return
    if (error?.response?.status === 404) { deleteReviewChecked.value = true; deleteReviewNotice.value = '评价当前不存在；无法仅凭此判断由哪次操作删除。' }
    else deleteReviewNotice.value = '读取失败，请检查网络或权限后重试。'
  } finally { if (revision === auth.sessionRevision) deleteReviewLoading.value = false }
}
async function finishDeleteReview() {
  const marker = deleteReview.value, revision = auth.sessionRevision
  if (!marker || !deleteReviewChecked.value || deleteReviewLoading.value) return
  if (!await askConfirm({ title: '解除删除限制', description: '请确认已核对当前评价；原请求仍可能在处理。', confirmText: '已核对，解除限制', cancelText: '继续核对' })) return
  if (!pageDisposed && revision === auth.sessionRevision && deleteReview.value === marker && deleteReviewChecked.value) { clearDeleteReview(); tableRef.value?.load() }
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreDeleteReview, { immediate: true, flush: 'sync' })
let refreshTimer: ReturnType<typeof setTimeout> | null = null
let stopEvaluationUpdates: (() => void) | undefined
let stopReconnectUpdates: (() => void) | undefined
function refreshEvaluations() {
  if (refreshTimer) clearTimeout(refreshTimer)
  refreshTimer = setTimeout(() => {
    refreshTimer = null
    if (!pageDisposed && auth.hasPerm('evaluation:view')) tableRef.value?.load()
  }, 500)
}
onMounted(() => {
  stopEvaluationUpdates = adminWs.on('evaluation_status_update', refreshEvaluations)
  stopReconnectUpdates = adminWs.on('connected', refreshEvaluations)
  adminWs.connect()
})
onBeforeUnmount(() => {
  pageDisposed = true; deleteEpoch++
  if (refreshTimer) clearTimeout(refreshTimer)
  stopEvaluationUpdates?.(); stopReconnectUpdates?.()
})

function onDelete(row: any) {
  if (deleteBusy.value || deleteReview.value || !auth.hasPerm('evaluation:delete')) return
  deleteTarget.value = row
  notice.value = ''
  deleteConflict.value = deleteUnavailable.value = false
  deleteNotice.value = ''
  deleteVisible.value = true
}
function closeDelete(done?: () => void) {
  if (deleteBusy.value) return
  const reload = deleteConflict.value || deleteUnavailable.value
  if (done) done(); else deleteVisible.value = false
  if (reload) tableRef.value?.load()
}
async function refreshDelete() {
  if (!deleteTarget.value || deleteBusy.value) return
  const epoch = deleteEpoch
  deleteBusy.value = true
  try {
    const res = await evaluationApi.retrieve(deleteTarget.value.id, true)
    if (epoch !== deleteEpoch) return
    deleteTarget.value = res.data
    deleteConflict.value = false
    deleteNotice.value = ''
  } catch (error: any) {
    if (epoch !== deleteEpoch || error?.code === 'SESSION_CHANGED') return
    if (error?.response?.status === 404) { deleteUnavailable.value = true; deleteConflict.value = false; deleteNotice.value = '评价当前不存在，请关闭后刷新列表。' }
    else feedback.error('读取失败，请检查网络后重试')
  } finally { if (epoch === deleteEpoch) deleteBusy.value = false }
}
async function submitDelete() {
  if (!deleteTarget.value || deleteBusy.value || deleteReview.value || deleteConflict.value || deleteUnavailable.value || !auth.hasPerm('evaluation:delete')) return
  const epoch = deleteEpoch
  deleteBusy.value = true
  beginDeleteReview(deleteTarget.value)
  try {
    await evaluationApi.deleteAtVersion(deleteTarget.value.id, deleteTarget.value.reply_version)
    if (epoch !== deleteEpoch) return
    clearDeleteReview()
    feedback.success('评价已删除')
    deleteVisible.value = false
    tableRef.value?.load()
  } catch (error: any) {
    if (epoch !== deleteEpoch || error?.code === 'SESSION_CHANGED') return
    const status = error?.response?.status ?? (typeof error?.code === 'number' ? error.code : undefined)
    if (status === 409 && error?.response?.data?.errorCode === 'EVALUATION_REPLY_CONFLICT') { clearDeleteReview(); deleteConflict.value = true }
    else if (status === 404) { clearDeleteReview(); deleteUnavailable.value = true; deleteNotice.value = '评价当前不存在，请关闭后刷新列表。' }
    else if (!status || status === 408 || status >= 500) { deleteUnavailable.value = true; deleteNotice.value = '删除结果未确认，请关闭并核对评价；请求可能仍在处理。' }
    else { clearDeleteReview(); feedback.error(error?.response?.data?.msg || error?.msg || '删除未完成，请检查权限后重试') }
  } finally { if (epoch === deleteEpoch) deleteBusy.value = false }
}
</script>

<style scoped>
.ev-page { display:grid; gap:12px; min-width:0; container:evaluations / inline-size; }
.ev-header { padding:12px 16px; gap:4px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.ev-header h2 { font-size:16px; font-weight:600; }
.ev-header p,.ev-filters label { font-size:12px; color:var(--muted-foreground); }
.ev-filters { display:flex; align-items:end; flex-wrap:wrap; gap:8px; min-width:0; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
.ev-filters label { display:grid; gap:4px; min-width:0; }
.ev-search { flex:1 1 180px; max-width:400px; }
.ev-filters :deep(input),.ev-filters :deep(select) { height:28px; min-height:28px; min-width:0; padding-block:4px; font-size:12px; }
.ev-advanced { flex-basis:100%; font-size:12px; }
.ev-advanced summary { width:fit-content; padding:4px 0; color:var(--primary); cursor:pointer; }
.ev-advanced summary:focus-visible { outline:2px solid var(--ring); outline-offset:2px; }
.ev-advanced-fields { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(100%,136px),1fr)); gap:8px; padding-top:4px; }
.ev-advanced-fields :deep(.suggestion-input) { width:auto; min-width:0; }
.ev-page :deep(.ev-table) { width:100%; }
.ev-page :deep(.ev-table th),.ev-page :deep(.ev-table td) { padding:8px; vertical-align:top; }
.ev-page :deep(.ev-table th) { background:var(--muted); font-size:12px; }
.ev-page :deep(.ev-table td) { white-space:normal; overflow-wrap:anywhere; font-size:13px; }
.ev-page :deep(.ev-table td:first-child),.ev-page :deep(.ev-table td:nth-child(9)) { font-variant-numeric:tabular-nums; }
.ev-list { display:none; }
.ev-card { display:grid; align-content:start; gap:8px; min-width:0; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
.ev-card-head { display:flex; align-items:start; justify-content:space-between; gap:8px; }
.ev-card-head strong { min-width:0; overflow-wrap:anywhere; }
.ev-score { font-weight:600; color:var(--primary); }
.ev-score span { color:var(--muted-foreground); font-size:12px; font-weight:400; }
.ev-content,.ev-detail dd,.ev-reply-context p { white-space:pre-wrap; overflow-wrap:anywhere; }
.ev-facts { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:4px 12px; padding-top:8px; border-top:1px solid var(--border); font-size:12px; }
.ev-facts div { display:flex; gap:4px; min-width:0; }
.ev-facts dt,.ev-detail dt { flex:none; color:var(--muted-foreground); }
.ev-facts dd { min-width:0; overflow-wrap:anywhere; }
.ev-wide { grid-column:1/-1; }
.ev-actions { display:flex; flex-wrap:wrap; gap:4px; }
.ev-delete { color:var(--destructive); }
:global(.ev-dialog) { max-height:calc(100dvh - 24px); overflow-y:auto; gap:12px; }
:global(.ev-dialog [data-slot="dialog-header"]) { padding-right:32px; }
.ev-detail { display:grid; gap:8px; margin:0; font-size:13px; }
.ev-detail div { display:grid; grid-template-columns:72px minmax(0,1fr); gap:8px; }
.ev-reply-context { padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--muted); overflow-wrap:anywhere; }
.ev-reply-context p { margin-top:4px; }
.ev-reply-label { font-size:13px; font-weight:600; }
@container evaluations (max-width:960px) {
  .ev-page :deep(.table-scroll) { border:0; border-radius:0; overflow:visible; }
  .ev-page :deep([data-slot="table-container"]) { display:none; }
  .ev-list { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .ev-page :deep(.pages) { justify-content:flex-start; }
}
@container evaluations (max-width:540px) {
  .ev-list { grid-template-columns:minmax(0,1fr); }
  .ev-search { flex-basis:100%; max-width:none; }
}
@media(pointer:coarse) {
  .ev-filters :deep(input),.ev-filters :deep(select),.ev-advanced summary,.ev-page :deep([data-slot="button"]),:global(.ev-dialog [data-slot="button"]) { min-width:44px; min-height:44px; }
  .ev-advanced summary { align-content:center; }
}
</style>
