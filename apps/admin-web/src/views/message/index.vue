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
    </crud-table>
  </div>

  <el-dialog v-model="dialogVisible" title="推送站内消息" width="520px">
    <el-form :model="form" label-width="100px">
      <el-form-item label="推送范围">
        <el-radio-group v-model="form.audience">
          <el-radio-button value="ALL">全部用户</el-radio-button>
          <el-radio-button value="CUSTOMER">老板端</el-radio-button>
          <el-radio-button value="PROVIDER">陪玩端</el-radio-button>
          <el-radio-button value="CUSTOM">指定用户</el-radio-button>
        </el-radio-group>
      </el-form-item>
      <el-form-item v-if="form.audience === 'CUSTOM'" label="接收人ID">
        <el-input v-model="recipientText" placeholder="多个用户ID用英文逗号分隔，如 1,2,3" />
      </el-form-item>
      <el-form-item label="消息类型">
        <el-select v-model="form.type" style="width: 100%">
          <el-option v-for="(v, k) in MESSAGE_TYPE" :key="k" :label="v.label" :value="k" />
        </el-select>
      </el-form-item>
      <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
      <el-form-item label="摘要"><el-input v-model="form.preview" /></el-form-item>
      <el-form-item label="详情"><el-input v-model="form.detail" type="textarea" :rows="4" /></el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialogVisible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="onSend">推送</el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { messageApi } from '@/api/modules'
import { MESSAGE_TYPE } from '@/utils/dict'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ type: '' })

const dialogVisible = ref(false)
const saving = ref(false)
const recipientText = ref('')
const form = reactive<any>({})

function blank() {
  return { audience: 'ALL', type: 'SYSTEM', title: '', preview: '', detail: '' }
}

function onPush() {
  Object.assign(form, blank())
  recipientText.value = ''
  dialogVisible.value = true
}

async function onSend() {
  if (!form.title) { ElMessage.warning('请填写标题'); return }
  let recipientIds: number[] = []
  if (form.audience === 'CUSTOM') {
    recipientIds = recipientText.value
      .split(',')
      .map((s) => Number(s.trim()))
      .filter((n) => Number.isInteger(n) && n > 0)
    if (recipientIds.length === 0) {
      ElMessage.warning('请填写接收人ID或开启全员广播')
      return
    }
  }
  saving.value = true
  try {
    const res = await messageApi.collectionAction('push', {
      broadcast: form.audience === 'ALL',
      audience: form.audience,
      recipient_ids: recipientIds,
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
