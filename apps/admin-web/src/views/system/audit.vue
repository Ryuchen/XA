<template>
  <div class="au-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">查看后台写请求记录
        <el-popover trigger="click" placement="bottom-start" :width="280">
          <template #reference><el-button link type="primary" aria-label="查看记录范围说明">范围说明</el-button></template>
          <p>仅记录后台写请求中 HTTP 响应码小于 400 且成功保存的记录。HTTP 400 及以上的请求不在此列表；没有记录不能证明操作未发生。响应码也不代表业务结果。</p>
        </el-popover>
      </p>
    </div>

    <crud-table ref="tableRef" :fetcher="auditApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input
          v-model="query.keyword"
          placeholder="操作人/路径/资源"
          aria-label="搜索操作人、请求路径或资源"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.method" placeholder="请求方法" aria-label="请求方法" clearable style="width: 130px" @change="reload">
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
          aria-label="记录日期范围"
          style="width: 240px"
          @change="onDateChange(reload)"
        />
        <el-button type="primary" @click="reload">查询</el-button>
        <el-button @click="resetFilters(reload)">清除筛选</el-button>
      </template>

      <template #mobile="{ rows }">
        <div class="au-list" role="list" aria-label="后台请求记录">
          <article v-for="row in rows" :key="row.id" class="au-card" role="listitem">
            <div class="au-card-head">
              <strong>{{ row.operator_name || '未知操作人' }}</strong>
              <el-tag :type="methodType(row.method)" effect="light" round>{{ row.method }}</el-tag>
            </div>
            <p class="au-target">{{ row.resource || '未记录资源' }}<template v-if="row.object_id"> · #{{ row.object_id }}</template></p>
            <div class="au-meta"><span>{{ formatDateTime(row.created_at) }}</span><span>HTTP {{ row.status_code }}</span></div>
            <div class="au-actions"><el-button @click="onDetail(row)">查看详情</el-button></div>
          </article>
        </div>
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
      <el-table-column prop="status_code" label="HTTP 状态码" width="110" />
      <el-table-column prop="ip" label="来源IP" width="130" />
      <el-table-column label="时间" width="170"><template #default="{ row }">{{ formatDateTime(row.created_at) }}</template></el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="onDetail(row)">详情</el-button>
        </template>
      </el-table-column>
    </crud-table>
  </div>

  <el-dialog v-model="detailVisible" title="审计详情" width="560px" class="ap-modal au-detail">
    <el-descriptions v-if="current" :column="1" border>
      <el-descriptions-item label="操作人">{{ current.operator_name }}</el-descriptions-item>
      <el-descriptions-item label="方法">{{ current.method }}</el-descriptions-item>
      <el-descriptions-item label="资源">{{ current.resource }}</el-descriptions-item>
      <el-descriptions-item label="对象ID">{{ current.object_id || '-' }}</el-descriptions-item>
      <el-descriptions-item label="请求路径">{{ current.path }}</el-descriptions-item>
      <el-descriptions-item label="HTTP 状态码">{{ current.status_code }}</el-descriptions-item>
      <el-descriptions-item label="来源IP">{{ current.ip || '-' }}</el-descriptions-item>
      <el-descriptions-item label="时间">{{ formatDateTime(current.created_at) }}</el-descriptions-item>
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
import { formatDateTime } from '@/utils/format'

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

function resetFilters(reload: () => void) {
  Object.assign(query, { keyword: '', method: '', start_date: '', end_date: '' })
  dateRange.value = null
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
.au-list { display: grid; gap: 12px; }
.au-card { padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.au-card-head, .au-meta { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.au-card-head strong { min-width: 0; overflow-wrap: anywhere; }
.au-target { margin: 10px 0 6px; overflow-wrap: anywhere; font-weight: 600; }
.au-meta { flex-wrap: wrap; color: var(--muted-foreground); font-size: 12px; }
.au-actions { display: flex; justify-content: flex-end; margin-top: 10px; }
</style>

<style lang="scss">
@media (max-width: 720px) {
  .au-detail .el-descriptions__row { display: block; }
  .au-detail .el-descriptions__cell { display: block; width: 100%; box-sizing: border-box; }
  .au-detail .el-descriptions__label { border-bottom: 0; }
}
</style>
