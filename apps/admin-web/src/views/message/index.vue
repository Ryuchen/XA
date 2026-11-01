<template>
  <div class="mg-page">
    <el-alert v-if="pending" title="上次推送结果待核对" type="warning" :closable="false" show-icon>
      <p>「{{ pending.title }}」（{{ formatDateTime(pending.startedAt) }}）可能已创建部分记录。请按标题和时间核对；原请求可能仍在处理。</p>
      <el-button link type="primary" @click="tableRef?.reload()">刷新列表</el-button><el-button link type="primary" @click="clearPendingAfterReview">已核对，解除限制</el-button>
    </el-alert>
    <div class="ap-page-head">
      <p class="ap-page-intro">查看站内消息记录与阅读状态</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('message:push')" type="primary" :disabled="!!pending" @click="onPush">推送消息</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="messageApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-select v-model="query.type" placeholder="消息类型" clearable style="width: 140px" @change="reload">
          <el-option v-for="(v, k) in MESSAGE_TYPE" :key="k" :label="v.label" :value="k" />
        </el-select>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column label="接收人" min-width="160"><template #default="{ row }">{{ row.recipient_name || '—' }}<small class="mg-identity">{{ recipientIdentity(row) }}</small></template></el-table-column>
      <el-table-column label="类型" width="120">
        <template #default="{ row }">
          <el-tag :type="MESSAGE_TYPE[row.type]?.type" effect="light" round>{{ row.type_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="title" label="标题" min-width="160" />
      <el-table-column prop="preview" label="摘要" min-width="200" show-overflow-tooltip />
      <el-table-column label="已读" width="80">
        <template #default="{ row }">
          <el-tag :type="row.is_read ? 'info' : 'warning'" effect="light" round>{{ row.is_read ? '已读' : '未读' }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right"><template #default="{ row }">
        <el-button link type="primary" @click="detailMessage = row; detailVisible = true">详情</el-button>
      </template></el-table-column>
      <template #mobile="{ rows }"><ul class="mg-list" aria-label="站内消息记录">
        <li v-for="row in rows" :key="row.id" class="mg-card"><div class="mg-card-head"><strong>{{ row.title }}</strong><el-tag :type="row.is_read ? 'info' : 'warning'" size="small">{{ row.is_read ? '已读' : '未读' }}</el-tag></div>
          <p>{{ row.type_display }} · {{ formatDateTime(row.created_at) }}</p><p>接收人 {{ row.recipient_name || '—' }} · {{ recipientIdentity(row) }}</p><p v-if="row.preview">{{ row.preview }}</p>
          <el-button size="small" @click="detailMessage = row; detailVisible = true">详情</el-button>
        </li>
      </ul></template>
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" title="推送站内消息" width="min(720px, calc(100vw - 24px))" class="ap-modal" :close-on-click-modal="false" :before-close="beforeClose">
    <el-alert v-if="sendUncertain" title="发送结果未确认" type="warning" :closable="false" show-icon>可能已有部分记录生成。请关闭后按标题和时间核对列表；原请求可能仍在处理，请勿立即重复发送。</el-alert>
    <el-form :model="form" :disabled="saving || reviewVisible" class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="推送范围" class="ap-field--full">
        <el-radio-group v-model="form.audience">
          <el-radio-button value="ALL">全部启用旧用户</el-radio-button>
          <el-radio-button value="CUSTOMER">老板端</el-radio-button>
          <el-radio-button value="PROVIDER">陪玩端</el-radio-button>
          <el-radio-button value="CUSTOM">指定用户</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-alert v-if="form.audience !== 'CUSTOM'" class="ap-field--full" type="info" :closable="false" show-icon :title="form.audience === 'ALL' ? '包含启用的后台旧账号，按发送时旧用户状态计算' : '按发送时启用的旧用户角色计算'">有些旧用户可能未关联业务账号；创建记录不代表已送达或阅读。</el-alert>
      <el-form-item v-if="form.audience === 'CUSTOM'" label="接收人">
        <el-select v-model="recipientIds" multiple filterable remote :remote-method="searchRecipients" :loading="recipientsLoading"
          placeholder="搜索编号、账号或昵称" style="width: 100%">
          <el-option v-for="account in recipientOptions" :key="account.id" :value="account.id" :disabled="account.recipient_mapped !== true"
            :label="`${account.nickname || account.username} · ${account.account_no}（${account.role_display}）${account.recipient_mapped === true ? '' : ' · 身份待核对'}`" />
        </el-select>
        <span v-if="recipientError" class="mg-error">接收人读取失败 <el-button link type="primary" @click="searchRecipients(lastKeyword)">重试</el-button></span>
        <span class="mg-hint">仅可选择有有效身份映射的账号；同名账号请核对编号。</span>
      </el-form-item>
      <el-form-item label="消息类型">
        <el-select v-model="form.type" style="width: 100%">
          <el-option v-for="(v, k) in MESSAGE_TYPE" :key="k" :label="v.label" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item label="标题"><el-input v-model="form.title" maxlength="100" show-word-limit /></el-form-item>
      <el-form-item label="摘要"><el-input v-model="form.preview" maxlength="255" show-word-limit /></el-form-item>
      <el-form-item label="详情" class="ap-field--full"><el-input v-model="form.detail" type="textarea" :rows="4" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="saving" @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="sendUncertain" @click="prepareSend">核对并推送</el-button>
    </template>
  </el-dialog>
  <el-dialog v-model="reviewVisible" title="核对站内消息" width="min(520px, calc(100vw - 24px))" append-to-body :close-on-click-modal="false" :close-on-press-escape="!saving" :show-close="!saving" class="ap-modal">
    <template v-if="review"><p><strong>接收范围：</strong>{{ review.audienceLabel }}</p><ul v-if="review.recipients.length" class="mg-recipients"><li v-for="item in review.recipients" :key="item.id">{{ item.label }}</li></ul>
      <p><strong>类型：</strong>{{ review.typeLabel }}</p><p><strong>标题：</strong>{{ review.payload.title }}</p><p><strong>摘要：</strong>{{ review.payload.preview || '—' }}</p><p class="mg-detail"><strong>详情：</strong>{{ review.payload.detail || '—' }}</p><p class="mg-hint">提交创建站内消息记录；数量以服务端结果为准，并非送达或阅读回执。</p></template>
    <template #footer><el-button :disabled="saving" @click="reviewVisible = false">返回修改</el-button><el-button type="primary" :loading="saving" @click="confirmSend">确认推送</el-button></template>
  </el-dialog>
  <el-drawer v-model="detailVisible" title="消息详情" size="min(480px, 100vw)" class="ap-panel">
    <el-descriptions v-if="detailMessage" :column="1" border>
      <el-descriptions-item label="接收人">{{ detailMessage.recipient_name }}</el-descriptions-item>
      <el-descriptions-item label="账号">{{ recipientIdentity(detailMessage) }}</el-descriptions-item>
      <el-descriptions-item label="标题">{{ detailMessage.title }}</el-descriptions-item>
      <el-descriptions-item label="摘要">{{ detailMessage.preview || '—' }}</el-descriptions-item>
      <el-descriptions-item label="内容"><div style="white-space: pre-wrap">{{ detailMessage.detail || '—' }}</div></el-descriptions-item>
      <el-descriptions-item label="阅读状态">{{ detailMessage.is_read ? '已读' : '未读' }}</el-descriptions-item>
      <el-descriptions-item label="发送时间">{{ formatDateTime(detailMessage.created_at) }}</el-descriptions-item>
    </el-descriptions>
  </el-drawer>
</template>

<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { messageApi } from '@/api/modules'
import { request } from '@/api/request'
import { MESSAGE_TYPE } from '@/utils/dict'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ type: '' })

const dialogVisible = ref(false)
const saving = ref(false)
const recipientIds = ref<number[]>([])
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
  try { await ElMessageBox.confirm(`已核对「${item.title}」的记录？原请求仍可能处理；解除后请自行判断是否需要再次发送。`, '解除待核对限制', { type: 'warning', confirmButtonText: '已核对，解除', cancelButtonText: '继续核对' }) }
  catch { return }
  if (revision === auth.sessionRevision && pending.value === item) { clearPending(); sendUncertain.value = false }
}
function recipientIdentity(row: any) { return row.recipient_no ? `${row.recipient_no} · ${row.recipient_username || '—'}` : row.recipient_username ? `旧账号 · ${row.recipient_username}` : '身份待核对' }
function beforeClose(done: () => void) { if (!saving.value && !reviewVisible.value) done() }
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
  ++searchSequence
  Object.assign(form, blank())
  recipientIds.value = []
  recipientOptions.value = []
  recipientError.value = false; lastKeyword.value = ''; sendUncertain.value = false; review.value = null
  dialogVisible.value = true
}

function prepareSend() {
  if (saving.value || sendUncertain.value || pending.value || !auth.hasPerm('message:push')) return
  if (!form.title.trim()) { ElMessage.warning('请填写标题'); return }
  if (form.audience === 'CUSTOM' && !recipientIds.value.length) { ElMessage.warning('请选择接收人'); return }
  const recipients = form.audience === 'CUSTOM' ? recipientIds.value.map(id => recipientOptions.value.find(item => item.id === id)) : []
  if (recipients.some(item => !item || item.recipient_mapped !== true)) { ElMessage.warning('接收人身份待核对，请重新选择'); return }
  const audienceLabel = form.audience === 'ALL' ? '全部启用旧用户（包括后台账号）' : form.audience === 'CUSTOMER' ? '启用的旧老板账号' : form.audience === 'PROVIDER' ? '启用的旧陪玩账号' : `${recipients.length} 个所选账号`
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
    if (snapshot.recipients.length && typeof res.data?.count === 'number' && res.data.count < snapshot.recipients.length) ElMessage.warning(`已创建 ${res.data.count} 条记录，少于所选 ${snapshot.recipients.length} 个账号，请核对列表`)
    else ElMessage.success(typeof res.data?.count === 'number' ? `已创建 ${res.data.count} 条站内消息记录` : '请求已完成，请在列表核对记录')
    reviewVisible.value = false
    dialogVisible.value = false
    tableRef.value?.reload()
  } catch (error: any) {
    if (snapshot.revision !== auth.sessionRevision) return
    reviewVisible.value = false
    if (!error?.response || error.response.status >= 500) sendUncertain.value = true
    else { clearPending(); ElMessage.error(error.response.data?.msg || '推送失败，请检查接收人后重试') }
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

<style scoped lang="scss">
.mg-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.mg-page :deep(.page-container) {
  padding: 0;
}
.tip {
  margin-left: 8px;
  font-size: 12px;
  color: var(--muted-foreground);
}
.mg-identity { display: block; color: var(--muted-foreground); overflow-wrap: anywhere; }
.mg-list { display: grid; gap: 12px; margin: 0; padding: 0; list-style: none; }
.mg-card { padding: 14px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card); }
.mg-card-head { display: flex; justify-content: space-between; gap: 12px; }
.mg-card strong, .mg-card p, .mg-detail, .mg-recipients { overflow-wrap: anywhere; }
.mg-card p { margin: 9px 0; color: var(--muted-foreground); font-size: 12px; }
.mg-error { color: var(--el-color-danger); font-size: 12px; }
.mg-hint { color: var(--muted-foreground); font-size: 12px; }
.mg-detail { white-space: pre-wrap; }
.mg-recipients { max-height: 160px; overflow-y: auto; padding-left: 20px; }
</style>
