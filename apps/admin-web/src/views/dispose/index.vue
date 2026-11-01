<template>
  <div class="dp-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">查看陪玩师的奖励与罚款流水记录</p>
    </div>

    <crud-table ref="tableRef" :fetcher="disposeRecordApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-tag v-if="query.account_id" closable @close="clearAccountFilter">陪玩账户 #{{ query.account_id }}</el-tag>
        <el-input
          v-model="query.keyword"
          placeholder="账号/昵称"
          aria-label="按账号或昵称搜索"
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

      <template #mobile="{ rows }">
        <div class="dp-list" role="list" aria-label="奖罚记录">
          <article v-for="row in rows" :key="row.id" class="dp-card" role="listitem">
            <div class="dp-head"><strong>{{ row.nickname || row.username || '陪玩待核对' }}</strong><el-tag :type="DISPOSE_TYPE[row.dispose_type]?.type" size="small" effect="light" round>{{ row.dispose_type_display }}</el-tag></div>
            <p class="dp-amount" :class="row.dispose_type === 'REWARD' ? 'dp-reward' : 'dp-penalty'">{{ row.dispose_type === 'REWARD' ? '+' : '-' }}{{ amountToXaCoin(row.amount) }} 币</p>
            <dl class="dp-facts">
              <div><dt>账号</dt><dd>{{ row.username || '—' }}</dd></div>
              <div><dt>记录</dt><dd>#{{ row.id }}</dd></div>
              <div class="dp-wide"><dt>原因</dt><dd>{{ row.reason || '未填写' }}</dd></div>
              <div><dt>操作人</dt><dd>{{ row.operator_name || '未记录' }}</dd></div>
              <div><dt>时间</dt><dd>{{ formatDateTime(row.created_at) }}</dd></div>
            </dl>
          </article>
        </div>
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
import { reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import CrudTable from '@/components/CrudTable.vue'
import { disposeRecordApi } from '@/api/modules'
import { DISPOSE_TYPE } from '@/utils/dict'
import { amountToXaCoin, formatDateTime } from '@/utils/format'

const tableRef = ref()
const route = useRoute()
const router = useRouter()
const query = reactive<Record<string, any>>({ keyword: '', dispose_type: '', account_id: '' })
watch(() => route.query.account_id, value => { query.account_id = typeof value === 'string' ? value : ''; tableRef.value?.reload() }, { immediate: true })
function clearAccountFilter() { const next = { ...route.query }; delete next.account_id; void router.replace({ query: next }) }
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
.dp-list { display: grid; gap: 12px; }
.dp-card { padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.dp-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; }
.dp-head strong { min-width: 0; overflow-wrap: anywhere; }
.dp-amount { margin: 8px 0 0; font-size: 18px; font-weight: 650; font-variant-numeric: tabular-nums; }
.dp-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px; margin: 12px 0 0; padding-top: 12px; border-top: 1px solid var(--border); font-size: 12px; }
.dp-facts div { display: flex; gap: 6px; min-width: 0; }
.dp-facts dt { flex: none; color: var(--muted-foreground); }
.dp-facts dd { margin: 0; min-width: 0; overflow-wrap: anywhere; }
.dp-wide { grid-column: 1 / -1; }
.dp-penalty {
  color: var(--destructive);
  font-variant-numeric: tabular-nums;
}
</style>
