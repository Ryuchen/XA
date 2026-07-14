<template>
  <div class="rc-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">查看企业微信客服确认并按 1:10 兑换规则录入的兴安币记录</p>
    </div>

    <crud-table ref="tableRef" :fetcher="rechargeRecordApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-autocomplete
          v-model="query.boss_no"
          :fetch-suggestions="querySuggest('boss_no')"
          placeholder="编号"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.nickname"
          :fetch-suggestions="querySuggest('nickname')"
          placeholder="昵称"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-autocomplete
          v-model="query.phone"
          :fetch-suggestions="querySuggest('phone')"
          placeholder="手机号"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <el-table-column label="编号" width="110">
        <template #default="{ row }">
          <span class="rc-no">{{ row.boss_no || '-' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="头像" width="70">
        <template #default="{ row }">
          <el-avatar :size="32" :src="row.avatar_url || undefined">{{ (row.nickname || row.username).charAt(0) }}</el-avatar>
        </template>
      </el-table-column>
      <el-table-column prop="username" label="账号" min-width="130" />
      <el-table-column prop="nickname" label="昵称" min-width="120" />
      <el-table-column prop="phone" label="手机号" width="130" />
      <el-table-column label="老板分级" width="110">
        <template #default="{ row }">{{ row.boss_type_name || '-' }}</template>
      </el-table-column>
      <el-table-column label="充值兴安币" width="120">
        <template #default="{ row }">{{ amountToXaCoin(row.amount) }} 币</template>
      </el-table-column>
      <el-table-column label="赠送兴安币" width="120">
        <template #default="{ row }">{{ amountToXaCoin(row.gift_amount) }} 币</template>
      </el-table-column>
      <el-table-column label="到账兴安币" width="120">
        <template #default="{ row }">{{ amountToXaCoin(row.total_amount) }} 币</template>
      </el-table-column>
      <el-table-column label="交易编号" min-width="150" show-overflow-tooltip>
        <template #default="{ row }">
          <span class="rc-trade">{{ row.trade_no || '-' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="凭证" width="80">
        <template #default="{ row }">
          <el-image
            v-if="row.proof_image_url"
            :src="row.proof_image_url"
            :preview-src-list="[row.proof_image_url]"
            :preview-teleported="true"
            fit="cover"
            class="rc-proof"
          />
          <span v-else class="rc-empty">-</span>
        </template>
      </el-table-column>
      <el-table-column prop="remark" label="备注" min-width="160" show-overflow-tooltip />
      <el-table-column prop="operator_name" label="操作人" width="120" />
      <el-table-column label="充值时间" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
    </crud-table>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { rechargeRecordApi } from '@/api/modules'
import { amountToXaCoin, formatDateTime } from '@/utils/format'

const tableRef = ref()
const query = reactive<Record<string, any>>({ boss_no: '', nickname: '', phone: '' })

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询后端，
 * 取返回记录的该字段值去重后作为下拉提示。
 */
function querySuggest(field: 'boss_no' | 'nickname' | 'phone') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword) {
      cb([])
      return
    }
    try {
      const res = (await rechargeRecordApi.list({ [field]: keyword, page: 1, page_size: 10 })) as any
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const r of res.data.list || []) {
        const value = r[field]
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
</script>

<style scoped lang="scss">
.rc-page {
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.rc-page :deep(.page-container) {
  padding: 0;
}
.rc-no {
  font-family: var(--font-mono);
  font-size: 12px;
  letter-spacing: 0.02em;
}
.rc-trade {
  font-family: var(--font-mono);
  font-size: 12px;
}
.rc-proof {
  width: 40px;
  height: 40px;
  border-radius: 4px;
  border: 1px solid var(--border);
  cursor: pointer;
}
.rc-empty {
  color: var(--muted-foreground);
}
</style>
