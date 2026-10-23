<template>
  <div class="mg-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">向平台用户批量推送系统通知，并查看站内消息的送达与阅读情况</p>
      <div class="ap-page-head-actions">
        <el-button v-if="auth.hasPerm('message:push')" type="primary" @click="onPush">推送消息</el-button>
      </div>
    </div>

    <crud-table ref="tableRef" :fetcher="messageApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-select v-model="query.type" placeholder="消息类型" clearable style="width: 140px" @change="reload">
          <el-option v-for="(v, k) in MESSAGE_TYPE" :key="k" :label="v.label" :value="k" />
        </el-select>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="recipient_name" label="接收人" min-width="120" />
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
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" title="推送站内消息" width="720px" class="ap-modal">
    <el-form :model="form"  class="ap-modal-form ap-form-grid" label-position="top">
      <el-form-item label="推送范围" class="ap-field--full">
        <el-radio-group v-model="form.audience">
          <el-radio-button value="ALL">全部用户</el-radio-button>
          <el-radio-button value="CUSTOMER">老板端</el-radio-button>
          <el-radio-button value="PROVIDER">陪玩端</el-radio-button>
          <el-radio-button value="CUSTOM">指定用户</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="form.audience === 'CUSTOM'" label="接收人">
        <el-select v-model="recipientIds" multiple filterable remote :remote-method="searchRecipients" :loading="recipientsLoading"
          placeholder="搜索编号、账号或昵称" style="width: 100%">
          <el-option v-for="account in recipientOptions" :key="account.id" :value="account.id"
            :label="`${account.nickname || account.username} · ${account.account_no}（${account.role_display}）`" />
        </el-select>
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
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSend">推送</el-button>
    </template>
  </el-dialog>
  <el-drawer v-model="detailVisible" title="消息详情" size="480px" class="ap-panel">
    <el-descriptions v-if="detailMessage" :column="1" border>
      <el-descriptions-item label="接收人">{{ detailMessage.recipient_name }}</el-descriptions-item>
      <el-descriptions-item label="标题">{{ detailMessage.title }}</el-descriptions-item>
      <el-descriptions-item label="摘要">{{ detailMessage.preview || '—' }}</el-descriptions-item>
      <el-descriptions-item label="内容"><div style="white-space: pre-wrap">{{ detailMessage.detail || '—' }}</div></el-descriptions-item>
      <el-descriptions-item label="阅读状态">{{ detailMessage.is_read ? '已读' : '未读' }}</el-descriptions-item>
      <el-descriptions-item label="发送时间">{{ formatDateTime(detailMessage.created_at) }}</el-descriptions-item>
    </el-descriptions>
  </el-drawer>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
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
const detailMessage = ref<any>(null)
const detailVisible = ref(false)
let searchSequence = 0
const form = reactive<any>({})

function blank() {
  return { audience: 'CUSTOM', type: 'SYSTEM', title: '', preview: '', detail: '' }
}

function onPush() {
  Object.assign(form, blank())
  recipientIds.value = []
  recipientOptions.value = []
  dialogVisible.value = true
}

async function onSend() {
  if (saving.value) return
  if (!form.title.trim()) { ElMessage.warning('请填写标题'); return }
  if (form.audience === 'CUSTOM' && !recipientIds.value.length) { ElMessage.warning('请选择接收人'); return }
  const audienceLabel = form.audience === 'ALL' ? '全部用户' : form.audience === 'CUSTOMER' ? '全部老板' : form.audience === 'PROVIDER' ? '全部陪玩' : `${recipientIds.value.length} 个所选账号`
  try { await ElMessageBox.confirm(`将向${audienceLabel}推送「${form.title.trim()}」，确认发送？`, '确认推送') }
  catch { return }
  saving.value = true
  try {
    const res = await messageApi.collectionAction('push', {
      broadcast: form.audience === 'ALL',
      audience: form.audience,
      recipient_account_ids: form.audience === 'CUSTOM' ? recipientIds.value : [],
      type: form.type,
      title: form.title,
      preview: form.preview,
      detail: form.detail,
    }) as any
    ElMessage.success(res.msg || '推送成功')
    dialogVisible.value = false
    tableRef.value.reload()
  } finally {
    saving.value = false
  }
}

async function searchRecipients(keyword: string) {
  const sequence = ++searchSequence
  const selected = recipientOptions.value.filter(account => recipientIds.value.includes(account.id))
  if (!keyword.trim()) { recipientOptions.value = selected; recipientsLoading.value = false; return }
  recipientsLoading.value = true
  try {
    const result = await request({ url: '/options/message-recipients/', params: { keyword, page_size: 30 } })
    if (sequence === searchSequence) {
      recipientOptions.value = [...new Map([...selected, ...result.data.list].map(account => [account.id, account])).values()]
    }
  } finally { if (sequence === searchSequence) recipientsLoading.value = false }
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
</style>
