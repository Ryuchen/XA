<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Button } from '@xa/shadcn-vue/button'
import { Input } from '@xa/shadcn-vue/input'
import { Card } from '@xa/shadcn-vue/card'
import { Badge } from '@xa/shadcn-vue/badge'
import { Skeleton } from '@xa/shadcn-vue/skeleton'
import { Alert, AlertDescription } from '@xa/shadcn-vue/alert'
import { NativeSelect, NativeSelectOption } from '@xa/shadcn-vue/native-select'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@xa/shadcn-vue/table'
import SimplePagedList from '@/components/SimplePagedList.vue'
import { passPurchaseApi, providerPassConfigApi } from '@/api/modules'
import { useAuthStore } from '@/stores/auth'
import { amountToXaCoin, xaCoinToAmount, formatDateTime } from '@/utils/format'

const auth = useAuthStore(), canEdit = computed(() => auth.hasPerm('escort:edit'))
const config = ref<any>(null), query = reactive({ keyword: '', tier: '' })
const loading = ref(false), saving = ref(false), loadError = ref(false), notice = ref('')
async function loadConfig() {
  loading.value = true; loadError.value = false
  try { const result = await providerPassConfigApi.get() as any; config.value = { ...result.data, products: result.data.products.map((item: any) => ({ ...item, price_coin: Number(amountToXaCoin(item.daily_price)) })) } }
  catch { loadError.value = true }
  finally { loading.value = false }
}
function validDelay(value: unknown) { return typeof value === 'number' && Number.isInteger(value) && value >= 0 && value <= 86400 }
async function saveConfig() {
  if (!config.value || saving.value || loading.value || loadError.value || !canEdit.value) return
  if (!validDelay(config.value.no_pass_delay_seconds) || config.value.products.some((item: any) => !validDelay(item.delay_seconds))) { notice.value = '可见延迟须为 0 至 86400 秒的整数'; return }
  if (config.value.products.some((item: any) => typeof item.price_coin !== 'number' || !Number.isFinite(item.price_coin) || item.price_coin < 0.1 || Math.abs(item.price_coin * 10 - Math.round(item.price_coin * 10)) > 1e-8)) { notice.value = '每日价格须至少为 0.1 币，最多一位小数'; return }
  saving.value = true; notice.value = ''
  try { await providerPassConfigApi.update({ products: config.value.products.map((item: any) => ({ tier: item.tier, daily_price: xaCoinToAmount(item.price_coin), delay_seconds: item.delay_seconds })), no_pass_delay_seconds: config.value.no_pass_delay_seconds }); notice.value = '通行证规则已更新'; await loadConfig() }
  catch (error) { notice.value = error instanceof Error ? error.message : '保存失败，请重试' }
  finally { saving.value = false }
}
onMounted(loadConfig)
</script>
<template>
  <div class="pass-page">
    <Card class="pass-config">
      <header class="section-heading">
        <div><h2>价格与接单优先级</h2><p>陪玩使用钱包按日购买，同档续费顺延有效期。</p></div>
        <Badge variant="outline">{{ canEdit ? '可配置' : '只读' }}</Badge>
      </header>
      <Alert v-if="loadError" variant="destructive" role="alert"><AlertDescription>规则加载失败，请重试 <Button size="xs" variant="outline" @click="loadConfig">重新加载</Button></AlertDescription></Alert>
      <div v-if="loading" class="pass-products" aria-label="正在读取规则"><Skeleton v-for="n in 4" :key="n" class="config-skeleton" /></div>
      <Alert v-if="notice" role="status"><AlertDescription>{{ notice }}</AlertDescription></Alert>
      <template v-if="config && !loading && !loadError">
        <p class="policy-note">价格调整仅适用于后续购买，已购有效期保留。可见延迟调整立即影响所有陪玩的公共单池。</p>
        <div class="pass-products">
          <article v-for="item in config.products" :key="item.tier" class="tier">
            <h3>{{ item.name }}</h3>
            <template v-if="canEdit">
              <label>每天价格（兴安币）<Input v-model.number="item.price_coin" type="number" min="0.1" step="0.1" :aria-label="item.name + '每天价格（兴安币）'" :disabled="saving || loading" /></label>
              <label>公共订单可见延迟（秒）<Input v-model.number="item.delay_seconds" type="number" min="0" max="86400" step="1" :aria-label="item.name + '公共订单可见延迟（秒）'" :disabled="saving || loading" /></label>
            </template>
            <dl v-else class="tier-values"><div><dt>每日价格</dt><dd>{{ amountToXaCoin(item.daily_price) }} <small>兴安币</small></dd></div><div><dt>订单可见延迟</dt><dd>{{ item.delay_seconds }} <small>秒</small></dd></div></dl>
          </article>
        </div>
        <footer class="config-footer">
          <label v-if="canEdit" class="pass-delay">无通行证可见延迟（秒）<Input v-model.number="config.no_pass_delay_seconds" type="number" min="0" max="86400" step="1" :disabled="saving || loading" /></label>
          <p v-else>无通行证可见延迟：<strong>{{ config.no_pass_delay_seconds }} 秒</strong></p>
          <Button v-if="canEdit" size="xs" :disabled="saving || loading || loadError" @click="saveConfig">{{ saving ? '保存中…' : '保存规则' }}</Button>
        </footer>
      </template>
    </Card>
    <Card class="pass-purchases">
      <header class="section-heading"><div><h2>购买记录</h2><p>查询陪玩购买、续费及对应的扣款流水。</p></div></header>
      <SimplePagedList :fetcher="passPurchaseApi.list" :query="query">
        <template #toolbar="{ reload }">
          <div class="pass-filter">
            <label>搜索购买记录<Input v-model="query.keyword" placeholder="陪玩编号 / 昵称 / 账号 / 流水号" @keyup.enter="reload" /></label>
            <label>通行证档位<NativeSelect v-model="query.tier" aria-label="通行证档位" @update:model-value="reload"><NativeSelectOption value="">全部通行证</NativeSelectOption><NativeSelectOption v-for="item in config?.products || []" :key="item.tier" :value="item.tier">{{ item.name }}</NativeSelectOption></NativeSelect></label>
            <Button size="xs" variant="outline" @click="reload">查询购买记录</Button>
          </div>
        </template>
        <template #default="{ rows }">
          <Table class="pass-table">
            <TableHeader><TableRow><TableHead>陪玩编号</TableHead><TableHead>陪玩</TableHead><TableHead>通行证</TableHead><TableHead>实付</TableHead><TableHead>生效时间</TableHead><TableHead>到期时间</TableHead><TableHead>扣款流水</TableHead><TableHead>购买时间</TableHead></TableRow></TableHeader>
            <TableBody><TableRow v-for="row in rows" :key="row.id">
              <TableCell data-label="陪玩编号">{{ row.account_no }}</TableCell><TableCell data-label="陪玩">{{ row.nickname || row.username }}</TableCell>
              <TableCell data-label="通行证">{{ row.tier_display }}</TableCell><TableCell data-label="实付" class="paid">{{ amountToXaCoin(row.paid_amount) }} 币</TableCell>
              <TableCell data-label="生效时间">{{ formatDateTime(row.starts_at) }}</TableCell><TableCell data-label="到期时间">{{ formatDateTime(row.expires_at) }}</TableCell>
              <TableCell data-label="扣款流水" class="transaction">{{ row.tx_no }}</TableCell><TableCell data-label="购买时间">{{ formatDateTime(row.created_at) }}</TableCell>
            </TableRow></TableBody>
          </Table>
        </template>
      </SimplePagedList>
    </Card>
  </div>
</template>
<style scoped>
.pass-page { display:grid; gap:16px; min-width:0; container:pass / inline-size; }
.pass-config,.pass-purchases { display:grid; gap:16px; min-width:0; padding:16px; border-radius:12px; border-color:var(--border); box-shadow:none; }
.section-heading,.config-footer { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; }
.pass-page h2,.pass-page h3,.pass-page p,.pass-page dl,.pass-page dd { margin:0; overflow-wrap:anywhere; }
.pass-page h2 { font-size:16px; line-height:1.5; } .pass-page h3 { font-size:14px; line-height:1.5; }
.section-heading p,.policy-note,.config-footer p,.pass-page dt,.pass-page small,.pass-page label { color:var(--muted-foreground); font-size:12px; font-weight:400; }
.section-heading p { margin-top:4px; } .policy-note { background:var(--secondary); border-radius:8px; padding:8px 12px; }
.pass-products { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:8px; }
.tier { display:grid; align-content:start; gap:12px; min-width:0; border:1px solid var(--border); border-radius:8px; padding:12px; background:var(--card); }
.tier-values { display:grid; gap:8px; } .tier-values dd { margin-top:4px; font-size:16px; line-height:1.25; font-weight:600; font-variant-numeric:tabular-nums; }
.config-footer { padding-top:12px; border-top:1px solid var(--border); align-items:end; } .config-footer strong,.paid { color:var(--foreground); font-variant-numeric:tabular-nums; }
.pass-page label { display:grid; gap:4px; min-width:0; } .pass-page :deep([data-slot='input']),.pass-page :deep(select) { height:28px; min-height:28px; font-size:12px; color:var(--foreground); }
.pass-filter :deep([data-slot='native-select-wrapper']) { width:100%; } .pass-page :deep(.pages [data-slot='native-select-wrapper']) { width:auto; } .pass-page :deep(select) { padding:4px 28px 4px 8px; line-height:16px; } .pass-page :deep([data-slot='badge']),.pass-page :deep([data-slot='button'][data-variant='outline']) { border-color:var(--border); } .config-skeleton { height:108px; border-radius:8px; }
.pass-filter { display:grid; grid-template-columns:minmax(160px,320px) minmax(108px,160px) auto; align-items:end; justify-content:start; gap:8px; }
.pass-filter button { justify-self:start; } .pass-page :deep(.paged-list),.pass-page :deep(.table-scroll) { min-width:0; }
.pass-page :deep(.pass-table) { min-width:860px; font-size:12px; } .pass-page :deep(.pass-table .paid) { font-weight:600; }
@container pass (max-width:860px) {
 .pass-products { grid-template-columns:repeat(2,minmax(0,1fr)); }
 .pass-page :deep(.table-scroll),.pass-page :deep([data-slot='table-container']) { border:0; border-radius:0; overflow:visible; }
 .pass-page :deep(.pass-table) { display:block; width:100%; min-width:0; }
 .pass-page :deep(.pass-table thead) { position:absolute; width:1px; height:1px; overflow:hidden; clip-path:inset(50%); }
 .pass-page :deep(.pass-table tbody) { display:grid; gap:8px; }
 .pass-page :deep(.pass-table tbody tr) { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:8px 12px; padding:12px; border:1px solid var(--border); border-radius:8px; }
 .pass-page :deep(.pass-table td) { display:block; min-width:0; padding:0; white-space:normal; overflow-wrap:anywhere; }
 .pass-page :deep(.pass-table td::before) { content:attr(data-label); display:block; font-size:12px; color:var(--muted-foreground); font-weight:400; margin-bottom:4px; }
 .pass-page :deep(.pass-table td.transaction) { grid-column:1 / -1; }
}
@container pass (max-width:520px) {
 .pass-config,.pass-purchases { gap:12px; padding:12px; } .pass-filter { grid-template-columns:minmax(0,1fr) auto; }
 .pass-filter>label:first-child { grid-column:1 / -1; } .tier { padding:8px; } .config-footer .pass-delay { flex:1 1 172px; }
}
@container pass (max-width:340px) { .pass-products:has(input) { grid-template-columns:minmax(0,1fr); } }
@media(max-width:600px),(pointer:coarse) { .pass-page :deep([data-slot='button']),.pass-page :deep(select),.pass-page :deep([data-slot='input']) { min-height:44px; min-width:44px; } }
</style>
