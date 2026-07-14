<template>
  <div class="dp-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">查看陪玩师的奖励与罚款流水记录</p>
    </div>

    <crud-table ref="tableRef" :fetcher="disposeRecordApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-input
          v-model="query.keyword"
          placeholder="账号/昵称"
          clearable
          style="width: 200px"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-select v-model="query.dispose_type" placeholder="类型" clearable style="width: 120px" @change="reload">
          <el-option v-for="(v, k) in DISPOSE_TYPE" :key="k" :label="v.label" :value="k" />
        </el-select>
        <el-button type="primary" @click="reload">查询</el-button>
      </template>

      <el-table-column prop="id" label="ID" width="70" />
      <el-table-column prop="username" label="账号" min-width="130" />
      <el-table-column prop="nickname" label="昵称" min-width="120" />
      <el-table-column label="类型" width="90">
        <template #default="{ row }">
          <el-tag :type="DISPOSE_TYPE[row.dispose_type]?.type" effect="light" round>{{ row.dispose_type_display }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="金额" width="120">
        <template #default="{ row }">
          <span :class="row.dispose_type === 'REWARD' ? 'dp-reward' : 'dp-penalty'">
            {{ row.dispose_type === 'REWARD' ? '+' : '-' }}{{ amountToXaCoin(row.amount) }} 币
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="reason" label="原因" min-width="180" show-overflow-tooltip />
      <el-table-column prop="operator_name" label="操作人" width="120" />
      <el-table-column label="时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
    </crud-table>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import CrudTable from '@/components/CrudTable.vue'
import { disposeRecordApi } from '@/api/modules'
import { DISPOSE_TYPE } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'

const tableRef = ref()
const query = reactive<Record<string, any>>({ keyword: '', dispose_type: '' })
</script>

<style scoped lang="scss">
.dp-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.dp-page :deep(.page-container) {
  padding: 0;
}

.dp-reward {
  color: var(--success);
  font-variant-numeric: tabular-nums;
}
.dp-penalty {
  color: var(--destructive);
  font-variant-numeric: tabular-nums;
}
</style>
