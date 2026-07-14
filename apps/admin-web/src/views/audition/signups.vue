<template>
  <div class="su-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">审核陪玩师通过试音链接提交的报名申请</p>
    </div>

    <crud-table ref="tableRef" :fetcher="auditionSignupApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input
          v-model="query.keyword"
          placeholder="游戏/联系方式/报名人"
          clearable
          style="width: 220px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.status" placeholder="审核状态" clearable style="width: 130px" @change="reload">
          <el-option v-for="(v, k) in AUDITION_SIGNUP_STATUS" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="link_title" label="试音活动" min-width="140" show-overflow-tooltip />
      <el-table-column prop="applicant_name" label="报名人" min-width="110" />
      <el-table-column prop="contact" label="联系方式" min-width="130" show-overflow-tooltip />
      <el-table-column prop="game" label="擅长游戏" min-width="120" show-overflow-tooltip />
      <el-table-column label="状态" width="90">
        <template #default="{ row }">
          <el-tag :type="AUDITION_SIGNUP_STATUS[row.status]?.type" effect="light" round>{{ row.status_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="提交时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onDetail(row)">详情</el-button>
          <template v-if="auth.hasPerm('audition:signup_audit') && row.status === 'PENDING'">
            <el-button link type="success" @click="onApprove(row)">通过</el-button>
            <el-button link type="danger" @click="onReject(row)">驳回</el-button>
          </template>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-drawer v-model="detailVisible" title="报名详情" size="480px">
    <template v-if="current">
      <el-descriptions :column="1" border>
        <el-descriptions-item label="试音活动">{{ current.link_title }}</el-descriptions-item>
        <el-descriptions-item label="报名人">{{ current.applicant_name }}</el-descriptions-item>
        <el-descriptions-item label="联系方式">{{ current.contact || '-' }}</el-descriptions-item>
        <el-descriptions-item label="擅长游戏">{{ current.game || '-' }}</el-descriptions-item>
        <el-descriptions-item label="备注">{{ current.remark || '-' }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ current.status_display }}</el-descriptions-item>
        <el-descriptions-item v-if="current.status === 'REJECTED'" label="驳回原因">
          {{ current.audit_remark || '-' }}
        </el-descriptions-item>
        <el-descriptions-item label="审核人">{{ current.auditor_name || '-' }}</el-descriptions-item>
        <el-descriptions-item label="提交时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
        <el-descriptions-item label="审核时间">{{ current.audited_at ? formatDateTime(current.audited_at) : '-' }}</el-descriptions-item>
      </el-descriptions>
    </template>
  </el-drawer>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import CrudTable from '@/components/CrudTable.vue'
import { auditionSignupApi } from '@/api/modules'
import { AUDITION_SIGNUP_STATUS } from '@/utils/dict'
import { formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '', status: '' })

const detailVisible = ref(false)
const current = ref<any>(null)

function onDetail(row: any) {
  current.value = row
  detailVisible.value = true
}

async function onApprove(row: any) {
  try {
    await ElMessageBox.confirm(
      `确认通过「${row.applicant_name}」对「${row.link_title}」的试音报名？`,
      '报名审核',
      { type: 'warning', confirmButtonText: '确认通过' },
    )
  } catch {
    return
  }
  await auditionSignupApi.action(row.id, 'approve')
  ElMessage.success('已通过')
  tableRef.value.load()
}

async function onReject(row: any) {
  let audit_remark = ''
  try {
    const { value } = await ElMessageBox.prompt('请填写驳回原因', '驳回报名', {
      inputPlaceholder: '驳回原因（必填）',
      inputValidator: (v) => (v && v.trim() ? true : '请填写驳回原因'),
    })
    audit_remark = value
  } catch {
    return
  }
  await auditionSignupApi.action(row.id, 'reject', { audit_remark })
  ElMessage.success('已驳回')
  tableRef.value.load()
}
</script>

<style scoped lang="scss">
.su-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.su-page :deep(.page-container) {
  padding: 0;
}
</style>
