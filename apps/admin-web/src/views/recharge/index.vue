<template>
  <div class="rc-page">
    <div class="ap-page-head">
      <p class="ap-page-intro">查看客服确认的充值与赠送记录</p>
    </div>

    <crud-table v-if="canReadRecords" ref="tableRef" :fetcher="rechargeRecordApi.list" :query="query">
      <template #toolbar="{ reload }">
        <el-tag v-if="query.wallet_id" closable @close="clearWalletFilter">钱包 #{{ query.wallet_id }} 关联账户</el-tag>
        <el-autocomplete
          v-model="query.boss_no"
          :fetch-suggestions="querySuggest('boss_no')"
          placeholder="编号"
          aria-label="按编号搜索充值记录"
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
          aria-label="按昵称搜索充值记录"
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
          aria-label="按手机号搜索充值记录"
          clearable
          value-key="value"
          style="width: 150px"
          @select="reload"
          @keyup.enter="reload"
          @clear="reload"
        />
        <el-input v-model="query.trade_no" placeholder="收款流水号（精确）" aria-label="按完整收款流水号搜索" clearable style="width: 190px" @keyup.enter="reload" @clear="reload" />
        <el-button type="primary" :icon="Search" @click="reload">查询</el-button>
      </template>

      <template #mobile="{ rows }">
        <div class="rc-mobile-list" role="list" aria-label="充值记录列表">
          <article v-for="row in rows" :key="row.id" class="rc-card" role="listitem">
            <div class="rc-card-head">
              <el-avatar :size="32" :src="row.avatar_url || undefined">{{ (row.nickname || row.username || '账').slice(0, 1) }}</el-avatar>
              <div class="rc-identity"><strong>{{ row.nickname || row.username || '名称待核对' }}</strong><span>{{ row.boss_no || `记录 #${row.id}` }} · {{ row.username || '账号待核对' }}</span></div>
              <el-tag effect="light" round>{{ roleLabel(row) }}</el-tag>
            </div>
            <dl class="rc-facts">
              <div><dt>到账兴安币</dt><dd class="rc-credit">{{ amountToXaCoin(row.total_amount) }} 币</dd></div>
              <div><dt>充值时间</dt><dd>{{ formatDateTime(row.created_at) }}</dd></div>
              <div><dt>实充</dt><dd>{{ amountToXaCoin(row.amount) }} 币</dd></div>
              <div><dt>赠送</dt><dd>{{ amountToXaCoin(row.gift_amount) }} 币</dd></div>
              <div class="rc-wide"><dt>收款流水号</dt><dd>{{ row.trade_no || '未记录' }}</dd></div>
              <div><dt>操作人</dt><dd>{{ row.operator_name || '未记录' }}</dd></div>
              <div><dt>手机号</dt><dd>{{ row.phone || '未填写' }}</dd></div>
              <div v-if="row.user_role === 'CUSTOMER'"><dt>老板分级</dt><dd>{{ row.boss_type_name || '未分级' }}</dd></div>
              <div v-if="row.remark" class="rc-wide"><dt>备注</dt><dd>{{ row.remark }}</dd></div>
            </dl>
            <div class="rc-proof-row"><span>收款凭证</span><PrivateImage v-if="row.proof_image_url" :src="row.proof_image_url" :preview-src-list="[row.proof_image_url]" :preview-teleported="true" fit="cover" :label="`查看充值记录 #${row.id} 的收款凭证`" class="rc-proof" /><span v-else>未提供</span></div>
          </article>
        </div>
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
      <el-table-column label="对象" width="80">
        <template #default="{ row }">{{ roleLabel(row) }}</template>
      </el-table-column>
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
          <PrivateImage
            v-if="row.proof_image_url"
            :src="row.proof_image_url"
            :preview-src-list="[row.proof_image_url]"
            :preview-teleported="true"
            fit="cover"
            :label="`查看充值记录 #${row.id} 的收款凭证`"
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
import PrivateImage from "@/components/PrivateImage.vue"
import { computed, reactive, ref, watch } from 'vue'
import { Search } from '@element-plus/icons-vue'
import CrudTable from '@/components/CrudTable.vue'
import { rechargeRecordApi } from '@/api/modules'
import { amountToXaCoin, formatDateTime } from '@/utils/format'
import { useAuthStore } from '@/stores/auth'
import { useRoute, useRouter } from 'vue-router'

const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
const canReadRecords = computed(() => Boolean(auth.token) && auth.hasPerm('recharge:view'))
const tableRef = ref()
const query = reactive<Record<string, any>>({ boss_no: '', nickname: '', phone: '', trade_no: '', wallet_id: '' })
watch(() => route.query.wallet_id, value => { query.wallet_id = typeof value === 'string' ? value : ''; tableRef.value?.reload() }, { immediate: true })
function clearWalletFilter() { const next = { ...route.query }; delete next.wallet_id; void router.replace({ query: next }) }
function roleLabel(row: any) { return row.user_role === 'PROVIDER' ? '打手' : row.user_role === 'CUSTOMER' ? '老板' : '对象待核对' }

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询后端，
 * 取返回记录的该字段值去重后作为下拉提示。
 */
function querySuggest(field: 'boss_no' | 'nickname' | 'phone') {
  return async (keyword: string, cb: (items: any[]) => void) => {
    if (!keyword || !canReadRecords.value) {
      cb([])
      return
    }
    try {
      const revision = auth.sessionRevision
      const walletId = query.wallet_id
      const res = (await rechargeRecordApi.list({ [field]: keyword, wallet_id: walletId, page: 1, page_size: 10 })) as any
      if (revision !== auth.sessionRevision || walletId !== query.wallet_id || !canReadRecords.value) { cb([]); return }
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
.rc-mobile-list { display: grid; gap: 12px; }
.rc-card { padding: 14px; border: 1px solid var(--border); border-radius: 12px; background: var(--card); }
.rc-card-head { display: flex; align-items: center; gap: 10px; }
.rc-identity { display: grid; flex: 1; min-width: 0; gap: 3px; }
.rc-identity strong, .rc-identity span { overflow-wrap: anywhere; }
.rc-identity span { color: var(--muted-foreground); font-size: 12px; }
.rc-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 14px; margin: 12px 0; padding-top: 12px; border-top: 1px solid var(--border); }
.rc-facts div { min-width: 0; }
.rc-facts dt { color: var(--muted-foreground); font-size: 12px; }
.rc-facts dd { margin: 2px 0 0; overflow-wrap: anywhere; font-variant-numeric: tabular-nums; }
.rc-wide { grid-column: 1 / -1; }
.rc-credit { color: var(--primary); font-weight: 650; }
.rc-proof-row { display: flex; align-items: center; gap: 10px; color: var(--muted-foreground); font-size: 12px; }
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
  width: 32px;
  height: 32px;
  border-radius: 4px;
  border: 1px solid var(--border);
  cursor: pointer;
}
.rc-empty {
  color: var(--muted-foreground);
}
</style>
