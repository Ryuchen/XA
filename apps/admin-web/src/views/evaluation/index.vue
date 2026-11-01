<template>
  <div class="ev-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">集中查看老板对陪玩的服务评价，可回复与管理评价内容</p>
    </div>
    <el-alert v-if="deleteReview && !deleteVisible" title="评价删除结果待核对" type="warning" :closable="false" show-icon>
      <p>订单 {{ deleteReview.orderNo }} 的请求可能仍在处理；读取结果仅是当前快照。核对前暂停删除。</p>
      <p v-if="deleteReviewNotice" role="status">{{ deleteReviewNotice }}</p>
      <el-button size="small" :loading="deleteReviewLoading" @click="checkDeleteReview">读取当前状态</el-button>
      <el-button v-if="deleteReviewChecked" size="small" :disabled="deleteReviewLoading" @click="finishDeleteReview">已核对，解除限制</el-button>
    </el-alert>

    <crud-table ref="tableRef" :fetcher="evaluationApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input v-model="query.keyword" placeholder="订单号 / 老板 / 陪玩" aria-label="订单号、老板或陪玩" clearable style="width: 220px" @keyup.enter="reload" @clear="reload" />
        <el-select v-model="query.score" placeholder="评分" clearable style="width: 110px" @change="reload">
          <el-option v-for="n in 5" :key="n" :label="`${n} 星`" :value="n" />
        </el-select>
        <el-select v-model="query.only_unreplied" placeholder="回复状态" clearable style="width: 130px" @change="reload">
          <el-option label="未回复" value="1" />
        </el-select>
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
        <details class="ev-advanced">
          <summary>更多筛选<span v-if="advancedCount">（{{ advancedCount }}）</span></summary>
          <div class="ev-advanced-fields">
        <el-autocomplete
          v-model="query.order_no"
          :fetch-suggestions="querySuggest('order_no')"
          placeholder="订单号"
          aria-label="按订单号筛选"
          clearable
          value-key="value"
          style="width: 180px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.customer_username"
          :fetch-suggestions="querySuggest('customer_username')"
          placeholder="老板账号"
          aria-label="按老板账号筛选"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.customer_nickname"
          :fetch-suggestions="querySuggest('customer_nickname')"
          placeholder="老板昵称"
          aria-label="按老板昵称筛选"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.provider_username"
          :fetch-suggestions="querySuggest('provider_username')"
          placeholder="陪玩账号"
          aria-label="按陪玩账号筛选"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.provider_nickname"
          :fetch-suggestions="querySuggest('provider_nickname')"
          placeholder="陪玩昵称"
          aria-label="按陪玩昵称筛选"
          clearable
          value-key="value"
          style="width: 140px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
          </div>
        </details>
      </template>

      <template #mobile="{ rows }">
        <div class="ev-list" role="list" aria-label="评价列表">
          <article v-for="row in rows" :key="row.id" class="ev-card" role="listitem">
            <div class="ev-card-head"><strong>{{ row.service_name || '服务待核对' }}</strong><el-tag :type="row.reply_content ? 'success' : 'info'" size="small" effect="light" round>{{ row.reply_content ? '已回复' : '未回复' }}</el-tag></div>
            <p class="ev-score">{{ row.score }} 分 <span>· 技术 {{ row.skill_score }} · 态度 {{ row.attitude_score }} · 沟通 {{ row.communication_score }}</span></p>
            <p class="ev-content">{{ row.content || '未填写评价内容' }}</p>
            <dl class="ev-facts">
              <div><dt>老板</dt><dd>{{ row.customer_name || '—' }}</dd></div>
              <div><dt>陪玩</dt><dd>{{ row.provider_name || '—' }}</dd></div>
              <div class="ev-wide"><dt>订单</dt><dd>{{ row.order_no }}</dd></div>
              <div class="ev-wide"><dt>时间</dt><dd>{{ formatDateTime(row.created_at) }}</dd></div>
            </dl>
            <div class="ev-actions">
              <el-button size="small" @click="onDetail(row)">详情</el-button>
              <el-button v-if="auth.hasPerm('evaluation:reply')" size="small" type="primary" plain @click="onReply(row)">{{ row.reply_content ? '修改回复' : '回复' }}</el-button>
              <el-button v-if="auth.hasPerm('evaluation:delete')" size="small" type="danger" text :disabled="!!deleteReview" @click="onDelete(row)">删除</el-button>
            </div>
          </article>
        </div>
      </template>

      <el-table-column prop="order_no" label="订单号" min-width="164" />
      <el-table-column prop="customer_name" label="老板" width="88" />
      <el-table-column prop="provider_name" label="陪玩" width="88">
        <template #default="{ row }">{{ row.provider_name || '-' }}</template>
      </el-table-column>
      <el-table-column prop="service_name" label="服务" min-width="100" />
      <el-table-column label="评分" width="72">
        <template #default="{ row }">
          {{ row.score }} / 5
        </template>
      </el-table-column>
      <el-table-column label="三维分" width="100">
        <template #default="{ row }">
          <span class="dim">技 {{ row.skill_score }}</span>
          <span class="dim">态 {{ row.attitude_score }}</span>
          <span class="dim">沟 {{ row.communication_score }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="content" label="评价内容" min-width="110" show-overflow-tooltip />
      <el-table-column label="回复" width="72">
        <template #default="{ row }">
          <el-tag v-if="row.reply_content" type="success" size="small" effect="light" round>已回复</el-tag>
          <el-tag v-else type="info" size="small" effect="light" round>未回复</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="评价时间" width="142">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="154" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onDetail(row)">详情</el-button>
          <el-button
            v-if="auth.hasPerm('evaluation:reply')"
            link
            type="warning"
            @click="onReply(row)"
          >{{ row.reply_content ? '修改回复' : '回复' }}</el-button>
          <el-button
            v-if="auth.hasPerm('evaluation:delete')"
            link
            type="danger"
            :disabled="!!deleteReview"
            @click="onDelete(row)"
          >删除</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="评价详情" size="480px" class="ap-panel">
    <template v-if="current">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="订单号">{{ current.order_no }}</el-descriptions-item>
        <el-descriptions-item label="老板">{{ current.customer_name }}</el-descriptions-item>
        <el-descriptions-item label="陪玩">{{ current.provider_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="服务">{{ current.service_name }}</el-descriptions-item>
        <el-descriptions-item label="评分">
          <el-rate :model-value="current.score" disabled size="small" />
        </el-descriptions-item>
        <el-descriptions-item label="专业技能">{{ current.skill_score }} 分</el-descriptions-item>
        <el-descriptions-item label="服务态度">{{ current.attitude_score }} 分</el-descriptions-item>
        <el-descriptions-item label="沟通配合">{{ current.communication_score }} 分</el-descriptions-item>
        <el-descriptions-item label="综合均分">{{ current.avg_score }} 分</el-descriptions-item>
        <el-descriptions-item label="评价内容">{{ current.content || '-' }}</el-descriptions-item>
        <el-descriptions-item label="匿名">{{ current.is_anonymous ? '是' : '否' }}</el-descriptions-item>
        <el-descriptions-item label="回复">{{ current.reply_content || '-' }}</el-descriptions-item>
        <el-descriptions-item label="回复时间">{{ current.replied_at ? formatDateTime(current.replied_at) : '-' }}</el-descriptions-item>
        <el-descriptions-item label="评价时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
      </el-descriptions>
    </template>
  </el-drawer>

  <el-dialog v-model="replyVisible" :title="replyTarget?.reply_content ? '修改回复' : '回复评价'" width="560px" class="ap-modal"
    :before-close="closeReply" :close-on-click-modal="!replying && !replyRefreshing" :close-on-press-escape="!replying && !replyRefreshing" :show-close="!replying && !replyRefreshing">
    <div v-if="replyTarget" class="ev-reply-context">
      <strong>{{ replyTarget.service_name || '服务待核对' }} · {{ replyTarget.score }} 分</strong>
      <p>订单 {{ replyTarget.order_no }}</p>
      <p>{{ replyTarget.content || '未填写评价内容' }}</p>
      <p v-if="replyTarget.reply_content">当前回复：{{ replyTarget.reply_content }}</p>
    </div>
    <el-alert v-if="replyTarget?.reply_content" title="保存后将覆盖当前回复，请先核对内容" type="info" :closable="false" show-icon />
    <el-alert v-if="replyConflict" title="回复已变化，草稿已保留" description="读取最新回复并核对后，再决定是否保存。" type="warning" :closable="false" show-icon>
      <el-button link type="primary" :loading="replyRefreshing" @click="refreshReply">读取最新回复</el-button>
    </el-alert>
    <el-alert v-if="replyUnknown" title="回复结果未确认" description="请求可能仍在处理。请关闭后刷新列表，核对当前回复再操作。" type="warning" :closable="false" show-icon />
    <label class="ev-reply-label" for="evaluation-reply-text">回复内容</label>
    <el-input
      id="evaluation-reply-text"
      v-model="replyText"
      type="textarea"
      :rows="4"
      maxlength="1000"
      show-word-limit
      placeholder="填写回复内容"
      :disabled="replying || replyUnknown || replyRefreshing"
    />
    <template #footer>
      <el-button :disabled="replying || replyRefreshing" @click="replyVisible = false">关闭</el-button>
      <el-button type="primary" :loading="replying" :disabled="replyUnknown || replyConflict || replyRefreshing" @click="submitReply">{{ replyTarget?.reply_content ? '保存回复' : '发布回复' }}</el-button>
    </template>
  </el-dialog>
  <el-dialog v-model="deleteVisible" title="删除评价" width="560px" class="ap-modal"
    :before-close="closeDelete" :close-on-click-modal="!deleteBusy" :close-on-press-escape="!deleteBusy" :show-close="!deleteBusy">
    <div v-if="deleteTarget" class="ev-reply-context">
      <strong>{{ deleteTarget.service_name || '服务待核对' }} · {{ deleteTarget.score }} 分</strong>
      <p>订单 {{ deleteTarget.order_no }}</p>
      <p>评价：{{ deleteTarget.content || '未填写评价内容' }}</p>
      <p>回复：{{ deleteTarget.reply_content || '尚未回复' }}</p>
    </div>
    <el-alert title="删除评价及回复，并回退陪玩评分统计" type="warning" :closable="false" show-icon />
    <el-alert v-if="deleteConflict" title="回复已变化，请重新核对评价" type="warning" :closable="false" show-icon>
      <el-button link type="primary" :loading="deleteBusy" @click="refreshDelete">读取最新评价</el-button>
    </el-alert>
    <el-alert v-if="deleteNotice" :title="deleteNotice" type="error" :closable="false" show-icon />
    <template #footer>
      <el-button :disabled="deleteBusy" @click="closeDelete()">关闭</el-button>
      <el-button type="danger" :loading="deleteBusy" :disabled="deleteConflict || deleteUnavailable" @click="submitDelete">确认删除</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { evaluationApi } from '@/api/modules'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
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
})
const advancedCount = computed(() => ['order_no', 'customer_username', 'customer_nickname', 'provider_username', 'provider_nickname'].filter(key => Boolean(query[key])).length)

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询评价后，
 * 取返回记录对应字段值去重后作为下拉提示。
 */
function querySuggest(
  field: 'order_no' | 'customer_username' | 'customer_nickname' | 'provider_username' | 'provider_nickname',
) {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
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
      cb(items)
    } catch {
      cb([])
    }
  }
}

const detailVisible = ref(false)
const current = ref<any>(null)

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
  replyTarget.value = row
  replyText.value = row.reply_content || ''
  replyUnknown.value = false
  replyConflict.value = false
  replyVisible.value = true
}

function closeReply(done: () => void) { if (!replying.value && !replyRefreshing.value) done() }

async function refreshReply() {
  if (!replyTarget.value || replyRefreshing.value) return
  replyRefreshing.value = true
  try {
    const res = await evaluationApi.retrieve(replyTarget.value.id, true)
    replyTarget.value = res.data
    replyConflict.value = false
  } catch (error: any) {
    if (error?.code !== 'SESSION_CHANGED') ElMessage.error('读取失败，请检查网络或评价是否已删除')
  } finally { replyRefreshing.value = false }
}

async function submitReply() {
  if (replying.value || replyUnknown.value || replyConflict.value || replyRefreshing.value || !replyTarget.value) return
  const content = replyText.value.trim()
  if (!content) {
    ElMessage.warning('请输入回复内容')
    return
  }
  replying.value = true
  try {
    await evaluationApi.action(replyTarget.value.id, 'reply', { reply_content: content, reply_version: replyTarget.value.reply_version }, true)
    ElMessage.success('已回复')
    replyVisible.value = false
    tableRef.value.load()
  } catch (error: any) {
    const status = error?.response?.status ?? (typeof error?.code === 'number' ? error.code : undefined)
    if (error?.code === 'SESSION_CHANGED') return
    if (status === 409 && error?.response?.data?.errorCode === 'EVALUATION_REPLY_CONFLICT') replyConflict.value = true
    else if (!status || status >= 500) replyUnknown.value = true
    else ElMessage.error(error?.response?.data?.msg || error?.msg || '回复未保存，请检查内容和权限后重试')
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
  try { await ElMessageBox.confirm('请确认已核对当前评价；原请求仍可能在处理。', '解除删除限制', { type: 'warning', confirmButtonText: '已核对，解除限制', cancelButtonText: '继续核对' }) } catch { return }
  if (!pageDisposed && revision === auth.sessionRevision && deleteReview.value === marker && deleteReviewChecked.value) { clearDeleteReview(); tableRef.value?.load() }
}
watch([() => auth.sessionRevision, () => auth.profile?.id], restoreDeleteReview, { immediate: true, flush: 'sync' })
onBeforeUnmount(() => { pageDisposed = true; deleteEpoch++ })

function onDelete(row: any) {
  if (deleteBusy.value || deleteReview.value || !auth.hasPerm('evaluation:delete')) return
  deleteTarget.value = row
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
    else ElMessage.error('读取失败，请检查网络后重试')
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
    ElMessage.success('评价已删除')
    deleteVisible.value = false
    tableRef.value?.load()
  } catch (error: any) {
    if (epoch !== deleteEpoch || error?.code === 'SESSION_CHANGED') return
    const status = error?.response?.status ?? (typeof error?.code === 'number' ? error.code : undefined)
    if (status === 409 && error?.response?.data?.errorCode === 'EVALUATION_REPLY_CONFLICT') { clearDeleteReview(); deleteConflict.value = true }
    else if (status === 404) { clearDeleteReview(); deleteUnavailable.value = true; deleteNotice.value = '评价当前不存在，请关闭后刷新列表。' }
    else if (!status || status === 408 || status >= 500) { deleteUnavailable.value = true; deleteNotice.value = '删除结果未确认，请关闭并核对评价；请求可能仍在处理。' }
    else { clearDeleteReview(); ElMessage.error(error?.response?.data?.msg || error?.msg || '删除未完成，请检查权限后重试') }
  } finally { if (epoch === deleteEpoch) deleteBusy.value = false }
}
</script>

<style scoped lang="scss">
.ev-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.ev-page :deep(.page-container) {
  padding: 0;
}
.dim {
  display: inline-block;
  margin-right: 3px;
  font-size: 12px;
  color: var(--muted-foreground);
}
.ev-advanced { flex-basis: 100%; color: var(--foreground); font-size: 13px; }
.ev-advanced summary { width: fit-content; padding: 4px 0; color: var(--el-color-primary); cursor: pointer; }
.ev-advanced summary:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.ev-advanced-fields { display: flex; flex-wrap: wrap; gap: 8px; padding-top: 8px; }
@media (max-width: 600px) { .ev-advanced-fields > :deep(.el-autocomplete) { width: 100% !important; } }
.ev-list { display: grid; gap: 12px; }
.ev-card { padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.ev-card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 12px; }
.ev-card-head strong { min-width: 0; overflow-wrap: anywhere; }
.ev-card-head .el-tag { flex: none; }
.ev-score { margin: 10px 0 0; font-weight: 600; color: var(--primary); }
.ev-score span { color: var(--muted-foreground); font-size: 12px; font-weight: 400; }
.ev-content { margin: 10px 0; white-space: pre-wrap; overflow-wrap: anywhere; }
.ev-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px; margin: 12px 0 0; padding-top: 12px; border-top: 1px solid var(--border); font-size: 12px; }
.ev-facts div { display: flex; gap: 6px; min-width: 0; }
.ev-facts dt { flex: none; color: var(--muted-foreground); }
.ev-facts dd { min-width: 0; margin: 0; overflow-wrap: anywhere; }
.ev-wide { grid-column: 1 / -1; }
.ev-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.ev-actions .el-button + .el-button { margin-left: 0; }
.ev-reply-context { padding: 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--background); overflow-wrap: anywhere; }
.ev-reply-context p { margin: 6px 0 0; white-space: pre-wrap; }
.ev-reply-context + .el-alert, .ev-reply-context + .el-alert + .el-alert { margin-top: 12px; }
.ev-reply-label { display: block; margin: 16px 0 8px; font-weight: 600; }
</style>
