<template>
  <div class="au-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">记录管理员在后台的写操作（新增/修改/删除），用于安全审计与追溯。</p>
    </div>

    <crud-table ref="tableRef" :fetcher="auditApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input
          v-model="query.keyword"
          placeholder="操作人/路径/资源"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.method" placeholder="请求方法" clearable style="width: 130px" @change="reload">
          <el-option label="POST" value="POST" />
          <el-option label="PUT" value="PUT" />
          <el-option label="PATCH" value="PATCH" />
          <el-option label="DELETE" value="DELETE" />
        </el-select>
        <el-date-picker
          v-model="dateRange"
          type="daterange"
          value-format="YYYY-MM-DD"
          range-separator="至"
          start-placeholder="开始日期"
          end-placeholder="结束日期"
          style="width: 240px"
          @change="onDateChange(reload)"
        />
        <el-button type="primary" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="operator_name" label="操作人" min-width="120" />
      <el-table-column label="方法" width="90">
        <template #default="{ row }">
          <el-tag :type="methodType(row.method)" effect="light" round>{{ row.method }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="resource" label="资源" min-width="120" />
      <el-table-column prop="object_id" label="对象ID" width="90" />
      <el-table-column prop="path" label="请求路径" min-width="200" show-overflow-tooltip />
      <el-table-column prop="status_code" label="状态码" width="90" />
      <el-table-column prop="ip" label="来源IP" width="130" />
      <el-table-column prop="created_at" label="时间" width="170" />
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="detailVisible" title="审计详情" width="560px">
    <el-descriptions v-if="current" :column="1" border>
      <el-descriptions-item label="操作人">{{ current.operator_name }}</el-descriptions-item>
      <el-descriptions-item label="方法">{{ current.method }}</el-descriptions-item>
      <el-descriptions-item label="资源">{{ current.resource }}</el-descriptions-item>
      <el-descriptions-item label="对象ID">{{ current.object_id || '-' }}</el-descriptions-item>
      <el-descriptions-item label="请求路径">{{ current.path }}</el-descriptions-item>
      <el-descriptions-item label="状态码">{{ current.status_code }}</el-descriptions-item>
      <el-descriptions-item label="来源IP">{{ current.ip || '-' }}</el-descriptions-item>
      <el-descriptions-item label="时间">{{ current.created_at }}</el-descriptions-item>
      <el-descriptions-item label="请求参数">
        <pre class="au-body">{{ prettyBody(current.request_body) }}</pre>
      </el-descriptions-item>
    </el-descriptions>
  </el-dialog>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import CrudTable from '@/components/CrudTable.vue'
import { auditApi } from '@/api/modules'

const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '', method: '', start_date: '', end_date: '' })
const dateRange = ref<[string, string] | null>(null)

const detailVisible = ref(false)
const current = ref<any>(null)

function methodType(method: string) {
  if (method === 'DELETE') return 'danger'
  if (method === 'POST') return 'success'
  return 'warning'
}

function onDateChange(reload: () => void) {
  query.start_date = dateRange.value?.[0] || ''
  query.end_date = dateRange.value?.[1] || ''
  reload()
}

function onDetail(row: any) {
  current.value = row
  detailVisible.value = true
}

function prettyBody(body: any) {
  try {
    return JSON.stringify(body ?? {}, null, 2)
  } catch {
    return String(body)
  }
}
</script>

<style scoped lang="scss">
.au-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.au-page :deep(.page-container) {
  padding: 0;
}
.au-body {
  margin: 0;
  max-height: 320px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
