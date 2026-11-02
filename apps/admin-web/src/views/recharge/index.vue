<template>
  <main class="rc-page">
    <Card class="rc-header"><h2>充值与赠送</h2><p>按账户或完整收款流水号核对客服确认的到账记录。金额单位为兴安币。</p></Card>
    <SimplePagedList v-if="canReadRecords" ref="tableRef" :fetcher="rechargeRecordApi.list" :query="query">
      <template #toolbar="{ reload }">
        <div class="rc-filters">
          <div v-if="query.wallet_id" class="rc-scope"><span>钱包 #{{ query.wallet_id }} 关联账户</span><Button size="xs" variant="outline" @click="clearWalletFilter">清除钱包筛选</Button></div>
          <label>账户编号<SuggestionInput :key="`number-${query.wallet_id}`" v-model="query.boss_no" :fetch-suggestions="querySuggest('boss_no')" placeholder="输入编号" label="按编号搜索充值记录" @select="reload" @clear="reload" @keyup.enter="reload" /></label>
          <label>昵称<SuggestionInput :key="`name-${query.wallet_id}`" v-model="query.nickname" :fetch-suggestions="querySuggest('nickname')" placeholder="输入昵称" label="按昵称搜索充值记录" @select="reload" @clear="reload" @keyup.enter="reload" /></label>
          <label>手机号<SuggestionInput :key="`phone-${query.wallet_id}`" v-model="query.phone" :fetch-suggestions="querySuggest('phone')" placeholder="输入手机号" label="按手机号搜索充值记录" @select="reload" @clear="reload" @keyup.enter="reload" /></label>
          <label>收款流水号<Input v-model="query.trade_no" placeholder="完整流水号" aria-label="按完整收款流水号搜索" @keyup.enter="reload" @update:model-value="value => { if (value === '') reload() }" /></label>
          <Button size="xs" @click="reload">查询</Button>
        </div>
      </template>
      <template #default="{ rows }">
        <div class="rc-records"><Table>
          <TableHeader><TableRow><TableHead>收款账户</TableHead><TableHead>到账明细</TableHead><TableHead>流水与凭证</TableHead><TableHead>经办记录</TableHead></TableRow></TableHeader>
          <TableBody><TableRow v-for="row in rows" :key="row.id" class="rc-card">
            <TableCell class="rc-owner">
              <div class="rc-identity"><Avatar><AvatarImage v-if="row.avatar_url" :src="row.avatar_url" alt="" /><AvatarFallback>{{ (row.nickname || row.username || '账').slice(0,1) }}</AvatarFallback></Avatar>
                <div><strong>{{ row.nickname || row.username || '名称待核对' }}</strong><Badge variant="outline">{{ roleLabel(row) }}</Badge></div>
              </div>
              <dl class="rc-facts"><div><dt>编号</dt><dd>{{ row.boss_no || '未记录' }}</dd></div><div><dt>账号</dt><dd>{{ row.username || '未记录' }}</dd></div>
                <div><dt>手机</dt><dd>{{ row.phone || '未填写' }}</dd></div><div v-if="row.user_role === 'CUSTOMER' || row.boss_type_name"><dt>老板分级</dt><dd>{{ row.boss_type_name || '未分级' }}</dd></div>
              </dl>
            </TableCell>
            <TableCell class="rc-money">
              <span class="rc-caption">到账兴安币</span><strong class="rc-credit">{{ coinLabel(row.total_amount) }}</strong>
              <dl class="rc-facts"><div><dt>实充</dt><dd>{{ coinLabel(row.amount) }}</dd></div><div><dt>赠送</dt><dd>{{ coinLabel(row.gift_amount) }}</dd></div></dl>
            </TableCell>
            <TableCell class="rc-receipt">
              <span class="rc-caption">收款流水号</span><span class="rc-number">{{ row.trade_no || '未记录' }}</span>
              <div class="rc-proof-row"><span>收款凭证</span><PrivateImage v-if="row.proof_image_url" :src="row.proof_image_url" :preview-src-list="[row.proof_image_url]" :preview-teleported="true" fit="cover" :label="`查看充值记录 #${row.id} 的收款凭证`" class="rc-proof" /><span v-else>未提供</span></div>
            </TableCell>
            <TableCell class="rc-operator">
              <span class="rc-caption">充值时间</span><time :datetime="row.created_at">{{ formatDateTime(row.created_at) }}</time>
              <dl class="rc-facts"><div><dt>操作人</dt><dd>{{ row.operator_name || '未记录' }}</dd></div><div><dt>备注</dt><dd>{{ row.remark || '未填写' }}</dd></div></dl>
            </TableCell>
          </TableRow></TableBody>
        </Table></div>
      </template>
    </SimplePagedList>
  </main>
</template>

<script setup lang="ts">
import PrivateImage from '@/components/PrivateImage.vue'
import { Avatar, AvatarImage, AvatarFallback } from '@xa/shadcn-vue/avatar'
import { Card } from '@xa/shadcn-vue/card'
import SuggestionInput from '@/components/SuggestionInput.vue'
import { computed, reactive, ref, watch } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Badge } from '@xa/shadcn-vue/badge'
import SimplePagedList from '@/components/SimplePagedList.vue'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
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
function coinLabel(value: number | null | undefined) {
  return typeof value === 'number' && Number.isFinite(value) ? amountToXaCoin(value) + ' 币' : '未记录'
}
function roleLabel(row: any) { return row.user_role === 'PROVIDER' ? '打手' : row.user_role === 'CUSTOMER' ? '老板' : '对象待核对' }

/**
 * 生成某字段的输入建议回调：以当前输入为该字段条件查询后端，
 * 取返回记录的该字段值去重后作为下拉提示。
 */
function querySuggest(field: 'boss_no' | 'nickname' | 'phone') {
  return async (keyword: string): Promise<string[]> => {
    if (!keyword || !canReadRecords.value) {
      return []
    }
    try {
      const revision = auth.sessionRevision
      const walletId = query.wallet_id
      const res = (await rechargeRecordApi.list({ [field]: keyword, wallet_id: walletId, page: 1, page_size: 10 })) as any
      if (revision !== auth.sessionRevision || walletId !== query.wallet_id || !canReadRecords.value) return []
      const seen = new Set<string>()
      const items: { value: string }[] = []
      for (const r of res.data.list || []) {
        const value = r[field]
        if (value && !seen.has(value)) {
          seen.add(value)
          items.push({ value })
        }
      }
      return items.map(item => item.value)
    } catch {
      return []
    }
  }
}
</script>

<style scoped>
.rc-page { display:grid; gap:12px; min-width:0; container:recharges / inline-size; }
.rc-header { padding:12px 16px; gap:4px; background:linear-gradient(112deg,var(--secondary),var(--card) 60%); }
.rc-header h2 { margin:0; font-size:16px; font-weight:600; }
.rc-header p,.rc-caption,.rc-facts dt,.rc-proof-row>span:first-child { font-size:12px; color:var(--muted-foreground); }
.rc-filters { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)) auto; align-items:end; gap:8px; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
.rc-filters label { display:grid; gap:4px; min-width:0; font-size:12px; }
.rc-filters :deep(.suggestion-input),.rc-filters :deep(input) { width:100%; min-width:0; }
.rc-filters :deep(input) { height:28px; min-height:28px; padding-block:4px; font-size:12px; }
.rc-scope,.rc-proof-row,.rc-identity,.rc-identity>div { display:flex; flex-wrap:wrap; align-items:center; gap:8px; }
.rc-scope { grid-column:1/-1; justify-content:space-between; padding-bottom:8px; border-bottom:1px solid var(--border); font-size:13px; }
.rc-records { background:var(--card); min-width:0; }
.rc-records :deep(th),.rc-records :deep(td) { padding:8px; white-space:normal; overflow-wrap:anywhere; vertical-align:top; }
.rc-records :deep(th) { font-size:12px; background:var(--muted); }
.rc-records :deep(td) { font-size:13px; }
.rc-identity { flex-wrap:nowrap; } .rc-identity>div { min-width:0; gap:4px; }
.rc-identity :deep([data-slot='avatar']) { width:24px; height:24px; }
.rc-credit,.rc-number,.rc-operator time { display:block; font-variant-numeric:tabular-nums; }
.rc-credit { font-size:16px; font-weight:600; color:var(--primary); }
.rc-number { font-family:var(--font-mono,monospace); font-size:12px; }
.rc-facts { display:grid; gap:4px; margin-top:8px; }
.rc-facts div { display:flex; align-items:baseline; gap:8px; }
.rc-facts dt { flex-shrink:0; } .rc-facts dd { min-width:0; margin:0; white-space:pre-wrap; }
.rc-proof-row { margin-top:8px; font-size:12px; }
.rc-records :deep(.rc-proof) { width:32px; height:32px; border-radius:8px; border:1px solid var(--border); }
@container recharges (max-width:760px) {
  .rc-filters { grid-template-columns:repeat(2,minmax(0,1fr)); } .rc-filters>button { justify-self:start; }
  .rc-page :deep(.table-scroll),.rc-records :deep([data-slot='table-container']) { overflow:visible; border:0; }
  .rc-records { background:transparent; } .rc-records :deep(table),.rc-records :deep(tbody) { display:grid; }
  .rc-records :deep(thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
  .rc-records :deep(tbody) { grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px; }
  .rc-records :deep(tbody tr) { display:grid; grid-template-columns:minmax(0,1.4fr) minmax(0,1fr); gap:8px; align-items:start; align-content:start; padding:12px; border:1px solid var(--border); border-radius:8px; background:var(--card); }
  .rc-records :deep(td) { padding:0; }
  .rc-records .rc-receipt,.rc-records .rc-operator { grid-column:1/-1; padding-top:8px; border-top:1px solid var(--border); }
  .rc-caption { display:block; } .rc-proof-row,.rc-facts { margin-top:4px; }
  .rc-page :deep(.pages) { justify-content:flex-start; }
}
@container recharges (max-width:500px) { .rc-records :deep(tbody) { grid-template-columns:minmax(0,1fr); } }
@media(pointer:coarse) {
  .rc-filters :deep(input),.rc-page :deep([data-slot='button']) { min-height:44px; }
  .rc-page :deep([data-slot='button']) { min-width:44px; } .rc-records :deep(.rc-proof) { width:44px; height:44px; }
}
</style>
